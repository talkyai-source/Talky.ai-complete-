"""Starting a campaign is a compare-and-set on its status (QA F2-09, QA-BE-062/064).

Six parallel starts of one campaign returned 200 three times and pushed 20 Redis
payloads for 12 jobs. Every start read the campaign as not running, did its
work and wrote ``status = 'running'`` unconditionally, so all of them went on to
dispatch. The write is now ``UPDATE ... WHERE id AND tenant_id AND direction AND
status = <the status this start read>``: one start wins it, and the others get
what starting an already-running campaign gets ("Campaign is already running")
before they can reach the queue.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.campaign_service import (
    CampaignDirectionError,
    CampaignService,
    CampaignStateError,
    StartCampaignResult,
)
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue, _Query


class _GatedQueue(_Queue):
    """Pushes wait for the test, so a second start can overlap the first one's dispatch."""

    def __init__(self) -> None:
        super().__init__([True] * 20)
        self.gate = asyncio.Event()

    async def enqueue_job(self, job):
        await self.gate.wait()
        return await super().enqueue_job(job)


def _change_after_reading(service: CampaignService, change) -> None:
    """Run ``change`` after this start has read the campaign and before it writes."""
    original = service._evaluate_outbound_readiness

    async def evaluate(campaign, campaign_id, tenant_id):
        change()
        return await original(campaign, campaign_id, tenant_id)

    service._evaluate_outbound_readiness = evaluate


def _set_campaign(db: _DB, **fields):
    return lambda: db.tables["campaigns"][0].update(fields)


async def _overlapping_starts(db: _DB, queue: _GatedQueue, **start_kwargs):
    """Two requests that have both read the campaign before either one writes."""
    arrived = []
    both_read = asyncio.Event()
    services = [CampaignService(db, queue_service=queue) for _ in range(2)]
    for service in services:
        original = service._evaluate_outbound_readiness

        async def evaluate(campaign, campaign_id, tenant_id, _original=original):
            arrived.append(1)
            if len(arrived) == 2:
                both_read.set()
            await both_read.wait()
            return await _original(campaign, campaign_id, tenant_id)

        service._evaluate_outbound_readiness = evaluate
    tasks = [asyncio.create_task(s.start_campaign("campaign-1", tenant_id="tenant-1", **start_kwargs))
             for s in services]
    for _ in range(100):  # let both run until they are parked at the queue
        await asyncio.sleep(0)
    queue.gate.set()
    return await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("before", ["draft", "paused", "stopped", "completed"])
async def test_overlapping_starts_dispatch_each_job_once_and_the_loser_is_already_running(before):
    db, queue = _DB(lead_count=3), _GatedQueue()
    db.tables["campaigns"][0]["status"] = before

    results = await _overlapping_starts(db, queue)

    winners = [r for r in results if isinstance(r, StartCampaignResult)]
    losers = [r for r in results if isinstance(r, CampaignStateError)]
    assert (len(winners), len(losers)) == (1, 1), results
    assert (losers[0].message, losers[0].status_code) == ("Campaign is already running", 400)
    pushed = [job.job_id for job in queue.enqueued]
    assert len(pushed) == 3 and len(set(pushed)) == 3, "each job is pushed once, by the start that won"
    assert db.tables["campaigns"][0]["status"] == "running"


@pytest.mark.parametrize("before", ["draft", "scheduled", "paused", "stopped", "completed", "cancelled", "active"])
async def test_the_guard_does_not_narrow_which_states_a_start_is_accepted_from(before):
    # Start has never refused a source state (the web client only offers draft, paused and
    # stopped, but the API accepts the rest), and 0088's status CHECK is NOT VALID, so legacy
    # values such as "active" can exist. The guard names the state each start read, never a list.
    db, queue = _DB(lead_count=2), _Queue([True, True])
    db.tables["campaigns"][0]["status"] = before

    result = await CampaignService(db, queue_service=queue).start_campaign("campaign-1", tenant_id="tenant-1")

    assert result.jobs_enqueued == 2
    assert db.tables["campaigns"][0]["status"] == "running"


async def test_a_start_that_loses_the_status_write_never_reaches_the_queue():
    db, queue = _DB(lead_count=2), _Queue([True, True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, status="running"))  # another start won in between

    with pytest.raises(CampaignStateError) as lost:
        await service.start_campaign("campaign-1", tenant_id="tenant-1")

    assert lost.value.message == "Campaign is already running"
    assert queue.enqueued == []


