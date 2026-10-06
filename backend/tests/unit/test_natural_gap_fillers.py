"""Gap fillers that fit the moment, and number read-backs with human pauses.

Owner request 2026-09-29: the agent should sound natural while it works out an
answer and when it reads a number back -- not "Ummm, let me check" and not a
flat run of digits. Practitioner guidance (ElevenLabs soft-timeout fillers,
Sierra's latency write-up, Duplex) converges on: fill only a real gap, once per
turn, not on every turn, rotate the wording, and fit what the caller said. The
2026-08-06 rule stands: no lookup narration ("let me check", "one sec").
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.services.scripts.prompts import accent_fillers as af
from app.services.scripts.spoken_email_normalizer import natural_phone_readback

# ── the filler in the real pipeline ───────────────────────────────────────

class _SlowStream:
    """First token arrives after ``delay`` s -- a slow knowledge/LLM turn."""

    def __init__(self, chunks, delay):
        self._chunks, self._delay = chunks, delay

    async def stream_chat_with_timeout(self, *args, **kwargs):
        await asyncio.sleep(self._delay)
        for chunk in self._chunks:
            yield chunk


async def _spoken(monkeypatch, chunks, caller, *, turn_id=3, last_filler_turn=None, delay=0.15):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "20")
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=_SlowStream(chunks, delay),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="call-filler-1", campaign_id="c1", lead_id="l1",
        provider_call_id="p1", system_prompt="<company_knowledge>Dojo Plus is £11.99 per location per month.</company_knowledge>",
        voice_id="voice-1",
    )
    session.turn_id = turn_id
    if last_filler_turn is not None:
        session._last_filler_turn = last_filler_turn
    session.conversation_history = [Message(role=MessageRole.USER, content=caller)]
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    return [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]


@pytest.mark.asyncio
async def test_slow_model_answer_is_not_prefixed_or_rewritten_by_a_canned_filler(monkeypatch):
    spoken = await _spoken(monkeypatch, ["Got it, Dojo Plus is £11.99 per location a month."], "How much is Dojo Plus?")
    assert spoken == ["Got it, Dojo Plus is £11.99 per location a month."]


@pytest.mark.asyncio
async def test_a_fast_answer_gets_no_filler(monkeypatch):
    spoken = await _spoken(monkeypatch, ["Dojo Plus is £11.99 per location a month."],
                           "How much is Dojo Plus?", delay=0.0)
    assert spoken == ["Dojo Plus is £11.99 per location a month."]


@pytest.mark.asyncio
async def test_no_filler_on_the_first_reply(monkeypatch):
    spoken = await _spoken(monkeypatch, ["Hi, is that Uzair?"], "Hello?", turn_id=0)
    assert spoken == ["Hi, is that Uzair?"]


@pytest.mark.asyncio
async def test_no_filler_two_turns_running(monkeypatch):
    spoken = await _spoken(monkeypatch, ["Dojo Plus is £11.99 per location a month."],
                           "How much is Dojo Plus?", turn_id=4, last_filler_turn=3)
    assert spoken == ["Dojo Plus is £11.99 per location a month."]


@pytest.mark.asyncio
async def test_no_filler_after_a_bare_yes(monkeypatch):
    spoken = await _spoken(monkeypatch, ["I'll pass that to Azian."], "Yes.")
    assert spoken == ["I'll pass that to Azian."]


# ── number read-backs ─────────────────────────────────────────────────────

def test_numbers_are_read_in_country_chunks_with_pauses():
    assert natural_phone_readback("+923120750496") == "plus 9 2, 3 1 2, 0 7 5, 0 4 9 6"
    assert natural_phone_readback("+447429916656") == "plus 4 4, 7 4 2 9, 9 1 6, 6 5 6"
    assert natural_phone_readback("+16473476870") == "plus 1, 6 4 7, 3 4 7, 6 8 7 0"
    assert natural_phone_readback("+442046132300") == "plus 4 4, 2 0, 4 6 1 3, 2 3 0 0"


def test_every_digit_survives_the_grouping():
    for phone in ("+923120750496", "+447429916656", "+971501234567", "+4791234567", "5551234567"):
        spoken = natural_phone_readback(phone)
        assert re.sub(r"\D", "", spoken) == re.sub(r"\D", "", phone)
