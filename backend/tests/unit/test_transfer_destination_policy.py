"""A transfer number must pass the same destination rules a normal call passes.

Covers the two guard methods ``CallGuard`` offers a transfer (destination
policy, minutes) and the proof that a transferred outbound call stays inside
the call's hard limit.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.services.call_guard import CallGuard, CheckResult, GuardCheck, TenantCallLimits
from app.domain.services.telephony import lifecycle

TENANT = "22222222-2222-2222-2222-222222222222"
ORDINARY = "+442071234567"


def _guard(limits=None, *, raises=None, quota=None):
    guard = CallGuard(db_pool=object(), redis_client=None)
    seen = {"tenants": [], "quota_calls": []}

    async def _limits(tenant_id):
        seen["tenants"].append(tenant_id)
        if raises is not None:
            raise raises
        return limits

    async def _quota(tenant_id, tenant_limits=None, estimated_duration_seconds=None, **kwargs):
        seen["quota_calls"].append((tenant_id, kwargs))
        if quota is not None:
            return quota
        return CheckResult(check=GuardCheck.MINUTES_QUOTA, passed=True)

    guard._get_tenant_limits = _limits
    guard._check_minutes_quota = _quota
    return guard, seen


# ------------------------------------------------------- destination policy
@pytest.mark.asyncio
async def test_an_ordinary_number_is_permitted_and_uses_the_given_tenant():
    guard, seen = _guard(TenantCallLimits())
    result = await guard.check_transfer_destination(TENANT, ORDINARY)
    assert result.passed is True
    assert seen["tenants"] == [TENANT]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "limits", "reason"),
    [
        ("+449012345678", TenantCallLimits(), "premium_rate_destination"),
        ("+19002345678", TenantCallLimits(), "premium_rate_destination"),
        (ORDINARY, TenantCallLimits(blocked_prefixes=["+4420"]), "blocked_prefix"),
        (ORDINARY, TenantCallLimits(blocked_country_codes=["GB"]), "blocked_country"),
        (ORDINARY, TenantCallLimits(allowed_country_codes=["US"]), "country_not_allowed"),
        ("not-a-number", TenantCallLimits(), "phone_number_missing"),
        ("+12", TenantCallLimits(), "phone_number_missing"),
    ],
)
async def test_a_number_a_normal_call_would_refuse_is_refused(number, limits, reason):
    guard, _seen = _guard(limits)
    result = await guard.check_transfer_destination(TENANT, number)
    assert result.passed is False
    assert result.reason.startswith(reason)


@pytest.mark.asyncio
async def test_a_country_inside_a_non_empty_allow_list_is_permitted():
    guard, _seen = _guard(TenantCallLimits(allowed_country_codes=["GB"]))
    assert (await guard.check_transfer_destination(TENANT, ORDINARY)).passed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("db down"), None])
async def test_unreadable_limits_fail_closed(failure):
    guard, _seen = _guard(None, raises=failure)
    result = await guard.check_transfer_destination(TENANT, ORDINARY)
    assert result.passed is False
    assert result.reason == "limits_unavailable"


@pytest.mark.asyncio
async def test_the_destination_check_does_not_run_dnc_rate_or_minutes():
    guard, seen = _guard(TenantCallLimits())

    async def _boom(*_a, **_k):
        raise AssertionError("must not run for a transfer destination")

    guard._check_dnc = _boom
    guard._check_rate_limit = _boom
    guard._check_velocity = _boom
    guard._check_concurrency = _boom
    assert (await guard.check_transfer_destination(TENANT, ORDINARY)).passed is True
    assert seen["quota_calls"] == []


# -------------------------------------------------------------------- minutes
@pytest.mark.asyncio
async def test_minutes_check_reuses_the_normal_quota_check_for_this_tenant_and_call():
    guard, seen = _guard(TenantCallLimits())
    result = await guard.check_transfer_minutes(TENANT, call_id="call-1")
    assert result.passed is True
    assert seen["quota_calls"] == [(TENANT, {"call_id": "call-1"})]


@pytest.mark.asyncio
async def test_no_minutes_left_is_refused():
    exhausted = CheckResult(
        check=GuardCheck.MINUTES_QUOTA, passed=False, reason="minutes_quota_exhausted: 10/10 used"
    )
    guard, _seen = _guard(TenantCallLimits(), quota=exhausted)
    result = await guard.check_transfer_minutes(TENANT, call_id="call-1")
    assert result.passed is False


@pytest.mark.asyncio
async def test_minutes_that_cannot_be_read_fail_closed():
    guard, _seen = _guard(TenantCallLimits())

    async def _quota(*_a, **_k):
        raise RuntimeError("db down")

    guard._check_minutes_quota = _quota
    result = await guard.check_transfer_minutes(TENANT, call_id="call-1")
    assert result.passed is False
    assert result.reason == "metering_unavailable"


# ------------------------------------------- the second phone leg stays bounded
def _session(*, age_s, idle_s=0.0, **attrs):
    started = datetime.utcnow() - timedelta(seconds=age_s)
    active = datetime.utcnow() - timedelta(seconds=idle_s)

    class _CS:
        def is_stale(self, timeout):
            return (datetime.utcnow() - active).total_seconds() > timeout

        def get_duration_seconds(self):
            return (datetime.utcnow() - started).total_seconds()

    return SimpleNamespace(call_session=_CS(), **attrs)


def _classify(vs):
    return lifecycle._collect_expired_sessions(
        [("out-1", vs)], inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=300
    )


def test_a_transferred_outbound_call_is_ended_at_the_hard_maximum():
    vs = _session(age_s=601, idle_s=900, _transfer_connected=True)
    assert _classify(vs) == ([], ["out-1"])


def test_a_transferred_outbound_call_is_not_ended_by_the_soft_cap_or_idleness():
    vs = _session(age_s=450, idle_s=900, _transfer_connected=True)
    assert _classify(vs) == ([], [])


def test_the_accounts_shorter_maximum_also_ends_a_transferred_outbound_call():
    vs = _session(age_s=200, idle_s=900, _transfer_connected=True, _max_call_duration_seconds=120)
    assert _classify(vs) == ([], ["out-1"])
