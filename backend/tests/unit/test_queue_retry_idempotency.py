from __future__ import annotations

import pytest

from app.domain.models.dialer_job import CallOutcome, DialerJob
from app.domain.services.queue_service import DialerQueueService


class _AtomicRedis:
    def __init__(self) -> None:
        self.markers: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.scheduled: dict[str, float] = {}
        self.eval_calls = 0

    async def eval(self, _script, numkeys, marker_key, zset_key, *args):
        assert numkeys == 2
        assert zset_key == DialerQueueService.SCHEDULED_ZSET
        self.eval_calls += 1
        execute_at, payload = args
        if marker_key in self.markers:
            return 0
        # The real Lua script commits these two mutations atomically.
        self.scheduled[payload] = float(execute_at)
        self.markers[marker_key] = payload
        return 1

    async def expire(self, key, ttl):
        if key not in self.markers:
            return 0
        self.expirations[key] = int(ttl)
        return 1

    async def hget(self, _key, _field):
        return None

    async def zrem(self, _key, _member):
        return 0


def _job() -> DialerJob:
    return DialerJob(
        job_id="job-once",
        campaign_id="campaign-1",
        lead_id="lead-1",
        tenant_id="tenant-1",
        phone_number="+15550001111",
        attempt_number=1,
        last_outcome=CallOutcome.BUSY,
    )


@pytest.mark.asyncio
async def test_schedule_retry_once_replay_keeps_exactly_one_zset_member():
    redis = _AtomicRedis()
    queue = DialerQueueService(redis_client=redis)
    queue._initialized = True

    assert await queue.schedule_retry_once(
        _job(), delay_seconds=300, idempotency_key="call-1:job-once:2",
    )
    assert await queue.schedule_retry_once(
        _job(), delay_seconds=300, idempotency_key="call-1:job-once:2",
    )

    assert redis.eval_calls == 2
    assert len(redis.markers) == 1
    assert len(redis.scheduled) == 1
    # Marker expiry is forbidden until PostgreSQL confirms its outbox ack.
    assert redis.expirations == {}

    assert await queue.confirm_retry_once("call-1:job-once:2")
    assert list(redis.expirations.values()) == [90 * 24 * 3600]
