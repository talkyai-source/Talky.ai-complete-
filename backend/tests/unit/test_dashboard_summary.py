"""Tests for the dashboard summary endpoint's new live + aggregate fields.

Covers active_calls, avg_call_duration_seconds, queued_jobs, and the
outcome_breakdown dict — the four fields added to replace the
synthetic Math.random / `total*0.18+6` values the old dashboard
rendered. We use a thin fake of the postgres-adapter Client so the
test doesn't need a live DB."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.api.v1.endpoints.dashboard import (
    DashboardSummary,
    get_dashboard_summary,
)


# ──────────────────────────────────────────────────────────────────
# Fake postgres-adapter Client
# ──────────────────────────────────────────────────────────────────
#
# The dashboard endpoint uses the chain-builder API:
#     db.table("calls").select("...").eq(...).execute()
# The table-builder is shared by every chain call so the test can
# program it to return different responses per query by inspecting the
# arguments. Each test below configures a single `_FakeClient` whose
# table-builder returns a canned PostgrestResponse for the specific
# select() / count combination it expects.


class _FakeBuilder:
    """Records the chain calls and returns the canned response on
    .execute(). For non-count selects, .data is iterated directly."""

    def __init__(self, *, count=None, data=None):
        self._count = count
        self._data = data or []
        self.calls: list[tuple] = []

    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        return self

    def eq(self, *args, **kwargs):
        self.calls.append(("eq", args, kwargs))
        return self

    def in_(self, *args, **kwargs):
        self.calls.append(("in_", args, kwargs))
        return self

    def gte(self, *args, **kwargs):
        self.calls.append(("gte", args, kwargs))
        return self

    def execute(self):
        return SimpleNamespace(count=self._count, data=self._data, error=None)


class _FakeClient:
    """Routes db.table(name) → a per-table builder you preconfigure."""

    def __init__(self, builders: dict[str, list[_FakeBuilder]]):
        # Per-table FIFO of builders so a single test can serve different
        # responses for each `db.table("calls")` invocation in order.
        self._builders = {k: list(v) for k, v in builders.items()}

    def table(self, name: str):
        queue = self._builders.get(name)
        if not queue:
            return _FakeBuilder(count=0, data=[])
        return queue.pop(0)


def _user(tenant_id: str = "tenant-uuid"):
    return SimpleNamespace(
        id="user-uuid",
        email="x@y",
        tenant_id=tenant_id,
        role="user",
        name=None,
        business_name=None,
        minutes_remaining=0,
    )


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_returns_zeros_for_empty_tenant():
    """All counters & aggregates are 0 when the tenant has no rows."""
    builders = {
        "calls": [
            _FakeBuilder(count=0, data=[]),    # 1: total_calls
            _FakeBuilder(count=0, data=[]),    # 2: answered_resp
            _FakeBuilder(count=0, data=[]),    # 3: failed_calls count
            _FakeBuilder(count=0, data=[]),    # 4: active_calls count
            _FakeBuilder(count=0, data=[]),    # 5: outcome_breakdown rows
        ],
        "campaigns": [_FakeBuilder(count=0, data=[])],
        "tenants": [_FakeBuilder(data=[{"minutes_allocated": 0}])],
        "dialer_jobs": [_FakeBuilder(count=0, data=[])],
    }
    db = _FakeClient(builders)
    result = await get_dashboard_summary(current_user=_user(), db_client=db)
    assert isinstance(result, DashboardSummary)
    assert result.active_calls == 0
    assert result.avg_call_duration_seconds == 0
    assert result.queued_jobs == 0
    assert result.outcome_breakdown == {}


@pytest.mark.asyncio
async def test_summary_computes_avg_duration_from_real_calls():
    """avg_call_duration_seconds = mean of duration_seconds across the
    answered/completed/in_progress rows in the current month, ignoring
    NULL / 0 durations (those are still-in-progress rows)."""
    answered_rows = [
        {"duration_seconds": 60},
        {"duration_seconds": 120},
        {"duration_seconds": 240},
        {"duration_seconds": None},  # in-progress, excluded from mean
        {"duration_seconds": 0},     # also excluded
    ]
    builders = {
        "calls": [
            _FakeBuilder(count=5, data=[]),                # total_calls
            _FakeBuilder(count=5, data=answered_rows),     # answered_resp (used twice)
            _FakeBuilder(count=0, data=[]),                # failed_calls
            _FakeBuilder(count=2, data=[]),                # active_calls
            _FakeBuilder(count=0, data=[]),                # outcome_breakdown
        ],
        "campaigns": [_FakeBuilder(count=1, data=[])],
        "tenants": [_FakeBuilder(data=[{"minutes_allocated": 100}])],
        "dialer_jobs": [_FakeBuilder(count=3, data=[])],
    }
    db = _FakeClient(builders)
    result = await get_dashboard_summary(current_user=_user(), db_client=db)
    # mean of [60, 120, 240] = 140
    assert result.avg_call_duration_seconds == 140
    assert result.active_calls == 2
    assert result.queued_jobs == 3


@pytest.mark.asyncio
async def test_summary_outcome_breakdown_groups_by_outcome():
    outcome_rows = [
        {"outcome": "goal_achieved"},
        {"outcome": "goal_achieved"},
        {"outcome": "answered"},
        {"outcome": "busy"},
        {"outcome": None},              # null → "unknown"
        {"outcome": "no_answer"},
        {"outcome": "no_answer"},
    ]
    builders = {
        "calls": [
            _FakeBuilder(count=7, data=[]),
            _FakeBuilder(count=7, data=[]),
            _FakeBuilder(count=0, data=[]),
            _FakeBuilder(count=0, data=[]),
            _FakeBuilder(count=7, data=outcome_rows),
        ],
        "campaigns": [_FakeBuilder(count=0, data=[])],
        "tenants": [_FakeBuilder(data=[{"minutes_allocated": 0}])],
        "dialer_jobs": [_FakeBuilder(count=0, data=[])],
    }
    db = _FakeClient(builders)
    result = await get_dashboard_summary(current_user=_user(), db_client=db)
    assert result.outcome_breakdown == {
        "goal_achieved": 2,
        "answered": 1,
        "busy": 1,
        "unknown": 1,
        "no_answer": 2,
    }


@pytest.mark.asyncio
async def test_summary_swallows_dialer_jobs_table_missing():
    """If dialer_jobs table is absent (fresh tenant), queued_jobs is 0
    rather than raising 500."""

    class _BoomBuilder:
        def __getattr__(self, name):
            raise RuntimeError("dialer_jobs missing")

    builders = {
        "calls": [
            _FakeBuilder(count=0, data=[]),
            _FakeBuilder(count=0, data=[]),
            _FakeBuilder(count=0, data=[]),
            _FakeBuilder(count=0, data=[]),
            _FakeBuilder(count=0, data=[]),
        ],
        "campaigns": [_FakeBuilder(count=0, data=[])],
        "tenants": [_FakeBuilder(data=[{"minutes_allocated": 0}])],
    }
    db = _FakeClient(builders)
    # Override .table so dialer_jobs raises on access.
    real_table = db.table

    def routed_table(name):
        if name == "dialer_jobs":
            return _BoomBuilder()
        return real_table(name)

    db.table = routed_table  # type: ignore[method-assign]
    result = await get_dashboard_summary(current_user=_user(), db_client=db)
    assert result.queued_jobs == 0
