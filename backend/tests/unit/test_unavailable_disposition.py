"""The DNC-vs-retry invariant, and the UNAVAILABLE contradiction it caught.

THE BUG (fixed 2026-07-28): ``call_service.NON_RETRYABLE_OUTCOMES``
listed ``UNAVAILABLE`` — so that outcome set the lead's status to 'dnc'
and counted in ``calls_failed`` — while ``disposition_policy`` ALSO gave
``UNAVAILABLE`` a retry schedule of [24h, 24h] with cap 3. Both ran on
the same completed call: we flagged the lead do-not-call and then dialled
it twice more. That is repeated contact after suppression (TCPA / Ofcom)
and internally incoherent.

Neither module was individually wrong-looking; what was missing was an
assertion that the two tables agree. These tests are that assertion.
They are structural — they read the policy tables themselves, so they
keep holding as new outcomes are added.
"""
from __future__ import annotations

import pytest

from app.domain.models.dialer_job import CallOutcome
from app.domain.services import call_service
from app.workers import disposition_policy
from app.workers.disposition_policy import decide


# ── THE INVARIANT ────────────────────────────────────────────────────

def test_no_outcome_is_both_dnc_and_retryable():
    """The missing invariant: DNC and "we'll call it again" are exclusive.

    An outcome in both sets means a lead gets marked do-not-call and then
    redialled — the exact regulatory failure this test exists to block.
    """
    overlap = disposition_policy.DNC_OUTCOMES & disposition_policy.RETRYABLE_OUTCOMES
    assert not overlap, (
        "outcome(s) are simultaneously do-not-call and scheduled for retry: "
        f"{sorted(o.value for o in overlap)}"
    )


def test_call_service_dnc_set_agrees_with_policy():
    """call_service must not keep a second, drifting copy of the set."""
    dnc_side = call_service.NON_RETRYABLE_OUTCOMES - {CallOutcome.GOAL_ACHIEVED}
    assert dnc_side == disposition_policy.DNC_OUTCOMES

    # And the derived set is genuinely disjoint from the retry schedule.
    assert not (
        call_service.NON_RETRYABLE_OUTCOMES & disposition_policy.RETRYABLE_OUTCOMES
    )


def test_every_dnc_outcome_actually_stops_the_dialer():
    """Set membership is not enough — ``decide`` must refuse to retry."""
    for outcome in disposition_policy.DNC_OUTCOMES:
        for attempt in (1, 2, 3):
            d = decide(outcome, attempt)
            assert d.should_retry is False, (
                f"{outcome.value} is DNC but decide() scheduled a retry "
                f"at attempt {attempt}"
            )
            assert d.delay_seconds == 0


def test_invariant_guard_is_not_vacuous():
    """Prove the guard fires: inject a fake overlapping outcome.

    Without this, a guard that silently compared two empty sets would
    pass forever. VOICEMAIL is genuinely retryable; pretending it is also
    DNC must trip ``_assert_disjoint``.
    """
    original = disposition_policy.DNC_OUTCOMES
    disposition_policy.DNC_OUTCOMES = original | {CallOutcome.VOICEMAIL}
    try:
        with pytest.raises(RuntimeError, match="voicemail"):
            disposition_policy._assert_disjoint()
        # ...and the test-level invariant catches it too.
        assert (
            disposition_policy.DNC_OUTCOMES
            & disposition_policy.RETRYABLE_OUTCOMES
        ) == {CallOutcome.VOICEMAIL}
    finally:
        disposition_policy.DNC_OUTCOMES = original

    # Restored — the real tables are still clean.
    disposition_policy._assert_disjoint()


# ── UNAVAILABLE specifically ─────────────────────────────────────────

def test_unavailable_is_dnc_and_never_redialled():
    """One dial, then stop. UNAVAILABLE's only producer is the operator
    suppression endpoint (``CallService.mark_as_spam(reason=
    "unavailable")``) — a human saying "stop calling this number".
    """
    assert CallOutcome.UNAVAILABLE in call_service.NON_RETRYABLE_OUTCOMES
    assert CallOutcome.UNAVAILABLE in disposition_policy.DNC_OUTCOMES
    assert CallOutcome.UNAVAILABLE not in disposition_policy.RETRYABLE_OUTCOMES

    d = decide(CallOutcome.UNAVAILABLE, 1)
    assert d.should_retry is False
    assert d.is_success is False
    assert d.delay_seconds == 0
    assert d.reason == "unavailable"


def test_unavailable_has_no_retry_schedule_or_cap():
    """Belt and braces: the tables themselves must not mention it."""
    assert CallOutcome.UNAVAILABLE not in disposition_policy._RETRY_SCHEDULES
    assert CallOutcome.UNAVAILABLE not in disposition_policy._ATTEMPT_CAPS


# ── the retryable side must stay intact ──────────────────────────────

@pytest.mark.parametrize(
    "outcome",
    [
        CallOutcome.BUSY,
        CallOutcome.NO_ANSWER,
        CallOutcome.VOICEMAIL,
        CallOutcome.FAILED,
        CallOutcome.TIMEOUT,
    ],
)
def test_retryable_outcomes_are_not_dnc(outcome):
    """Guard the other direction: this fix must not have swept legitimate
    retryable dispositions onto the do-not-call list."""
    assert outcome in disposition_policy.RETRYABLE_OUTCOMES
    assert outcome not in call_service.NON_RETRYABLE_OUTCOMES
    assert decide(outcome, 1).should_retry is True
