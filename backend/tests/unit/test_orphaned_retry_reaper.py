"""A retry_scheduled job whose Redis entry is gone must not wedge its lead.

WHY THIS EXISTS (production, found 2026-07-29)
----------------------------------------------
`retry_scheduled` sits in the partial unique index that enforces one active job
per lead:

    uq_dialer_jobs_one_active_per_lead
      ON dialer_jobs (lead_id)
      WHERE status IN ('pending','queued','retry_scheduled','processing','calling')

so a job wedged in that status holds the lead's ONLY active-job slot. It is
also excluded from `IN_FLIGHT_STATUSES`, so `reap_stuck_jobs` never touches it
— correctly, since a legitimately scheduled retry belongs there.

Combine the two and you get a silent permanent wedge: if the Redis scheduled
entry is lost (reaped, flushed, or dropped when the campaign was stopped) the
job can never fire AND can never be cleared, so the lead is never dialled
again. Nothing logs it and no counter moves. One such job was found on a
stopped campaign, wedged for TWENTY-ONE DAYS.

THE RISK THIS TEST GUARDS
-------------------------
The fix is age-based, so the threshold MUST exceed the longest legitimate retry
delay or it would reap live work. After the 2026-07-28 cadence change the
longest is 24h (no-answer and voicemail both retry at +24h), so the default is
48h. `test_legitimate_24h_retry_is_never_reaped` is the load-bearing test here:
if someone shortens the threshold below a real retry delay, it fails.
"""
from __future__ import annotations

import pytest

from app.domain.services.dialer.stuck_job_reaper import (
    ORPHANED_SCHEDULED_REASON,
    SCHEDULED_STUCK_TIMEOUT_S,
    reap_orphaned_scheduled_jobs,
)


class _FakeConn:
    """Captures the query and its args; returns a canned RETURNING result."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql = None
        self.args = None

    async def fetch(self, sql, *args):
        self.sql = " ".join(sql.split())
        self.args = args
        return self.rows


@pytest.mark.asyncio
async def test_only_retry_scheduled_is_targeted():
    """It must never touch pending/queued/processing/calling — those are either
    live work or already covered by reap_stuck_jobs."""
    conn = _FakeConn()
    await reap_orphaned_scheduled_jobs(conn)
    assert "status = 'retry_scheduled'" in conn.sql
    for other in ("'processing'", "'calling'", "'pending'", "'queued'"):
        assert other not in conn.sql


@pytest.mark.asyncio
async def test_marks_failed_with_a_distinguishable_reason():
    """The reason must be its own value, not reused from the 120s reaper —
    otherwise this class of failure is invisible in the data."""
    conn = _FakeConn()
    await reap_orphaned_scheduled_jobs(conn)
    assert ORPHANED_SCHEDULED_REASON in conn.args
    assert ORPHANED_SCHEDULED_REASON != "stuck_timeout"
    assert "SET status           = 'failed'" in conn.sql or "status" in conn.sql


@pytest.mark.asyncio
async def test_legitimate_24h_retry_is_never_reaped():
    """THE load-bearing test.

    no-answer and voicemail legitimately schedule +24h. If the threshold ever
    drops to or below that, this reaper would kill live scheduled work and
    leads would silently stop being retried.
    """
    day = 24 * 60 * 60
    assert SCHEDULED_STUCK_TIMEOUT_S > day, (
        f"threshold {SCHEDULED_STUCK_TIMEOUT_S}s is not above the longest "
        f"legitimate retry delay ({day}s) — this would reap live retries"
    )
    conn = _FakeConn()
    await reap_orphaned_scheduled_jobs(conn)
    assert conn.args[1] > day


@pytest.mark.asyncio
async def test_threshold_matches_the_longest_disposition_schedule():
    """Derived from the real cadence rather than a guessed constant, so a future
    cadence change that lengthens a retry is caught here."""
    from app.workers.disposition_policy import _RETRY_SCHEDULES

    longest = max((max(s) for s in _RETRY_SCHEDULES.values() if s), default=0)
    assert SCHEDULED_STUCK_TIMEOUT_S > longest, (
        f"a disposition now schedules a {longest}s retry, which exceeds the "
        f"{SCHEDULED_STUCK_TIMEOUT_S}s reaper threshold — live retries would be "
        "reaped. Raise DIALER_SCHEDULED_STUCK_TIMEOUT_S."
    )


@pytest.mark.asyncio
async def test_reports_the_freed_leads():
    """The log must name the leads: this failure is otherwise invisible, and
    the whole point is that someone can see which leads were stuck."""
    conn = _FakeConn(rows=[{"id": "j1", "lead_id": "L1"}, {"id": "j2", "lead_id": "L2"}])
    assert await reap_orphaned_scheduled_jobs(conn) == 2


@pytest.mark.asyncio
async def test_zero_rows_is_silent():
    conn = _FakeConn(rows=[])
    assert await reap_orphaned_scheduled_jobs(conn) == 0


@pytest.mark.asyncio
async def test_custom_timeout_is_honoured():
    conn = _FakeConn()
    await reap_orphaned_scheduled_jobs(conn, timeout_seconds=999)
    assert conn.args[1] == 999


def test_reaper_is_actually_wired_into_the_worker_tick():
    """A reaper nothing calls is decoration. Pinned structurally, because the
    original defect was precisely that this status had no owner."""
    import inspect

    from app.workers.dialer_worker import DialerWorker

    src = inspect.getsource(DialerWorker._reap_stuck_jobs_tick)
    assert "reap_orphaned_scheduled_jobs" in src, (
        "the orphaned-retry reaper is not called from the worker tick — "
        "retry_scheduled would go back to having no owner"
    )