async def test_the_loser_gets_exactly_what_starting_a_running_campaign_gets():
    running_db = _DB(lead_count=0)
    running_db.tables["campaigns"][0]["status"] = "running"
    with pytest.raises(CampaignStateError) as already_running:
        await CampaignService(running_db, queue_service=_Queue([])).start_campaign(
            "campaign-1", tenant_id="tenant-1")

    db = _DB(lead_count=2)
    service = CampaignService(db, queue_service=_Queue([True, True]))
    _change_after_reading(service, _set_campaign(db, status="running"))
    with pytest.raises(CampaignStateError) as lost:
        await service.start_campaign("campaign-1", tenant_id="tenant-1")

    assert (type(lost.value), lost.value.message, lost.value.status_code) == (
        type(already_running.value), already_running.value.message, already_running.value.status_code)


async def test_a_start_with_no_leads_is_guarded_too():
    db = _DB(lead_count=0)
    service = CampaignService(db, queue_service=_Queue([]))
    _change_after_reading(service, _set_campaign(db, status="running"))

    with pytest.raises(CampaignStateError) as lost:
        await service.start_campaign("campaign-1", tenant_id="tenant-1")

    assert lost.value.message == "Campaign is already running"


async def test_a_campaign_deleted_while_it_was_starting_is_not_revived():
    db, queue = _DB(lead_count=2), _Queue([True, True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, status="deleted"))

    with pytest.raises(CampaignStateError) as refused:
        await service.start_campaign("campaign-1", tenant_id="tenant-1")

    assert "changed while it was starting" in refused.value.message
    assert queue.enqueued == []
    assert db.tables["campaigns"][0]["status"] == "deleted"


async def test_a_direction_change_while_starting_is_still_the_direction_error():
    db, queue = _DB(lead_count=2), _Queue([True, True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, direction="inbound"))

    with pytest.raises(CampaignDirectionError):
        await service.start_campaign("campaign-1", tenant_id="tenant-1")

    assert queue.enqueued == []


async def test_the_status_write_carries_the_tenant_and_the_status_the_start_read(monkeypatch):
    seen = []
    execute = _Query.execute

    def spy(self):
        seen.append((self.operation, self.table, self.payload, list(self.filters)))
        return execute(self)

    monkeypatch.setattr(_Query, "execute", spy)
    db = _DB(lead_count=1)
    db.tables["campaigns"][0]["status"] = "paused"

    await CampaignService(db, queue_service=_Queue([True])).start_campaign("campaign-1", tenant_id="tenant-1")

    (filters,) = [f for op, table, payload, f in seen
                  if op == "update" and table == "campaigns" and payload.get("status") == "running"]
    assert ("eq", "id", "campaign-1") in filters
    assert ("eq", "tenant_id", "tenant-1") in filters
    assert ("eq", "direction", "outbound") in filters
    assert ("in", "status", ("paused",)) in filters


async def test_the_real_query_builder_puts_the_tenant_and_status_in_the_sql(monkeypatch):
    # The production role bypasses row-level security, so the tenant must be in the SQL itself.
    from types import SimpleNamespace

    from app.core.postgres_adapter import QueryBuilder

    captured = []

    def execute(self):
        captured.append(self._build_where_clause())
        return SimpleNamespace(data=[{"id": "campaign-1"}], error=None)

    monkeypatch.setattr(QueryBuilder, "execute", execute)
    service = CampaignService(SimpleNamespace(table=lambda name: QueryBuilder(None, name)))

    await service._update_campaign_status(
        "campaign-1", "running", tenant_id="tenant-1", from_statuses=("paused",))

    assert captured == [("WHERE id = $1 AND tenant_id = $2 AND status = ANY($3) AND direction = $4",
                         ["campaign-1", "tenant-1", ["paused"], "outbound"])]


async def test_a_list_start_on_a_campaign_that_turns_running_is_not_a_start_to_win():
    # allow_running (call this list) re-enters a running campaign on purpose.
    db, queue = _DB(lead_count=2), _Queue([True, True])
    service = CampaignService(db, queue_service=queue)
    _change_after_reading(service, _set_campaign(db, status="running"))

    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert result.jobs_enqueued == 2 and len(queue.enqueued) == 2
