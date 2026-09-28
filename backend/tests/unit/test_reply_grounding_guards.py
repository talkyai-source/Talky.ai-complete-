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
from app.domain.services.voice_pipeline.conversation_guards import (
    CONTACT_REASK,
    is_repeated_question,
    unbacked_contact_claim,
)
from app.domain.services.voice_pipeline.grounded_figures import (
    UNGROUNDED_FIGURE_REPLACEMENT,
    ground_spoken_figures,
)
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
        system_prompt=prompt,
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
async def test_a_total_nobody_gave_is_never_quoted(monkeypatch):
    """d644f0ea: "That comes to £21.99 a month." -- £11.99 + £10, two units."""
    spoken = await _spoken(
        ["That comes to £21.99 a month."], monkeypatch,
        history=[(MessageRole.USER, "So what's the total? Twenty one dot ninety nine?")],
    )
    assert spoken == [UNGROUNDED_FIGURE_REPLACEMENT]


@pytest.mark.asyncio
async def test_a_price_from_the_knowledge_is_spoken_as_written(monkeypatch):
    spoken = await _spoken(["Dojo Plus is £11.99 per location each month."], monkeypatch)
    assert spoken == ["Dojo Plus is £11.99 per location each month."]


@pytest.mark.asyncio
async def test_a_wrong_currency_sign_is_corrected_to_the_given_one(monkeypatch):
    spoken = await _spoken(["The Go Max is $229 upfront."], monkeypatch)
    assert spoken == ["The Go Max is £229 upfront."]


def test_percentages_and_worded_amounts_are_checked_too():
    assert ground_spoken_figures("That's 50% off.", [DOJO_FACTS])[1] == ["50%"]
    assert ground_spoken_figures("It costs 30 pounds.", [DOJO_FACTS])[1] == ["30 pounds"]


def test_times_dates_and_plain_counts_are_not_prices():
    for line in ("Call me at 3pm on the 5th.", "We serve 150,000 businesses.", "Press 1."):
        assert ground_spoken_figures(line, [DOJO_FACTS]) == (line, [])


def test_the_callers_own_guess_is_not_a_source():
    """Grounding is what the model was GIVEN; the caller's words are not in it."""
    assert ground_spoken_figures("Yes, £21.99.", [DOJO_FACTS])[1] == ["£21.99"]


# ── claiming a contact the agent does not have ─────────────────────────────

@pytest.mark.asyncio
async def test_no_pass_it_on_without_a_number(monkeypatch):
    """d644f0ea: the number never parsed; the agent said it would pass it on."""
    spoken = await _spoken(
        ["Thanks, I’ll pass that on for Azian to contact you via WhatsApp."],
        monkeypatch,
        history=[(MessageRole.USER, "It's zero c one two zero seven five zero four nine six.")],
    )
    assert spoken == [CONTACT_REASK]


def test_pass_it_on_about_a_complaint_is_left_alone():
    assert unbacked_contact_claim(
        "I’ll pass that on to Azian.", CallState(),
        "When you pay my world pay cancellation fee,",
    ) is None


def test_pass_it_on_with_a_confirmed_number_is_left_alone():
    from app.services.scripts.call_state_tracker import update_state_from_user_turn

    state = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    state = update_state_from_user_turn(
        state, "yes", phone_readback_issued=True, phone_confirmation_verdict="affirm",
    )
    assert unbacked_contact_claim("I'll pass that on.", state, "yes") is None


# ── re-asking the same scripted question ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_question_asked_twice_is_not_tacked_on_a_third_time(monkeypatch):
    spoken = await _spoken(
        ["The Go Max is lighter and has a bigger screen. "
         "Are you still using Dojo for payments, or have you moved to another provider?"],
        monkeypatch,
        history=[
            (MessageRole.ASSISTANT, ASK_PROVIDER_1),
            (MessageRole.USER, "How much is Dojo plus?"),
            (MessageRole.ASSISTANT, ASK_PROVIDER_2),
            (MessageRole.USER, "What is the difference between the go and the go max?"),
        ],
    )
    assert spoken == ["The Go Max is lighter and has a bigger screen."]


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


def test_differently_worded_repeats_are_recognised():
    earlier = [
        "Would you prefer Azian to call you or contact you by WhatsApp?",
        "Got it. Would you prefer Azian to call you or reach you via WhatsApp?",
    ]
    assert is_repeated_question("Would you prefer him to call you or reach you via WhatsApp?", earlier)
    assert not is_repeated_question("What number should we use for his WhatsApp message?", earlier)
    assert not is_repeated_question("Is that okay?", ["Is that okay?", "Is that okay?"])
