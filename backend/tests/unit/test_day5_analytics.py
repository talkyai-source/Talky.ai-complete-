"""Day 5 analytics: D5-5 totals that add up, D5-7 short windows, D5-23 zone in /auth/me.

Behaviour tests: the real router is mounted, the database is an in-memory fake
that applies the filters it is given (so tenant scoping is exercised, not grepped).
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.endpoints import analytics
from app.api.v1.endpoints.auth import profile
from app.domain.models.dialer_job import CallOutcome as DialerOutcome
from app.domain.services import call_outcomes
from app.domain.services.call_status import CallOutcome

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
OTHER_TENANT = "11111111-1234-4abc-8def-0123456789ab"
UTC = timezone.utc
NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)

# One value of every kind: answered, failed, not-yet-set, and written-but-unlisted.
ANSWERED = ["answered", "customer_hung_up", "agent_hung_up", "goal_achieved", "goal_not_achieved"]
FAILED = ["no_answer", "busy", "rejected", "unreachable", "network_failure", "failed", "cancelled", "voicemail"]
IN_BETWEEN = [None, "", "in_progress", "queued", "invalid", "timeout", "spam", "unavailable", "disconnected", "Mystery"]


def _parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class _Query:
    def __init__(self, table, rows, log):
        self.table, self.rows, self.log, self.filters = table, rows, log, []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self.filters.append(("gte", col, val))
        return self

    def lt(self, col, val):
        self.filters.append(("lt", col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.log.append((self.table, list(self.filters)))
        out = []
        for row in self.rows:
            ok = True
            for op, col, val in self.filters:
                have = row.get(col)
                if op == "eq":
                    ok &= have == val
                elif op == "gte":
                    ok &= have >= _parse(val)
                else:
                    ok &= have < _parse(val)
            if ok:
                out.append(row)
        return SimpleNamespace(data=out, error=None, count=len(out))


class _DB:
    def __init__(self, calls, rules=None):
        self.calls, self.log = calls, []
        self.tenants = [{"id": TENANT, "calling_rules": rules or {}}]

    def table(self, name):
        return _Query(name, self.calls if name == "calls" else self.tenants, self.log)

    def calls_statements(self):
        return [f for t, f in self.log if t == "calls"]


def _call(when, outcome="answered", tenant=TENANT, direction="outbound", is_test=False):
    return {"created_at": when, "outcome": outcome, "tenant_id": tenant, "direction": direction, "is_test": is_test}


@pytest.fixture
def mount(monkeypatch):
    monkeypatch.setattr(analytics, "_utc_now", lambda: NOW)

    def build(calls, rules=None):
        db = _DB(calls, rules)
        app = FastAPI()
        app.include_router(analytics.router, prefix="/api/v1")
        user = SimpleNamespace(id="u", tenant_id=TENANT)
        app.dependency_overrides[analytics.get_current_user] = lambda: user
        app.dependency_overrides[analytics.get_db_client] = lambda: db
        app.dependency_overrides[analytics._require_analytics_read] = lambda: None
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid")
        return client, db

    return build


# ---------------------------------------------------------------- D5-5

def _mixed_calls(day):
    base = datetime.fromisoformat(day).replace(tzinfo=UTC, hour=9)
    values = ANSWERED + FAILED + IN_BETWEEN
    return [_call(base + timedelta(minutes=i), v) for i, v in enumerate(values)]


def test_classifier_is_total_for_every_outcome_the_code_knows():
    known = {m.value for m in CallOutcome} | {m.value for m in DialerOutcome}
    known |= call_outcomes.ANSWERED_OUTCOMES | call_outcomes.FAILED_OUTCOMES | {None, "", "x"}
    for value in known:
        bucket = {"total": 0, "answered": 0, "failed": 0, "other": 0, "goal": 0}
        analytics._classify(value, bucket)
        assert bucket["answered"] + bucket["failed"] + bucket["other"] == bucket["total"] == 1, value
        label = call_outcomes.classify_outcome(value)
        expected = ("answered" if (value or "").lower() in call_outcomes.ANSWERED_OUTCOMES
                    else "failed" if (value or "").lower() in call_outcomes.FAILED_OUTCOMES else "other")
        assert label == expected and bucket[label] == 1, value


def test_every_call_status_outcome_is_answered_or_failed_never_other():
    # Guards "do not move an outcome": the dialer's own vocabulary stays classified.
    for member in CallOutcome:
        assert call_outcomes.classify_outcome(member.value) in ("answered", "failed"), member


def test_unlisted_dialer_outcomes_are_other_today():
    for value in ("timeout", "spam", "invalid", "unavailable", "disconnected", "in_progress", None, ""):
        assert call_outcomes.classify_outcome(value) == "other", value


@pytest.mark.parametrize("query,days", [
    ("", 31),
    ("?from=2026-11-10&to=2026-11-10", 1),
    ("?from=2026-11-01&to=2026-11-17", 17),
])
async def test_daily_rows_and_summary_add_up(mount, query, days):
    calls = [c for d in ("2026-11-10", "2026-11-11", "2026-11-16") for c in _mixed_calls(d)]
    client, _ = mount(calls)
    async with client:
        body = (await client.get("/api/v1/analytics/calls" + query)).json()
    assert body["series"], body
    for row in body["series"]:
        assert row["answered"] + row["failed"] + row["other"] == row["total_calls"], row
        assert row["other"] == len(IN_BETWEEN), row
    s = body["summary"]
    assert s["answered"] + s["failed"] + s["other"] == s["total_calls"]
    assert s["total_calls"] == sum(r["total_calls"] for r in body["series"])
    assert s["other"] == sum(r["other"] for r in body["series"])
    assert body["buckets"] is None and body["window_hours"] is None


async def test_undated_default_is_still_31_dates(mount):
    client, db = mount([])
    async with client:
        await client.get("/api/v1/analytics/calls")
    (filters,) = db.calls_statements()
    lo = next(v for op, c, v in filters if op == "gte")
    hi = next(v for op, c, v in filters if op == "lt")
    assert hi - lo == timedelta(days=31)


# ---------------------------------------------------------------- D5-7

@pytest.mark.parametrize("hours,minutes", [(1, 15), (4, 15), (8, 30), (24, 60)])
async def test_window_bucket_count_and_length(mount, hours, minutes):
    client, _ = mount([])
    async with client:
        body = (await client.get(f"/api/v1/analytics/calls?window_hours={hours}")).json()
    assert body["window_hours"] == hours and body["bucket_minutes"] == minutes
    assert body["series"] == [] and body["timezone"] == "UTC"
    buckets = body["buckets"]
    assert len(buckets) == hours * 60 // minutes
    previous_end = None
    for b in buckets:
        start, end = datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])
        assert end - start == timedelta(minutes=minutes)
        assert previous_end is None or start == previous_end
        previous_end = end
        assert (b["total_calls"], b["answered"], b["failed"], b["other"]) == (0, 0, 0, 0)
    assert datetime.fromisoformat(buckets[0]["start"]) == NOW - timedelta(hours=hours)
    assert datetime.fromisoformat(buckets[-1]["end"]) == NOW
    assert body["summary"] == {"total_calls": 0, "answered": 0, "failed": 0, "other": 0, "goal_achieved": 0}


async def test_boundary_call_is_counted_once_in_the_later_bucket(mount):
    edge = NOW - timedelta(minutes=30)  # edge between two 15-minute buckets of the 1h window
    client, _ = mount([_call(edge, "answered")])
    async with client:
        body = (await client.get("/api/v1/analytics/calls?window_hours=1")).json()
    hit = [b for b in body["buckets"] if b["total_calls"]]
    assert len(hit) == 1 and datetime.fromisoformat(hit[0]["start"]) == edge
    assert body["summary"]["total_calls"] == 1


async def test_window_start_is_inclusive_and_now_side_is_exclusive(mount):
    calls = [_call(NOW - timedelta(hours=1), "answered"), _call(NOW - timedelta(hours=1, microseconds=1), "answered"),
             _call(NOW, "answered")]
    client, _ = mount(calls)
    async with client:
        body = (await client.get("/api/v1/analytics/calls?window_hours=1")).json()
    assert body["summary"]["total_calls"] == 1  # only the call exactly at the start


async def test_one_hour_and_24_hours_differ_when_calls_are_older_than_an_hour(mount):
    calls = [_call(NOW - timedelta(minutes=10), "answered"), _call(NOW - timedelta(minutes=5), "busy"),
             _call(NOW - timedelta(hours=5), "answered"), _call(NOW - timedelta(hours=20), None)]
    client, _ = mount(calls)
    async with client:
        h1 = (await client.get("/api/v1/analytics/calls?window_hours=1")).json()
        h24 = (await client.get("/api/v1/analytics/calls?window_hours=24")).json()
    assert h1["summary"] == {"total_calls": 2, "answered": 1, "failed": 1, "other": 0, "goal_achieved": 0}
    assert h24["summary"]["total_calls"] == 4 and h24["summary"]["other"] == 1
    for body in (h1, h24):
        assert sum(b["total_calls"] for b in body["buckets"]) == body["summary"]["total_calls"]
        for b in body["buckets"]:
            assert b["answered"] + b["failed"] + b["other"] == b["total_calls"]


async def test_window_uses_account_zone_offsets(mount):
    client, _ = mount([], rules={"reporting_timezone": "Asia/Karachi"})
    async with client:
        body = (await client.get("/api/v1/analytics/calls?window_hours=1")).json()
    assert body["timezone"] == "Asia/Karachi"
    assert body["buckets"][0]["start"].endswith("+05:00") and body["buckets"][-1]["end"].endswith("+05:00")
    assert body["buckets"][-1]["end"].startswith("2026-11-17T17:00:00")


async def test_window_across_a_daylight_saving_change(mount, monkeypatch):
    # New York: clocks go forward 2026-03-08 02:00 local (07:00Z).
    now = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(analytics, "_utc_now", lambda: now)
    edge = datetime(2026, 3, 8, 6, 59, tzinfo=UTC)  # 01:59 EST, last minute before the change
    after = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 03:00 EDT
    client, _ = mount([_call(edge, "answered"), _call(after, "busy")])
    async with client:
        body = (await client.get("/api/v1/analytics/calls?window_hours=24&tz=America/New_York")).json()
    buckets = body["buckets"]
    assert len(buckets) == 24
    for b in buckets:
        assert datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"]) == timedelta(hours=1)
    offsets = [b["start"][-6:] for b in buckets]
    assert offsets[0] == "-05:00" and offsets[-1] == "-04:00"
    assert sum(1 for a, b in zip(offsets, offsets[1:]) if a != b) == 1
    by_start = {datetime.fromisoformat(b["start"]).astimezone(UTC): b for b in buckets}
    assert by_start[datetime(2026, 3, 8, 6, 0, tzinfo=UTC)]["answered"] == 1
    assert by_start[datetime(2026, 3, 8, 7, 0, tzinfo=UTC)]["failed"] == 1
    assert body["summary"]["total_calls"] == 2


@pytest.mark.parametrize("query", [
    "window_hours=2", "window_hours=0", "window_hours=-1", "window_hours=48", "window_hours=abc",
    "window_hours=1&from=2026-11-01", "window_hours=4&to=2026-11-17",
    "window_hours=24&from=2026-11-01&to=2026-11-17",
])
async def test_invalid_window_or_combination_with_dates_is_422(mount, query):
    client, db = mount([])
    async with client:
        response = await client.get("/api/v1/analytics/calls?" + query)
    assert response.status_code == 422, response.text
    assert db.calls_statements() == []
    if "abc" not in query:
        assert isinstance(response.json()["detail"], str)


async def test_window_is_one_tenant_scoped_time_bounded_statement(mount):
    calls = [_call(NOW - timedelta(minutes=5), "answered"),
             _call(NOW - timedelta(minutes=5), "answered", tenant=OTHER_TENANT),
             _call(NOW - timedelta(minutes=5), "answered", direction="inbound"),
             _call(NOW - timedelta(minutes=5), "answered", is_test=True)]
    client, db = mount(calls)
    async with client:
        body = (await client.get("/api/v1/analytics/calls?window_hours=8")).json()
    assert body["summary"]["total_calls"] == 1
    (filters,) = db.calls_statements()  # one statement, never one per bucket
    assert ("eq", "tenant_id", TENANT) in filters
    assert [op for op, c, _ in filters if c == "created_at"] == ["gte", "lt"]


# ---------------------------------------------------------------- D5-23

@pytest.fixture
def me_app(monkeypatch):
    state = SimpleNamespace(row={})

    class Connection:
        async def fetchrow(self, query, *args):
            base = {"tenant_id": TENANT, "partner_id": None, "tenant_status": "active", "partner_status": None,
                    "tenant_suspended_at": None, "tenant_suspension_reason": None,
                    "partner_suspended_at": None, "partner_suspension_reason": None}
            return {**base, **state.row}

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Connection()

    async def acceptance_status(conn, *, tenant_id, user_id):
        return {"legal_acceptance_required": False}

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.legal_acceptance.acceptance_status", acceptance_status)
    user = SimpleNamespace(id="bbccddee-1234-4abc-8def-0123456789ab", tenant_id=TENANT, email="r@example.test",
                           name="R", business_name="S", role="readonly", minutes_remaining=None,
                           minutes_state="unavailable")
    app = FastAPI()
    app.include_router(profile.router, prefix="/api/v1/auth")
    app.dependency_overrides[profile.get_current_user] = lambda: user
    app.dependency_overrides[profile.get_db_client] = lambda: SimpleNamespace(pool=object())
    return app, state


@pytest.mark.parametrize("row,reporting,account", [
    ({"reporting_timezone": "Asia/Karachi", "account_timezone": "America/New_York"}, "Asia/Karachi", "Asia/Karachi"),
    ({"account_timezone": "America/New_York"}, None, "America/New_York"),
    ({}, None, None),
])
async def test_auth_me_carries_the_zone_the_analytics_answer_would_use(me_app, row, reporting, account):
    app, state = me_app
    state.row = row
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        body = (await client.get("/api/v1/auth/me")).json()
    assert body["reporting_timezone"] == reporting and body["account_timezone"] == account
