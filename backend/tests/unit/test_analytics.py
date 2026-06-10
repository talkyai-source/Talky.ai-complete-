"""Tests for analytics series helpers — outcome classification + bucketing.

Calls finish as status='ended'/'completed' with the real result in `outcome`,
so the dashboard charts must classify by outcome, not status.
"""
from datetime import datetime, timezone

from app.api.v1.endpoints.analytics import (
    _ANSWERED_OUTCOMES,
    _FAILED_OUTCOMES,
    _bucket_key,
    _classify,
    _to_series,
)


def test_classify_keys_on_outcome():
    b = {"total": 0, "answered": 0, "failed": 0, "goal": 0}
    _classify("answered", b)        # connected
    _classify("agent_hung_up", b)   # connected
    _classify("goal_achieved", b)   # connected + goal
    _classify("cancelled", b)       # failed
    _classify(None, b)              # counts toward total only
    assert b["total"] == 5
    assert b["answered"] == 3       # answered, agent_hung_up, goal_achieved
    assert b["failed"] == 1         # cancelled
    assert b["goal"] == 1           # goal_achieved


def test_bucket_key_grains():
    dt = datetime(2026, 6, 9, 14, 37, tzinfo=timezone.utc)
    assert _bucket_key(dt, "hour") == "2026-06-09T14:00"
    assert _bucket_key(dt, "day") == "2026-06-09"
    assert _bucket_key(dt, "month") == "2026-06-01"


def test_answered_and_failed_sets_are_disjoint():
    assert not (_ANSWERED_OUTCOMES & _FAILED_OUTCOMES)


def test_to_series_is_sorted_by_bucket():
    buckets = {
        "2026-06-09": {"total": 2, "answered": 1, "failed": 1, "goal": 0},
        "2026-06-08": {"total": 1, "answered": 1, "failed": 0, "goal": 1},
    }
    series = _to_series(buckets)
    assert [s.date for s in series] == ["2026-06-08", "2026-06-09"]
    assert series[0].goal_achieved == 1
