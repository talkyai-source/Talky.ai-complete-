import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


class EmptySTTProvider:
    async def stream_transcribe(self, *args, **kwargs):
        if False:
            yield None


class StreamingLLMProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


class StreamingTTSProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_synthesize(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


def _make_session() -> CallSession:
    session = CallSession(
        call_id="call-123",
        campaign_id="campaign-123",
        lead_id="lead-123",
        provider_call_id="provider-123",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-123",
    )
    session.barge_in_event = asyncio.Event()
    return session


def test_find_sentence_end_accepts_terminal_punctuation_without_space():
    assert VoicePipelineService._find_sentence_end("Hello there.") == len("Hello there.") - 1
    assert VoicePipelineService._find_sentence_end("Can you hear me?") == len("Can you hear me?") - 1
    assert VoicePipelineService._find_sentence_end("Great!") == len("Great!") - 1


def test_find_sentence_end_does_not_split_terminal_abbreviation():
    assert VoicePipelineService._find_sentence_end("Please ask Dr.") == -1
    assert VoicePipelineService._find_sentence_end("Please ask A.") == -1


@pytest.mark.asyncio
async def test_process_audio_stream_does_not_start_turn_before_user_speech():
    media_gateway = MagicMock()
    media_gateway.get_audio_queue.return_value = asyncio.Queue()

    service = VoicePipelineService(
        stt_provider=EmptySTTProvider(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=media_gateway,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    session.stt_active = False

    await service.process_audio_stream(session)

    service.latency_tracker.start_turn.assert_not_called()
    service.latency_tracker.mark_listening_start.assert_not_called()


@pytest.mark.asyncio
async def test_zero_token_turn_speaks_recovery_line(monkeypatch):
    """If the LLM stream completes with NO spoken tokens (e.g. a reasoning model
    burned its budget), the turn must speak a recovery line, not go silent."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")  # disable filler for the test
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=StreamingLLMProvider([]),  # empty -> zero tokens
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)

    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service._stream_llm_and_tts(session)

    assert service.synthesize_and_send_audio.await_count >= 1
    spoken = service.synthesize_and_send_audio.await_args.args[1].lower()
    assert "catch that" in spoken or "say it again" in spoken


@pytest.mark.asyncio
async def test_normal_turn_does_not_trigger_recovery_line():
    """A normal reply must NOT also append the recovery line."""
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=StreamingLLMProvider(["Hello there."]),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)

    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service._stream_llm_and_tts(session)

    spoken = [c.args[1].lower() for c in service.synthesize_and_send_audio.await_args_list]
    assert any("hello there" in s for s in spoken)
    assert not any("catch that" in s for s in spoken)


@pytest.mark.asyncio
async def test_get_llm_response_marks_first_token_latency():
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=StreamingLLMProvider(["Hello", " there."]),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    response = await service.get_llm_response(session, "Hi")

    assert response == "Hello there."
    service.latency_tracker.mark_llm_first_token.assert_called_once_with(session.call_id)


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_skips_latency_tracking_for_greeting():
    media_gateway = AsyncMock()
    tts_provider = StreamingTTSProvider([b"\x00" * 160])

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service.synthesize_and_send_audio(
        session,
        "Hello there.",
        track_latency=False,
    )

    media_gateway.send_audio.assert_awaited_once_with(session.call_id, b"\x00" * 160)
    media_gateway.flush_tts_buffer.assert_awaited_once_with(session.call_id)
    service.latency_tracker.mark_tts_first_chunk.assert_not_called()
    service.latency_tracker.mark_response_start.assert_not_called()
    service.latency_tracker.mark_audio_start.assert_not_called()
    service.latency_tracker.mark_tts_end.assert_not_called()
    service.latency_tracker.mark_completed.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_marks_user_turn_latency_hooks():
    media_gateway = AsyncMock()
    tts_provider = StreamingTTSProvider([b"\x00" * 160])

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service.synthesize_and_send_audio(
        session,
        "Hello there.",
        track_latency=True,
    )

    service.latency_tracker.mark_tts_first_chunk.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_response_start.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_audio_start.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_tts_end.assert_called_once_with(session.call_id)
    service.latency_tracker.mark_completed.assert_called_once_with(session.call_id)
