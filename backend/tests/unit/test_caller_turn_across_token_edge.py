"""The self-dialogue cut must work when the boundary falls between tokens.

Live test, 2026-09-23, call 51450718 (softphone 940007 -> ext 940003):

    AGENT: "So that's 312-207-504-96, correct?Yes, that's correct. What date..."

The 9e7f9c65 fix detected a terminator with no space after it, and was verified
on whole strings. In production the model streams tokens; a token ended exactly
on the '?' and the next token was "Yes". The splitter saw end-of-buffer,
flushed an ordinary sentence, and "Yes" began a fresh one - the missing space
fell BETWEEN tokens, where the whole-buffer check could not see it.
model_wrote_caller_turn fired 0 times on a call where it should have fired.

These drive the REAL VoicePipelineService._stream_llm_and_tts with the tokens
split the way production split them.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


async def _spoken(chunks, monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_Stream(chunks),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="call-edge-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    return [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]


@pytest.mark.asyncio
async def test_the_live_call_split_is_cut_after_the_agents_own_question(monkeypatch):
    spoken = await _spoken(
        [
            "So that's 312 075 0496, correct?",
            "Yes, that's correct.",
            " What date would you prefer for your appointment?",
        ],
        monkeypatch,
    )
    joined = " ".join(spoken)
    assert "correct?" in joined
    assert "Yes, that's correct" not in joined, spoken
    assert "What date" not in joined, spoken


@pytest.mark.asyncio
async def test_a_period_split_before_an_invented_line_is_cut(monkeypatch):
    spoken = await _spoken(
        ["Could I get the best email for that?", "Sure", ", it's bob at gmail dot com."],
        monkeypatch,
    )
    assert not any("bob at gmail" in s for s in spoken), spoken


@pytest.mark.asyncio
async def test_normal_tokens_with_their_leading_space_are_all_spoken(monkeypatch):
    """Real tokens carry their own leading space; nothing may be cut.

    Two sentences: this bare session's sentence cap is 2, and dropping a third
    is the cap working, not this guard.
    """
    spoken = await _spoken(
        ["Thanks for that.", " What date would you prefer?"],
        monkeypatch,
    )
    joined = " ".join(spoken)
    assert "Thanks for that." in joined
    assert "What date would you prefer?" in joined


@pytest.mark.asyncio
async def test_a_decimal_split_across_tokens_is_not_a_boundary(monkeypatch):
    spoken = await _spoken(
        ["The rate is 1.", "5 percent on card payments.", " Does that work for you?"],
        monkeypatch,
    )
    joined = " ".join(spoken)
    assert "5 percent" in joined and "Does that work" in joined, spoken
