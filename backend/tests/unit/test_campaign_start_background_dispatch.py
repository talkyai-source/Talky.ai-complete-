"""D3-14: a large Start answers at once and queues its calls in the background.

Start answered only after every job had been pushed to Redis (in batches of
100, with a status re-check per batch), so a 20,000-contact campaign held the
request for minutes and only the clicking tab knew it was "preparing". Now,
above CAMPAIGN_START_INLINE_DISPATCH_MAX jobs (default 200), Start answers as
soon as the jobs are durable ('pending') and the campaign is 'running', and
the same dispatch runs in a background task. GET /campaigns/{id} shows
progress.phase "preparing" with jobs_awaiting_dispatch until every job is
queued. Smaller starts are unchanged.
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.domain.services import campaign_service
from app.domain.services.campaign_service import CampaignService, campaign_phase
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue


async def _drain():
    await asyncio.gather(*list(campaign_service._BACKGROUND_DISPATCHES))


async def test_a_large_start_answers_before_queueing_and_then_queues_everything(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_START_INLINE_DISPATCH_MAX", "2")
    db, queue = _DB(lead_count=3), _Queue([True, True, True])

    result = await CampaignService(db, queue_service=queue).start_campaign("campaign-1", tenant_id="tenant-1")

    assert (result.jobs_dispatching, result.jobs_enqueued) == (3, 0)
    assert queue.enqueued == [], "nothing is pushed before Start answers"
    assert [job["status"] for job in db.tables["dialer_jobs"]] == ["pending"] * 3
    assert db.tables["campaigns"][0]["status"] == "running"

    await _drain()
    assert len(queue.enqueued) == 3
    assert {job["status"] for job in db.tables["dialer_jobs"]} == {"queued"}


async def test_a_small_start_still_queues_before_answering(monkeypatch):
    monkeypatch.delenv("CAMPAIGN_START_INLINE_DISPATCH_MAX", raising=False)
    db, queue = _DB(lead_count=2), _Queue([True, True])

    result = await CampaignService(db, queue_service=queue).start_campaign("campaign-1", tenant_id="tenant-1")

    assert (result.jobs_dispatching, result.jobs_enqueued) == (0, 2)
    assert len(queue.enqueued) == 2
    assert not campaign_service._BACKGROUND_DISPATCHES


async def test_a_background_failure_leaves_the_rest_pending_for_a_repeat_start(monkeypatch, caplog):
    monkeypatch.setenv("CAMPAIGN_START_INLINE_DISPATCH_MAX", "1")
    db, queue = _DB(lead_count=2), _Queue([True, False])

    result = await CampaignService(db, queue_service=queue).start_campaign("campaign-1", tenant_id="tenant-1")
    assert result.jobs_dispatching == 2
    await _drain()

    assert sorted(job["status"] for job in db.tables["dialer_jobs"]) == ["pending", "queued"]
    assert "campaign_background_dispatch_failed" in caplog.text


@pytest.mark.parametrize("status,live,waiting,phase", [
    ("running", 0, 5, "preparing"),
    ("running", 2, 5, "preparing"),
    ("running", 2, 0, "running"),
    ("stopped", 1, 3, "stopping"),
    ("paused", 0, 3, "paused"),
])
def test_phase_is_preparing_while_a_running_campaigns_jobs_wait_to_be_queued(status, live, waiting, phase):
    assert campaign_phase(status, live, waiting) == phase


async def test_get_campaign_reports_the_jobs_still_waiting_to_be_queued(monkeypatch):
    queries = []

    class Conn:
        async def fetchval(self, query, *args):
            queries.append((query, args))
            if "FROM dialer_jobs" in query:
                return 7
            return 0 if "count(*)" in query else None

        async def fetch(self, query, *args):
            return []

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Conn()

    class Service:
        async def get_campaign(self, campaign_id):
            return {"id": campaign_id, "tenant_id": "t1", "status": "running"}

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: Service())
    response = await campaigns_ep.get_campaign(
        campaign_id="c1", request=SimpleNamespace(),
        current_user=SimpleNamespace(id="u1", tenant_id="t1"), db_client=SimpleNamespace(pool=object()))

    assert response["progress"] == {"live_calls": 0, "jobs_awaiting_dispatch": 7, "phase": "preparing"}
    query = next(q for q, _ in queries if "FROM dialer_jobs" in q)
    assert "tenant_id = $1" in query and "campaign_id = $2" in query and "'pending'" in query
