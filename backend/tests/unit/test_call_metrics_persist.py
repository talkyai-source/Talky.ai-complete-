"""Tests for the duration helper + tenant-minutes live helper.

The terminal-metrics write previously lived in
`save_call_metrics_on_hangup` (now removed). The atomic write+counter
chain is owned by `call_service.handle_call_status` instead — covered
in `test_outcome_resolver.py` and `test_call_service.py`.

What this file still covers:
  * `_compute_duration_seconds` — wall-clock helper still used by the
    lifecycle hook.
  * `compute_tenant_minutes_used` / `compute_tenant_minutes_remaining`
    — the dashboard's live deduction logic (unchanged behaviour)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.scripts.call_transcript_persister import (
    _compute_duration_seconds,
)
from app.services.scripts.tenant_minutes import (
    compute_tenant_minutes_remaining,
    compute_tenant_minutes_used,
)


# ──────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────


class _FakeConn:
    """Records executed queries; returns canned values for fetchval.

    Includes a no-op .transaction() context manager so the persister's
    SET LOCAL bypass-RLS wrapping works in tests."""

    def __init__(self, fetchval_value: Any = None):
        self.fetchval_value = fetchval_value
        self.calls: list[tuple] = []

    async def fetchval(self, query: str, *args):
        self.calls.append(("fetchval", query, args))
        return self.fetchval_value

    async def execute(self, query: str, *args):
        self.calls.append(("execute", query, args))
        return None

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self):
                return outer

            async def __aexit__(self, *a):
                return None

        return _Tx()


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return outer._conn

            async def __aexit__(self, *a):
                return None

        return _Ctx()


def _make_voice_session(
    *,
    started_at: datetime,
    dialer_call_id: str | None = None,
):
    cs = SimpleNamespace(started_at=started_at)
    return SimpleNamespace(
        call_session=cs,
        _dialer_call_id=dialer_call_id,
    )


# ──────────────────────────────────────────────────────────────────
# _compute_duration_seconds
# ──────────────────────────────────────────────────────────────────


def test_compute_duration_returns_zero_without_session():
    vs = SimpleNamespace()
    assert _compute_duration_seconds(vs) == 0


def test_compute_duration_handles_naive_datetime():
    started = datetime.utcnow() - timedelta(seconds=125)
    vs = _make_voice_session(started_at=started)
    secs = _compute_duration_seconds(vs)
    assert 120 <= secs <= 135  # tolerance for test scheduling


def test_compute_duration_handles_aware_datetime():
    started = datetime.now(timezone.utc) - timedelta(seconds=42)
    vs = _make_voice_session(started_at=started)
    secs = _compute_duration_seconds(vs)
    assert 38 <= secs <= 50


# ──────────────────────────────────────────────────────────────────
# (save_call_metrics_on_hangup tests retired — the function was
# removed; outcome+counter chain is covered by test_outcome_resolver
# and test_call_service)
# ──────────────────────────────────────────────────────────────────
# compute_tenant_minutes_used / remaining
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compute_minutes_returns_zero_for_no_pool():
    assert await compute_tenant_minutes_used(None, "abc") == 0


@pytest.mark.asyncio
async def test_compute_minutes_returns_zero_for_no_tenant():
    pool = _FakePool(_FakeConn(fetchval_value=600))
    assert await compute_tenant_minutes_used(pool, None) == 0


@pytest.mark.asyncio
async def test_compute_minutes_returns_zero_for_invalid_uuid():
    pool = _FakePool(_FakeConn(fetchval_value=600))
    assert await compute_tenant_minutes_used(pool, "not-a-uuid") == 0


@pytest.mark.asyncio
async def test_compute_minutes_floors_seconds_to_minutes():
    # 305 seconds → 5 minutes (305 // 60 = 5)
    conn = _FakeConn(fetchval_value=305)
    pool = _FakePool(conn)
    out = await compute_tenant_minutes_used(pool, str(uuid4()))
    assert out == 5
    # The aggregation runs inside a transaction that first issues a
    # `SET LOCAL app.bypass_rls` execute(), so the SUM lands on the
    # fetchval call — locate it by kind rather than a fixed index.
    fetchval_queries = [q for kind, q, _ in conn.calls if kind == "fetchval"]
    assert fetchval_queries, "expected a fetchval SUM aggregation"
    query = fetchval_queries[0]
    assert "SUM(duration_seconds)" in query
    assert "calls" in query


@pytest.mark.asyncio
async def test_compute_minutes_handles_db_error_as_zero():
    class _BoomConn:
        async def fetchval(self, *a, **k):
            raise RuntimeError("db down")

    pool = _FakePool(_BoomConn())  # type: ignore[arg-type]
    # Must not raise; treat as 0 used.
    out = await compute_tenant_minutes_used(pool, str(uuid4()))
    assert out == 0


@pytest.mark.asyncio
async def test_compute_minutes_remaining_subtracts_used():
    pool = _FakePool(_FakeConn(fetchval_value=600))  # 600s = 10m used
    remaining = await compute_tenant_minutes_remaining(
        pool, tenant_id=str(uuid4()), minutes_allocated=120
    )
    assert remaining == 110


@pytest.mark.asyncio
async def test_compute_minutes_remaining_floored_at_zero():
    pool = _FakePool(_FakeConn(fetchval_value=10_000_000))  # ridiculous usage
    remaining = await compute_tenant_minutes_remaining(
        pool, tenant_id=str(uuid4()), minutes_allocated=100
    )
    assert remaining == 0
