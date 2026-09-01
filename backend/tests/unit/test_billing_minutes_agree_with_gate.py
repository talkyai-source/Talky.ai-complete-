"""Every minutes figure a tenant can see must equal the one that blocks them.

WHY THIS EXISTS (2026-08-03)
----------------------------
There were three different definitions of "minutes used" for one tenant:

  1. `minutes_quota.compute_minutes_status`  — SUM(duration_seconds), no filter.
     This is the GATE: the dialer, campaign-start and call-guard all block on it.
  2. `tenant_minutes.compute_tenant_minutes_used` — the same sum but filtered to
     `status IN ('answered','completed','in_progress')`. Feeds /auth/me,
     /auth/login, /profile, /billing/subscription and the overage alerts.
  3. `billing_service.get_usage_summary` — summed `usage_records` (empty; its
     only writer has no callers) against `tenants.minutes_used` (0 for every
     tenant). Feeds GET /billing/usage. Returned zero, always.

Only `completed` and `ended` have ever been written to `calls.status`;
`answered` and `in_progress` have never existed. So definition 2's filter
matched two values that never occur and excluded `ended`, the majority of
recorded call time. Compared against production data, the billing figure
ranged from a small fraction of the gate's number down to zero.

A tenant could be blocked for exhausting their plan while every screen they
could see said most of it was left.
"""
from __future__ import annotations

import pytest

from tests.unit._source_scan import BACKEND as _BACKEND
from tests.unit._source_scan import code as _code
from tests.unit._source_scan import code_only as _code_only
from tests.unit._source_scan import read as _read


# --------------------------------------------------------------------------
# One definition, not three
# --------------------------------------------------------------------------

def test_tenant_minutes_delegates_to_the_gate():
    """The billing/auth path must not carry its own SUM."""
    code = _code("app/services/scripts/tenant_minutes.py")
    assert "compute_minutes_status" in code, (
        "compute_tenant_minutes_used must delegate to the same computation "
        "the quota gate enforces, not re-derive it"
    )
    assert "SUM(duration_seconds)" not in code, (
        "tenant_minutes.py has grown its own aggregate again — that is how "
        "the two definitions drifted apart the first time"
    )


def test_the_dead_status_filter_is_gone_everywhere():
    """`status IN ('answered','completed','in_progress')` must not gate minutes.

    Two of those three values have never been written to `calls.status`, and
    the predicate excludes `ended` — the majority of real calls.
    """
    offenders = []
    for rel in (
        "app/services/scripts/tenant_minutes.py",
        "app/api/v1/endpoints/billing.py",
        "app/api/v1/endpoints/dashboard.py",
    ):
        for i, line in enumerate(_code(rel).splitlines(), 1):
            if "in_progress" in line and "answered" in line:
                offenders.append(f"{rel}:{i}")
    assert not offenders, (
        "the stale status predicate is back at " + str(offenders)
    )


def test_outcome_sets_have_exactly_one_definition():
    """They were duplicated in dashboard.py and analytics.py; a third copy in
    billing.py was what prompted hoisting them."""
    defining = []
    for path in (_BACKEND / "app").rglob("*.py"):
        code = _code_only(path.read_text(encoding="utf-8"))
        if '"customer_hung_up"' in code:
            defining.append(str(path.relative_to(_BACKEND)).replace("\\", "/"))
    # call_status.py owns the enum member; call_outcomes.py groups them by
    # reference. Nobody else may spell the string out.
    assert defining == ["app/domain/services/call_status.py"], (
        "the answered-outcome vocabulary should be spelled out only in the "
        "CallOutcome enum; found it in " + str(defining)
    )


def test_classification_is_total_over_the_outcome_enum():
    """Every CallOutcome must be either connected or failed.

    An unclassified member counts as neither, so `successful + failed` would
    silently stop equalling the calls that actually finished — the class of
    bug where a dashboard looks fine and is quietly wrong.
    """
    from app.domain.services.call_status import CallOutcome
    from app.domain.services.call_outcomes import (
        ANSWERED_OUTCOMES,
        FAILED_OUTCOMES,
    )

    classified = ANSWERED_OUTCOMES | FAILED_OUTCOMES
    unclassified = {o.value for o in CallOutcome} - classified
    assert not unclassified, (
        "CallOutcome member(s) " + str(sorted(unclassified)) + " are neither "
        "connected nor failed — classify them in call_outcomes.py"
    )


def test_the_three_consumers_import_the_canonical_sets():
    for rel in (
        "app/api/v1/endpoints/analytics.py",
        "app/api/v1/endpoints/dashboard.py",
        "app/api/v1/endpoints/billing.py",
    ):
        assert "call_outcomes" in _read(rel), rel


