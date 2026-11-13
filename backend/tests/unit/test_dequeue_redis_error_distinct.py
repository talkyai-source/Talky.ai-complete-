"""A Redis failure in dequeue_job must not look like an empty queue."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.queue_service import DialerQueueService, QueueUnavailableError
from app.workers.dialer_worker import DialerWorker


def _service_with_broken_redis() -> DialerQueueService:
    redis = MagicMock()
    redis.lmove = AsyncMock(side_effect=ConnectionError("redis down"))
    service = DialerQueueService(redis_client=redis)
    service._initialized = True
    return service


@pytest.mark.asyncio
async def test_redis_error_raises_typed_error_not_none():
    service = _service_with_broken_redis()
    with pytest.raises(QueueUnavailableError):
        await service.dequeue_job(tenant_ids=["t1"])


@pytest.mark.asyncio
async def test_genuinely_empty_queue_still_returns_none():
    redis = MagicMock()
    redis.lmove = AsyncMock(return_value=None)
    service = DialerQueueService(redis_client=redis)
    service._initialized = True
    assert await service.dequeue_job(tenant_ids=["t1"]) is None
    assert await service.dequeue_job(tenant_ids=[]) is None


@pytest.mark.asyncio
async def test_worker_backs_off_and_survives_queue_outage(monkeypatch):
    worker = DialerWorker()
    worker.running = True
    worker.queue_service = MagicMock()
    worker.queue_service.process_scheduled_jobs = AsyncMock(return_value=0)
    calls = {"n": 0}

    async def dequeue(**_kw):
        calls["n"] += 1
        if calls["n"] >= 3:
            worker.running = False
        raise QueueUnavailableError("redis down")

    worker.queue_service.dequeue_job = dequeue
    worker._reap_stuck_jobs_tick = AsyncMock()
    worker._get_active_tenant_ids = AsyncMock(return_value=["t1"])
    worker.process_job = AsyncMock()
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    # More than MAX_CONSECUTIVE_ERRORS outages must not exit the process.
    await worker._run_loop(0)
    assert calls["n"] == 3
    assert len(sleeps) == 3 and all(s >= worker.POLL_INTERVAL for s in sleeps)
    worker.process_job.assert_not_called()
