"""F11-01: real assistant query tools must apply the range they report."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.infrastructure.assistant.agent import SYSTEM_PROMPT
from app.infrastructure.assistant.tools import QUERY_TOOLS, dispatch
from app.infrastructure.assistant.tools.calls import get_recent_calls
from app.infrastructure.assistant.tools.dashboard import GetDashboardStatsInput, get_dashboard_stats
from app.infrastructure.assistant.tools.leads import GetLeadsInput, get_leads, get_qualified_leads
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS


TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
CAMPAIGN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SECOND = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ACTOR = "33333333-3333-4333-8333-333333333333"
SINCE = "2026-10-01T05:00:00+05:00"
UNTIL = "2026-11-01T05:00:00+05:00"
START_UTC = "2026-10-01T00:00:00+00:00"
END_UTC = "2026-11-01T00:00:00+00:00"
TOOLS = (get_recent_calls, get_leads, get_qualified_leads, get_dashboard_stats)


def instant(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))


class Query:
    def __init__(self, client, table):
        self.client, self.table, self.filters = client, table, []
        self.columns = "*"
        self.cap = None
        self.sort = None
        client.queries.append(self)

    def select(self, columns="*", **kwargs):
        self.columns = columns
        return self

    def _filter(self, op, column, value):
        self.filters.append((op, column, value))
        return self

    def eq(self, column, value):
        return self._filter("eq", column, value)

    def neq(self, column, value):
        return self._filter("neq", column, value)

    def gte(self, column, value):
        return self._filter("gte", column, value)

    def lt(self, column, value):
        return self._filter("lt", column, value)

    def lte(self, column, value):
        return self._filter("lte", column, value)

    def in_(self, column, values):
        return self._filter("in", column, values)

    def order(self, column, *, desc=False):
        self.sort = (column, desc)
        return self

    def limit(self, number):
        self.cap = number
        return self

    def _matches(self, row):
        for op, column, value in self.filters:
            actual = row.get(column)
            if op == "eq" and actual != value:
                return False
            if op == "neq" and actual == value:
                return False
            if op == "in" and actual not in value:
                return False
            if op in {"gte", "lt", "lte"}:
                if actual is None:
                    return False
                left, right = instant(actual), instant(value)
                if op == "gte" and left < right or op == "lt" and left >= right or op == "lte" and left > right:
                    return False
        return True

    def execute(self):
        self.client.executed.append(self)
        rows = [dict(row) for row in self.client.tables[self.table] if self._matches(row)]
        count = len(rows)
        if self.sort:
            column, desc = self.sort
            rows.sort(key=lambda row: str(row.get(column) or ""), reverse=desc)
        if self.cap is not None:
            rows = rows[:self.cap]
        if self.columns != "*":
            wanted = [field.strip() for field in self.columns.split(",")]
            rows = [{key: row.get(key) for key in wanted} for row in rows]
        return SimpleNamespace(data=rows, count=count, error=None)


class Client:
    def __init__(self):
        self.queries = []
        self.executed = []
        rows = []
        for key, timestamp, tenant, campaign in (
            ("before", "2026-09-30T23:59:59.999999Z", TENANT, CAMPAIGN),
            ("at_start", "2026-10-01T00:00:00Z", TENANT, CAMPAIGN),
            ("late", "2026-10-31T23:59:59.999999Z", TENANT, CAMPAIGN),
            ("at_end", "2026-11-01T00:00:00Z", TENANT, CAMPAIGN),
            ("wrong_campaign", "2026-10-15T12:00:00Z", TENANT, SECOND),
            ("foreign", "2026-10-15T12:00:00Z", OTHER, CAMPAIGN),
        ):
            rows.append({"id": key, "tenant_id": tenant, "campaign_id": campaign,
                         "created_at": timestamp, "qualified_at": timestamp,
                         "status": "completed", "outcome": "answered", "is_lead": True,
                         "goal_achieved": key == "late", "phone_number": "+15550001000"})
        self.tables = {
            "calls": [dict(row) for row in rows], "leads": [dict(row) for row in rows],
            "campaigns": [
                {"id": CAMPAIGN, "tenant_id": TENANT, "name": "Scoped", "status": "running"},
                {"id": SECOND, "tenant_id": TENANT, "name": "Other campaign", "status": "running"},
                {"id": CAMPAIGN, "tenant_id": OTHER, "name": "Foreign", "status": "running"},
            ],
        }

    def table(self, name):
        return Query(self, name)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda fn: fn.__name__)
async def test_actual_tools_apply_both_bounds_and_tenant_campaign_scope(tool):
    client = Client()
    result = await tool(TENANT, client, campaign_id=CAMPAIGN, since=SINCE, until=UNTIL)
    assert "error" not in result, result
    table = "calls" if tool in {get_recent_calls, get_dashboard_stats} else "leads"
    field = "qualified_at" if tool is get_qualified_leads else "created_at"
    if tool is get_dashboard_stats:
        assert result["total_calls"] == 2 and result["completed"] == 2 and result["goal_achieved"] == 1
        assert result["campaign_id"] == CAMPAIGN and result["campaign_name"] == "Scoped"
    else:
        key = {get_recent_calls: "calls", get_leads: "leads", get_qualified_leads: "qualified_leads"}[tool]
        assert {row["id"] for row in result[key]} == {"at_start", "late"}
    assert result["applied_range"] == {"since": START_UTC, "until": END_UTC, "timestamp_field": field}
    queries = [q for q in client.executed if q.table == table]
    assert len(queries) == 1
    assert ("eq", "tenant_id", TENANT) in queries[0].filters
    assert ("eq", "campaign_id", CAMPAIGN) in queries[0].filters
    assert [(op, col) for op, col, _ in queries[0].filters if op in {"gte", "lt", "lte"}] == [("gte", field), ("lt", field)]
    for op, _, bound in queries[0].filters:
        if op in {"gte", "lt"}:
            assert instant(bound).utcoffset().total_seconds() == 0


async def test_qualified_leads_filter_when_qualified_not_when_contact_was_created():
    client = Client()
    for row in client.tables["leads"]:
        row["created_at"] = "2020-01-01T00:00:00Z"
    result = await get_qualified_leads(TENANT, client, campaign_id=CAMPAIGN, since=SINCE, until=UNTIL)
    assert {row["id"] for row in result["qualified_leads"]} == {"at_start", "late"}


@pytest.mark.parametrize("tool", TOOLS, ids=lambda fn: fn.__name__)
async def test_invalid_range_returns_error_without_querying_or_claiming_application(tool):
    client = Client()
    result = await tool(TENANT, client, campaign_id=CAMPAIGN, since=UNTIL, until=SINCE)
    assert result.get("error") and "applied_range" not in result
    assert client.executed == []


@pytest.mark.parametrize("bound", ["2026-10-01", "2026-10-01T00:00:00", "not-a-date", 17, "2026-10-01T00:00:00Z\n"])
async def test_naive_malformed_and_nonstring_bounds_are_refused(bound):
    client = Client()
    result = await get_recent_calls(TENANT, client, since=bound, until=UNTIL)
    assert result.get("error") and "applied_range" not in result
    assert client.executed == []


@pytest.mark.parametrize("kwargs,expected", [({"since": SINCE}, {"at_start", "late", "at_end"}), ({"until": UNTIL}, {"before", "at_start", "late"})])
async def test_one_sided_range_overrides_today_only_and_reports_open_boundary(kwargs, expected):
    client = Client()
    result = await get_recent_calls(TENANT, client, campaign_id=CAMPAIGN, **kwargs)
    assert {row["id"] for row in result["calls"]} == expected
    assert result["applied_range"]["since"] == (START_UTC if "since" in kwargs else None)
    assert result["applied_range"]["until"] == (END_UTC if "until" in kwargs else None)


async def test_dashboard_rejects_conflicting_single_day_and_explicit_range():
    client = Client()
    result = await get_dashboard_stats(TENANT, client, date_str="2026-11-06", since=SINCE, until=UNTIL)
    assert result.get("error") and "applied_range" not in result
    assert client.executed == []


async def test_unbounded_call_result_does_not_claim_a_date_filter():
    result = await get_recent_calls(TENANT, Client(), today_only=False, campaign_id=CAMPAIGN)
    assert result["total_count"] == 4
    assert result["applied_range"] == {"since": None, "until": None, "timestamp_field": "created_at"}


@pytest.mark.parametrize("name", [fn.__name__ for fn in TOOLS])
def test_llm_schema_exposes_explicit_aware_inclusive_and_exclusive_bounds(name):
    schema = next(entry["function"] for entry in GROQ_TOOL_SCHEMAS if entry["function"]["name"] == name)
    properties = schema["parameters"]["properties"]
    # Existing strict-schema normalization admits null for every optional
    # argument. Numbers/naive strings are independently refused by real tools.
    assert properties["since"]["type"] == ["string", "null"]
    assert properties["until"]["type"] == ["string", "null"]
    assert "inclusive" in properties["since"]["description"].lower()
    assert "exclusive" in properties["until"]["description"].lower()
    assert "rfc3339" in properties["since"]["description"].lower()
    assert "timezone" in properties["since"]["description"].lower()


@pytest.mark.parametrize("model", [GetDashboardStatsInput, GetLeadsInput])
def test_existing_input_models_retain_range_values(model):
    args = model(since=SINCE, until=UNTIL).model_dump()
    assert args.get("since") == SINCE and args.get("until") == UNTIL


async def test_dispatch_uses_real_registered_tool_and_preserves_the_range(monkeypatch):
    from app.core.security import principal

    async def allowed(*args, **kwargs):
        return None

    monkeypatch.setattr(principal, "check_assistant_session", allowed)
    monkeypatch.setattr(dispatch, "_authorize_query_tool", allowed)
    assert QUERY_TOOLS["get_recent_calls"]["function"] is get_recent_calls
    result = await dispatch.dispatch_tool(
        "get_recent_calls", TENANT, Client(), None,
        {"campaign_id": CAMPAIGN, "since": SINCE, "until": UNTIL}, actor_user_id=ACTOR,
    )
    assert "error" not in result, result
    assert {row["id"] for row in result["calls"]} == {"at_start", "late"}
    assert result["applied_range"]["until"] == END_UTC


def test_real_assistant_prompt_forbids_inventing_an_unapplied_range():
    prompt = SYSTEM_PROMPT.lower()
    assert "never state a date range the tool didn't apply" in prompt
    assert "applied_range" in prompt
