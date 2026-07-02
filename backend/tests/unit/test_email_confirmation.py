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

def _msg(role, content):
    from app.domain.models.conversation import Message, MessageRole
    return Message(role=role, content=content)


def test_gate_detects_dotted_and_digit_local_parts():
    # the agent SPEAKS separators/digits as words; the gate must still detect the
    # read-back (else common emails never confirm — re-audit CF #2).
    from app.domain.services.voice_pipeline.turn_runner import _agent_read_back_email
    from app.domain.models.conversation import MessageRole as R
    h = [_msg(R.ASSISTANT, "Okay, so that's j dot smith at gmail dot com — did I get that right?")]
    assert _agent_read_back_email(h, "j.smith@gmail.com") is True
    h2 = [_msg(R.ASSISTANT, "So that's john seven eight nine zero at gmail dot com, right?")]
    assert _agent_read_back_email(h2, "john7890@gmail.com") is True


def test_gate_skips_interposed_silence_check():
    # a silence-check must not mask the real read-back (re-audit flow #1).
    from app.domain.services.voice_pipeline.turn_runner import _agent_read_back_email
    from app.domain.models.conversation import MessageRole as R
    h = [
        _msg(R.ASSISTANT, "So that's bob at acme dot com, did I get that right?"),
        _msg(R.USER, "..."),
        _msg(R.ASSISTANT, "Are you still there?"),
    ]
    assert _agent_read_back_email(h, "bob@acme.com") is True


def test_gate_false_when_last_real_turn_is_unrelated():
    from app.domain.services.voice_pipeline.turn_runner import _agent_read_back_email
    from app.domain.models.conversation import MessageRole as R
    h = [_msg(R.ASSISTANT, "Are you the homeowner?")]
    assert _agent_read_back_email(h, "bob@acme.com") is False


# ── bounded-attempts safety net (re-audit cf #6): the read-back can't loop forever

def test_readback_attempts_increment_and_trigger_fallback():
    from app.services.scripts.prompt_builder import compose_system_prompt
    s = update_state_from_user_turn(CallState(), "bob at acme dot com")
    assert s.email_readback_attempts == 0
    # ambiguous replies after a read-back accumulate attempts (never confirming)
    for _ in range(3):
        s = update_state_from_user_turn(s, "um hold on let me think", readback_issued=True)
    assert s.email_readback_attempts >= 3
    out = compose_system_prompt("BASE", s).lower()
    assert ("spell it slowly" in out) or ("a different way" in out)
    assert "bob@acme.com" in out


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

def test_prompt_unconfirmed_email_demands_readback_not_confirmed():
    out = compose_system_prompt(BASE, CallState(email="bob@acme.com", email_confirmed=False))
    low = out.lower()
    assert "bob@acme.com" in out
    # payload-first imperative: the exact spoken read-back + confirm question
    assert "say exactly" in low
    assert "did i get that right" in low
    assert "only once they say yes" in low
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
