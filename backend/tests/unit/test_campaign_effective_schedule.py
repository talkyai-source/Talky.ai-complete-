"""D2-1/2/3: the campaign response states the calling schedule the dialer enforces.

The dialer judges the window with the account's calling rules overlaid by the
campaign's schedule (or 09:00-19:00 UTC, Monday-Friday when neither set one),
in each contact's own timezone first, and honours "call anytime" only while
the platform switch DIALER_ALLOW_IGNORE_SCHEDULE allows it. None of that was
in any response, so the UI could only describe it in words. GET
/campaigns/{id} now returns effective_schedule, built with the dialer's own
functions so it cannot drift from the gate.
"""
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
import pytz

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer.campaign_schedule import describe_schedule, effective_rules

WINDOW_KEYS = ("timezone", "time_window_start", "time_window_end", "allowed_days")


def _window(schedule):
    return {k: schedule[k] for k in WINDOW_KEYS}


def test_nothing_configured_reports_the_platform_default():
    schedule = describe_schedule(None, None)
    assert _window(schedule) == {"timezone": "UTC", "time_window_start": "09:00",
                                 "time_window_end": "19:00", "allowed_days": [0, 1, 2, 3, 4]}
    assert set(schedule["sources"].values()) == {"platform_default"}
    assert schedule["account_rules"]["configured"] is False


def test_each_field_names_where_it_came_from():
    account = {"timezone": "Europe/London", "time_window_start": "08:00", "time_window_end": "20:00"}
    schedule = describe_schedule(account, {"timezone": "America/New_York", "batch_size": 5})
    assert _window(schedule) == {"timezone": "America/New_York", "time_window_start": "08:00",
                                 "time_window_end": "20:00", "allowed_days": [0, 1, 2, 3, 4]}
    assert schedule["sources"] == {"timezone": "campaign", "time_window_start": "account",
                                   "time_window_end": "account", "allowed_days": "platform_default"}
    assert schedule["account_rules"]["configured"] is True
    assert schedule["account_rules"]["timezone"] == "Europe/London"


@pytest.mark.parametrize("account,config", [
    (None, None),
    ({"timezone": "Asia/Karachi", "allowed_days": [0, 1, 2, 3, 4, 5]}, {}),
    ({"time_window_start": "10:00"}, {"time_window_end": "17:30", "allowed_days": [5, 6]}),
    ({"timezone": "Europe/London"}, {"allowed_days": []}),
    ('{"timezone": "Europe/Berlin"}', {"timezone": "Europe/Paris"}),
    ({"timezone": "Europe/London", "default_country": "not-a-country"}, None),
])
def test_the_reported_window_is_the_one_the_dialer_computes(account, config):
    import json
    raw = json.loads(account) if isinstance(account, str) else account
    try:
        tenant = CallingRules.from_dict(raw) if raw else CallingRules.default()
    except Exception:
        tenant = CallingRules.default()
    dialer = effective_rules(tenant, config)
    assert _window(describe_schedule(account, config)) == {k: getattr(dialer, k) for k in WINDOW_KEYS}


def test_a_malformed_campaign_override_is_not_reported_as_the_campaigns():
    schedule = describe_schedule({"timezone": "Europe/London"}, {"timezone": "Asia/Tokyo",
                                                                  "allowed_days": "weekdays"})
    assert schedule["timezone"] == "Europe/London", "the dialer drops the whole malformed overlay"
    assert "campaign" not in schedule["sources"].values()


def test_call_anytime_reports_the_campaign_choice_and_the_platform_switch(monkeypatch):
    monkeypatch.delenv("DIALER_ALLOW_IGNORE_SCHEDULE", raising=False)
    on = describe_schedule(None, {"ignore_schedule": True})
    assert (on["ignore_schedule_requested"], on["ignore_schedule_allowed"], on["ignore_schedule"]) == (True, True, True)

    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", "false")
    off = describe_schedule(None, {"ignore_schedule": True})
    assert (off["ignore_schedule_requested"], off["ignore_schedule_allowed"], off["ignore_schedule"]) == (True, False, False)


def test_contact_timezones_count_only_zones_the_dialer_honours(monkeypatch):
    monkeypatch.delenv("DIALER_PER_LEAD_TIMEZONE", raising=False)
    schedule = describe_schedule(None, {"timezone": "Europe/London"},
                                 {"America/Los_Angeles": 1, "Not/AZone": 2, "Asia/Karachi": 3})
    contact = schedule["contact_timezones"]
    assert contact["contact_zone_first"] is True
    assert contact["phone_derived_zone"] is True
    assert contact["contacts_with_own_timezone"] == 4
    assert contact["own_timezones"] == [{"timezone": "Asia/Karachi", "contacts": 3},
                                        {"timezone": "America/Los_Angeles", "contacts": 1}]

    monkeypatch.setenv("DIALER_PER_LEAD_TIMEZONE", "0")
    assert describe_schedule(None, None, {})["contact_timezones"]["phone_derived_zone"] is False
    assert describe_schedule(None, None)["contact_timezones"]["contacts_with_own_timezone"] is None


def test_a_london_campaign_holds_a_los_angeles_contact_until_la_hours():
    rules = effective_rules(CallingRules.default(), {"timezone": "Europe/London"})
    ten_am_london = pytz.timezone("Europe/London").localize(datetime(2026, 11, 10, 10, 0))
    assert rules.is_within_time_window(ten_am_london)[0] is True
    assert rules.is_within_time_window(ten_am_london, "America/Los_Angeles")[0] is False


TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
CAMPAIGN = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def endpoint(monkeypatch):
    state = SimpleNamespace(queries=[], leases=[], fail=False,
                            account={"timezone": "Europe/London"},
                            zones=[{"timezone": "America/Los_Angeles", "contacts": 1}])

    class Conn:
        async def fetchval(self, query, *args):
            state.queries.append((query, args))
            return state.account

        async def fetch(self, query, *args):
            state.queries.append((query, args))
            if state.fail:
                raise RuntimeError("synthetic read failure")
            return state.zones

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        state.leases.append(tenant_id)
        yield Conn()

    class Service:
        async def get_campaign(self, campaign_id):
            return {"id": campaign_id, "tenant_id": TENANT,
                    "calling_config": {"time_window_start": "10:00"}}

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: Service())
    return state


async def _get():
    return await campaigns_ep.get_campaign(
        campaign_id=CAMPAIGN, request=SimpleNamespace(),
        current_user=SimpleNamespace(id="u1", tenant_id=TENANT),
        db_client=SimpleNamespace(pool=object()))


async def test_get_campaign_returns_the_effective_schedule(endpoint):
    response = await _get()
    schedule = response["effective_schedule"]
    assert schedule["timezone"] == "Europe/London" and schedule["sources"]["timezone"] == "account"
    assert schedule["time_window_start"] == "10:00" and schedule["sources"]["time_window_start"] == "campaign"
    assert schedule["contact_timezones"]["own_timezones"] == [{"timezone": "America/Los_Angeles", "contacts": 1}]
    assert endpoint.leases == [TENANT]
    for query, args in endpoint.queries:
        assert TENANT in args, "every read names the tenant explicitly"
    lead_query = next(q for q, _ in endpoint.queries if "FROM leads" in q)
    assert "tenant_id = $1" in lead_query and "campaign_id = $2" in lead_query and "deleted" in lead_query


async def test_a_schedule_read_failure_does_not_break_the_campaign_page(endpoint):
    endpoint.fail = True
    response = await _get()
    assert response["campaign"]["id"] == CAMPAIGN
    assert response["effective_schedule"] is None
