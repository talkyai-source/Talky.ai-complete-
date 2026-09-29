"""Browser test call 5dfa4416 (2026-09-29, "Estimation new"), replayed.

The caller asked twice for their mobile number to be taken; the agent never
asked for it, never re-confirmed the email, said goodbye with both open, and
nothing at all was saved. Four independent causes, each pinned here:

1. A phone request made while the email read-back was waiting was dropped:
   only the field whose confirmation was in progress was advanced.
2. The email the agent read back was spelled entirely from the caller's own
   words, but was marked agent-invented, so it was never stored.
3. A caller turn answering something else ("It's two PM Sunday.") counted as
   a failed try at the number the agent never asked for.
4. The open action sat mid system prompt and lost to the script's next line;
   and nothing stopped "Thanks for your time" or "I'll pass the project
   details" from being spoken while a contact was still open.
"""
from __future__ import annotations

from pathlib import Path

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus
from app.domain.services.voice_pipeline.conversation_guards import (
    closing_while_contact_open,
    pending_contact_ask,
    unbacked_contact_claim,
)
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.conversation_guards import _CLAIM
from app.domain.services.voice_pipeline.turn_runner import _spelled_by_caller
from app.services.scripts.prompt_builder import (
    compose_system_prompt,
    turn_directive,
    with_turn_directive,
)

from tests.unit.test_call_1436672a_regressions import Replay

READBACK = "So that's allstateestimation at gmail dot com — did I get that right?"


def _up_to_the_email_readback() -> Replay:
    r = Replay()
    r.agent("Perfect. What’s the best email address for you?")
    r.caller("Allstate estimation at Gmail dot com.")
    r.agent("Is that allstateestimation all one word, or allstate dot estimation, at gmail dot com?")
    r.caller("in one word.")
    r.agent("Let me confirm that — allstateestimation at gmail dot com. Is that correct?")
    return r


def test_a_phone_request_during_the_email_read_back_is_kept_not_dropped():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")

    # The email is still waiting for its yes -- and it is the caller's own.
    email = r.state.email_capture
    assert email.status is CaptureStatus.AWAITING_CONFIRMATION
    assert email.normalized_value == "allstateestimation@gmail.com"
    assert email.from_caller is True
    # The phone request exists, as a request -- not a failed try.
    phone = r.state.phone_capture
    assert phone is not None
    assert phone.status is CaptureStatus.NEEDS_CLARIFICATION
    assert phone.attempts == 0
    assert "What's the best number to reach you on?" in phone.clarification_prompt

    # This turn: settle the email. Next: the number. Both in the prompt.
    directive = turn_directive(r.state)
    assert "allstateestimation at gmail dot com" in directive
    assert "Do not say goodbye" in directive
    prompt = compose_system_prompt("BASE", r.state)
    assert "Once that is settled, next: The caller wants you to take their phone number" in prompt


def test_the_caller_stated_email_is_stored_even_before_the_yes():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")
    rows = snapshot_slots(r.state)
    assert rows["email"]["value"] == "allstateestimation@gmail.com"
    assert rows["email"]["confirmed"] is False


def test_a_false_acknowledgement_is_replaced_by_the_open_read_back():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")
    said = unbacked_contact_claim(
        "Perfect. I'll pass the project details to the estimating team so they can follow up with you.",
        r.state,
        "And note down my mobile number as well?",
    )
    assert said == READBACK


def test_with_the_fix_the_flow_confirms_the_email_then_takes_the_number():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")
    r.agent(READBACK)
    r.caller("Yes.")
    assert r.state.email_confirmed is True
    assert r.state.active_contact_kind == "phone"
    assert pending_contact_ask(r.state) == "What's the best number to reach you on?"
    assert "take their phone number" in turn_directive(r.state)

    r.agent("What's the best number to reach you on?")
    r.caller("zero three one two, zero seven five, zero four nine six.")
    assert r.state.phone_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert r.state.phone == "+923120750496"


def test_as_it_happened_the_open_contacts_survive_and_the_goodbye_is_replaced():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")
    r.agent("Perfect. I’ll pass the project details to the estimating team so they can follow up with you. Would you prefer someone to call you back as well?")
    r.caller("Yes. And you have to record my mobile number as well.")
    r.agent("What time works best for you?")
    r.caller("It's two PM Sunday.")

    # Answering the time question is not a failed try at the number.
    assert r.state.phone_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert r.state.phone_capture.attempts == 0
    # And neither contact is forgotten.
    assert r.state.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert turn_directive(r.state) is not None

    ask = closing_while_contact_open(
        "Thanks for your time.", r.state, "It's two PM Sunday."
    )
    assert ask in {READBACK, "What's the best number to reach you on?"}
    assert closing_while_contact_open("Brilliant.", r.state, "It's two PM Sunday.") is None


def test_a_caller_who_is_leaving_is_let_go():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")
    assert closing_while_contact_open(
        "No problem, have a good day.", r.state, "Sorry, I have to go now, bye."
    ) is None


def test_nothing_open_nothing_replaced():
    r = Replay()
    assert closing_while_contact_open("Thanks for your time.", r.state, "ok") is None
    assert turn_directive(r.state) is None


def test_the_directive_is_sent_last_and_the_goodbye_gate_is_wired():
    """Wiring guard (behaviour is proven above): the directive is appended
    after the history for both LLM call paths, and the goodbye check sits in
    the gate every spoken sentence passes."""
    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "domain", "services", "voice_pipeline", "turn_streamer.py"
    ).read_text(encoding="utf-8")
    assert "llm_messages = with_turn_directive(messages, _directive)" in src
    assert src.count("llm_messages,") == 2
    gate = src[src.index("def _validate_for_tts(") :]
    gate = gate[: gate.index("valid, reason = guardrails.validate_response(")]
    assert "closing_while_contact_open(" in gate


# ── review findings: none of these may be hijacked ──────────────────────

def _open_state():
    r = _up_to_the_email_readback()
    r.caller("And note down my mobile number as well?")
    return r.state


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


def test_an_address_counts_as_the_callers_only_if_they_said_it():
    def u(text):
        return Message(role=MessageRole.USER, content=text)

    said = [u("Allstate estimation at Gmail dot com."), u("in one word.")]
    assert _spelled_by_caller("allstateestimation@gmail.com", said)
    assert _spelled_by_caller("allstate.estimation@gmail.com", said)
    assert not _spelled_by_caller("allstateestimation@yahoo.com", said)
    # Letters that merely occur somewhere in what they said are not an address.
    assert not _spelled_by_caller(
        "al@ex.com", [u("I run a company called Alex Com Logistics")]
    )


def test_the_directive_is_a_marked_note_on_the_callers_latest_turn():
    history = [
        Message(role=MessageRole.ASSISTANT, content="Is that correct?"),
        Message(role=MessageRole.USER, content="And note down my mobile number as well?"),
    ]
    sent = with_turn_directive(history, "Do X.")
    assert [m.role for m in sent] == [MessageRole.ASSISTANT, MessageRole.USER]
    assert sent[-1].content.startswith("And note down my mobile number as well?")
    assert "the caller did not say this: Do X." in sent[-1].content
    # The stored history is untouched.
    assert history[-1].content == "And note down my mobile number as well?"
    assert with_turn_directive(history, None) is history
