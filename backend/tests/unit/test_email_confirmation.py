"""Issue #1 (CRITICAL): an email must be READ BACK + confirmed before it is
treated as a committed fact.

Before this fix a parsed email was instantly labelled "confirmed — do not
re-ask", so a mis-transcribed address was locked as truth on the first
utterance and never read back. These tests pin the confirm-before-commit
behaviour on the live CallState path.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."


# ── tracker: capture → pending → confirmed/rejected ──────────────────────────

def test_freshly_parsed_email_is_unconfirmed():
    s = update_state_from_user_turn(CallState(), "my email is bob@acme.com")
    assert s.email == "bob@acme.com"
    assert s.email_confirmed is False


def test_affirm_after_capture_confirms_email():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    assert s.email == "bob@acme.com" and s.email_confirmed is False
    s = update_state_from_user_turn(s, "yes that's right")
    assert s.email_confirmed is True


def test_reject_after_capture_reopens_email():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "no that's wrong")
    assert s.email is None
    assert s.email_confirmed is False


def test_corrected_email_recaptures_as_unconfirmed_even_if_previously_confirmed():
    s = CallState(email="alice@acme.com", email_confirmed=True)
    # Caller states a different email (a correction). Even though the old one was
    # confirmed, the new value must be re-confirmed before it is trusted.
    s = update_state_from_user_turn(s, "bob at acme dot com")
    assert s.email == "bob@acme.com"
    assert s.email_confirmed is False


def test_unclear_reply_keeps_email_pending():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "hmm okay so what's next")
    assert s.email == "bob@acme.com"
    assert s.email_confirmed is False


def test_rehearing_same_confirmed_email_keeps_it_confirmed():
    s = CallState(email="bob@acme.com", email_confirmed=True)
    s = update_state_from_user_turn(s, "yeah bob at acme dot com")
    assert s.email == "bob@acme.com"
    assert s.email_confirmed is True


# ── prompt: pending email demands a read-back, not a "confirmed" fact ─────────

def test_prompt_unconfirmed_email_demands_readback_not_confirmed():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=False))
    low = out.lower()
    assert "bob@acme.com" in out
    assert "read it back" in low
    assert "confirm" in low
    # An unconfirmed email must NOT be presented as a settled do-not-re-ask fact.
    assert "do not re-ask" not in low


def test_prompt_confirmed_email_is_a_captured_fact():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=True))
    assert "CAPTURED" in out
    assert "do not re-ask" in out.lower()
    assert "bob@acme.com" in out


# ── issue #5: inject the EXACT deterministic spoken read-back ─────────────────

def test_unconfirmed_email_prompt_injects_exact_spoken_readback():
    from app.services.scripts.spoken_email_normalizer import natural_email_readback
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=False))
    # the model is given the exact words to say ("bob at acme dot com"), so it
    # doesn't re-derive a garbled spoken form from the raw transcript.
    assert natural_email_readback("bob@acme.com") in out


def test_confirmed_email_prompt_injects_exact_spoken_readback():
    from app.services.scripts.spoken_email_normalizer import natural_email_readback
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=True))
    assert natural_email_readback("bob@acme.com") in out
