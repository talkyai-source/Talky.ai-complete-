"""Regression tests for the 2026-07-13 END_CALL root-cause fix.

Before the fix, turn_streamer ran every sentence through the REAL
``guardrails.clean_response`` (audio-tag stripping) BEFORE the sentinel was
ever read — clean_response's bracket-tag regex treats "[[END_CALL]]" as an
audio tag and erases it, so ``session._end_call_requested`` was never set and
the agent never hung up. These tests exercise the REAL (unmocked)
llm_guardrails singleton — the same one production uses — with a non-
eleven_v3 TTS model id, which is exactly the destructive path the bug lived
on, to prove the sentinel now survives it.
"""
import asyncio

import pytest

from app.domain.models.session import CallSession
from app.domain.services.llm_guardrails import get_guardrails
from app.domain.services.voice_pipeline_service import VoicePipelineService


class StreamingLLMProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


def _make_session() -> CallSession:
    session = CallSession(
        call_id="call-endcall-1",
        campaign_id="campaign-123",
        lead_id="lead-123",
        provider_call_id="provider-123",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-123",
    )
    session.barge_in_event = asyncio.Event()
    return session


def _make_service(chunks) -> VoicePipelineService:
    from unittest.mock import AsyncMock, MagicMock

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=StreamingLLMProvider(chunks),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    # Non-eleven_v3, non-cartesia model id -> _PROFILE_NONE -> clean_response
    # strips EVERY bracket tag, including "[[END_CALL]]". This is the exact
    # destructive path the 2026-07-13 fix protects against.
    service.tts_provider._model_id = "deepgram-aura-2"
    return service


@pytest.mark.asyncio
async def test_sentinel_in_trailing_tail_survives_real_clean_response(monkeypatch):
    """Common real-world shape: the model's closing line ends in punctuation
    (flushes as a complete sentence) and the sentinel rides the unterminated
    tail right after it — the ~676 trailing-buffer emit in turn_streamer."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    from unittest.mock import AsyncMock

    service = _make_service(["Thanks for calling, goodbye! [[END_CALL]]"])
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    # Sanity check the real guardrails singleton actually destroys the raw
    # token on this model id — proves the test is exercising the real bug
    # surface, not a mock that would hide a regression.
    guardrails = get_guardrails()
    mangled = guardrails.clean_response(
        "Thanks for calling, goodbye! [[END_CALL]]", tts_model_id="deepgram-aura-2",
    )
    assert "END_CALL" not in mangled

    await service._stream_llm_and_tts(session)

    assert session._end_call_requested is True

    spoken_texts = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    for text in spoken_texts:
        assert "END_CALL" not in text
        assert "[[" not in text and "]]" not in text
        assert text.strip() != "[]"


@pytest.mark.asyncio
async def test_sentinel_inline_with_terminal_punctuation_survives(monkeypatch):
    """The sentinel lands INSIDE a fully-flushed sentence (e.g. punctuation
    after the token) — the per-sentence flush loop path."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    from unittest.mock import AsyncMock

    service = _make_service(["Take care now [[END_CALL]]."])
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service._stream_llm_and_tts(session)

    assert session._end_call_requested is True
    spoken_texts = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    for text in spoken_texts:
        assert "END_CALL" not in text
        assert "[[" not in text and "]]" not in text


@pytest.mark.asyncio
async def test_token_only_reply_sets_flag_and_never_synthesizes_stray_brackets(monkeypatch):
    """Voicemail case: the model replies with the sentinel ALONE. Must set
    the flag and must never hand a "[]"/empty-bracket remnant to TTS."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    from unittest.mock import AsyncMock

    service = _make_service(["[[END_CALL]]"])
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service._stream_llm_and_tts(session)

    assert session._end_call_requested is True
    for c in service.synthesize_and_send_audio.await_args_list:
        text = c.args[1]
        assert "END_CALL" not in text
        assert text.strip() != "[]"


@pytest.mark.asyncio
async def test_normal_reply_without_sentinel_does_not_flag_end_call(monkeypatch):
    """Control case: a normal reply must NOT set the hangup flag."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    from unittest.mock import AsyncMock

    service = _make_service(["Sure, I can help with that today."])
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service._stream_llm_and_tts(session)

    assert getattr(session, "_end_call_requested", False) is False
