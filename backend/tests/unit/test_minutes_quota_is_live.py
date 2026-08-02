"""The minutes quota must be enforced against LIVE usage.

WHY THIS EXISTS (2026-08-03)
----------------------------
`CallGuard._check_minutes_quota` compared an estimate against
`tenant_call_limits.monthly_minutes_used`. A repo-wide search showed exactly one
writer of that column: the admin `PUT /admin/tenants/{id}/call-limits` endpoint.
NOTHING increments it when a call actually happens.

So the check compared against a number frozen at whatever an operator last typed
— normally 0. And `POST /sip/telephony/call` is reachable by any logged-in user,
not just the dialer, with this guard as its only quota gate. The method's own
docstring claimed it "closes the direct-origination revenue leak"; it did not,
because the counter never moved.

The fix routes it through `compute_minutes_status` — the same live computation
the dialer's gate uses, summed from the `calls` table — so both paths enforce
against the same reality instead of two different ones.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_BACKEND / rel).read_text(encoding="utf-8")


def _method_body(src: str, name: str) -> str:
    """Source of one method, sliced at its DEFINITION.

    Splitting on the bare name grabs a dispatch-table mention ~600 lines
    earlier and slices the wrong region entirely — the first version of this
    test failed against perfectly correct code for exactly that reason.
    """
    after = src.split("async def " + name, 1)[1]
    return after.split("\n    async def ", 1)[0]


def test_the_stored_column_still_has_no_incrementing_writer():
    """Pins the ROOT CAUSE, not just the symptom.

    If someone later adds a real writer for monthly_minutes_used, this fails
    and prompts a decision: keep the live computation, or go back to the
    column. What must never happen again is enforcing against a column that
    nothing maintains, silently.
    """
    hits = []
    for path in (_BACKEND / "app").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            if "monthly_minutes_used" not in low:
                continue
            if ("update" in low and "set" in low) or "+=" in line:
                hits.append(str(path.relative_to(_BACKEND)) + ":" + str(i))
    assert not hits, (
        "monthly_minutes_used now appears to have an incrementing writer at "
        + str(hits)
        + " — revisit whether the guard should use the column or the live "
        "computation, but do not leave BOTH half-wired"
    )


def test_guard_uses_the_live_computation():
    quota = _method_body(
        _read("app/domain/services/call_guard.py"), "_check_minutes_quota"
    )
    assert "compute_minutes_status" in quota, (
        "the minutes guard must enforce against live usage from the calls "
        "table, not the stored counter that nothing increments"
    )


def test_guard_and_dialer_share_one_source_of_truth():
    """Two gates enforcing different numbers is how a tenant gets blocked by
    minutes that never appear on their invoice."""
    guard = _read("app/domain/services/call_guard.py")
    assert (
        "from app.domain.services.minutes_quota import compute_minutes_status"
        in guard
    )


def test_live_lookup_failure_falls_back_rather_than_blocking():
    """The method's stated contract is that a metering hiccup never strands a
    legitimate call. Adding a DB round trip must not change that."""
    quota = _method_body(
        _read("app/domain/services/call_guard.py"), "_check_minutes_quota"
    )
    assert "except Exception" in quota
    assert "falling back" in quota.lower()


@pytest.mark.parametrize(
    "allocated,used_seconds,should_be_exhausted",
    [
        (100, 0, False),
        (100, 3600, False),      # 60 of 100 minutes
        (100, 6000, True),       # 100 of 100
        (0, 999999, False),      # 0 allocation == unlimited
    ],
)
def test_status_maths(allocated, used_seconds, should_be_exhausted):
    from app.domain.services.minutes_quota import _status_from

    st = _status_from(allocated, used_seconds)
    assert st.exhausted is should_be_exhausted
    assert st.used_minutes == used_seconds // 60


def test_status_field_is_used_minutes_not_minutes_used():
    """Trivial, and it caught a real slip: the first version of the fix read
    `_status.minutes_used`, which does not exist — the AttributeError would
    have been swallowed by the except-branch and silently kept using the stale
    column, leaving the leak open while looking fixed."""
    from app.domain.services.minutes_quota import _status_from

    st = _status_from(10, 60)
    assert hasattr(st, "used_minutes")
    assert not hasattr(st, "minutes_used")


def test_teardown_quiesces_the_call_before_reading_its_state():
    """Separate bug, same deploy.

    `_on_call_ended` popped the session then did several awaited DB round trips
    — transcript persist, outcome resolve, opt-out purge — while the in-flight
    turn task (a SIBLING task, not a child of pipeline_task) kept running. It
    could set `_caller_opted_out` AFTER the purge decision had been made, so a
    caller who said "take me off your list" and hung up mid-reply could be
    called again.
    """
    src = _read("app/domain/services/telephony/lifecycle.py")
    # Anchored on the DEFINITION. Splitting on the bare name lands on a
    # docstring mention ~1300 lines earlier — the same slicing mistake this
    # file already made once for _check_minutes_quota.
    ended = _method_body(src, "_on_call_ended")
    assert "cancel_active_turn" in ended, (
        "teardown must cancel the in-flight turn BEFORE reading and persisting "
        "call state, or the opt-out flag can land after the purge decision"
    )
