"""Unit tests for the dialer Phase-1 modules: status vocabulary, stuck-job
reaper, and campaign/lead job-lifecycle cancellation."""
import pytest

from app.domain.services.dialer import job_states as st
from app.domain.services.dialer.stuck_job_reaper import (
    reap_stuck_jobs,
    reap_stuck_calls,
    STUCK_REASON,
    _INFLIGHT_CALL_STATUSES,
    _LIVE_CALL_STATUSES,
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


@pytest.mark.asyncio
async def test_stuck_call_reaper_closes_zombie_calls():
    # A stale non-terminal call must be closed so it frees its batch slot and
    # leaves the live-calls panel.
    conn = _FakeConn(returned_ids=["c1", "c2"])
    n = await reap_stuck_calls(conn, timeout_seconds=600)
    assert n == 2
    sql, args = conn.calls[0]
    assert args[0] == list(_INFLIGHT_CALL_STATUSES)
    assert args[1] == 600
    assert "UPDATE calls" in sql and "'ended'" in sql
    # Preserves any real outcome already written; only fills a missing one.
    assert "COALESCE(outcome" in sql


@pytest.mark.asyncio
async def test_stuck_call_reaper_noop_returns_zero():
    conn = _FakeConn(returned_ids=[])
    assert await reap_stuck_calls(conn) == 0


# ── reaper: must not kill a live/answered call's job (BUG 1 fix) ──────────
#
# `reap_stuck_jobs` now binds a 4th positional arg (`_LIVE_CALL_STATUSES`)
# and its SQL carries a `NOT EXISTS (... calls ... c.dialer_job_id = ...)`
# anti-join excluding jobs whose linked call is currently live. The OLD
# query only ever bound 3 args and had no awareness of the `calls` table at
# all, so it reaped ANY in-flight job past 120s regardless of whether the
# call it originated was mid-conversation. `_SemanticFakeConn` below
# actually evaluates that WHERE clause (driven by the bound args, not
# hardcoded) against a small seeded dataset, so these tests fail against the
# old 3-arg query — it errors reaching `args[3]` — and would pass-through
# every stuck job (including the live one) if the exclusion clause were
# ever dropped.

class _SemanticFakeConn:
    """Fake asyncpg connection whose ``fetch`` reproduces the exact
    predicate ``reap_stuck_jobs`` issues: in-flight status + past timeout,
    MINUS any job whose linked ``calls`` row (via ``dialer_job_id``) is
    currently in a live status. Driven entirely by the bound query args so a
    regression to the old (call-unaware) query is caught: the old call site
    only passes 3 args, so indexing ``args[3]`` below raises immediately.
    """

    def __init__(self, jobs, calls_by_job_id):
        self._jobs = jobs  # list of dicts: id, status, updated_at (age in seconds)
        self._calls_by_job_id = calls_by_job_id  # job_id -> call status (or absent)
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        in_flight_statuses, reason, timeout_seconds, live_call_statuses = (
            args[0], args[1], args[2], args[3],
        )
        assert "NOT EXISTS" in sql
        assert "calls" in sql
        assert "dialer_job_id" in sql

        reaped = []
        for job in self._jobs:
            if job["status"] not in in_flight_statuses:
                continue
            if job["age_seconds"] < timeout_seconds:
                continue
            call_status = self._calls_by_job_id.get(job["id"])
            if call_status in live_call_statuses:
                continue  # excluded: a live call is running for this job
            reaped.append(job["id"])
        return [{"id": i} for i in reaped]


@pytest.mark.asyncio
async def test_reaper_query_binds_live_call_statuses_arg():
    """Structural guard: the query must carry the calls-table exclusion and
    bind `_LIVE_CALL_STATUSES` as its 4th arg. Fails on the old 3-arg query."""
    conn = _SemanticFakeConn(jobs=[], calls_by_job_id={})
    await reap_stuck_jobs(conn, timeout_seconds=120)
    sql, args = conn.calls[0]
    assert len(args) == 4
    assert args[3] == list(_LIVE_CALL_STATUSES)
    # "initiated" must NOT be treated as live — a call stuck there never
    # progressed past origination and is exactly the hung case to reap.
    assert "initiated" not in _LIVE_CALL_STATUSES


@pytest.mark.asyncio
async def test_reaper_does_not_reap_job_with_live_answered_call():
    """A job whose call has been ANSWERED and is >2 minutes into a live
    conversation must survive the 120s job timeout. This is the exact
    double-dial bug: old code reaped the job mid-call, released the
    active-job dedup slot, and let the lead be re-enqueued and dialed again
    while the first call was still live."""
    jobs = [{"id": "job-live", "status": "processing", "age_seconds": 150}]
    calls_by_job_id = {"job-live": "answered"}
    conn = _SemanticFakeConn(jobs, calls_by_job_id)

    reaped = await reap_stuck_jobs(conn, timeout_seconds=120)

    assert reaped == 0
    sql, args = conn.calls[0]
    assert args[3] == list(_LIVE_CALL_STATUSES)


@pytest.mark.asyncio
async def test_reaper_still_reaps_hung_job_with_no_call_at_all():
    """A job that never even reached origination (no `calls` row) is a
    genuine zombie and must still be reaped — the fix must only narrow, not
    disable, reaping."""
    jobs = [{"id": "job-hung", "status": "processing", "age_seconds": 150}]
    conn = _SemanticFakeConn(jobs, calls_by_job_id={})

    reaped = await reap_stuck_jobs(conn, timeout_seconds=120)

    assert reaped == 1


@pytest.mark.asyncio
async def test_reaper_still_reaps_job_whose_call_never_left_initiated():
    """A call row that only ever reached `initiated` (created but the
    provider never confirmed the channel) is the textbook hung-origination
    case — it must still be reaped, not treated as live."""
    jobs = [{"id": "job-stalled", "status": "calling", "age_seconds": 300}]
    calls_by_job_id = {"job-stalled": "initiated"}
    conn = _SemanticFakeConn(jobs, calls_by_job_id)

    reaped = await reap_stuck_jobs(conn, timeout_seconds=120)

    assert reaped == 1


@pytest.mark.asyncio
async def test_reaper_mixed_batch_reaps_only_the_hung_one():
    jobs = [
        {"id": "job-live", "status": "processing", "age_seconds": 200},
        {"id": "job-hung", "status": "calling", "age_seconds": 200},
        {"id": "job-fresh", "status": "processing", "age_seconds": 5},
    ]
    calls_by_job_id = {"job-live": "in_call"}
    conn = _SemanticFakeConn(jobs, calls_by_job_id)

    reaped = await reap_stuck_jobs(conn, timeout_seconds=120)

    assert reaped == 1  # only job-hung: job-live is excluded, job-fresh is too young


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
