"""What the caller hears is grounded, honest and not a loop -- on any campaign.

Every case below is a line from live call d644f0ea (2026-09-28, Dojo-PC →
940007), driven through the REAL VoicePipelineService._stream_llm_and_tts with
a scripted model (same harness as test_phone_readback_guard.py), so the
assertions are about what reached TTS, not about a helper's return value.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.services.scripts.call_state_tracker import CallState

DOJO_FACTS = (
    "Dojo Plus is £11.99 per location per month. Seven-Day Settlement is a "
    "separate £10 per month add-on. Go Max: £229 upfront or £25 monthly."
)
ASK_PROVIDER_1 = "Just so I have it right, are you still using Dojo for payments, or are you using another provider now?"
ASK_PROVIDER_2 = "Dojo Plus is £11.99 per location each month. Are you still using Dojo for payments, or have you switched to another provider?"


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


async def _spoken(chunks, monkeypatch, *, history=(), prompt=DOJO_FACTS, slots=None):
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
        call_id="call-grounding-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt=f"<company_knowledge>{prompt}</company_knowledge>",
        voice_id="voice-1",
    )
    session.captured_slots = slots if slots is not None else CallState()
    session.conversation_history = [Message(role=r, content=c) for r, c in history]
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    return [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]


# ── made-up figures ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_price_from_the_knowledge_is_spoken_as_written(monkeypatch):
    spoken = await _spoken(["Dojo Plus is £11.99 per location each month."], monkeypatch)
    assert spoken == ["Dojo Plus is £11.99 per location each month."]


# ── claiming a contact the agent does not have ─────────────────────────────


# ── re-asking the same scripted question ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_second_ask_is_still_allowed(monkeypatch):
    spoken = await _spoken(
        ["Dojo Plus is £11.99 per location each month. "
         "Are you still using Dojo for payments, or have you switched to another provider?"],
        monkeypatch,
        history=[(MessageRole.ASSISTANT, ASK_PROVIDER_1), (MessageRole.USER, "How much is Dojo plus?")],
    )
    assert len(spoken) == 2


@pytest.mark.asyncio
async def test_a_reply_that_is_only_the_question_is_still_spoken(monkeypatch):
    """Dropping it would leave the caller in silence."""
    spoken = await _spoken(
        ["Could you let me know if you're still using Dojo for payments, or have you switched to another provider?"],
        monkeypatch,
        history=[
            (MessageRole.ASSISTANT, ASK_PROVIDER_1),
            (MessageRole.ASSISTANT, ASK_PROVIDER_2),
            (MessageRole.USER, "But it will give me on a card payment."),
        ],
    )
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_a_price_split_across_tokens_is_spoken_whole_and_kept(monkeypatch):
    """"£11." then "99 ..." must not become "£11" (ungrounded) + "99"."""
    spoken = await _spoken(["Dojo Plus is £11.", "99 per location each month."], monkeypatch)
    assert spoken == ["Dojo Plus is £11.99 per location each month."]
