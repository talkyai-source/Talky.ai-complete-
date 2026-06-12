"""Unit tests for the dialer Phase-1 modules: status vocabulary, stuck-job
reaper, and campaign/lead job-lifecycle cancellation."""
import pytest

from app.domain.services.dialer import job_states as st
from app.domain.services.dialer.stuck_job_reaper import (
    reap_stuck_jobs,
    STUCK_REASON,
)
from app.domain.services.dialer.job_lifecycle import (
    cancel_active_jobs_for_campaign,
    cancel_active_jobs_for_lead,
    REASON_CAMPAIGN_STOPPED,
)


# ── status vocabulary ─────────────────────────────────────────
def test_active_and_terminal_are_disjoint():
    assert set(st.ACTIVE_STATUSES).isdisjoint(st.TERMINAL_STATUSES)


def test_in_flight_is_subset_of_active():
    assert set(st.IN_FLIGHT_STATUSES) <= set(st.ACTIVE_STATUSES)


def test_is_active_is_terminal():
    assert st.is_active("processing") is True
    assert st.is_active("completed") is False
    assert st.is_terminal("cancelled") is True
    assert st.is_terminal(None) is False


# ── reaper ────────────────────────────────────────────────────
class _FakeConn:
    def __init__(self, returned_ids):
        self._returned = returned_ids
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [{"id": i} for i in self._returned]


@pytest.mark.asyncio
async def test_reaper_marks_in_flight_and_returns_count():
    conn = _FakeConn(returned_ids=["a", "b", "c"])
    n = await reap_stuck_jobs(conn, timeout_seconds=90)
    assert n == 3
    sql, args = conn.calls[0]
    # queries the in-flight statuses, with the stuck reason + timeout
    assert args[0] == list(st.IN_FLIGHT_STATUSES)
    assert args[1] == STUCK_REASON
    assert args[2] == 90
    assert "UPDATE dialer_jobs" in sql and "failed" in sql


@pytest.mark.asyncio
async def test_reaper_noop_returns_zero():
    conn = _FakeConn(returned_ids=[])
    assert await reap_stuck_jobs(conn) == 0


# ── job lifecycle (Supabase-style adapter fake) ───────────────
class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, captured, rows):
        self.captured = captured
        self._rows = rows

    def update(self, vals):
        self.captured["update"] = vals
        return self

    def eq(self, col, val):
        self.captured.setdefault("eq", []).append((col, val))
        return self

    def in_(self, col, vals):
        self.captured["in"] = (col, list(vals))
        return self

    def execute(self):
        return _FakeResult(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self.captured = {}
        self._rows = rows

    def table(self, name):
        self.captured["table"] = name
        return _FakeQuery(self.captured, self._rows)


def test_cancel_active_jobs_for_campaign_builds_correct_query():
    db = _FakeDB(rows=[{"id": 1}, {"id": 2}])
    n = cancel_active_jobs_for_campaign(db, "camp-1", reason=REASON_CAMPAIGN_STOPPED)
    assert n == 2
    cap = db.captured
    assert cap["table"] == "dialer_jobs"
    assert cap["update"]["status"] == "cancelled"
    assert cap["update"]["failure_reason"] == REASON_CAMPAIGN_STOPPED
    assert ("campaign_id", "camp-1") in cap["eq"]
    assert cap["in"] == ("status", list(st.ACTIVE_STATUSES))


def test_cancel_active_jobs_for_lead_builds_correct_query():
    db = _FakeDB(rows=[{"id": 1}])
    n = cancel_active_jobs_for_lead(db, "lead-9", reason="removed_from_campaign")
    assert n == 1
    cap = db.captured
    assert ("lead_id", "lead-9") in cap["eq"]
    assert cap["update"]["status"] == "cancelled"
    assert cap["in"] == ("status", list(st.ACTIVE_STATUSES))


def test_cancel_is_noop_when_nothing_active():
    db = _FakeDB(rows=[])
    assert cancel_active_jobs_for_campaign(db, "camp-x", reason="campaign_stopped") == 0
