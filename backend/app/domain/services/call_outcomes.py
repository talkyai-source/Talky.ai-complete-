"""Canonical classification of a finished call's disposition.

Whether a call *connected* is decided by `calls.outcome`, never by
`calls.status`. Only two statuses have ever been written in production:
`completed` and `ended`.

`answered`, `in_progress`, `busy` &c. are **outcomes**. Endpoints that
filtered on `status IN ('answered', 'completed', 'in_progress')` were
therefore matching two values that have never existed while excluding
`ended`, the largest real bucket — see `tenant_minutes.py` for the measured
fallout.

These sets lived inline in `dashboard.py` and again in `analytics.py`. They
are hoisted here because a third caller (`billing.py`'s daily-usage series)
needed them, and duplicated-then-diverged constants are precisely the failure
this module exists to stop.

The sets are built FROM `call_status.CallOutcome` rather than restating its
strings, so a typo cannot silently create a fourth vocabulary — and
`test_billing_minutes_agree_with_gate` asserts the partition stays TOTAL. If
someone adds a member to `CallOutcome` without classifying it here, that test
fails. Otherwise the new outcome would count as neither connected nor failed
and every dashboard total would quietly stop adding up.

NOT A BILLING PREDICATE
-----------------------
These classify a call for *display* ("how many connected / failed"). They
must not be used to decide which calls consume plan minutes. Billable time is
the sum of `duration_seconds` with **no** disposition filter — an unconnected
call carries no duration, so it filters itself, while `voicemail` counts as a
failed *disposition* yet is real connected airtime the carrier charges for.
Minute totals come from `minutes_quota.compute_minutes_status`, the single
source of truth.
"""
from __future__ import annotations

from app.domain.services.call_status import CallOutcome

# Written directly by `call_repository.mark_goal_achieved` and the
# disposition policy, bypassing the CallOutcome enum. Tracked separately so
# the totality check over CallOutcome stays meaningful.
_GOAL_ACHIEVED = "goal_achieved"
_GOAL_NOT_ACHIEVED = "goal_not_achieved"

# The caller was reached and a conversation (or an attempt at one) happened.
ANSWERED_OUTCOMES = frozenset({
    CallOutcome.ANSWERED.value,
    CallOutcome.CUSTOMER_HUNG_UP.value,
    CallOutcome.AGENT_HUNG_UP.value,
    _GOAL_ACHIEVED,
    _GOAL_NOT_ACHIEVED,
})

# The call did not reach a live human.
FAILED_OUTCOMES = frozenset({
    CallOutcome.NO_ANSWER.value,
    CallOutcome.BUSY.value,
    CallOutcome.REJECTED.value,
    CallOutcome.UNREACHABLE.value,
    CallOutcome.NETWORK_FAILURE.value,
    CallOutcome.FAILED.value,
    CallOutcome.CANCELLED.value,
    CallOutcome.VOICEMAIL.value,
})

# The subset of answered calls that met the campaign's objective.
GOAL_OUTCOMES = frozenset({_GOAL_ACHIEVED})

# Postgres array literals for `outcome = ANY($n::text[])` predicates.
ANSWERED_OUTCOME_LIST = sorted(ANSWERED_OUTCOMES)
FAILED_OUTCOME_LIST = sorted(FAILED_OUTCOMES)
