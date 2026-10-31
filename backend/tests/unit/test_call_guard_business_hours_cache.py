"""Business hours must still block when tenant limits come from the cache.

Bug hunt 2026-10-10 (outbound-telephony-dialer-5): _get_tenant_limits cached
respect_business_hours but not business_hours_start/end. On a cache hit the
times came back None, _check_business_hours skipped its window test and
passed. Only the first call of each 60 s window was held to business hours;
every later call in a dialing campaign went out at any hour.
"""
from __future__ import annotations

import json
from datetime import datetime, time
from unittest.mock import patch

import pytz

from app.domain.services.call_guard import CallGuard
from tests.unit.test_call_guard_spend_limit_decimal import _FakeConn, _FakePool, _row

TENANT = "00000000-0000-0000-0000-000000000001"


class _DictRedis:
    def __init__(self):
        self.store, self.hits = {}, 0

    async def get(self, key):
        value = self.store.get(key)
        if value is not None:
            self.hits += 1
        return value

    async def setex(self, key, ttl, value):
        self.store[key] = value


class _EightPmUtc:
    @staticmethod
    def now(tz=None):
        frozen = datetime(2026, 1, 15, 20, 0, tzinfo=pytz.UTC)
        return frozen.astimezone(tz) if tz else frozen


def _guard(redis):
    row = _row(respect_business_hours=True, business_hours_start=time(9, 0),
               business_hours_end=time(17, 0), business_hours_timezone="UTC")
    return CallGuard(db_pool=_FakePool(_FakeConn(row)), redis_client=redis)


async def test_a_cached_evaluation_still_blocks_outside_business_hours():
    redis = _DictRedis()
    guard = _guard(redis)
    from_db = await guard._get_tenant_limits(TENANT)
    from_cache = await guard._get_tenant_limits(TENANT)
    assert redis.hits == 1
    assert (from_cache.business_hours_start, from_cache.business_hours_end) == (time(9, 0), time(17, 0))

    with patch("app.domain.services.call_guard.datetime", _EightPmUtc):
        for limits in (from_db, from_cache):
            result = await guard._check_business_hours(
                tenant_id=TENANT, phone_number="unparseable", tenant_limits=limits)
            assert result.passed is False and result.reason == "outside_business_hours"


async def test_an_entry_cached_without_the_hours_is_reloaded_not_trusted():
    # Entries written before the hours were cached live for up to 60 s after deploy.
    redis = _DictRedis()
    redis.store[f"callguard:limits:tenant:{TENANT}"] = json.dumps(
        {"respect_business_hours": True, "business_hours_timezone": "UTC"})
    limits = await _guard(redis)._get_tenant_limits(TENANT)
    assert (limits.business_hours_start, limits.business_hours_end) == (time(9, 0), time(17, 0))