def test_answered_and_failed_never_overlap():
    from app.domain.services.call_outcomes import (
        ANSWERED_OUTCOMES,
        FAILED_OUTCOMES,
        GOAL_OUTCOMES,
    )
    assert not (ANSWERED_OUTCOMES & FAILED_OUTCOMES)
    assert GOAL_OUTCOMES <= ANSWERED_OUTCOMES


def test_analytics_aliases_still_resolve():
    """test_analytics.py imports the underscore names; keep them working."""
    from app.api.v1.endpoints.analytics import (
        _ANSWERED_OUTCOMES,
        _FAILED_OUTCOMES,
        _GOAL_OUTCOMES,
    )
    from app.domain.services.call_outcomes import ANSWERED_OUTCOMES

    assert _ANSWERED_OUTCOMES is ANSWERED_OUTCOMES
    assert _FAILED_OUTCOMES and _GOAL_OUTCOMES


# --------------------------------------------------------------------------
# The daily series
# --------------------------------------------------------------------------

def _daily_query() -> str:
    code = _code("app/api/v1/endpoints/billing.py")
    return code.split("async def get_daily_usage", 1)[1].split("\n@router", 1)[0]


def test_daily_failed_count_is_not_structurally_zero():
    """The old query filtered the outer WHERE to a status set and then counted
    `status NOT IN` that same set — a number that could never be nonzero.
    """
    q = _daily_query()
    assert "NOT IN" not in q, (
        "a NOT IN counter inside a query whose WHERE already restricts the "
        "same column is always zero — that exact bug shipped once"
    )
    assert "outcome = ANY" in q, (
        "connected/failed must be classified by outcome, not status"
    )


def test_daily_minutes_have_no_disposition_filter():
    """Call outcome/status never decides whether measured time is usage."""
    q = _daily_query()
    assert "status = ANY" not in q
    assert "SUM(duration_seconds)" in q


def test_daily_minutes_exclude_unsettled_inbound_usage_but_keep_outbound():
    """Daily billing must use the gate's settlement rule for parent time.

    Legacy outbound rows do not participate in inbound settlement and must
    remain visible.  Inbound reservations and reconciliation holds are not
    customer usage until their immutable finalize transaction commits.
    """
    q = _daily_query()
    assert "SUM(duration_seconds) FILTER" in q
    assert "direction IS DISTINCT FROM 'inbound'" in q
    assert "OR billing_status='finalized'" in q


# --------------------------------------------------------------------------
# GET /billing/usage
# --------------------------------------------------------------------------

def _usage_summary_body() -> str:
    code = _code("app/domain/services/billing_service.py")
    after = code.split("async def get_usage_summary", 1)[1]
    return after.split("\n    async def ", 1)[0]


def test_usage_summary_no_longer_reads_the_dead_column():
    """Asserts on the SELECTed columns, not a bare substring — `minutes_used`
    is also a suffix of `compute_tenant_minutes_used`, which is the function
    that FIXES this."""
    body = _usage_summary_body()
    selected = body.split(".select(", 1)[1].split(")", 1)[0]
    assert "minutes_used" not in selected, (
        "tenants.minutes_used is zero for every tenant in production; "
        "selecting it is what made this endpoint always return 0"
    )
    assert "minutes_allocated" in selected


def test_usage_summary_uses_the_live_minutes_computation():
    body = _usage_summary_body()
    assert "compute_tenant_minutes_used" in body


def test_usage_summary_still_serves_non_minute_types():
    """usage_records is unwired, not wrong — metered add-ons will use it."""
    body = _usage_summary_body()
    assert "usage_records" in body
    assert 'usage_type == "minutes"' in body


def test_record_usage_still_has_no_callers():
    """Pins the root cause. If someone wires record_usage up, this fails and
    forces a decision rather than leaving BOTH sources half-live."""
    hits = []
    for path in (_BACKEND / "app").rglob("*.py"):
        code = _code_only(path.read_text(encoding="utf-8"))
        for i, line in enumerate(code.splitlines(), 1):
            if "record_usage(" in line and "async def" not in line:
                hits.append(str(path.relative_to(_BACKEND)) + ":" + str(i))
    assert not hits, (
        "record_usage now has callers at " + str(hits) + " — decide whether "
        "usage_records or the calls table is authoritative for minutes"
    )


# --------------------------------------------------------------------------
# The arithmetic itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rows,expected_minutes",
    [
        ([], 0),
        ([("completed", 3600), ("ended", 5400)], 150),    # both statuses count
        ([("ended", 59)], 0),                             # floors, never rounds up
        ([("ended", 60)], 1),
    ],
)
def test_status_is_irrelevant_to_the_total(rows, expected_minutes):
    """Whatever statuses appear, the minute total is just the seconds."""
    from app.domain.services.minutes_quota import _status_from

    total = sum(secs for _status, secs in rows)
    assert _status_from(0, total).used_minutes == expected_minutes
