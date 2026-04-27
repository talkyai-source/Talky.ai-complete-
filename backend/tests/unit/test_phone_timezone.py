"""T1.5 — destination-timezone lookup.

Covers the phonenumbers-backed resolver and the Redis cache layer.
These tests rely on libphonenumber's geocoder, which is
deterministic: a US number → one of the Americas tz names, a UK
number → Europe/London, etc.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domain.services.phone_timezone import (
    lookup_timezone_sync,
    resolve_timezone,
)


# ──────────────────────────────────────────────────────────────────────────
# Minimal fake redis.asyncio supporting just GET and SET.
# ──────────────────────────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets = 0
        self.sets = 0

    async def get(self, key: str):
        self.gets += 1
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None):
        self.sets += 1
        self.store[key] = value
        return True


# ──────────────────────────────────────────────────────────────────────────
# Sync lookup
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "e164,expected_prefix",
    [
        ("+14155551234", "America/"),   # San Francisco area code
        ("+442079460000", "Europe/"),    # London
        ("+819012345678", "Asia/"),      # Japan mobile
        ("+61298765432", "Australia/"),  # Sydney
    ],
)
def test_lookup_returns_sensible_region(e164: str, expected_prefix: str):
    tz = lookup_timezone_sync(e164)
    assert tz is not None, f"no tz for {e164}"
    assert tz.startswith(expected_prefix), f"{tz} doesn't start with {expected_prefix}"


def test_lookup_none_on_empty_string():
    assert lookup_timezone_sync("") is None
    assert lookup_timezone_sync(None) is None  # type: ignore[arg-type]


def test_lookup_none_on_unparseable_garbage():
    assert lookup_timezone_sync("not-a-phone-number") is None


# ──────────────────────────────────────────────────────────────────────────
# Async resolver — cache behaviour
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_hits_cache_on_second_call():
    r = _FakeRedis()
    first = await resolve_timezone("+14155551234", redis_client=r, tenant_fallback_tz="UTC")
    second = await resolve_timezone("+14155551234", redis_client=r, tenant_fallback_tz="UTC")
    assert first == second
    # First call: 1 GET (miss), 1 SET. Second call: 1 more GET (hit), no SET.
    assert r.gets == 2
    assert r.sets == 1


@pytest.mark.asyncio
async def test_resolve_falls_back_to_tenant_tz_on_lookup_failure():
    r = _FakeRedis()
    tz = await resolve_timezone(
        "unparseable-junk", redis_client=r, tenant_fallback_tz="America/Chicago"
    )
    assert tz == "America/Chicago"
    # Cache should NOT store the fallback — we want to re-try real
    # resolution on the next call.
    assert r.sets == 0


@pytest.mark.asyncio
async def test_resolve_uses_fallback_when_no_redis():
    tz = await resolve_timezone("+14155551234", redis_client=None, tenant_fallback_tz="UTC")
    # libphonenumber gives us a real tz (some America/…), not the fallback.
    assert tz.startswith("America/")


@pytest.mark.asyncio
async def test_resolve_empty_number_returns_fallback():
    tz = await resolve_timezone("", redis_client=None, tenant_fallback_tz="America/New_York")
    assert tz == "America/New_York"


# ──────────────────────────────────────────────────────────────────────────
# CallGuard business-hours check integration
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_business_hours_uses_callee_timezone_not_tenant():
    """Simulate: tenant in UTC+0, business hours 09:00–17:00 UTC, dialing
    a Los Angeles number (UTC-8). At 17:30 UTC the check should FAIL —
    LA local is 09:30, which is IN hours but we're checking against
    the tenant's 09:00-17:00 which is already past close.

    Actually the point is reversed — this test confirms we're looking at
    the callee, not the tenant. Easiest way: set hours identical both
    sides but force the check to prove it's using the callee's tz.
    """
    from datetime import time as _time
    from unittest.mock import patch
    from app.domain.services.call_guard import (
        CallGuard, GuardCheck, TenantCallLimits,
    )

    guard = CallGuard(db_pool=None, redis_client=None)

    # Tenant in UTC, business hours 09:00-17:00 UTC
    limits = TenantCallLimits(
        respect_business_hours=True,
        business_hours_start=_time(9, 0),
        business_hours_end=_time(17, 0),
        business_hours_timezone="UTC",
    )

    # Freeze "now" to a moment where:
    #   - UTC time = 16:00 (inside tenant's 09–17 window)
    #   - Los Angeles local = 08:00 (OUTSIDE same 09–17 window)
    # A callee-timezone-aware check must REJECT.
    from datetime import datetime
    import pytz

    frozen_utc = datetime(2026, 1, 15, 16, 0, tzinfo=pytz.UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen_utc.astimezone(tz) if tz else frozen_utc

    # LA number
    with patch("app.domain.services.call_guard.datetime", _FrozenDatetime):
        result = await guard._check_business_hours(
            tenant_id="t1",
            phone_number="+13235551234",  # Los Angeles area code
            tenant_limits=limits,
        )

    assert result.passed is False, (
        "Expected outside-business-hours in LA (08:00 local) even though "
        "UTC is 16:00 which is inside the tenant's window — the check "
        "must use the callee's timezone"
    )
    assert result.details.get("tz_source") == "callee"
    assert "Los_Angeles" in result.details.get("timezone", "") or "America/" in result.details.get("timezone", "")


@pytest.mark.asyncio
async def test_business_hours_falls_back_to_tenant_tz_on_unknown_callee():
    """Unparseable phone number → fall back to tenant tz. This
    preserves the PRE-T1.5 behaviour so we never accidentally allow
    calls we would have blocked."""
    from datetime import time as _time, datetime
    from unittest.mock import patch
    import pytz
    from app.domain.services.call_guard import CallGuard, TenantCallLimits

    guard = CallGuard(db_pool=None, redis_client=None)
    limits = TenantCallLimits(
        respect_business_hours=True,
        business_hours_start=_time(9, 0),
        business_hours_end=_time(17, 0),
        business_hours_timezone="America/New_York",
    )

    frozen_utc = datetime(2026, 1, 15, 22, 0, tzinfo=pytz.UTC)  # 17:00 ET (end)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen_utc.astimezone(tz) if tz else frozen_utc

    with patch("app.domain.services.call_guard.datetime", _FrozenDatetime):
        result = await guard._check_business_hours(
            tenant_id="t1",
            phone_number="unparseable",
            tenant_limits=limits,
        )

    # 17:00 ET is the tail end of business hours — inclusive range passes.
    assert result.passed is True
    assert result.details.get("tz_source") == "tenant_fallback"


@pytest.mark.asyncio
async def test_business_hours_check_skipped_when_disabled():
    from app.domain.services.call_guard import CallGuard, TenantCallLimits
    guard = CallGuard(db_pool=None, redis_client=None)
    limits = TenantCallLimits(respect_business_hours=False)
    result = await guard._check_business_hours(
        tenant_id="t1",
        phone_number="+14155551234",
        tenant_limits=limits,
    )
    assert result.passed is True
    assert result.reason == "business_hours_not_enforced"
