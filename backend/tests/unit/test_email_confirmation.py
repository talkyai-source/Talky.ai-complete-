"""Historical email parser compatibility and current neutral prompt state.

The parser is no longer the live contact workflow. Model-tool confirmation and
persistence are qualified in test_model_contact_recording; exact scripted
readback gates were intentionally retired."""
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
    # confirmation only counts once the agent has read it back
    s = update_state_from_user_turn(s, "yes that's right", readback_issued=True)
    assert s.email_confirmed is True


def test_reject_after_capture_reopens_email():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "no that's wrong", readback_issued=True)
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
    s = update_state_from_user_turn(s, "hmm okay so what's next", readback_issued=True)
    assert s.email == "bob@acme.com"
    assert s.email_confirmed is False


# ── regressions caught by the re-audit: incidental words must NOT flip a core value

def test_incidental_negation_does_not_wipe_email():
    # REG-1: a 'yes' with a follow-up request, and a 'no' about something else,
    # must NOT clear a correctly-captured email.
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "yes, actually can you also email my assistant", readback_issued=True)
    assert s.email == "bob@acme.com"        # not wiped
    s2 = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s2 = update_state_from_user_turn(s2, "no I do not need a callback, the email is fine", readback_issued=True)
    assert s2.email == "bob@acme.com"       # not wiped


def test_incidental_right_does_not_confirm_email():
    # REG-2: bare mid-sentence 'right' must NOT confirm.
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "right, so what happens next", readback_issued=True)
    assert s.email_confirmed is False


def test_confirmation_ignored_without_readback():
    # REG-2 core: a clean 'yes' when NO read-back was issued (e.g. answering some
    # other question) must not confirm the email.
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "yes", readback_issued=False)
    assert s.email_confirmed is False


# ── re-audit round 2: the read-back GATE must be robust ──────────────────────


# ── issue #4: a bare DOMAIN mention is NOT a read-back of the LOCAL part ──────


# ── issue #2: an assembled multi-word email enters the gate via the agent read-back


# ── bounded-attempts safety net (re-audit cf #6): the read-back can't loop forever

def test_legacy_attempt_count_survives_without_scripted_fallback():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    for _ in range(3):
        s = update_state_from_user_turn(s, "um hold on let me think", readback_issued=True)
    assert s.email_readback_attempts >= 3
    out = compose_system_prompt("BASE", s)
    assert "bob@acme.com" in out and "needs_clarification" in out
    assert "spell it slowly" not in out and "Say EXACTLY" not in out


def test_new_email_resets_readback_attempts():
    s = CallState(email="alice@x.com", email_readback_attempts=5)
    s = update_state_from_user_turn(s, "bob at acme dot com")  # correction
    assert s.email == "bob@acme.com"
    assert s.email_readback_attempts == 0


# ── reliability re-audit: affirm/reject classifier edge cases ────────────────

def test_affirmative_no_discourse_marker_does_not_wipe_and_confirms():
    # CORE-1: a discourse-marker 'no' that AFFIRMS correctness must confirm, not wipe.
    for reply in ("no problem, that's correct", "no that's right", "no worries, that's correct"):
        s = update_state_from_user_turn(CallState(), "bob at acme dot com")
        s = update_state_from_user_turn(s, reply, readback_issued=True)
        assert s.email == "bob@acme.com", reply     # not wiped
        assert s.email_confirmed is True, reply      # correctly confirmed


def test_bare_no_still_rejects():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(s, "no", readback_issued=True)
    assert s.email is None


def test_formal_that_is_right_and_wrong():
    from app.services.scripts.call_state_tracker import _classify_core_confirmation as c
    # both the contraction and the formal phrasing must be recognized
    assert c("that is right") == "affirm"
    assert c("that is correct") == "affirm"
    assert c("that is wrong") == "reject"
    assert c("that is not right") == "reject"


# ── hybrid: a pre-computed verdict (from the LLM fallback) overrides the regex ─

def test_confirmation_verdict_override_affirm():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    # text is ambiguous to regex, but the resolved verdict says affirm
    s = update_state_from_user_turn(
        s, "close enough i suppose", readback_issued=True, confirmation_verdict="affirm"
    )
    assert s.email == "bob@acme.com" and s.email_confirmed is True


def test_confirmation_verdict_override_reject():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(
        s, "eh not really", readback_issued=True, confirmation_verdict="reject"
    )
    assert s.email is None


def test_confirmation_verdict_override_unclear_stays_pending():
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    s = update_state_from_user_turn(
        s, "hmm", readback_issued=True, confirmation_verdict="unclear"
    )
    assert s.email == "bob@acme.com" and s.email_confirmed is False
    assert s.email_readback_attempts == 1


def test_partial_correction_does_not_commit():
    # CORE-2: an affirm word followed by a partial-correction hedge must NOT commit.
    for reply in ("perfect except the number", "yes that is my old email", "yeah almost, one letter off"):
        s = update_state_from_user_turn(CallState(), "bob at acme dot com")
        s = update_state_from_user_turn(s, reply, readback_issued=True)
        assert s.email == "bob@acme.com", reply      # not wiped
        assert s.email_confirmed is False, reply      # NOT falsely committed


def test_rehearing_same_confirmed_email_keeps_it_confirmed():
    s = CallState(email="bob@acme.com", email_confirmed=True)
    s = update_state_from_user_turn(s, "yeah bob at acme dot com")
    assert s.email == "bob@acme.com"
    assert s.email_confirmed is True


# ── prompt: pending email demands a read-back, not a "confirmed" fact ─────────

def test_prompt_unconfirmed_email_reports_pending_not_confirmed():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=False))
    assert "bob@acme.com" in out and '"status": "awaiting_confirmation"' in out
    assert "say exactly" not in out.lower() and '"status": "confirmed"' not in out


def test_prompt_confirmed_email_is_a_captured_fact():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=True))
    assert "CONTACT CONTEXT" in out and '"status": "confirmed"' in out
    assert "bob@acme.com" in out and "do not re-ask" not in out.lower()


# ── issue #5: inject the EXACT deterministic spoken read-back ─────────────────
