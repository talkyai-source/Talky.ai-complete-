"""The assistant answers about the campaign the user asked about, never a blend.

Owner report (30 Oct 2026): asking the assistant a question returned answers
drawn from every campaign. The data tools could not be narrowed to one
campaign and returned rows without saying which campaign they came from, so
the model blended them. These tests run the real tool functions against an
in-memory table client that applies the same filters the adapter applies.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.infrastructure.assistant.agent import SYSTEM_PROMPT
from app.infrastructure.assistant.tools.calls import get_recent_calls
from app.infrastructure.assistant.tools.campaign_admin import get_campaign_detail
from app.infrastructure.assistant.tools.campaigns import get_campaigns
from app.infrastructure.assistant.tools.dashboard import get_dashboard_stats
from app.infrastructure.assistant.tools.leads import get_leads
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
CAMPAIGN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CAMPAIGN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
FOREIGN_CAMPAIGN = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TODAY = "2026-10-30T10:00:00"


class _Query:
    def __init__(self, client: "_Client", table: str) -> None:
        self.client, self.table, self.filters = client, table, []
        self.columns = "*"
        self.limit_n = None
        client.queries.append(self)

    def select(self, columns: str = "*", count=None):
        self.columns = columns
        return self

    def _add(self, op, column, value):
        self.filters.append((op, column, value))
        return self

    def eq(self, column, value):
        return self._add("eq", column, value)

    def neq(self, column, value):
        return self._add("neq", column, value)

    def gte(self, column, value):
        return self._add("gte", column, value)

    def lte(self, column, value):
        return self._add("lte", column, value)

    def ilike(self, column, value):
        return self._add("ilike", column, value)

    def in_(self, column, values):
        return self._add("in", column, list(values))

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def _matches(self, row) -> bool:
        for op, column, value in self.filters:
            have = row.get(column)
            if op == "eq" and str(have) != str(value):
                return False
            if op == "neq" and str(have) == str(value):
                return False
            if op == "gte" and not (have is not None and str(have) >= str(value)):
                return False
            if op == "lte" and not (have is not None and str(have) <= str(value)):
                return False
            if op == "in" and str(have) not in {str(v) for v in value}:
                return False
            if op == "ilike":
                pattern = "^" + re.escape(str(value)).replace("%", ".*") + "$"
                if not re.match(pattern, str(have or ""), re.IGNORECASE):
                    return False
        return True

    def execute(self):
        rows = [r for r in self.client.tables.get(self.table, []) if self._matches(r)]
        count = len(rows)
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        if self.columns.strip() != "*":
            wanted = [c.strip() for c in self.columns.split(",") if c.strip()]
            rows = [{c: r.get(c) for c in wanted} for r in rows]
        return SimpleNamespace(data=rows, count=count, error=None)


class _Client:
    def __init__(self, tables):
        self.tables, self.queries = tables, []

    def table(self, name):
        return _Query(self, name)


def _client():
    campaigns = [
        {"id": CAMPAIGN_A, "tenant_id": TENANT, "name": "Roofing Leads", "status": "running"},
        {"id": CAMPAIGN_B, "tenant_id": TENANT, "name": "Dental Reminders", "status": "running"},
        {"id": FOREIGN_CAMPAIGN, "tenant_id": OTHER_TENANT, "name": "Roofing Leads", "status": "running"},
    ]
    calls = [
        {"id": "call-a1", "tenant_id": TENANT, "campaign_id": CAMPAIGN_A, "status": "completed",
         "outcome": "interested", "goal_achieved": True, "phone_number": "+15550000001",
         "duration_seconds": 61, "created_at": TODAY},
        {"id": "call-a2", "tenant_id": TENANT, "campaign_id": CAMPAIGN_A, "status": "failed",
         "outcome": "no_answer", "goal_achieved": False, "phone_number": "+15550000002",
         "duration_seconds": 0, "created_at": TODAY},
        {"id": "call-b1", "tenant_id": TENANT, "campaign_id": CAMPAIGN_B, "status": "completed",
         "outcome": "booked", "goal_achieved": True, "phone_number": "+15550000003",
         "duration_seconds": 95, "created_at": TODAY},
        {"id": "call-x1", "tenant_id": OTHER_TENANT, "campaign_id": FOREIGN_CAMPAIGN, "status": "completed",
         "outcome": "interested", "goal_achieved": True, "phone_number": "+15550000009",
         "duration_seconds": 40, "created_at": TODAY},
    ]
    leads = [
        {"id": "lead-a", "tenant_id": TENANT, "campaign_id": CAMPAIGN_A, "status": "pending",
         "first_name": "Ann", "created_at": TODAY},
        {"id": "lead-b", "tenant_id": TENANT, "campaign_id": CAMPAIGN_B, "status": "pending",
         "first_name": "Bob", "created_at": TODAY},
        {"id": "lead-x", "tenant_id": OTHER_TENANT, "campaign_id": FOREIGN_CAMPAIGN, "status": "pending",
         "first_name": "Xena", "created_at": TODAY},
    ]
    return _Client({"campaigns": campaigns, "calls": calls, "leads": leads})


def _schema(name):
    return next(s["function"] for s in GROQ_TOOL_SCHEMAS if s["function"]["name"] == name)


@pytest.mark.asyncio
async def test_recent_calls_can_be_limited_to_one_campaign():
    result = await get_recent_calls(TENANT, _client(), today_only=False, campaign_id=CAMPAIGN_A)

    assert [c["id"] for c in result["calls"]] == ["call-a1", "call-a2"]
    assert result["total_count"] == 2


@pytest.mark.asyncio
async def test_every_recent_call_names_its_campaign_and_never_another_tenants():
    result = await get_recent_calls(TENANT, _client(), today_only=False)

    by_id = {c["id"]: c for c in result["calls"]}
    assert set(by_id) == {"call-a1", "call-a2", "call-b1"}
    assert by_id["call-a1"]["campaign_id"] == CAMPAIGN_A
    assert by_id["call-a1"]["campaign_name"] == "Roofing Leads"
    assert by_id["call-b1"]["campaign_name"] == "Dental Reminders"


@pytest.mark.asyncio
async def test_another_tenants_campaign_id_returns_no_calls():
    result = await get_recent_calls(TENANT, _client(), today_only=False, campaign_id=FOREIGN_CAMPAIGN)

    assert result["calls"] == []
    assert result["total_count"] == 0


@pytest.mark.asyncio
async def test_dashboard_stats_for_one_campaign_count_only_that_campaign():
    result = await get_dashboard_stats(TENANT, _client(), date_str="2026-10-30", campaign_id=CAMPAIGN_A)

    assert result["total_calls"] == 2
    assert result["completed"] == 1
    assert result["failed"] == 1
    assert result["campaign_id"] == CAMPAIGN_A
    assert result["campaign_name"] == "Roofing Leads"


@pytest.mark.asyncio
async def test_dashboard_stats_without_a_campaign_say_they_cover_the_whole_account():
    result = await get_dashboard_stats(TENANT, _client(), date_str="2026-10-30")

    assert result["total_calls"] == 3
    assert result["scope"] == "all_campaigns"


@pytest.mark.asyncio
async def test_dashboard_stats_refuse_another_tenants_campaign():
    result = await get_dashboard_stats(TENANT, _client(), date_str="2026-10-30", campaign_id=FOREIGN_CAMPAIGN)

    assert result == {"error": "campaign not found"}


@pytest.mark.asyncio
async def test_listed_leads_say_which_campaign_they_belong_to():
    result = await get_leads(TENANT, _client())

    assert {(l["id"], l["campaign_id"]) for l in result["leads"]} == {
        ("lead-a", CAMPAIGN_A), ("lead-b", CAMPAIGN_B)}


@pytest.mark.asyncio
async def test_campaigns_can_be_found_by_name_within_the_tenant_only():
    result = await get_campaigns(TENANT, _client(), name="roofing")

    assert [c["id"] for c in result["campaigns"]] == [CAMPAIGN_A]


@pytest.mark.asyncio
async def test_campaign_detail_by_a_duplicated_name_asks_instead_of_guessing():
    client = _client()
    client.tables["campaigns"].append(
        {"id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "tenant_id": TENANT,
         "name": "Roofing Leads", "status": "draft"})

    result = await get_campaign_detail(TENANT, client, name="Roofing Leads")

    assert result.get("ambiguous") is True
    assert "campaign" not in result
    assert {c["id"] for c in result["candidates"]} == {CAMPAIGN_A, "dddddddd-dddd-4ddd-8ddd-dddddddddddd"}


def test_the_model_can_scope_call_stats_and_campaign_lookups():
    assert "campaign_id" in _schema("get_recent_calls")["parameters"]["properties"]
    assert "campaign_id" in _schema("get_dashboard_stats")["parameters"]["properties"]
    assert "name" in _schema("get_campaigns")["parameters"]["properties"]
    assert "List all campaigns" not in SYSTEM_PROMPT
    assert "only about that campaign" in SYSTEM_PROMPT


def test_every_argument_a_tool_schema_offers_is_accepted_by_its_function():
    import inspect

    from app.infrastructure.assistant.tools import ALL_TOOLS

    rejected = {}
    for schema in GROQ_TOOL_SCHEMAS:
        spec = schema["function"]
        signature = inspect.signature(ALL_TOOLS[spec["name"]]["function"])
        if any(p.kind is p.VAR_KEYWORD for p in signature.parameters.values()):
            continue
        missing = [k for k in spec["parameters"].get("properties", {}) if k not in signature.parameters]
        if missing:
            rejected[spec["name"]] = missing
    assert rejected == {}
