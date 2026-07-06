"""Regression tests for the minutes-quota guard check (P0-1).

Closes the direct-origination revenue leak: `POST /sip/telephony/call` reached
the carrier with NO minutes gate, so an exhausted tenant billed unmetered
calls. The check must (a) be wired into evaluate()'s checks list, (b) block
when minutes are exhausted, and (c) fail-OPEN when no quota is configured or on
error (never strand a legitimate call on a metering hiccup).
"""
import inspect

import pytest

from app.domain.services.call_guard import CallGuard, GuardCheck, TenantCallLimits

# _check_minutes_quota reads only its args (not self) — call it unbound with a
# dummy self so the test needs no db/redis/limiter wiring.
_SELF = object()


@pytest.mark.asyncio
async def test_no_allocation_passes():
    """No quota configured (allocated <= 0) → unlimited → pass (fail-open)."""
    r = await CallGuard._check_minutes_quota(
        _SELF, tenant_id="t", tenant_limits=TenantCallLimits(monthly_minutes_allocated=0)
    )
    assert r.passed is True
    assert r.reason == "no_minutes_quota"


@pytest.mark.asyncio
async def test_missing_limits_passes():
    """No limits row at all → fail-open."""
    r = await CallGuard._check_minutes_quota(_SELF, tenant_id="t", tenant_limits=None)
    assert r.passed is True


@pytest.mark.asyncio
async def test_within_quota_passes():
    lim = TenantCallLimits(monthly_minutes_allocated=100, monthly_minutes_used=50)
    r = await CallGuard._check_minutes_quota(
        _SELF, tenant_id="t", tenant_limits=lim, estimated_duration_seconds=60
    )
    assert r.passed is True
    assert r.details["remaining"] == 50


@pytest.mark.asyncio
async def test_exhausted_blocks():
    lim = TenantCallLimits(monthly_minutes_allocated=100, monthly_minutes_used=100)
    r = await CallGuard._check_minutes_quota(
        _SELF, tenant_id="t", tenant_limits=lim, estimated_duration_seconds=60
    )
    assert r.passed is False
    assert "minutes_quota_exhausted" in r.reason


@pytest.mark.asyncio
async def test_this_call_would_exceed_blocks():
    """9 used of 10, a 2-minute call (ceil) → 9+2=11 > 10 → block."""
    lim = TenantCallLimits(monthly_minutes_allocated=10, monthly_minutes_used=9)
    r = await CallGuard._check_minutes_quota(
        _SELF, tenant_id="t", tenant_limits=lim, estimated_duration_seconds=120
    )
    assert r.passed is False


def test_check_is_wired_into_evaluate():
    """The check must be registered in evaluate()'s checks list, or it never
    runs and the leak silently reopens."""
    src = inspect.getsource(CallGuard.evaluate)
    assert "GuardCheck.MINUTES_QUOTA" in src
    assert "self._check_minutes_quota" in src
    assert hasattr(CallGuard, "_check_minutes_quota")
