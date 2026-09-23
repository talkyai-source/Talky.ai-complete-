"""A NEEDS_CLARIFICATION/INVALID phone must never be spoken back as confirmed.

Production, call 6aaeb4dd (2026-09-23, build 76426ed6). The agent asked "And
a contact phone number?" (arming phone mode -- the §15 fix, working
correctly). The caller said "nine two three zero one six two five three one
nine." (11 digits, no '+', no campaign-configured region), which
contact_capture.py correctly classified NEEDS_CLARIFICATION with
clarification_prompt "Please repeat the complete phone number beginning with
+ and its country code." -- and correctly refused to let it become a
confirmable value (state.phone stayed None).

The ONLY enforcement was that clarification_prompt injected as advisory text
into the system prompt (prompt_builder.compose_system_prompt). Cerebras
gpt-oss-120b ignored it and generated:

    "So that's 923 016 253 19, correct?"

the caller said "Yeah. Yeah.", and nothing was ever stored -- except the
fabricated number leaked into call.json's summary_json.key_points, an
operator-facing false assurance.

Contrast: call b97ce4c5 the same day gave a valid +92 number, was correctly
read back and confirmed, and lead_slot_capture wrote it. That path (an
AWAITING_CONFIRMATION/CONFIRMED read-back) must keep working untouched.

Both states below are built with the real state machine
(update_state_from_agent_turn / update_state_from_user_turn), driving the
REAL VoicePipelineService._stream_llm_and_tts -- same harness as
test_caller_turn_across_token_edge.py -- to prove what the caller actually
hears, not just what the state machine computed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


async def _spoken(chunks, captured_slots, monkeypatch):
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
        call_id="call-phone-readback-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.captured_slots = captured_slots
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    return [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]


def _needs_clarification_state() -> CallState:
    """The exact 6aaeb4dd shape: armed phone mode, then an 11-digit,
    region-less number the normaliser cannot place."""
    state = update_state_from_agent_turn(CallState(), "And a contact phone number?")
    state = update_state_from_user_turn(
        state,
        "Yeah. It's nine two three zero one six two five three one nine.",
        phone_region=None,
    )
    assert state.phone_capture is not None
    assert state.phone_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert state.phone is None
    return state


def _awaiting_confirmation_state() -> CallState:
    """The b97ce4c5 shape: a valid number, correctly pending confirmation."""
    state = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    assert state.phone_capture is not None
    assert state.phone_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    return state


@pytest.mark.asyncio
async def test_an_unvalidated_number_is_not_read_back_as_confirmed(monkeypatch):
    state = _needs_clarification_state()
    spoken = await _spoken(
        ["So that's 923 016 253 19, correct?"],
        state,
        monkeypatch,
    )
    joined = " ".join(spoken)
    # The fabricated read-back must never reach TTS...
    assert "923" not in joined, spoken
    assert "correct?" not in joined, spoken
    # ...the caller must instead hear the capture's own re-ask.
    assert "country code" in joined, spoken
    # And the turn ends there -- no follow-on sentence in the same completion.
    assert len(spoken) == 1, spoken


@pytest.mark.asyncio
async def test_a_valid_pending_readback_is_spoken_untouched(monkeypatch):
    """b97ce4c5: a validated, AWAITING_CONFIRMATION number must pass through."""
    state = _awaiting_confirmation_state()
    spoken = await _spoken(
        ["So that's 415 555 2671, is that right?"],
        state,
        monkeypatch,
    )
    joined = " ".join(spoken)
    assert "415 555 2671" in joined, spoken
    assert "is that right?" in joined, spoken
