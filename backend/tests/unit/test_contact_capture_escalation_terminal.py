"""Regression tests for the reviewer's two blocking findings on 319cc598.

319cc598 bounded the "spell it again" loop (call 6aaeb4dd,
email-respell-unbounded) by escalating once past MAX_CLARIFICATION_ATTEMPTS.
A reviewer (2026-09-24) proved the escalation itself was unbounded:

* phone-capture-clarification-ignored: nothing ever left NEEDS_CLARIFICATION,
  so the read-back-or-move-on escalation was re-sent as "ACTION THIS TURN" on
  every later turn, including turns with no contact content at all
  ("Tomorrow afternoon please.").
* the realtime regression: `RealtimeBridge._contact_state_signature` folds in
  `attempts` and `clarification_prompt`, both of which kept changing turn
  over turn, so `contact_changed` was True on every one of those unrelated
  turns and `_enforce_contact_directive` cleared the gateway's output buffer,
  cancelled the in-flight response and started a new one -- every turn, for
  the rest of the call.

The fix (contact_capture.py's `_clarification_progress`) moves the field to a
terminal CANCELLED ("gave up for good") state the turn after the escalation
is delivered. CANCELLED already drops out of `compose_system_prompt`'s
pending block (prompt_builder.py only surfaces NEEDS_CLARIFICATION/INVALID)
and out of `advance_capture`'s two re-open branches, so from that turn on the
state -- and therefore the signature realtime_bridge.py diffs against --
stops changing.

These tests drive the SAME production entry points the reviewer's proof
scripts drove (`update_state_from_agent_turn` / `update_state_from_user_turn`,
and the real `RealtimeBridge._contact_state_signature` static method, not a
reimplementation of it) with the reviewer's own utterances. See
issues_all.txt: phone-capture-clarification-ignored.
"""
from __future__ import annotations

from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus,
    MAX_CLARIFICATION_ATTEMPTS,
    capture_mode_directive,
)
from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

_UNRELATED_TURNS = [
    "Tomorrow afternoon please.",
    "What time do you open?",
    "Yeah, correct.",
]


def _drive_phone_past_the_cap() -> CallState:
    """Arm phone mode the way a real agent turn does, then exhaust the cap
    with the reviewer's region-less repeat ("923016253193", no leading +,
    no region on file -- the missing_region NEEDS_CLARIFICATION branch).

    MAX_CLARIFICATION_ATTEMPTS turns fail plainly; the (MAX+1)th turn is the
    one-time escalation (still NEEDS_CLARIFICATION); the (MAX+2)th turn is
    what moves the field to the terminal CANCELLED state -- so this needs
    MAX_CLARIFICATION_ATTEMPTS + 2 identical failing turns in total.
    """
    state = CallState()
    state = update_state_from_agent_turn(
        state, "What's the best phone number to reach you on?"
    )
    assert state.active_contact_kind == "phone"
    for _ in range(MAX_CLARIFICATION_ATTEMPTS + 2):
        state = update_state_from_user_turn(state, "923016253193")
    return state


def test_phone_escalation_reaches_a_terminal_state_not_a_frozen_loop():
    """The crossing turn (attempts > cap) escalates; it does not stay stuck
    asking to repeat forever, and it does not silently keep NEEDS_
    CLARIFICATION alive either -- it becomes terminal (CANCELLED)."""
    state = _drive_phone_past_the_cap()
    assert state.phone_capture.status is CaptureStatus.CANCELLED
    assert state.phone_capture.attempts > MAX_CLARIFICATION_ATTEMPTS


def test_unrelated_turns_after_escalation_do_not_reopen_or_repeat_it():
    """This is the reviewer's exact proof sequence for
    phone-capture-clarification-ignored: two unrelated turns and a bare
    affirmation, none of which should resurrect the clarification loop or
    keep re-sending the escalation.

    Before this fix (commit 477df18b): every one of these three turns bumped
    `attempts` again (5, 6, 7) and stayed in NEEDS_CLARIFICATION, so the
    identical escalation text -- "You have already asked for the phone number
    3 times..." -- kept being re-delivered as an ACTION THIS TURN item on
    turns that have nothing to do with the phone number at all.
    """
    state = _drive_phone_past_the_cap()
    frozen_capture = state.phone_capture
    for utterance in _UNRELATED_TURNS:
        state = update_state_from_user_turn(state, utterance)
        # Terminal: no further counting, no reopened clarification, and the
        # capture itself stops changing (byte-for-byte) turn over turn.
        assert state.phone_capture == frozen_capture
        assert state.phone_capture.status is CaptureStatus.CANCELLED


def test_realtime_signature_stops_changing_once_terminal():
    """Reproduces the actual realtime-path mechanism, using the real
    `RealtimeBridge._contact_state_signature` (not a reimplementation of it):
    once terminal, an unrelated caller turn must not look like a contact
    change, or `_enforce_contact_directive` fires again -- clearing the
    gateway's output buffer and cancelling the in-flight response on a turn
    that has nothing to do with the phone number.

    Before this fix, `changed` was True on every single one of these three
    turns (attempts kept incrementing 5, 6, 7): this is the regression the
    reviewer measured with review_sig.py against 477df18b.
    """
    state = _drive_phone_past_the_cap()
    signature = RealtimeBridge._contact_state_signature(state)
    for utterance in _UNRELATED_TURNS:
        state = update_state_from_user_turn(state, utterance)
        new_signature = RealtimeBridge._contact_state_signature(state)
        assert new_signature == signature, (
            f"turn {utterance!r} changed the contact signature after the "
            "field had already gone terminal"
        )
        signature = new_signature


def test_terminal_capture_drops_out_of_the_per_turn_pending_block():
    """The other half of 'stop emitting the directive': prompt_builder's
    ACTION THIS TURN block must not keep asking about a field that gave up.
    has_callback_executor=True isolates this from the unrelated, always-on
    CALLBACK POLICY line (prompt_builder.py)."""
    state = _drive_phone_past_the_cap()
    out = compose_system_prompt("BASE", state, has_callback_executor=True)
    assert out == "BASE"
    assert "ACTION THIS TURN" not in out
    assert "phone" not in out.lower()


def test_terminal_capture_directive_is_stop_asking_not_another_spelling_request():
    """capture_mode_directive is what the realtime path actually speaks for
    the ONE turn the transition happens on -- it must tell the model to stop
    and move on, never to ask for the number again in any form."""
    state = _drive_phone_past_the_cap()
    directive = capture_mode_directive(state.phone_capture)
    assert directive is not None
    lowered = directive.lower()
    assert "repeat" not in lowered
    assert "spell" not in lowered
    assert "stop asking" in lowered or "move on" in lowered


def test_email_clarification_also_reaches_a_stable_terminal_state():
    """Same bound, same termination, for email (the field call 6aaeb4dd
    actually hit) -- using advance_capture directly since email's
    unparseable-spelling shape does not need the agent-turn arming helper."""
    from app.domain.services.voice_pipeline.contact_capture import advance_capture

    state = None
    for _ in range(MAX_CLARIFICATION_ATTEMPTS + 2):
        state = advance_capture(
            state, kind="email", utterance="at gmail dot com.", mode_active=True
        )
    assert state.status is CaptureStatus.CANCELLED

    frozen = state
    for utterance in ("Tomorrow afternoon please.", "What time do you open?"):
        state = advance_capture(state, kind="email", utterance=utterance, mode_active=True)
        assert state == frozen
