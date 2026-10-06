"""Historical phone parser compatibility and current neutral prompt state.

These parser utilities do not establish live model interpretation. Current
model-tool confirmation/persistence is covered in test_model_contact_recording."""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."


# ── tracker: capture → pending → confirmed/rejected ──────────────────────────

def test_freshly_captured_phone_is_unconfirmed():
    s = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    assert s.phone == "+14155552671"
    assert s.phone_confirmed is False


def test_affirm_after_readback_confirms_phone():
    s = update_state_from_user_turn(CallState(), "call me at +1 415-555-2671")
    assert s.phone == "+14155552671" and s.phone_confirmed is False
    # confirmation only counts once the agent has read it back
    s = update_state_from_user_turn(s, "yes that's right", phone_readback_issued=True)
    assert s.phone_confirmed is True


def test_reject_after_readback_reopens_phone():
    s = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    s = update_state_from_user_turn(s, "no that's wrong", phone_readback_issued=True)
    assert s.phone is None
    assert s.phone_confirmed is False


def test_unclear_reply_keeps_phone_pending_and_counts():
    s = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    s = update_state_from_user_turn(s, "um hold on", phone_readback_issued=True)
    assert s.phone == "+14155552671"
    assert s.phone_confirmed is False
    assert s.phone_readback_attempts == 1


def test_confirmation_ignored_without_readback():
    # a clean 'yes' when NO read-back was issued must not confirm the number.
    s = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    s = update_state_from_user_turn(s, "yes", phone_readback_issued=False)
    assert s.phone_confirmed is False


def test_corrected_phone_recaptures_as_unconfirmed():
    s = CallState(phone="+14155552671", phone_confirmed=True)
    s = update_state_from_user_turn(s, "actually my number is +1 212 555 0123")
    assert s.phone == "+12125550123"
    assert s.phone_confirmed is False


def test_new_phone_resets_readback_attempts():
    s = CallState(phone="5550000000", phone_readback_attempts=5)
    s = update_state_from_user_turn(s, "call me on +1 415 555 2671")
    assert s.phone == "+14155552671"
    assert s.phone_readback_attempts == 0


def test_phone_verdict_override_affirm_and_reject():
    s = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    s = update_state_from_user_turn(
        s, "close enough", phone_readback_issued=True, phone_confirmation_verdict="affirm"
    )
    assert s.phone_confirmed is True
    s2 = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    s2 = update_state_from_user_turn(
        s2, "eh not really", phone_readback_issued=True, phone_confirmation_verdict="reject"
    )
    assert s2.phone is None


def test_email_and_phone_gates_are_independent():
    # a phone read-back reply must not touch the pending email and vice versa.
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "my number is +1 415 555 2671")
    assert s.email == "bob@acme.com" and s.phone == "+14155552671"
    # confirm ONLY the phone
    s = update_state_from_user_turn(s, "yes that's right", phone_readback_issued=True)
    assert s.phone_confirmed is True
    assert s.email_confirmed is False   # email untouched — its own read-back never happened


# ── read-back gate (turn_runner) ─────────────────────────────────────────────


# ── prompt surfacing ─────────────────────────────────────────────────────────

def test_prompt_unconfirmed_phone_reports_pending():
    out = compose_system_prompt(BASE, CallState(phone="5551234567", phone_confirmed=False))
    assert "5551234567" in out and '"status": "awaiting_confirmation"' in out
    assert "say exactly" not in out.lower() and "did i get that right" not in out.lower()


def test_prompt_confirmed_phone_is_a_captured_fact():
    out = compose_system_prompt(BASE, CallState(phone="5551234567", phone_confirmed=True))
    assert "CONTACT CONTEXT" in out and '"status": "confirmed"' in out
    assert "5551234567" in out


def test_legacy_phone_attempt_state_survives_without_scripted_fallback():
    s = update_state_from_user_turn(CallState(), "my number is +1 415 555 2671")
    for _ in range(3):
        s = update_state_from_user_turn(s, "um hold on", phone_readback_issued=True)
    assert s.phone_readback_attempts >= 3
    out = compose_system_prompt("BASE", s)
    assert "+14155552671" in out and "needs_clarification" in out
    assert "digit by digit" not in out and "Say EXACTLY" not in out
