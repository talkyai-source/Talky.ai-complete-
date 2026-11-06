"""QA F6-04: dispatching a big campaign stops once the campaign is stopped.

Starting a 20,000-lead campaign pushed jobs one by one for ~20 minutes, and
the loop kept pushing cancelled jobs into the dialer queue for minutes after
Stop. Dispatch now re-reads the campaign every 100 jobs and stops when it is
no longer running; the rest stay pending, which Stop already cancels.
"""
from __future__ import annotations

import pytest

from tests.unit.test_campaign_dispatch_ack_race import _DB, _job, _service


class _Queue:
    def __init__(self):
        self.pushed = 0

    async def enqueue_job(self, job):
        self.pushed += 1
        return True


@pytest.mark.asyncio
async def test_dispatch_stops_once_the_campaign_is_stopped():
    jobs = [_job(n) for n in range(1, 451)]
    db = _DB(jobs)
    svc = _service(db)
    checks = []

    async def still_running(campaign_id, tenant_id):
        checks.append((campaign_id, tenant_id))
        return len(checks) < 2  # running at job 100, stopped by job 200

    svc._campaign_still_running = still_running
    queue = _Queue()
    accepted = await svc._dispatch_durable_jobs(queue, jobs)

    assert accepted == 200 and queue.pushed == 200
    statuses = [row["status"] for row in db.rows.values()]
    assert statuses.count("queued") == 200 and statuses.count("pending") == 250


@pytest.mark.asyncio
async def test_an_unreadable_status_keeps_dispatching():
    """The dialer re-checks the campaign before every call, so carrying on
    is safe; stopping would strand a running campaign half-dispatched."""
    jobs = [_job(n) for n in range(1, 251)]
    svc = _service(_DB(jobs))

    async def unreadable(campaign_id, tenant_id):
        raise RuntimeError("database blip")

    svc._campaign_still_running = unreadable
    assert await svc._dispatch_durable_jobs(_Queue(), jobs) == 250
