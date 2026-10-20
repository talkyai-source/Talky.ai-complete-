"""billing-3: the outbound minutes check-and-reserve is atomic per tenant."""
import asyncio
import time
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.domain.services.call_guard import CallGuard, TenantCallLimits
from tests.unit.test_op01_metering_unavailable import Meter, Pool

TENANT = "11111111-1111-4111-8111-111111111111"
LIM = TenantCallLimits(monthly_minutes_allocated=10)


def _guard(redis, **meter):
    return SimpleNamespace(_db_pool=Pool(Meter(**meter)), _redis=redis)


async def _eval(guard, **kw):
    return await CallGuard._check_minutes_quota(
        guard, tenant_id=TENANT, tenant_limits=LIM, estimated_duration_seconds=60, **kw)


async def test_burst_with_room_for_one_call_lets_exactly_one_through():
    redis = fakeredis.aioredis.FakeRedis()
    # 9 of 10 minutes used, no calls row yet: room for exactly one 1-min call.
    guard = _guard(redis, allocated=10, used=9 * 60)
    results = await asyncio.gather(*[_eval(guard, lead_id=f"lead-{i}") for i in range(12)])
    assert sum(r.passed for r in results) == 1


async def test_same_lead_reevaluated_is_not_double_counted():
    redis = fakeredis.aioredis.FakeRedis()
    guard = _guard(redis, allocated=10, used=9 * 60)
    assert (await _eval(guard, lead_id="a")).passed
    assert (await _eval(guard, lead_id="a")).passed
    assert not (await _eval(guard, lead_id="b")).passed


async def test_expired_reservations_stop_counting():
    redis = fakeredis.aioredis.FakeRedis()
    guard = _guard(redis, allocated=10, used=9 * 60)
    assert (await _eval(guard, lead_id="a")).passed
    assert not (await _eval(guard, lead_id="b")).passed
    # Age the reservation past its expiry score.
    await redis.zadd(f"minutes:pending:{TENANT}", {"a": time.time() - 1})
    assert (await _eval(guard, lead_id="b")).passed


async def test_tenants_do_not_share_reservations():
    redis = fakeredis.aioredis.FakeRedis()
    guard = _guard(redis, allocated=10, used=9 * 60)
    assert (await _eval(guard, lead_id="a")).passed
    other = "22222222-2222-4222-8222-222222222222"
    r = await CallGuard._check_minutes_quota(
        guard, tenant_id=other, tenant_limits=LIM, estimated_duration_seconds=60, lead_id="a")
    assert r.passed


async def test_redis_error_falls_back_to_non_atomic_check():
    class Broken:
        async def eval(self, *a, **k):
            raise ConnectionError("redis down")

    ok = await _eval(_guard(Broken(), allocated=10, used=9 * 60), lead_id="a")
    assert ok.passed is True
    over = await _eval(_guard(Broken(), allocated=10, used=10 * 60), lead_id="a")
    assert over.passed is False and "minutes_quota_exhausted" in over.reason


async def test_unlimited_tenant_never_touches_redis():
    class Spy:
        calls = 0

        async def eval(self, *a, **k):
            Spy.calls += 1
            return 1

    r = await CallGuard._check_minutes_quota(
        _guard(Spy(), allocated=0, plan_minutes=0), tenant_id=TENANT,
        tenant_limits=TenantCallLimits(monthly_minutes_allocated=0))
    assert r.reason == "verified_unlimited" and Spy.calls == 0
