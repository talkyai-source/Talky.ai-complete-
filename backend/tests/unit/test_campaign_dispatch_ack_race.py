"""Campaign Start must not report failure when the dialer took a job first.

Live 2026-10-20 11:35 UTC: Start enqueued its jobs one by one. The dialer
popped one before Start marked it queued, found its batch full and re-deferred
it, writing dialer_jobs.status='retry_scheduled'. Start's guarded
pending -> queued update then matched 0 rows and the request returned 503
("Dialer queue acknowledgement could not be persisted") while the campaign
went on dialing. A row that has left 'pending' no longer needs the outbox
replay, so it is an acknowledged dispatch; a row still 'pending' (or gone) is
not.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.dialer_job import DialerJob
from app.domain.services.campaign_service import CampaignDispatchError, CampaignService

TENANT = "11111111-1111-4111-8111-111111111111"
CAMPAIGN = "22222222-2222-4222-8222-222222222222"


def _job(n: int) -> DialerJob:
    return DialerJob(
        job_id=f"00000000-0000-4000-8000-{n:012d}",
        campaign_id=CAMPAIGN,
        lead_id=f"33333333-3333-4333-8333-{n:012d}",
        tenant_id=TENANT,
        phone_number="+442046132300",
    )


class _Query:
    def __init__(self, db, op, payload=None):
        self.db, self.op, self.payload, self.filters = db, op, payload, {}

    def eq(self, column, value):
        self.filters[column] = str(value)
        return self

    def execute(self):
        row = self.db.rows.get(self.filters.get("id"))
        scoped = row is not None and all(
            str(row.get(k)) == v for k, v in self.filters.items() if k != "status"
        )
        if self.op == "update":
            if scoped and row["status"] == self.filters.get("status"):
                row.update(self.payload)
                return SimpleNamespace(data=[dict(row)], error=None)
            return SimpleNamespace(data=[], error=None)
        self.db.selects.append(dict(self.filters))
        return SimpleNamespace(data=[dict(row)] if scoped else [], error=None)


class _Table:
    def __init__(self, db):
        self.db = db

    def update(self, payload):
        return _Query(self.db, "update", payload)

    def select(self, _columns):
        return _Query(self.db, "select")


class _DB:
    def __init__(self, jobs):
        self.rows = {
            j.job_id: {"id": j.job_id, "tenant_id": TENANT, "campaign_id": CAMPAIGN,
                       "lead_id": j.lead_id, "status": "pending"}
            for j in jobs
        }
        self.selects = []

    def table(self, name):
        assert name == "dialer_jobs"
        return _Table(self)


class _FastDialerQueue:
    """Redis accepts each job; the dialer re-defers the chosen ones at once."""

    def __init__(self, db, taken_first, status="retry_scheduled"):
        self.db, self.taken_first, self.status = db, set(taken_first), status

    async def enqueue_job(self, job):
        if job.job_id in self.taken_first:
            self.db.rows[job.job_id]["status"] = self.status
        return True


def _service(db):
    return CampaignService(db, SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["retry_scheduled", "calling", "processing", "completed", "cancelled"])
async def test_start_succeeds_when_the_dialer_moved_a_row_before_the_ack(status):
    jobs = [_job(1), _job(2), _job(3)]
    db = _DB(jobs)
    queue = _FastDialerQueue(db, {jobs[1].job_id}, status=status)

    accepted = await _service(db)._dispatch_durable_jobs(queue, jobs)

    assert accepted == 3
    assert db.rows[jobs[0].job_id]["status"] == "queued"
    assert db.rows[jobs[1].job_id]["status"] == status  # the dialer's state is kept
    assert db.rows[jobs[2].job_id]["status"] == "queued"
    # the re-read is tenant-scoped
    assert db.selects and db.selects[0]["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_a_row_still_pending_after_the_update_is_still_a_failure():
    job = _job(1)
    db = _DB([job])

    class _UpdateLost(_Query):
        def execute(self):
            if self.op == "update":
                return SimpleNamespace(data=[], error=None)  # nothing written
            return super().execute()

    db.table = lambda name: SimpleNamespace(
        update=lambda payload: _UpdateLost(db, "update", payload),
        select=lambda cols: _Query(db, "select"),
    )
    with pytest.raises(RuntimeError, match="not exact"):
        await _service(db)._confirm_job_dispatched(job)


@pytest.mark.asyncio
async def test_a_missing_row_is_still_a_failure():
    job = _job(1)
    db = _DB([])
    with pytest.raises(RuntimeError, match="not exact"):
        await _service(db)._confirm_job_dispatched(job)


@pytest.mark.asyncio
async def test_start_still_reports_a_real_acknowledgement_failure():
    jobs = [_job(1), _job(2)]
    db = _DB(jobs)
    del db.rows[jobs[1].job_id]  # the row vanished: a replay cannot be proven unnecessary

    with pytest.raises(CampaignDispatchError, match="acknowledgement could not be persisted") as err:
        await _service(db)._dispatch_durable_jobs(_FastDialerQueue(db, set()), jobs)
    assert err.value.jobs_enqueued == 2
