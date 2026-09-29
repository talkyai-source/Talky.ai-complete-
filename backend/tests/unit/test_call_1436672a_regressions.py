"""Browser test call 1436672a (2026-09-29, "Estimation new"), replayed.

Five things went wrong on one call, each reproduced here from the real lines:

1. "john.cena at gmail dot com" was read back as "our website at gmail dot com"
   -- the made-up-link guard took the name part of an email for a web address.
2. "zero three one two, zero seven five, zero four nine six" was not taken as
   a phone number at all (digit words, commas, no country code), and the
   agent's own read-back dropped the last digit and was spoken anyway.
3. The caller asked to record a SECOND email; the first was confirmed and
   sticky, so the second had nowhere to go and was never stored.
4. For that second email the one-word-or-dot question was never asked; the
   agent guessed "John dot Cena" twice.
5. "We'll ring you at 2 pm on that number" -- a call back the agent cannot book.
"""
from __future__ import annotations

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import turn_runner as tr
from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus,
    advance_capture,
)
from app.domain.services.voice_pipeline.conversation_guards import (
    phone_readback_changed,
    promises_timed_callback,
)
from app.domain.services.voice_pipeline.grounded_links import ground_spoken_links
from app.domain.services.voice_pipeline.lead_slot_capture import (
    contact_outcome,
    snapshot_slots,
)
from app.services.scripts.call_state_tracker import (
    CallState,
    _classify_core_confirmation,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt


class Replay:
    """Drives CallState the way turn_runner does, one line at a time."""

    def __init__(self):
        self.state = CallState()
        self.history: list[Message] = []

    def agent(self, text: str) -> None:
        self.history.append(Message(role=MessageRole.ASSISTANT, content=text))
        self.state = update_state_from_agent_turn(self.state, text)

    def caller(self, text: str) -> None:
        pending, email_rb = tr.email_on_the_table(self.state, text, self.history)
        phone_rb = tr._agent_read_back_phone(self.history, pending.phone)
        email_open = bool(pending.email and not pending.email_confirmed and email_rb)
        phone_open = bool(pending.phone and not pending.phone_confirmed and phone_rb)
        self.state = update_state_from_user_turn(
            pending,
            text,
            readback_issued=email_rb,
            confirmation_verdict=_classify_core_confirmation(text) if email_open else None,
            phone_readback_issued=phone_rb,
            phone_confirmation_verdict=(
                _classify_core_confirmation(text) if phone_open else None
            ),
            phone_region=None,
        )
        self.history.append(Message(role=MessageRole.USER, content=text))


# ── 1. an email's name part is not a web address ──────────────────────────

def test_an_email_read_back_is_not_rewritten_as_our_website():
    line = "Let me confirm that — john.cena at gmail dot com. Is that correct?"
    spoken, changed = ground_spoken_links(line, ["allstateestimation.co.uk"])
    assert spoken == line
    assert changed == []


def test_a_made_up_web_address_is_still_replaced():
    spoken, changed = ground_spoken_links(
        "See allstate-samples.com/reports for examples.", ["allstateestimation.co.uk"]
    )
    assert "our website" in spoken
    assert changed


# ── 2. a spoken number with no country code ───────────────────────────────

def test_a_number_said_as_words_is_captured_with_its_country():
    r = Replay()
    r.agent("Could you please share the phone number I should use?")
    r.caller("zero three one two, zero seven five, zero four nine six.")
    capture = r.state.phone_capture
    assert capture is not None
    assert capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert capture.normalized_value == "+923120750496"
    prompt = compose_system_prompt("BASE", r.state)
    assert "3 1 2, 0 7 5, 0 4 9 6" in prompt


def test_a_read_back_that_drops_a_digit_is_never_spoken():
    caller = ["zero three one two, zero seven five, zero four nine six."]
    wrong = "So that’s 0 3 1 2 , 0 7 5 , 0 4 9 Is that correct?"
    right = "So that's plus 9 2, 3 1 2, 0 7 5, 0 4 9 6 — did I get that right?"
    assert phone_readback_changed(wrong, caller, "+923120750496") is True
    assert phone_readback_changed(right, caller, "+923120750496") is False


# ── 5. no call back at a time the agent cannot book ──────────────────────

def test_a_timed_call_back_promise_is_caught():
    assert promises_timed_callback("We’ll ring you at 2 pm on that number.") is True
    assert promises_timed_callback("Would you like a call back at 2 pm?") is False
    assert promises_timed_callback("I'll pass on 2 pm as your preferred time.") is False


# ── 3 + 4. the second email: asked properly, confirmed, and stored ───────

def _first_email_confirmed() -> Replay:
    r = Replay()
    r.agent("What’s the best email address for you?")
    r.caller("Allstate estimation at Gmail dot com.")
    r.agent("Is that allstateestimation all one word, or allstate dot estimation, at gmail dot com?")
    r.caller("Allstate estimation.")
    r.agent("Let me confirm that — allstateestimation at gmail dot com. Is that correct?")
    r.caller("Yes.")
    assert r.state.email == "allstateestimation@gmail.com"
    assert r.state.email_confirmed is True
    return r


def test_a_second_email_is_asked_one_word_or_dot_then_stored_beside_the_first():
    r = _first_email_confirmed()

    r.caller("Can you record my other email?")
    # The first is kept; no spell-it-out loop is opened for a sentence that
    # names no address.
    assert [c.normalized_value for c in r.state.earlier_email_captures] == [
        "allstateestimation@gmail.com"
    ]
    assert r.state.email_capture is None
    assert r.state.active_contact_kind == "email"

    r.agent("What’s the other email address?")
    r.caller("John Cena at Gmail dot com.")
    capture = r.state.email_capture
    assert capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert "johncena all one word, or john dot cena" in capture.clarification_prompt
    prompt = compose_system_prompt("BASE", r.state)
    assert "johncena all one word, or john dot cena" in prompt
    assert "allstateestimation@gmail.com" in prompt  # still a known fact

    r.agent("Is that johncena all one word, or john dot cena, at gmail dot com?")
    r.caller("One word, no dot.")
    r.agent("Let me confirm — johncena at gmail dot com. Is that correct?")
    r.caller("Yes.")
    assert r.state.email == "johncena@gmail.com"
    assert r.state.email_confirmed is True

    rows = snapshot_slots(r.state)
    assert rows["email"]["value"] == "allstateestimation@gmail.com"
    assert rows["email"]["confirmed"] is True
    assert rows["email_2"]["value"] == "johncena@gmail.com"
    assert rows["email_2"]["confirmed"] is True
    assert rows["email_2"]["field_type"] == "email"
    assert contact_outcome(r.state)["email"] == "confirmed"


def test_a_later_address_without_asking_for_another_does_not_replace_the_first():
    r = _first_email_confirmed()
    r.caller("John Cena at Gmail dot com.")
    assert r.state.email == "allstateestimation@gmail.com"
    assert r.state.email_confirmed is True
    assert r.state.earlier_email_captures == ()


def test_a_fresh_two_word_address_after_a_failed_one_gets_the_question_too():
    first = advance_capture(
        None, kind="email", utterance="my email is umm", mode_active=True
    )
    assert first is not None and first.status in {
        CaptureStatus.NEEDS_CLARIFICATION, CaptureStatus.INVALID,
    }
    second = advance_capture(
        first, kind="email", utterance="John Cena at Gmail dot com.", mode_active=True
    )
    assert second.status is CaptureStatus.NEEDS_CLARIFICATION
    assert "johncena all one word, or john dot cena" in second.clarification_prompt


def test_a_second_phone_number_is_stored_as_phone_2():
    r = Replay()
    r.agent("Could you please share the phone number I should use?")
    r.caller("zero three one two, zero seven five, zero four nine six.")
    r.agent("So that's plus 9 2, 3 1 2, 0 7 5, 0 4 9 6 — did I get that right?")
    r.caller("Yes.")
    assert r.state.phone_confirmed is True

    r.caller("Record my other number as well.")
    assert r.state.phone_capture is None
    r.agent("Sure — what's the other number?")
    r.caller("plus four four seven nine one one one two three four five six.")
    r.agent("So that's plus 4 4, 7 9 1 1, 1 2 3, 4 5 6 — did I get that right?")
    r.caller("Yes.")
    rows = snapshot_slots(r.state)
    assert rows["phone"]["value"] == "+923120750496"
    assert rows["phone_2"]["value"] == "+447911123456"
    assert rows["phone_2"]["confirmed"] is True


def test_both_checks_sit_in_the_gate_every_spoken_sentence_passes():
    """Wiring guard (not proof of behaviour -- the tests above are): the
    read-back and call-back checks must run in _validate_for_tts, where the
    contact-claim check already lives, and replace the sentence."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "domain", "services", "voice_pipeline", "turn_streamer.py"
    ).read_text(encoding="utf-8")
    gate = src[src.index("def _validate_for_tts(") :]
    gate = gate[: gate.index("valid, reason = guardrails.validate_response(")]
    assert "phone_readback_changed(" in gate
    assert "return PHONE_REASK, None" in gate
    assert "promises_timed_callback(text)" in gate
    assert "return CALLBACK_PREFERENCE, None" in gate
