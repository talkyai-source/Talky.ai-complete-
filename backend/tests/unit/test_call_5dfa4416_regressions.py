"""Call5dfa4416: keep pending email/phone visible without a scripted dialogue.

Current model-tool regression uses synthetic arguments/SQL; it does not prove
speech understanding. Remaining conversation-guard tests exercise historical
pure utilities only, not current live speech judging or caller-message commands.
"""
from __future__ import annotations

import pytest

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState
from app.domain.services.voice_pipeline.conversation_guards import (
    closing_while_contact_open,
    unbacked_contact_claim,
)
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.conversation_guards import _CLAIM
from app.services.scripts.prompt_builder import (
    compose_system_prompt,
    turn_directive,
    with_turn_directive,
)

from app.services.scripts.call_state_tracker import CallState

READBACK = "So that's allstateestimation at gmail dot com — did I get that right?"


@pytest.mark.asyncio
async def test_model_phone_request_preserves_pending_email_and_unrelated_turn_does_not_consume_attempt():
    # Model-selected arguments exercise persistence, not a speech-understanding claim.
    from app.domain.services.voice_pipeline.contact_recording import record_contact
    from tests.unit.test_model_contact_recording import SQLPort, args, caller, session
    pool, state = SQLPort(), session()
    email = "allstateestimation@gmail.com"
    caller(state, "Allstate estimation at Gmail dot com.", 1)
    result = await record_contact(state, args("Allstate estimation at Gmail dot com.", value=email), pool=pool)
    assert result["saved"] and not state.captured_slots.email_confirmed
    request = "And note down my mobile number as well?"
    caller(state, request, 2)
    result = await record_contact(state, args(request, kind="phone", value=request), pool=pool)
    assert result["saved"] and result["validation_status"] == "needs_clarification"
    rows = snapshot_slots(state.captured_slots)
    assert rows["email"]["value"] == email and not rows["email"]["confirmed"]
    assert rows["phone"]["value"] is None and rows["phone"]["raw_value"] == request
    # An unrelated accepted caller turn does not manufacture a failed number attempt.
    caller(state, "It's two PM Sunday.", 3)
    assert snapshot_slots(state.captured_slots) == rows
    assert state.captured_slots.phone_capture.attempts == 0
    prompt = compose_system_prompt("BASE", state.captured_slots)
    assert email in prompt and request in prompt
    assert turn_directive(state.captured_slots) is None
    caller(state, "Yes, that email is correct.", 4)
    result = await record_contact(state, args("Yes, that email is correct.", operation="confirm", value=email, expected=email), pool=pool)
    assert result["saved"] and state.captured_slots.email_confirmed
    assert state.captured_slots.phone_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert {"email", "phone"} <= {parameters[4] for _, parameters in pool.writes}


def test_unowned_pending_state_cannot_be_saved_as_caller_evidence():
    rows = snapshot_slots(_open_state())
    assert "email" not in rows  # No caller source owns this synthetic display state.


def test_legacy_claim_detector_retains_pending_readback_result():
    said = unbacked_contact_claim(
        "Perfect. I'll pass the project details to the estimating team so they can follow up with you.",
        _open_state(), "And note down my mobile number as well?",
    )
    assert said == READBACK


def test_a_caller_who_is_leaving_is_let_go():
    assert closing_while_contact_open(
        "No problem, have a good day.", _open_state(), "Sorry, I have to go now, bye.",
    ) is None


def test_nothing_open_nothing_replaced():
    state = CallState()
    assert closing_while_contact_open("Thanks for your time.", state, "ok") is None
    assert turn_directive(state) is None


# ── review findings: none of these may be hijacked ──────────────────────

def _open_state():
    return CallState(email="allstateestimation@gmail.com", email_confirmed=False,
        phone_capture=ContactCaptureState(kind="phone", status=CaptureStatus.NEEDS_CLARIFICATION,
            raw_value="And note down my mobile number as well?"))


def test_only_a_sentence_that_is_just_a_goodbye_is_replaced():
    state = _open_state()
    for goodbye in (
        "Thanks for your time.",
        "Have a good day.",
        "Brilliant, thanks so much for your time.",
        "Thanks again, bye!",
    ):
        assert closing_while_contact_open(goodbye, state, "ok") == READBACK, goodbye
    for ordinary in (
        "Thanks for your time explaining the issue.",
        "Please take care of that paperwork before Monday.",
        "All the best with the renovation project.",
        "Great, thank you.",
    ):
        assert closing_while_contact_open(ordinary, state, "ok") is None, ordinary


def test_a_claim_must_be_about_contact_details():
    assert _CLAIM.search("I'll pass the project details to the estimating team.")
    assert not _CLAIM.search("I'll pass the invoice number along to accounting.")


def test_caller_message_is_not_modified_by_a_retired_directive():
    history = [
        Message(role=MessageRole.ASSISTANT, content="Is that correct?"),
        Message(role=MessageRole.USER, content="And note down my mobile number as well?"),
    ]
    sent = with_turn_directive(history, "Do X.")
    assert sent is history
    assert sent[-1].content == "And note down my mobile number as well?"
    assert "Do X." not in sent[-1].content
    assert with_turn_directive(history, None) is history


# ── a question the caller already answered is not asked again ────────────

def _call_so_far():
    def a(text):
        return Message(role=MessageRole.ASSISTANT, content=text)

    def u(text):
        return Message(role=MessageRole.USER, content=text)

    return [
        a("Oh, hi."), u("Hello"), a("Hello?"), u("Hello?"),
        a("Hi, this is Sarah, the AI assistant for All State Estimation. I’ll keep "
          "this quick—do you have any upcoming projects you might need an estimate for?"),
        u("Yes, I have plenty of it."),
        a("Great. Is it something you’re currently tendering for, or a project "
          "you’ve already secured?"),
        u("I have, uh, both of them."),
        a("Got it. What type of project is it?"),
        u("They're the construction projects."),
        a("Let me confirm that — allstateestimation at gmail dot com. Is that correct?"),
        u("yes it is"),
    ]


def test_the_repeat_from_the_call_is_recognised_and_new_questions_are_not():
    from app.domain.services.voice_pipeline.conversation_guards import (
        answered_note,
        answered_questions,
        repeats_answered_question,
    )

    answered = answered_questions(_call_so_far())
    assert repeats_answered_question(
        "Do you have any upcoming projects that need estimating?", answered
    ) == "Yes, I have plenty of it."
    # A new question sharing a word or two is not a repeat.
    assert repeats_answered_question("And when do you need the estimate by?", answered) is None
    # Read-backs and contact asks may come back; they are never "answered".
    assert repeats_answered_question(READBACK, answered) is None
    assert repeats_answered_question("What's the best number to reach you on?", answered) is None
    note = answered_note(answered)
    assert "do not ask these again" in note
    assert "plenty of it" in note
