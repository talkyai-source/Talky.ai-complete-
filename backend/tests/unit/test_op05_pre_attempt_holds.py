"""Real worker/queue holds with synthetic Redis, no provider or database I/O."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis
import pytest

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.dialer.job_states import ACTIVE_STATUSES
from app.domain.services.dialer.testing_override import TESTING_OVERRIDE_ENV
from app.domain.services.queue_service import DialerQueueService
from tests.unit.test_dialer_worker_block_visibility import _job, _worker


async def held_worker(monkeypatch, reason="outside_time_window_14:00_17:00", *, job=None):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    queue = DialerQueueService(fakeredis.FakeRedis(decode_responses=True))
    await queue.initialize()
    original = job or _job()
    assert await queue.enqueue_job(original)
    job = await queue.dequeue_job([original.tenant_id])
    assert job is not None
    worker = _worker()
    worker.queue_service = queue
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker.rules_engine.can_make_call = AsyncMock(return_value=(False, reason))
    return worker, job, queue


@pytest.mark.parametrize("reason", [
    "outside_time_window_14:00_17:00", "calling_not_allowed_on_Tue",
    "daily_lead_cap_reached_3/3", "max_concurrent_calls_reached_10/10",
])
async def test_pre_attempt_hold_retains_active_owner_and_original_attempt(monkeypatch, reason):
    worker, job, queue = await held_worker(monkeypatch, reason)
    try:
        before = datetime.now(timezone.utc).timestamp()
        await worker.process_job(job)
        entries = await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1, withscores=True)
        assert len(entries) == 1
        saved, due = entries[0]
        payload = json.loads(saved)
        assert payload["job_id"] == job.job_id
        assert payload["attempt_number"] == job.attempt_number == 1
        assert payload["status"] in ACTIVE_STATUSES
        assert worker._update_job_status.await_args.args[1] == JobStatus.RETRY_SCHEDULED
        assert worker._update_lead_status.await_args.args[1] == "pending"
        assert worker._update_job_status.await_args.kwargs["reason"] == reason
        if reason.startswith(("outside_time", "calling_not")):
            assert before + 66_000 <= due <= datetime.now(timezone.utc).timestamp() + 66_000
        elif reason.startswith("max_concurrent"):
            assert before + 300 <= due <= datetime.now(timezone.utc).timestamp() + 300
        else:
            assert 300 <= due - before <= 86_700
        assert await queue._redis.llen(queue.INFLIGHT_LIST) == 0
        worker._make_call.assert_not_awaited()
        worker._create_call_intent.assert_not_awaited()
    finally:
        await queue._redis.aclose()


@pytest.mark.parametrize("published", [False, True])
async def test_failed_or_lost_schedule_ack_keeps_original_evidence_and_recovers_once(monkeypatch, published):
    worker, job, queue = await held_worker(monkeypatch)
    redis = queue._redis
    original = await redis.hget(queue.INFLIGHT_HASH, job.job_id)
    zadd = redis.zadd
    fail = True

    async def interrupted_zadd(key, mapping, *args, **kwargs):
        nonlocal fail
        if key == queue.SCHEDULED_ZSET and fail:
            fail = False
            if published:
                await zadd(key, mapping, *args, **kwargs)
            raise ConnectionError("synthetic schedule acknowledgement unavailable")
        return await zadd(key, mapping, *args, **kwargs)

    monkeypatch.setattr(redis, "zadd", interrupted_zadd)
    try:
        await worker.process_job(job)
        assert job.attempt_number == 1
        assert await redis.hget(queue.INFLIGHT_HASH, job.job_id) == original
        assert await redis.lrange(queue.INFLIGHT_LIST, 0, -1) == [original]
        assert worker._update_job_status.await_args.args[1] == JobStatus.RETRY_SCHEDULED
        worker._make_call.assert_not_awaited()
        assert await redis.zcard(queue.SCHEDULED_ZSET) == int(published)
        await worker.process_job(DialerJob.from_redis_dict(json.loads(original)))
        scheduled = await redis.zrange(queue.SCHEDULED_ZSET, 0, -1)
        assert len(scheduled) == 1
        assert json.loads(scheduled[0])["attempt_number"] == 1
        assert await redis.llen(queue.INFLIGHT_LIST) == 0
        assert worker._update_job_status.await_args.args[1] == JobStatus.RETRY_SCHEDULED
    finally:
        await redis.aclose()


async def test_missing_original_payload_does_not_invent_a_retry(monkeypatch):
    worker, job, queue = await held_worker(monkeypatch)
    try:
        await queue._redis.hdel(queue.INFLIGHT_HASH, job.job_id)
        await worker.process_job(job)
        assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 0
        assert await queue._redis.llen(queue.INFLIGHT_LIST) == 1
        assert job.attempt_number == 1
        assert worker._update_job_status.await_args.args[1] == JobStatus.RETRY_SCHEDULED
        worker._make_call.assert_not_awaited()
    finally:
        await queue._redis.aclose()


@pytest.mark.parametrize("write", ["_update_lead_status", "_update_job_status"])
async def test_deferral_bookkeeping_failure_cannot_create_new_attempt(monkeypatch, write):
    worker, job, queue = await held_worker(monkeypatch)
    callback = getattr(worker, write)
    callback.side_effect = [ConnectionError("synthetic bookkeeping outage"), None]
    try:
        await worker.process_job(job)
        scheduled = await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1)
        if write == "_update_job_status":
            assert scheduled == []
            assert await queue._redis.hget(queue.INFLIGHT_HASH, job.job_id)
            callback.side_effect = None
            await worker.process_job(job)
            scheduled = await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1)
        assert len(scheduled) == 1
        assert json.loads(scheduled[0])["attempt_number"] == job.attempt_number == 1
        worker._make_call.assert_not_awaited()
        worker._create_call_intent.assert_not_awaited()
        callback.side_effect = None
        await queue._redis.zadd(queue.SCHEDULED_ZSET, {scheduled[0]: 0})
        assert await queue.process_scheduled_jobs() == 1
        resumed = await queue.dequeue_job([job.tenant_id])
        assert resumed.job_id == job.job_id and resumed.attempt_number == 1
        worker.rules_engine.can_make_call = AsyncMock(return_value=(True, "all_rules_passed"))
        await worker.process_job(resumed)
        worker._make_call.assert_awaited_once()
        assert await queue.process_scheduled_jobs() == 0
    finally:
        await queue._redis.aclose()


async def test_due_hold_resumes_same_attempt_and_one_provider_boundary(monkeypatch):
    worker, job, queue = await held_worker(monkeypatch)
    try:
        await worker.process_job(job)
        saved = (await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1))[0]
        await queue._redis.zadd(queue.SCHEDULED_ZSET, {saved: 0})
        assert await queue.process_scheduled_jobs() == 1
        resumed = await queue.dequeue_job([job.tenant_id])
        assert resumed.job_id == job.job_id and resumed.attempt_number == 1
        worker.rules_engine.can_make_call = AsyncMock(return_value=(True, "all_rules_passed"))
        await worker.process_job(resumed)
        worker._make_call.assert_awaited_once()
        assert worker._create_call_intent.await_args.args[0].attempt_number == 1
        assert await queue.process_scheduled_jobs() == 0
    finally:
        await queue._redis.aclose()


async def test_real_post_attempt_retry_still_advances_exactly_once():
    queue = DialerQueueService(fakeredis.FakeRedis(decode_responses=True))
    await queue.initialize()
    job = _job()
    try:
        assert await queue.enqueue_job(job)
        job = await queue.dequeue_job([job.tenant_id])
        assert await queue.schedule_retry(job, delay_seconds=60)
        saved = json.loads((await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1))[0])
        assert saved["job_id"] == job.job_id
        assert saved["attempt_number"] == job.attempt_number == 2
        assert saved["status"] == "retry_scheduled"
        assert await queue._redis.llen(queue.INFLIGHT_LIST) == 0
    finally:
        await queue._redis.aclose()
