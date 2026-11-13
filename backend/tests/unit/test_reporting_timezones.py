"""F3-12: mounted reporting routes and assistant tools use local calendar days."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1 import dependencies
from app.api.v1.endpoints import analytics, dashboard
from app.core.error_handlers import register_error_handlers
from app.infrastructure.assistant.tools import date_ranges
from app.infrastructure.assistant.tools.calls import get_recent_calls
from app.infrastructure.assistant.tools.dashboard import get_dashboard_stats
from app.infrastructure.assistant.tools.leads import get_leads, get_qualified_leads
from tests.unit.assistant.test_applied_date_ranges import (
    CAMPAIGN, OTHER, TENANT, Client as RangeClient, Query as RangeQuery, instant,
)


NOW = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
TOOLS = (get_recent_calls, get_dashboard_stats, get_leads, get_qualified_leads)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    for module in (analytics, dashboard, date_ranges):
        monkeypatch.setattr(module, "datetime", FrozenDatetime, raising=False)
    try:
        shared = importlib.import_module("app.domain.services.reporting_time")
    except ModuleNotFoundError as exc:
        if exc.name != "app.domain.services.reporting_time":
            raise
    else:
        monkeypatch.setattr(shared, "datetime", FrozenDatetime)


class Query(RangeQuery):
    single_row = False

    def single(self):
        self.single_row = True
        return self

    def execute(self):
        if self.table == "tenants" and self.client.fail_defaults:
            raise RuntimeError("synthetic timezone storage unavailable")
        result = super().execute()
        if self.single_row:
            result.data = result.data[0] if result.data else None
        return result


class Client(RangeClient):
    def __init__(self, *, account_zone="Asia/Karachi", campaign_zone=None):
        super().__init__()
        self.fail_defaults = False
        self.pool = object()
        self.tables["tenants"] = [
            {"id": TENANT, "calling_rules": {"timezone": account_zone}},
            {"id": OTHER, "calling_rules": {"timezone": "America/Los_Angeles"}},
        ]
        for row in self.tables["campaigns"]:
            row["calling_config"] = {"timezone": campaign_zone}
            row["direction"] = "outbound"
        self.tables["dialer_jobs"] = []

    def table(self, name):
        return Query(self, name)

    def population(self, start, end):
        from datetime import timedelta
        start, end = instant(start), instant(end)
        rows = []
        for key, timestamp, outcome, tenant in (
            ("before", start - timedelta(microseconds=1), "failed", TENANT),
            ("start", start, "goal_achieved", TENANT),
            ("last", end - timedelta(microseconds=1), "answered", TENANT),
            ("next", end, "failed", TENANT),
            ("foreign", start, "failed", OTHER),
        ):
            rows.append({"id": key, "tenant_id": tenant, "campaign_id": CAMPAIGN,
                "created_at": timestamp.isoformat(), "qualified_at": timestamp.isoformat(),
                "lead_id": "lead-" + key, "status": "completed", "outcome": outcome,
                "goal_achieved": outcome == "goal_achieved", "duration_seconds": 60,
                "direction": "outbound", "is_test": False, "is_lead": True,
                "phone_number": "+15550001000"})
        self.tables["calls"] = [dict(row) for row in rows]
        self.tables["leads"] = [dict(row) for row in rows]


def app(client):
    application = FastAPI()
    register_error_handlers(application)
    application.include_router(analytics.router, prefix="/api/v1")
    application.include_router(dashboard.router, prefix="/api/v1")
    user = SimpleNamespace(id="actor", tenant_id=TENANT, role="tenant_admin")
    application.dependency_overrides[dependencies.get_current_user] = lambda: user
    application.dependency_overrides[dependencies.get_db_client] = lambda: client
    application.dependency_overrides[analytics._require_analytics_read] = lambda: user
    application.dependency_overrides[dashboard._require_analytics_read] = lambda: user
    return application


async def request(client, path, params=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app(client)), base_url="http://test") as browser:
        return await browser.get(path, params=params)


def call_bounds(client):
    query = next(q for q in client.executed if q.table == "calls" and any(op == "gte" for op, _, _ in q.filters))
    assert ("eq", "tenant_id", TENANT) in query.filters
    bounds = {op: instant(value) for op, column, value in query.filters if column == "created_at" and op in {"gte", "lt"}}
    assert set(bounds) == {"gte", "lt"}
    return bounds["gte"], bounds["lt"]


@pytest.mark.parametrize("path", ["/analytics/calls", "/analytics/calls/by-campaign", "/analytics/best-time", "/analytics/retry-effectiveness"])
async def test_every_analytics_route_uses_the_requested_local_day(path):
    client = Client()
    start, end = "2026-11-05T19:00:00+00:00", "2026-11-06T19:00:00+00:00"
    client.population(start, end)
    response = await request(client, "/api/v1" + path, {"from": "2026-11-06", "to": "2026-11-06", "tz": "Asia/Karachi"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "Asia/Karachi"
    assert call_bounds(client) == (instant(start), instant(end))
    if path == "/analytics/calls":
        assert body["series"] == [{"date": "2026-11-06", "total_calls": 2, "answered": 2, "failed": 0, "goal_achieved": 1}]
    elif path == "/analytics/calls/by-campaign":
        assert len(body["campaigns"]) == 1
        assert body["campaigns"][0]["series"][0]["total_calls"] == 2
        assert body["campaigns"][0]["series"][0]["date"] == "2026-11-06"
    elif path == "/analytics/best-time":
        assert sum(row["total"] for row in body["hours"]) == 2
        assert body["hours"][0]["total"] == 1 and body["hours"][23]["total"] == 1
    else:
        assert sum(row["total"] for row in body["attempts"]) == 2


@pytest.mark.parametrize("day,start,end,hours", [
    ("2026-03-08", "2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z", 23),
    ("2026-11-01", "2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z", 25),
])
async def test_dst_days_use_independent_local_midnights(day, start, end, hours):
    client = Client()
    client.population(start, end)
    response = await request(client, "/api/v1/analytics/calls", {"from": day, "to": day, "tz": "America/New_York"})
    assert response.status_code == 200, response.text
    actual_start, actual_end = call_bounds(client)
    assert (actual_start, actual_end) == (instant(start), instant(end))
    assert (actual_end - actual_start).total_seconds() == hours * 3600
    assert response.json()["series"][0]["date"] == day
    assert response.json()["series"][0]["total_calls"] == 2


@pytest.mark.parametrize("account_zone,expected_zone", [("Asia/Karachi", "Asia/Karachi"), (None, "UTC"), ("invalid/saved-zone", "UTC")])
async def test_analytics_without_tz_uses_valid_stored_account_else_utc(account_zone, expected_zone):
    client = Client(account_zone=account_zone)
    response = await request(client, "/api/v1/analytics/calls", {"from": "2026-11-06", "to": "2026-11-06"})
    assert response.status_code == 200, response.text
    assert response.json()["timezone"] == expected_zone
    expected_start = "2026-11-05T19:00:00Z" if expected_zone == "Asia/Karachi" else "2026-11-06T00:00:00Z"
    expected_end = "2026-11-06T19:00:00Z" if expected_zone == "Asia/Karachi" else "2026-11-07T00:00:00Z"
    assert call_bounds(client) == (instant(expected_start), instant(expected_end))
    reads = [q for q in client.executed if q.table == "tenants"]
    assert reads and all(("eq", "id", TENANT) in q.filters for q in reads)


@pytest.mark.parametrize("path", ["/analytics/calls", "/analytics/calls/by-campaign", "/analytics/best-time", "/analytics/retry-effectiveness", "/dashboard/summary"])
async def test_invalid_explicit_zone_is_not_silently_reported_as_utc(path):
    client = Client()
    response = await request(client, "/api/v1" + path, {"tz": "not/an-iana-zone"})
    assert response.status_code == 400, response.text
    assert not any(q.table == "calls" for q in client.executed)


async def test_dashboard_uses_local_calendar_month_but_preserves_authoritative_billing_meter(monkeypatch):
    from app.services.scripts import tenant_minutes

    async def meter(pool, tenant_id):
        assert tenant_id == TENANT
        return SimpleNamespace(state="known", used_minutes=37, allocated=90,
                               allowance=lambda: {"minutes_remaining": 53})

    monkeypatch.setattr(tenant_minutes, "compute_tenant_minutes_status", meter)
    client = Client(account_zone="Asia/Karachi")
    start, end = "2026-10-31T19:00:00Z", "2026-11-30T19:00:00Z"
    client.population(start, end)
    response = await request(client, "/api/v1/dashboard/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "Asia/Karachi"
    assert body["total_calls"] == 2 and body["answered_calls"] == 2 and body["failed_calls"] == 0
    assert call_bounds(client) == (instant(start), instant(end))
    assert body["minutes_used"] == 37 and body["minutes_remaining"] == 53 and body["minutes_included"] == 90


async def test_unavailable_default_lookup_is_not_a_successful_utc_fallback():
    client = Client()
    client.fail_defaults = True
    response = await request(client, "/api/v1/analytics/calls", {"from": "2026-11-06", "to": "2026-11-06"})
    assert response.status_code == 503, response.text
    assert not any(q.table == "calls" for q in client.executed)


async def test_dashboard_internal_meter_value_error_is_not_mislabeled_as_bad_timezone(monkeypatch):
    from app.services.scripts import tenant_minutes

    async def unavailable_meter(pool, tenant_id):
        raise ValueError("synthetic internal meter failure; not client input")

    monkeypatch.setattr(tenant_minutes, "compute_tenant_minutes_status", unavailable_meter)
    response = await request(Client(), "/api/v1/dashboard/summary", {"tz": "Asia/Karachi"})
    assert response.status_code == 500, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"] == "Failed to fetch dashboard summary"
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]
    assert "synthetic internal" not in response.text


@pytest.mark.parametrize("tool", TOOLS, ids=lambda fn: fn.__name__)
async def test_assistant_date_only_bounds_share_the_effective_iana_window(tool):
    client = Client(account_zone="Asia/Karachi", campaign_zone="America/New_York")
    start, end = "2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"
    client.population(start, end)
    result = await tool(TENANT, client, campaign_id=CAMPAIGN, since="2026-03-08", until="2026-03-09")
    assert "error" not in result, result
    assert result["timezone"] == "America/New_York"
    assert instant(result["applied_range"]["since"]) == instant(start)
    assert instant(result["applied_range"]["until"]) == instant(end)
    assert all(("eq", "tenant_id", TENANT) in q.filters for q in client.executed if q.table == "campaigns")
    if tool is get_dashboard_stats:
        assert result["total_calls"] == 2
    else:
        key = {get_recent_calls: "calls", get_leads: "leads", get_qualified_leads: "qualified_leads"}[tool]
        assert {row["id"] for row in result[key]} == {"start", "last"}


@pytest.mark.parametrize("campaign_zone,explicit,expected", [
    ("America/New_York", "Europe/London", "Europe/London"),
    (None, None, "Asia/Karachi"),
    ("invalid/saved-zone", None, "Asia/Karachi"),
])
async def test_assistant_explicit_zone_overrides_campaign_then_account(campaign_zone, explicit, expected):
    client = Client(campaign_zone=campaign_zone)
    kwargs = {"tz": explicit} if explicit else {}
    result = await get_dashboard_stats(TENANT, client, campaign_id=CAMPAIGN, date_str="2026-11-06", **kwargs)
    assert "error" not in result, result
    assert result["timezone"] == expected


async def test_assistant_naive_timestamp_still_refused_and_explicit_instants_do_not_shift():
    client = Client()
    invalid = await get_recent_calls(TENANT, client, since="2026-11-06T00:00:00", tz="Asia/Karachi")
    assert invalid.get("error") and "applied_range" not in invalid
    assert client.executed == []
    client.population("2026-11-06T00:00:00Z", "2026-11-07T00:00:00Z")
    result = await get_recent_calls(TENANT, client, since="2026-11-06T00:00:00Z", until="2026-11-07T00:00:00Z", tz="Asia/Karachi")
    assert result["applied_range"]["since"] == "2026-11-06T00:00:00+00:00"
    assert result["applied_range"]["until"] == "2026-11-07T00:00:00+00:00"
