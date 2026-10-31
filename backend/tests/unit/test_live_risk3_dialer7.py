"""dialer-7: a queued/overdue-retry job whose payload is gone from Redis is
re-dispatched once on Start; present jobs, live calls and uncertainty are not."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.campaign_service import CampaignService
from app.domain.services.queue_service import DialerQueueService
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue

OLD = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


class _PresenceQueue(_Queue):
    def __init__(self, present, results=(True,)):
        super().__init__(list(results))
        self.present = present

    async def find_present_job_ids(self, tenant_id, job_ids):
        return None if self.present is None else {j for j in job_ids if j in self.present}


def _db_with_job(status="queued", scheduled_at=OLD, updated_at=OLD):
    db = _DB()
    db.tables["dialer_jobs"].append({
        "id": "job-1", "tenant_id": "tenant-1", "campaign_id": "campaign-1",
        "lead_id": "lead-1", "phone_number": "+14155550101", "priority": 5,
        "status": status, "attempt_number": 2, "scheduled_at": scheduled_at,
        "created_at": OLD, "updated_at": updated_at,
    })
    db.tables["calls"] = []
    return db


async def _start(db, queue):
    return await CampaignService(db, queue_service=queue).start_campaign(
        "campaign-1", tenant_id="tenant-1"
    )


@pytest.mark.asyncio
async def test_lost_queued_job_is_redispatched_once():
    db = _db_with_job()
    queue = _PresenceQueue(present=set())
    result = await _start(db, queue)
    assert result.jobs_enqueued == 1
    assert [j.job_id for j in queue.enqueued] == ["job-1"]
    assert queue.enqueued[0].attempt_number == 2
    assert len(db.tables["dialer_jobs"]) == 1
    assert db.tables["dialer_jobs"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_overdue_retry_scheduled_job_lost_from_redis_is_redispatched():
    db = _db_with_job(status="retry_scheduled")
    queue = _PresenceQueue(present=set())
    await _start(db, queue)
    assert [j.job_id for j in queue.enqueued] == ["job-1"]


@pytest.mark.asyncio
async def test_job_present_in_redis_is_not_duplicated():
    db = _db_with_job()
    queue = _PresenceQueue(present={"job-1"})
    await _start(db, queue)
    assert queue.enqueued == []
    assert db.tables["dialer_jobs"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_job_with_live_call_is_untouched():
    db = _db_with_job()
    db.tables["calls"].append(
        {"id": "c1", "tenant_id": "tenant-1", "dialer_job_id": "job-1", "status": "ringing"}
    )
    queue = _PresenceQueue(present=set())
    await _start(db, queue)
    assert queue.enqueued == []
    assert db.tables["dialer_jobs"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_recent_or_not_yet_overdue_jobs_are_left_alone():
    fresh = datetime.now(timezone.utc).isoformat()
    for db in (
        _db_with_job(updated_at=fresh),
        _db_with_job(status="retry_scheduled", scheduled_at=fresh),
    ):
        queue = _PresenceQueue(present=set())
        await _start(db, queue)
        assert queue.enqueued == []


@pytest.mark.asyncio
async def test_unknown_redis_presence_fails_closed():
    db = _db_with_job()
    queue = _PresenceQueue(present=None)
    await _start(db, queue)
    assert queue.enqueued == []
    assert db.tables["dialer_jobs"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_do_not_call_lead_with_lost_job_is_not_redispatched():
    db = _db_with_job()
    db.tables["leads"][0]["do_not_call"] = True
    queue = _PresenceQueue(present=set())
    await _start(db, queue)
    assert queue.enqueued == []


class _FakeRedis:
    def __init__(self):
        self.lists, self.z, self.h = {}, {}, {}

    async def zrange(self, key, a, b):
        return list(self.z.get(key, []))

    async def lrange(self, key, a, b):
        return list(self.lists.get(key, []))

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))


def _payload(job_id, tenant="tenant-1"):
    return json.dumps({"job_id": job_id, "tenant_id": tenant})


@pytest.mark.asyncio
async def test_find_present_job_ids_checks_every_structure_and_tenant():
    svc = DialerQueueService(redis_client=_FakeRedis())
    svc._initialized = True
    r = svc._redis
    r.lists[svc.TENANT_QUEUE_PREFIX.format(tenant_id="tenant-1")] = [_payload("a")]
    r.lists[svc.PRIORITY_QUEUE] = [_payload("b")]
    r.z[svc.SCHEDULED_ZSET] = [_payload("c")]
    r.h[svc.INFLIGHT_HASH] = {"d": _payload("d")}
    r.z[svc.PROCESSING_ZSET] = ["e"]
    r.lists[svc.INFLIGHT_LIST] = [_payload("f", tenant="other")]
    got = await svc.find_present_job_ids("tenant-1", list("abcdefg"))
    assert got == set("abcde")


@pytest.mark.asyncio
async def test_find_present_job_ids_returns_none_on_redis_error():
    class _Boom(_FakeRedis):
        async def zrange(self, *a):
            raise RuntimeError("down")

    svc = DialerQueueService(redis_client=_Boom())
    svc._initialized = True
    assert await svc.find_present_job_ids("tenant-1", ["a"]) is None


@pytest.mark.asyncio
async def test_second_start_on_running_campaign_repairs_lost_job_but_adds_no_new_work():
    db = _db_with_job()
    db.tables["campaigns"][0]["status"] = "running"
    db.tables["leads"].append(dict(db.tables["leads"][0], id="lead-2"))
    queue = _PresenceQueue(present=set())
    await _start(db, queue)
    assert [j.job_id for j in queue.enqueued] == ["job-1"]
    assert len(db.tables["dialer_jobs"]) == 1
