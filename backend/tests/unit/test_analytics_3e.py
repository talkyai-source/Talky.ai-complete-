"""Unit tests for Phase-3e analytics: best-time-to-call + retry-effectiveness."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.analytics import (
    get_best_time_to_call,
    get_retry_effectiveness,
)


class _Resp:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """Chainable fake supporting select/gte/lt/eq/order/in_/execute."""
    def __init__(self, rows):
        self._rows = rows
    def select(self, *_a, **_k):
        return self
    def gte(self, *_a, **_k):
        return self
    def lt(self, *_a, **_k):
        return self
    def eq(self, *_a, **_k):
        return self
    def in_(self, *_a, **_k):
        return self
    def order(self, *_a, **_k):
        return self
    def execute(self):
        return _Resp(self._rows)


class _DB:
    def __init__(self, rows):
        self._rows = rows
    def table(self, _name):
        return _Query(self._rows)


class _User:
    tenant_id = "t1"


def _iso(y, mo, d, h):
    return datetime(y, mo, d, h, 0, tzinfo=timezone.utc).isoformat()


# ── best-time-to-call ─────────────────────────────────────────────
def test_best_time_buckets_by_hour_and_picks_best():
    # Hour 14 UTC: 6 answered of 6 → best. Hour 3: 1 of 6 answered.
    rows = (
        [{"created_at": _iso(2026, 6, 1, 14), "outcome": "answered"} for _ in range(6)]
        + [{"created_at": _iso(2026, 6, 2, 3), "outcome": "answered"}]
        + [{"created_at": _iso(2026, 6, 2, 3), "outcome": "no_answer"} for _ in range(5)]
    )
    res = asyncio.run(get_best_time_to_call(
        from_date="2026-06-01", to_date="2026-06-02", tz="UTC",
        current_user=_User(), db_client=_DB(rows),
    ))
    assert len(res.hours) == 24
    h14 = next(h for h in res.hours if h.hour == 14)
    assert h14.total == 6 and h14.answered == 6 and h14.answer_rate == 1.0
    h3 = next(h for h in res.hours if h.hour == 3)
    assert h3.total == 6 and h3.answered == 1
    assert res.best_hour == 14


def test_best_time_ignores_low_volume_for_best_hour():
    # Only 2 calls total, both answered at hour 9 — below min volume → no best.
    rows = [{"created_at": _iso(2026, 6, 1, 9), "outcome": "answered"} for _ in range(2)]
    res = asyncio.run(get_best_time_to_call(
        from_date="2026-06-01", to_date="2026-06-01", tz="UTC",
        current_user=_User(), db_client=_DB(rows),
    ))
    assert res.best_hour is None


def test_best_time_bad_tz_falls_back_to_utc():
    rows = [{"created_at": _iso(2026, 6, 1, 10), "outcome": "answered"}]
    res = asyncio.run(get_best_time_to_call(
        from_date="2026-06-01", to_date="2026-06-01", tz="Not/AZone",
        current_user=_User(), db_client=_DB(rows),
    ))
    assert res.timezone == "UTC"


# ── retry-effectiveness ───────────────────────────────────────────
def test_retry_effectiveness_orders_attempts_per_lead():
    # lead A: attempt1 no_answer, attempt2 answered.
    # lead B: attempt1 answered.
    rows = [
        {"lead_id": "A", "created_at": _iso(2026, 6, 1, 10), "outcome": "no_answer"},
        {"lead_id": "A", "created_at": _iso(2026, 6, 1, 12), "outcome": "answered"},
        {"lead_id": "B", "created_at": _iso(2026, 6, 1, 11), "outcome": "answered"},
    ]
    res = asyncio.run(get_retry_effectiveness(
        from_date="2026-06-01", to_date="2026-06-01",
        current_user=_User(), db_client=_DB(rows),
    ))
    by_attempt = {a.attempt: a for a in res.attempts}
    assert by_attempt[1].total == 2          # A#1 + B#1
    assert by_attempt[1].answered == 1       # only B#1
    assert by_attempt[2].total == 1          # A#2
    assert by_attempt[2].answered == 1
    assert by_attempt[2].answer_rate == 1.0


def test_retry_effectiveness_empty():
    res = asyncio.run(get_retry_effectiveness(
        from_date="2026-06-01", to_date="2026-06-01",
        current_user=_User(), db_client=_DB([]),
    ))
    assert res.attempts == []
