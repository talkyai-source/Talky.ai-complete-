"""D4-8: the customer can set the account's reporting timezone.

Reports, the dashboard and /auth/me fell back to tenants.calling_rules.timezone,
which no customer endpoint can set and which the column default makes
America/New_York for every self-signed-up account. That zone is also the
dialer's fallback window zone, so it cannot simply be made editable as "my
timezone" without moving calling hours. The account now has its own
reporting_timezone (PUT /auth/account/timezone, tenant admins), which reports
and /auth/me prefer; the dialer never reads it.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.auth import profile
from app.api.v1.endpoints.auth.schemas import AccountTimezoneRequest
from app.domain.models.calling_rules import CallingRules
from app.domain.services.reporting_time import resolve_reporting_zone

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, error=None)


def _db(account_rules, campaign_config=None):
    tables = {"tenants": [{"calling_rules": account_rules}],
              "campaigns": [{"calling_config": campaign_config}] if campaign_config is not None else []}
    return SimpleNamespace(table=lambda name: _Query(tables[name]))


@pytest.mark.parametrize("rules,expected", [
    ({"timezone": "America/New_York", "reporting_timezone": "Asia/Karachi"}, "Asia/Karachi"),
    ({"timezone": "America/New_York"}, "America/New_York"),
    ({"timezone": "America/New_York", "reporting_timezone": "Not/AZone"}, "America/New_York"),
    ({}, "UTC"),
])
def test_reports_prefer_the_accounts_reporting_timezone(rules, expected):
    assert resolve_reporting_zone(_db(rules), TENANT).key == expected


def test_an_explicit_tz_and_a_campaigns_own_zone_still_win():
    rules = {"reporting_timezone": "Asia/Karachi"}
    assert resolve_reporting_zone(_db(rules), TENANT, requested="Europe/London").key == "Europe/London"
    db = _db(rules, campaign_config={"timezone": "America/Chicago"})
    assert resolve_reporting_zone(db, TENANT, campaign_id="c1").key == "America/Chicago"


def test_the_dialer_ignores_the_reporting_timezone():
    rules = CallingRules.from_dict({"timezone": "Europe/London", "reporting_timezone": "Asia/Karachi"})
    assert rules.timezone == "Europe/London"


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(saved="Asia/Karachi", before=None, writes=[], leases=[])

    class Conn:
        def transaction(self):
            @asynccontextmanager
            async def tx():
                yield
            return tx()

        async def fetchval(self, query, *args):
            assert "FOR UPDATE" in query and args == (TENANT,)
            return state.before

        async def fetchrow(self, query, *args):
            state.writes.append((query, args))
            return {"reporting_timezone": args[1], "dialing_timezone": "America/New_York"}

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        state.leases.append(tenant_id)
        yield Conn()

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    return state


def _admin(role="tenant_admin"):
    return SimpleNamespace(id="u1", tenant_id=TENANT, role=role)


async def test_a_tenant_admin_saves_a_reporting_timezone(harness):
    audit = SimpleNamespace(log=AsyncMock())
    response = await profile.set_account_timezone(
        AccountTimezoneRequest(timezone="Asia/Karachi"), _admin(), SimpleNamespace(pool=object()), audit)
    assert (response.reporting_timezone, response.account_timezone) == ("Asia/Karachi", "Asia/Karachi")
    query, args = harness.writes[0]
    assert "jsonb_set" in query and "'{reporting_timezone}'" in query and "WHERE id = $1::uuid" in query
    assert args == (TENANT, "Asia/Karachi")
    assert harness.leases == [TENANT]
    audit.log.assert_awaited_once()
    assert audit.log.await_args.kwargs["after_state"] == {"reporting_timezone": "Asia/Karachi"}


async def test_clearing_falls_back_to_the_dialing_zone(harness):
    response = await profile.set_account_timezone(
        AccountTimezoneRequest(timezone=None), _admin(), SimpleNamespace(pool=object()),
        SimpleNamespace(log=AsyncMock()))
    assert (response.reporting_timezone, response.account_timezone) == (None, "America/New_York")
    query, args = harness.writes[0]
    assert "- 'reporting_timezone'" in query and args == (TENANT, None)


async def test_an_invalid_zone_is_refused_before_any_write(harness):
    with pytest.raises(HTTPException) as refused:
        await profile.set_account_timezone(
            AccountTimezoneRequest(timezone="Mars/Base"), _admin(), SimpleNamespace(pool=object()),
            SimpleNamespace(log=AsyncMock()))
    assert refused.value.status_code == 422
    assert harness.writes == []


async def test_only_a_tenant_admin_can_change_it(harness):
    with pytest.raises(HTTPException) as refused:
        await profile.set_account_timezone(
            AccountTimezoneRequest(timezone="Asia/Karachi"), _admin("user"), SimpleNamespace(pool=object()),
            SimpleNamespace(log=AsyncMock()))
    assert refused.value.status_code == 403
    assert harness.writes == []


async def test_auth_me_returns_the_saved_zone_and_uses_it_as_the_account_zone(monkeypatch):
    class Conn:
        async def fetchrow(self, query, *args):
            if "user_profiles" not in query:
                return None  # legal acceptance receipt: none configured
            assert "reporting_timezone" in query
            return {"tenant_id": TENANT, "partner_id": None, "tenant_status": "active",
                    "partner_status": None, "tenant_suspended_at": None,
                    "tenant_suspension_reason": None, "partner_suspended_at": None,
                    "partner_suspension_reason": None, "subscription_status": "active",
                    "account_timezone": "America/New_York", "reporting_timezone": "Asia/Karachi",
                    "mfa_enabled": False}

        async def fetch(self, *_a):
            return []

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Conn()

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    user = SimpleNamespace(id="u1", tenant_id=TENANT, email="o@example.com", name=None,
                           business_name=None, role="tenant_admin", minutes_remaining=None,
                           minutes_state="unavailable")
    me = await profile.get_me(user, SimpleNamespace(pool=object()))
    assert (me.reporting_timezone, me.account_timezone) == ("Asia/Karachi", "Asia/Karachi")
