"""Gap #1 (HIGH): a phone / callback number must be READ BACK + confirmed before
it is treated as a committed fact — the SAME fail-closed gate email has.

Before this fix `CallState` had only an `email` confirmable slot; a callback
number was protected by prompt prose alone. These tests pin the confirm-before-
commit behaviour for the phone slot on the live CallState path, and its prompt
surfacing.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."


# ── tracker: capture → pending → confirmed/rejected ──────────────────────────

def test_freshly_captured_phone_is_unconfirmed():
    s = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    assert s.phone == "5551234567"
    assert s.phone_confirmed is False


def test_affirm_after_readback_confirms_phone():
    s = update_state_from_user_turn(CallState(), "call me at 555-123-4567")
    assert s.phone == "5551234567" and s.phone_confirmed is False
    # confirmation only counts once the agent has read it back
    s = update_state_from_user_turn(s, "yes that's right", phone_readback_issued=True)
    assert s.phone_confirmed is True


def test_reject_after_readback_reopens_phone():
    s = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    s = update_state_from_user_turn(s, "no that's wrong", phone_readback_issued=True)
    assert s.phone is None
    assert s.phone_confirmed is False


def test_unclear_reply_keeps_phone_pending_and_counts():
    s = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    s = update_state_from_user_turn(s, "um hold on", phone_readback_issued=True)
    assert s.phone == "5551234567"
    assert s.phone_confirmed is False
    assert s.phone_readback_attempts == 1


def test_confirmation_ignored_without_readback():
    # a clean 'yes' when NO read-back was issued must not confirm the number.
    s = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    s = update_state_from_user_turn(s, "yes", phone_readback_issued=False)
    assert s.phone_confirmed is False


def test_corrected_phone_recaptures_as_unconfirmed():
    s = CallState(phone="5551234567", phone_confirmed=True)
    s = update_state_from_user_turn(s, "actually my number is 555 765 4321")
    assert s.phone == "5557654321"
    assert s.phone_confirmed is False


def test_new_phone_resets_readback_attempts():
    s = CallState(phone="5550000000", phone_readback_attempts=5)
    s = update_state_from_user_turn(s, "call me on 555 123 4567")
    assert s.phone == "5551234567"
    assert s.phone_readback_attempts == 0


def test_phone_verdict_override_affirm_and_reject():
    s = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    s = update_state_from_user_turn(
        s, "close enough", phone_readback_issued=True, phone_confirmation_verdict="affirm"
    )
    assert s.phone_confirmed is True
    s2 = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    s2 = update_state_from_user_turn(
        s2, "eh not really", phone_readback_issued=True, phone_confirmation_verdict="reject"
    )
    assert s2.phone is None


def test_email_and_phone_gates_are_independent():
    # a phone read-back reply must not touch the pending email and vice versa.
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "my number is 555 123 4567")
    assert s.email == "bob@acme.com" and s.phone == "5551234567"
    # confirm ONLY the phone
    s = update_state_from_user_turn(s, "yes that's right", phone_readback_issued=True)
    assert s.phone_confirmed is True
    assert s.email_confirmed is False   # email untouched — its own read-back never happened


# ── read-back gate (turn_runner) ─────────────────────────────────────────────

def _msg(role, content):
    from app.domain.models.conversation import Message
    return Message(role=role, content=content)


def test_agent_read_back_phone_matches_any_formatting():
    from app.domain.services.voice_pipeline.turn_runner import _agent_read_back_phone
    from app.domain.models.conversation import MessageRole as R
    h = [_msg(R.ASSISTANT, "So that's 555-123-4567 — did I get that right?")]
    assert _agent_read_back_phone(h, "5551234567") is True
    h2 = [_msg(R.ASSISTANT, "So that's 5 5 5 1 2 3 4 5 6 7, right?")]
    assert _agent_read_back_phone(h2, "5551234567") is True
    h3 = [_msg(R.ASSISTANT, "Are you the homeowner?")]
    assert _agent_read_back_phone(h3, "5551234567") is False


def test_agent_read_back_phone_skips_silence_check():
    from app.domain.services.voice_pipeline.turn_runner import _agent_read_back_phone
    from app.domain.models.conversation import MessageRole as R
    h = [
        _msg(R.ASSISTANT, "So that's 555 123 4567, did I get that right?"),
        _msg(R.USER, "..."),
        _msg(R.ASSISTANT, "Are you still there?"),
    ]
    assert _agent_read_back_phone(h, "5551234567") is True


# ── prompt surfacing ─────────────────────────────────────────────────────────

def test_prompt_unconfirmed_phone_demands_readback():
    out = compose_system_prompt(BASE, CallState(phone="5551234567", phone_confirmed=False))
    low = out.lower()
    assert "5551234567" in out
    assert "say exactly" in low
    assert "did i get that right" in low
    assert "5 5 5 1 2 3 4 5 6 7" in out   # the exact digit-by-digit read-back
    assert "do not re-ask" not in low


def test_prompt_confirmed_phone_is_a_captured_fact():
    out = compose_system_prompt(BASE, CallState(phone="5551234567", phone_confirmed=True))
    assert "CAPTURED" in out
    assert "do not re-ask" in out.lower()
    assert "5551234567" in out


def test_prompt_phone_readback_attempts_trigger_fallback():
    s = update_state_from_user_turn(CallState(), "my number is 555 123 4567")
    for _ in range(3):
        s = update_state_from_user_turn(s, "um hold on", phone_readback_issued=True)
    assert s.phone_readback_attempts >= 3
    out = compose_system_prompt("BASE", s).lower()
    assert ("digit by digit" in out) or ("another way" in out)
    assert "5551234567" in out
