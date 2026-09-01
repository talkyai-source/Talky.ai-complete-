"""Tests for the shared tenant minute-quota helper.

The same computation gates the dialer (per-job skip), the start-campaign
endpoint (block start), and the frontend display — so the math living in
one place, correct, matters. Uses a tiny fake asyncpg connection.
"""
from __future__ import annotations

import pytest

from app.domain.services.minutes_quota import compute_minutes_status


class FakeConn:
    """Returns canned values for the two fetchval queries the helper
    runs: the first SELECT (minutes_allocated) then the SUM(duration)."""

    def __init__(self, allocated, used_seconds):
        self._allocated = allocated
        self._used = used_seconds
        self._calls = 0
        self.queries = []

    async def fetchval(self, query, *args):
        self._calls += 1
        self.queries.append((query, args))
        # First call = allocation lookup; second = used-seconds sum.
        return self._allocated if self._calls == 1 else self._used


@pytest.mark.asyncio
async def test_under_quota_not_exhausted():
    s = await compute_minutes_status(FakeConn(530, 3120), "t1")  # 52 min used
    assert s.allocated == 530
    assert s.used_minutes == 52
    assert s.remaining_minutes == 478
    assert s.unlimited is False
    assert s.exhausted is False


@pytest.mark.asyncio
async def test_at_quota_is_exhausted():
    s = await compute_minutes_status(FakeConn(30, 1800), "t1")  # exactly 30 min
    assert s.used_minutes == 30
    assert s.remaining_minutes == 0
    assert s.exhausted is True  # >= allocation


@pytest.mark.asyncio
async def test_over_quota_is_exhausted_remaining_clamped():
    s = await compute_minutes_status(FakeConn(30, 3176), "t1")  # 52 > 30
    assert s.used_minutes == 52
    assert s.remaining_minutes == 0          # clamped, never negative
    assert s.exhausted is True


@pytest.mark.asyncio
async def test_zero_allocation_is_unlimited_never_exhausted():
    s = await compute_minutes_status(FakeConn(0, 999999), "t1")
    assert s.unlimited is True
    assert s.exhausted is False               # unlimited is never blocked
    assert s.remaining_minutes == 0           # callers branch on `unlimited`


@pytest.mark.asyncio
async def test_null_allocation_treated_as_unlimited():
    s = await compute_minutes_status(FakeConn(None, 600), "t1")
    assert s.unlimited is True
    assert s.exhausted is False


@pytest.mark.asyncio
async def test_seconds_floor_to_whole_minutes():
    # 119 seconds = 1 whole minute (integer floor, matches dashboard).
    s = await compute_minutes_status(FakeConn(10, 119), "t1")
    assert s.used_minutes == 1
    assert s.exhausted is False


@pytest.mark.asyncio
async def test_used_total_includes_only_finalized_transfer_actual_seconds():
    conn = FakeConn(10, 179)

    status = await compute_minutes_status(conn, "t1")

    assert status.used_minutes == 2
    usage_sql = " ".join(conn.queries[1][0].split()).lower()
    assert "from call_legs leg" in usage_sql
    assert "join calls parent on parent.id=leg.call_id" in usage_sql
    assert "leg.leg_type='transfer'" in usage_sql
    assert "leg.billing_status='finalized'" in usage_sql
    assert "leg.billing_status='reserved'" not in usage_sql
    assert "leg.billing_status='held'" not in usage_sql
    assert "c.direction is distinct from 'inbound'" in usage_sql
    assert "c.billing_status='finalized'" in usage_sql
