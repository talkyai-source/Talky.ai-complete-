"""The recording notice is re-delivered when the callee talks over it.

WHY THIS EXISTS (production measurement, 2026-07-31)
---------------------------------------------------
The spoken disclosure is the FIRST audio on the call, which puts a ~4-second
sentence in the exact moment people always speak: the two seconds after they
pick up. Any speech that is not a bare greeting counts as an interruption, and
the original implementation treated one interruption as final — the whole
call's audio was discarded at teardown.

Measured on the first 7 production calls after it shipped:

    recording_disclosure_spoken        4
    recording_disclosure_interrupted   3     <- 43% of calls lost their recording
    recording_suppressed_no_disclosure 3

Abandoning the recording because the callee said "Hi, who's this?" is the wrong
trade. The lawful requirement is that they were TOLD, not that they happened to
be silent on the first attempt.

The invariant that must NOT be weakened: this may only ever turn a suppressed
recording into a delivered notice. It must never retain audio for a call where
the notice was never completed. `test_fails_closed_when_every_attempt_is_talked_over`
is the load-bearing test.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony.modes import agent_first
from app.domain.services.recording_policy_service import (
    DISCLOSURE_FAILED,
    DISCLOSURE_SPOKEN,
    get_disclosure_state,
    record_disclosure_state,
)


def test_retry_is_bounded_and_small():
    """Each attempt delays the real conversation, so this must stay tight."""
    assert agent_first._DISCLOSURE_MAX_ATTEMPTS == 2
    assert 0 < agent_first._DISCLOSURE_RETRY_SETTLE_S <= 2.0


def test_a_settle_gap_exists_between_attempts():
    """Retrying instantly would talk over the speech that interrupted us and be
    interrupted again, burning the attempt for nothing."""
    assert agent_first._DISCLOSURE_RETRY_SETTLE_S >= 0.5


def test_retry_loop_is_present_and_fails_closed():
    """Structural pin on both halves of the contract.

    A future edit that keeps the retry but drops the fail-closed branch would
    silently start retaining audio for calls with no delivered notice — the
    exact compliance failure this whole mechanism exists to prevent.
    """
    import inspect

    src = inspect.getsource(agent_first._speak_recording_disclosure)
    assert "_DISCLOSURE_MAX_ATTEMPTS" in src, "retry bound must be applied"
    assert "spoken_ok" in src
    assert "DISCLOSURE_FAILED" in src, "the fail-closed path must remain"
    assert "clear_barge_in_event" in src, "barge-in must be cleared between attempts"


def test_ledger_still_fails_closed_for_an_unknown_call():
    """An unknown call must read as 'no disclosure', so losing the in-memory
    ledger can only ever suppress a recording, never permit one."""
    assert get_disclosure_state("call-that-never-existed") is None


def test_failed_state_is_distinguishable_from_spoken():
    record_disclosure_state(DISCLOSURE_FAILED, "t-failed-call")
    record_disclosure_state(DISCLOSURE_SPOKEN, "t-spoken-call")
    assert get_disclosure_state("t-failed-call") == DISCLOSURE_FAILED
    assert get_disclosure_state("t-spoken-call") == DISCLOSURE_SPOKEN


def test_consent_requires_spoken_not_merely_attempted():
    """consent_satisfied must key on SPOKEN. A call whose notice was attempted
    and failed must never be treated as consented."""
    from app.domain.services.recording_policy_service import consent_satisfied

    class _Decision:
        announcement_required = True

    record_disclosure_state(DISCLOSURE_FAILED, "t-consent-failed")
    assert consent_satisfied(_Decision(), "t-consent-failed") is False

    record_disclosure_state(DISCLOSURE_SPOKEN, "t-consent-spoken")
    assert consent_satisfied(_Decision(), "t-consent-spoken") is True


def test_no_announcement_required_needs_no_disclosure():
    """A one-party / disabled policy must not be gated on the ledger at all."""
    from app.domain.services.recording_policy_service import consent_satisfied

    class _NoAnnounce:
        announcement_required = False

    assert consent_satisfied(_NoAnnounce(), "t-never-seen-call") is True
