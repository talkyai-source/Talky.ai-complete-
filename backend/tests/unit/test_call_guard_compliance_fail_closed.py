"""Calling-hours, geography and abuse-velocity checks fail closed (QA F7-07, audit TEL-07).

When one of these lookups raised, ``CallGuard.evaluate`` treated the check as
passed, so a database error or a failed time-zone lookup let through a call the
check exists to stop. They now block like DNC and the account checks, and the
dialer defers the job (``check_error_blocked`` is a transient reason there).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import time

import asyncpg
import pytest

from app.domain.services import call_guard as cg

_CHECKS = {
    "_check_platform_calls_enabled": cg.GuardCheck.PLATFORM_CALLS_ENABLED,
    "_check_tenant_active": cg.GuardCheck.TENANT_ACTIVE,
    "_check_partner_active": cg.GuardCheck.PARTNER_ACTIVE,
    "_check_subscription": cg.GuardCheck.SUBSCRIPTION_VALID,
    "_check_feature_enabled": cg.GuardCheck.FEATURE_ENABLED,
    "_check_number_valid": cg.GuardCheck.NUMBER_VALID,
    "_check_geographic": cg.GuardCheck.GEOGRAPHIC_ALLOWED,
    "_check_dnc": cg.GuardCheck.DNC_CHECK,
    "_check_rate_limit": cg.GuardCheck.RATE_LIMIT,
    "_check_concurrency": cg.GuardCheck.CONCURRENCY_LIMIT,
    "_check_minutes_quota": cg.GuardCheck.MINUTES_QUOTA,
    "_check_spend_limit": cg.GuardCheck.SPEND_LIMIT,
    "_check_business_hours": cg.GuardCheck.BUSINESS_HOURS,
    "_check_velocity": cg.GuardCheck.VELOCITY_CHECK,
}


def _ok_guard():
    """A guard whose every check passes; each test then breaks exactly one."""
    guard = cg.CallGuard(db_pool=object(), redis_client=None)
    limits = cg.TenantCallLimits(
        respect_business_hours=True,
        business_hours_start=time(9, 0),
        business_hours_end=time(17, 0),
    )

    async def tenant_limits(_tenant_id):
        return limits

    async def nothing(*_args, **_kwargs):
        return None

    guard._get_tenant_limits = tenant_limits
    guard._get_partner_limits = nothing
    guard._get_partner_id = nothing
    guard._log_decision = nothing
    for attr, check in _CHECKS.items():
        async def passes(*_args, _check=check, **_kwargs):
            return cg.CheckResult(check=_check, passed=True)

        setattr(guard, attr, passes)
    return guard


async def _raises(*_args, **_kwargs):
    raise RuntimeError("lookup error")


@pytest.mark.asyncio
@pytest.mark.parametrize("attr", ["_check_business_hours", "_check_geographic", "_check_velocity"])
async def test_a_compliance_lookup_that_raises_blocks_the_call(attr):
    guard = _ok_guard()
    setattr(guard, attr, _raises)
    result = await guard.evaluate(tenant_id="t", phone_number="+12125550100")
    assert result.decision.value == "block"
    failed = [check for check in result.check_results if not check.passed]
    assert [check.check for check in failed] == [_CHECKS[attr]]
    assert failed[0].reason.startswith("check_error_blocked")


@pytest.mark.asyncio
async def test_a_failed_time_zone_lookup_in_the_real_calling_hours_check_blocks(monkeypatch):
    from app.domain.services import phone_timezone

    guard = _ok_guard()
    guard._check_business_hours = cg.CallGuard._check_business_hours.__get__(guard)
    monkeypatch.setattr(phone_timezone, "resolve_timezone", _raises)
    result = await guard.evaluate(tenant_id="t", phone_number="+12125550100")
    assert result.decision.value == "block"


@pytest.mark.asyncio
async def test_the_real_velocity_check_does_not_pass_a_database_error(monkeypatch):
    @asynccontextmanager
    async def broken(_pool, _tenant):
        raise asyncpg.PostgresError("abuse_events unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(cg, "acquire_with_tenant", broken)
    with pytest.raises(asyncpg.PostgresError):
        await cg.CallGuard(db_pool=object(), redis_client=None)._check_velocity("t")


@pytest.mark.asyncio
async def test_a_deployment_without_the_abuse_table_still_skips_velocity(monkeypatch):
    @asynccontextmanager
    async def missing(_pool, _tenant):
        raise asyncpg.UndefinedTableError('relation "abuse_events" does not exist')
        yield  # pragma: no cover

    monkeypatch.setattr(cg, "acquire_with_tenant", missing)
    result = await cg.CallGuard(db_pool=object(), redis_client=None)._check_velocity("t")
    assert result.passed is True


def test_availability_checks_still_fail_open():
    assert cg.GuardCheck.RATE_LIMIT not in cg._FAIL_CLOSED_ON_ERROR_CHECKS
    assert cg.GuardCheck.CONCURRENCY_LIMIT not in cg._FAIL_CLOSED_ON_ERROR_CHECKS
