"""Ending a call must record what actually happened.

WHY THIS EXISTS (2026-08-03)
----------------------------
`POST /calls/{id}/hangup` — the operator "hang up" button — wrote

    outcome = COALESCE(outcome, 'agent_hung_up')

unconditionally. Its own docstring says the button is largely used to clear
"phantom stuck rows whose channel is already gone": calls that were never
answered. But `agent_hung_up` means "the AI agent ended a call it was having"
and sits in `call_outcomes.ANSWERED_OUTCOMES`, so each phantom an operator
cleared was counted as a successful conversation.

In production this became the largest single outcome bucket — more rows than
every genuinely answered call combined, none of them with an `answered_at`, a
transcript or a recording. The reported connect rate was more than double the
truth, and the error is self-reinforcing: the more stuck calls the system
produces, the better its numbers look.

`POST /admin/calls/{id}/terminate` had a matching defect from the other
direction: it wrote `status='terminated'` and `outcome='terminated_by_admin'`,
neither of which exists in the shared vocabulary. The status is not a
`CallState`, so the row was invisible to the live panel, the reapers and every
dashboard filter; the outcome is in neither ANSWERED_OUTCOMES nor
FAILED_OUTCOMES, so `successful + failed` silently stopped equalling the calls
that finished.

Neither endpoint set `duration_seconds`, so ending a genuinely live
conversation billed zero minutes for it.
"""
from __future__ import annotations

import re

import pytest

from tests.unit._source_scan import app_sources, code, function_body

_HANGUP = "app/api/v1/endpoints/calls.py"
_ADMIN = "app/api/v1/endpoints/admin/calls.py"


# --------------------------------------------------------------------------
# The operator hangup
# --------------------------------------------------------------------------

def _hangup_body() -> str:
    return function_body(code(_HANGUP), "hangup_live_call")


def test_outcome_is_conditional_not_constant():
    body = _hangup_body()
    assert "agent_hung_up" in body, "the answered case should still record it"
    assert "CASE WHEN" in body, (
        "the outcome must branch on whether the call was actually answered; a "
        "constant here is what made every cleared phantom look like a "
        "successful conversation"
    )


def test_the_branch_keys_off_evidence_that_the_call_connected():
    body = _hangup_body()
    assert "answered_at IS NOT NULL" in body, (
        "answered_at is the evidence a call connected — the phantom rows all "
        "had it NULL"
    )


def test_unanswered_calls_are_classified_as_failed():
    """`cancelled` is CallOutcome's 'we hung up before answer'."""
    from app.domain.services.call_outcomes import (
        ANSWERED_OUTCOMES,
        FAILED_OUTCOMES,
    )

    body = _hangup_body()
    assert "'cancelled'" in body
    assert "cancelled" in FAILED_OUTCOMES
    assert "cancelled" not in ANSWERED_OUTCOMES


def test_hangup_records_billable_duration():
    """An operator ending a live conversation used to bill zero minutes."""
    body = _hangup_body()
    assert "duration_seconds" in body
    assert "EXTRACT(EPOCH FROM" in body


def test_hangup_never_overwrites_a_real_outcome():
    """The ARI callback's verdict wins; this only fills a NULL."""
    body = _hangup_body()
    assert "COALESCE(\n" in body or "COALESCE(" in body
    # Both the outcome and the duration must be COALESCEd, not assigned.
    assert body.count("COALESCE(") >= 3  # ended_at, outcome, duration_seconds


def test_hangup_touches_updated_at():
    """It didn't, so the row's updated_at stayed stale after an operator
    ended the call — and the reapers key off updated_at."""
    assert "updated_at = NOW()" in _hangup_body()


# --------------------------------------------------------------------------
# The admin terminate
# --------------------------------------------------------------------------

def test_admin_terminate_uses_a_status_the_system_knows():
    from app.domain.services.call_status import CallState

    body = function_body(code(_ADMIN), "terminate_call")
    assert '"terminated"' not in body, (
        "'terminated' is not a CallState — the row becomes invisible to the "
        "live panel, the reapers and every dashboard filter"
    )
    assert CallState.ENDED.value in body


def test_admin_terminate_outcome_is_classifiable():
    from app.domain.services.call_outcomes import (
        ANSWERED_OUTCOMES,
        FAILED_OUTCOMES,
    )

    body = function_body(code(_ADMIN), "terminate_call")
    assert "terminated_by_admin" not in body, (
        "an outcome in neither ANSWERED_OUTCOMES nor FAILED_OUTCOMES makes "
        "successful + failed stop equalling the calls that finished"
    )
    classified = ANSWERED_OUTCOMES | FAILED_OUTCOMES
    assert "agent_hung_up" in classified and "cancelled" in classified


# --------------------------------------------------------------------------
# Nobody else may invent an outcome
# --------------------------------------------------------------------------

_OUTCOME_ASSIGN = re.compile(
    r"""["']outcome["']\s*:\s*["']([a-z_]+)["']"""   # {"outcome": "..."}
    r"""|outcome\s*=\s*["']([a-z_]+)["']"""          # outcome = '...'
    r"""|COALESCE\(outcome,\s*'([a-z_]+)'\)"""       # SQL COALESCE default
)


def _writes_the_calls_table(src: str) -> bool:
    """`outcome` is an overloaded word — latency_tracker records a turn
    outcome, resilient_llm an LLM-call outcome. Only modules that write the
    `calls` table are in scope here."""
    return any(
        marker in src
        for marker in ('table("calls")', "UPDATE calls", "INSERT INTO calls")
    )


def test_every_literal_outcome_written_anywhere_is_classified():
    """Catches the class of bug directly, not just the two known instances.

    An unclassified outcome is invisible rather than wrong-looking: totals
    still render, they just quietly stop adding up.
    """
    from app.domain.services.call_outcomes import (
        ANSWERED_OUTCOMES,
        FAILED_OUTCOMES,
    )

    known = ANSWERED_OUTCOMES | FAILED_OUTCOMES
    scanned = 0
    offenders: list[str] = []
    for rel, src in app_sources():
        if not _writes_the_calls_table(src):
            continue
        scanned += 1
        for i, line in enumerate(src.splitlines(), 1):
            for match in _OUTCOME_ASSIGN.finditer(line):
                value = next(g for g in match.groups() if g)
                if value not in known:
                    offenders.append(f"{rel}:{i} -> {value!r}")
    assert scanned >= 3, (
        f"only {scanned} modules matched the calls-table filter — the "
        "markers have drifted and this test is no longer scanning anything"
    )
    assert not offenders, (
        "outcome literal(s) that classify as neither connected nor failed: "
        + str(offenders)
        + " — add them to call_outcomes or use an existing CallOutcome"
    )


@pytest.mark.parametrize("was_answered,expected", [(True, "agent_hung_up"), (False, "cancelled")])
def test_admin_terminate_maps_both_cases(was_answered, expected):
    """Mirrors the endpoint's own expression so the mapping is pinned."""
    from app.domain.services.call_status import CallOutcome

    got = (
        CallOutcome.AGENT_HUNG_UP.value if was_answered
        else CallOutcome.CANCELLED.value
    )
    assert got == expected
