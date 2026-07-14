"""FIX 1(a) (2026-07-13): `_create_call_record` must persist `dialer_job_id`
on the `calls` row at INSERT time.

Root cause: the pooled call-teardown path (`call_service._handle_call_
status_pooled`) resolves the dialer job to finalize from `calls.dialer_
job_id`. Before this fix, the dialer worker never wrote that column, so
the column was always NULL for every dialer-originated call and job
finalization (lead status update + dialer_jobs PROCESSING -> terminal)
was permanently dead on the pooled (production) path.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker


class _RecordingConn:
    """Records every executed query + its positional args. No real state —
    `_create_call_record` only ever calls `execute`, never reads results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> None:
        self.calls.append((" ".join(query.split()), args))


class _FakePool:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def acquire(self):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return outer._conn

            async def __aexit__(self, *a):
                return None

        return _Ctx()


@pytest.mark.asyncio
async def test_create_call_record_persists_dialer_job_id():
    worker = DialerWorker()
    conn = _RecordingConn()
    worker._db_pool = _FakePool(conn)

    job = DialerJob(
        job_id="job-abc-123",
        campaign_id="camp-1",
        lead_id="lead-1",
        tenant_id="tenant-1",
        phone_number="+15551234567",
    )

    internal_call_id, talklee_call_id, leg_id = await worker._create_call_record(
        job, "provider-call-99",
    )

    assert internal_call_id and talklee_call_id and leg_id

    insert_calls_queries = [
        (q, args) for q, args in conn.calls if q.startswith("INSERT INTO calls")
    ]
    assert insert_calls_queries, "expected an INSERT INTO calls statement"
    query, args = insert_calls_queries[0]

    assert "dialer_job_id" in query, (
        "INSERT INTO calls must write dialer_job_id — without it the "
        "pooled teardown path can never resolve the dialer_jobs row to "
        "finalize (see call_service._handle_call_status_pooled)."
    )
    assert job.job_id in args, (
        "the job's id must be one of the bound INSERT parameters"
    )


@pytest.mark.asyncio
async def test_create_call_record_swallows_db_errors_and_still_returns_ids():
    class _BoomConn:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    worker = DialerWorker()
    worker._db_pool = _FakePool(_BoomConn())  # type: ignore[arg-type]

    job = DialerJob(
        job_id="job-xyz",
        campaign_id="camp-1",
        lead_id="lead-1",
        tenant_id="tenant-1",
        phone_number="+15551234567",
    )

    internal_call_id, talklee_call_id, leg_id = await worker._create_call_record(
        job, "provider-call-1",
    )
    assert internal_call_id
    assert talklee_call_id
    assert leg_id == ""
