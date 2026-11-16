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


# ------------------------- geographic check: a country outside the table is not a pass
# Frozen copy of the table's answers before the fix (the equivalence proof).
_TABLE = {
    "+1": "US", "+44": "GB", "+33": "FR", "+49": "DE", "+39": "IT", "+34": "ES", "+31": "NL",
    "+32": "BE", "+41": "CH", "+43": "AT", "+46": "SE", "+47": "NO", "+45": "DK", "+358": "FI",
    "+48": "PL", "+420": "CZ", "+36": "HU", "+30": "GR", "+353": "IE", "+351": "PT", "+61": "AU",
    "+64": "NZ", "+81": "JP", "+82": "KR", "+86": "CN", "+91": "IN", "+92": "PK", "+880": "BD",
    "+234": "NG", "+84": "VN", "+62": "ID", "+55": "BR", "+52": "MX", "+54": "AR", "+56": "CL",
    "+57": "CO", "+27": "ZA", "+20": "EG", "+971": "AE", "+966": "SA", "+65": "SG", "+852": "HK",
    "+886": "TW", "+7": "RU", "+380": "UA", "+90": "TR", "+98": "IR", "+963": "SY",
}
ESTONIA = "+37251234567"
# +999 is an unassigned calling code: phonenumbers gives no region (checked with
# region_of_e164, which returns None for it).
NO_REGION = "+99912345678"


async def _geo(number, **limits):
    guard = CallGuard(db_pool=object(), redis_client=None)
    return await guard._check_geographic(number, TenantCallLimits(**limits))


@pytest.mark.asyncio
async def test_allow_list_admits_its_country_and_refuses_the_rest():
    assert (await _geo(ORDINARY, allowed_country_codes=["GB"])).passed is True
    estonia = await _geo(ESTONIA, allowed_country_codes=["GB"])
    assert estonia.passed is False and estonia.reason == "country_not_allowed: EE"
    us = await _geo("+12015550123", allowed_country_codes=["GB"])
    assert us.passed is False and us.reason == "country_not_allowed: US"


@pytest.mark.asyncio
async def test_a_blocked_country_outside_the_table_is_refused():
    result = await _geo(ESTONIA, blocked_country_codes=["EE"])
    assert result.passed is False and result.reason == "blocked_country: EE"


@pytest.mark.asyncio
async def test_a_number_with_no_region_is_refused_by_an_allow_list_only():
    refused = await _geo(NO_REGION, allowed_country_codes=["GB"])
    assert refused.passed is False and refused.reason == "country_not_allowed: unknown"
    allowed = await _geo(NO_REGION)
    assert allowed.passed is True and allowed.reason == "country_code_unknown"


@pytest.mark.asyncio
async def test_blocked_prefixes_still_run_first_for_an_unknown_country():
    result = await _geo(NO_REGION, blocked_prefixes=["+999"], allowed_country_codes=["GB"])
    assert result.reason == "blocked_prefix: +999"


@pytest.mark.parametrize(("prefix", "code"), sorted(_TABLE.items()))
def test_every_table_prefix_answers_as_before(prefix, code):
    guard = CallGuard(db_pool=object(), redis_client=None)
    assert guard._extract_country_code(prefix + "2015550123") == code


@pytest.mark.asyncio
async def test_no_tenant_limits_passes_as_before():
    guard = CallGuard(db_pool=object(), redis_client=None)
    assert (await guard._check_geographic(ESTONIA, None)).passed is True
    assert (await guard._check_geographic(NO_REGION, None)).passed is True
