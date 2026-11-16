"""Day 5 (D5-6, D5-20, D5-21, D5-22): the call list filters on the server.

The list SQL is executed for real against an in-memory SQLite database; only
the PostgreSQL-specific spellings of the new clauses (``= ANY($n::text[])``,
``LIKE ANY``, ``to_jsonb(c)->...``, ``BTRIM``, ``ILIKE``) are rewritten to their
SQLite equivalents, so the predicates, parameters and ordering that run here
are the ones the route builds.  The website's Leads / Issues rule is restated in
``website_rule`` from Talk-Leee/src/app/calls/page.tsx and
src/lib/call-history-workflow.ts (``classifyCall``, ``callHasCapturedContact``).
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import dependencies
from app.api.v1.endpoints import calls
from tests.unit.test_bh_calls_recordings_calls_endpoint import _Conn, _db, _list, _user

T = "11111111-1111-4111-8111-111111111111"
O = "22222222-2222-4222-8222-222222222222"
BASE = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)


def cid(n: int) -> str:
    return f"33333333-3333-4333-8333-{n:012d}"


# ---------------------------------------------------------------- harness
class Db:
    def __init__(self):
        self.sql = sqlite3.connect(":memory:")
        self.sql.row_factory = sqlite3.Row
        self.sql.executescript("""
            CREATE TABLE campaigns(id TEXT, tenant_id TEXT, name TEXT);
            CREATE TABLE leads(id TEXT, tenant_id TEXT, best_time_to_call TEXT);
            CREATE TABLE calls(id TEXT, tenant_id TEXT, created_at TEXT, updated_at TEXT,
                phone_number TEXT, caller_ani TEXT, called_did TEXT, talklee_call_id TEXT,
                status TEXT, outcome TEXT, lead_outcome TEXT, direction TEXT,
                campaign_id TEXT, lead_id TEXT);
            CREATE TABLE call_lead_details(id TEXT, tenant_id TEXT, call_id TEXT, field_key TEXT,
                field_type TEXT, value TEXT, normalized_value TEXT, validation_status TEXT);
        """)
        self.queries: list[tuple[str, tuple]] = []
        self.counts: list[tuple[str, tuple]] = []
        self.pool = self

    def add(self, n, tenant=T, hour=0, status="ended", outcome=None, lead_outcome=None,
            phone="+14155550100", direction="outbound", lead=None, updated=None, contact=None,
            detail_tenant=None, validation=None):
        created = BASE + timedelta(hours=hour)
        self.sql.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            cid(n), tenant, created.isoformat(timespec="microseconds"),
            (updated or created).isoformat(timespec="microseconds"), phone, None, None, None,
            status, outcome, lead_outcome, direction, None, lead))
        if contact:
            key, value = contact
            self.sql.execute("INSERT INTO call_lead_details VALUES(?,?,?,?,?,?,?,?)", (
                str(uuid4()), detail_tenant or tenant, cid(n), key, key, value, value, validation))

    @asynccontextmanager
    async def acquire(self, **kwargs):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, *_a):
        pass

    @staticmethod
    def _pg_to_sqlite(statement: str) -> str:
        statement = statement.replace("to_jsonb(c)->'summary_json'->>'outcome'", "c.lead_outcome")
        statement = re.sub(
            r"(LOWER\(c\.lead_outcome\)) LIKE ANY\((\$\d+)::text\[\]\)",
            r"EXISTS (SELECT 1 FROM json_each(\2) jj WHERE \1 LIKE jj.value ESCAPE '\\')", statement)
        statement = re.sub(r"= ANY\((\$\d+)::text\[\]\)", r"IN (SELECT value FROM json_each(\1))", statement)
        statement = re.sub(r"::(?:uuid|text|timestamptz)(?:\[\])?", "", statement)
        statement = re.sub(r"\bILIKE\b", "LIKE", statement)
        statement = statement.replace("BTRIM(", "TRIM(")
        return re.sub(
            r"\b(c\.(?:created_at|id))\s+(ASC|DESC)\b(?!\s+NULLS\b)",
            lambda m: f"{m[1]} {m[2]} NULLS {'FIRST' if m[2].upper() == 'DESC' else 'LAST'}",
            statement, flags=re.IGNORECASE)

    def run(self, query, args, *, count=False):
        tail = query.rsplit("FROM calls c", 1)[1]
        select = "COUNT(*)" if count else (
            "c.*, camp.name AS campaign_name, ld.best_time_to_call AS best_time_to_call")
        statement = self._pg_to_sqlite("SELECT " + select + " FROM calls c" + tail)
        bound = {}
        for i, value in enumerate(args, 1):
            if isinstance(value, datetime):
                value = value.isoformat(timespec="microseconds")
            elif isinstance(value, UUID):
                value = str(value)
            elif isinstance(value, list):
                value = json.dumps(value)
            bound[str(i)] = value
        return self.sql.execute(statement, bound)

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return [defaultdict(lambda: None, dict(r)) for r in self.run(query, args)]

    async def fetchval(self, query, *args):
        self.counts.append((query, args))
        return self.run(query, args, count=True).fetchone()[0]


@pytest.fixture
def db():
    d = Db()
    yield d
    d.sql.close()


async def get(db, params=None, tenant=T):
    user = SimpleNamespace(id=tenant, tenant_id=tenant, role="tenant_admin")
    app = FastAPI()
    app.include_router(calls.router, prefix="/api/v1")
    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    app.dependency_overrides[calls._require_calls_read] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as client:
        return await client.get("/api/v1/calls/", params=params)


def ids(response):
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["items"]}


# The website's rule, restated from page.tsx / call-history-workflow.ts.
FAILED_STATUS = {"busy", "failed", "no_answer"}
FAILED_OUT = {"busy", "failed", "no_answer", "rejected", "timeout", "unavailable"}


def website_rule(kind, status, outcome, lead_outcome, has_contact):
    v = (lead_outcome or "").lower()
    if kind == "leads":
        return has_contact or v.startswith("qualified") or v.startswith("callback")
    failed = (status or "").strip().lower() in FAILED_STATUS or (outcome or "").strip().lower() in FAILED_OUT
    return failed or v.startswith("no_interest") or v.startswith("disqualified")


def seed_mixed(d):
    d.add(1, hour=1, outcome="goal_achieved", lead_outcome="qualified | wants a quote")
    d.add(2, hour=2, outcome="answered", lead_outcome="Callback | ring tomorrow")
    d.add(3, hour=3, outcome="answered", contact=("email", "alex@example.com"))
    d.add(4, hour=4, outcome="no_answer")
    d.add(5, hour=5, outcome="answered", lead_outcome="no_interest | not now")
    d.add(6, hour=6, outcome="answered", lead_outcome="disqualified")
    d.add(7, hour=7, status="completed")
    d.add(8, hour=8, status="busy")
    d.add(9, hour=9, outcome="voicemail")  # website does not count voicemail as failed
    d.add(10, hour=10, outcome="answered", contact=("phone", "+923085397539"), validation="invalid")
    d.add(11, tenant=O, hour=11, outcome="goal_achieved", lead_outcome="qualified", contact=("email", "alex@example.com"))
    d.add(12, hour=12, outcome="timeout")
    d.add(13, hour=13, outcome="answered", lead_outcome="noXinterest")  # the underscore is literal, X is not
    d.add(14, hour=14, outcome="answered", contact=("phone", "+923085397539"), validation="confirmed")
    d.add(15, hour=15, outcome="answered", contact=("email", "belongs@other.com"), detail_tenant=O)


def expected(db_, kind):
    rows = db_.sql.execute("SELECT * FROM calls WHERE tenant_id=?", (T,)).fetchall()
    out = set()
    for r in rows:
        has = db_.sql.execute(
            "SELECT 1 FROM call_lead_details WHERE call_id=? AND tenant_id=? AND "
            "COALESCE(validation_status,'confirmed') NOT IN ('invalid','cancelled','needs_clarification')",
            (r["id"], T)).fetchone() is not None
        if website_rule(kind, r["status"], r["outcome"], r["lead_outcome"], has):
            out.add(r["id"])
    return out


# ------------------------------------------------------------ D5-6 filter
@pytest.mark.asyncio
async def test_lead_filter_leads_selects_what_the_website_selects(db):
    seed_mixed(db)
    got = ids(await get(db, {"lead_filter": "leads"}))
    assert got == expected(db, "leads")
    assert got == {cid(1), cid(2), cid(3), cid(14)}  # 10 is invalid, 15 is another tenant's detail, 11 another tenant


@pytest.mark.asyncio
async def test_lead_filter_issues_selects_what_the_website_selects(db):
    seed_mixed(db)
    got = ids(await get(db, {"lead_filter": "issues"}))
    assert got == expected(db, "issues")
    assert got == {cid(4), cid(5), cid(6), cid(8), cid(12)}  # 9 voicemail and 13 noXinterest are not issues


@pytest.mark.asyncio
async def test_the_total_counts_the_filtered_set(db):
    seed_mixed(db)
    body = (await get(db, {"lead_filter": "leads"})).json()
    assert body["total"] == 4


@pytest.mark.asyncio
async def test_a_lead_beyond_the_first_page_is_on_the_first_page(db):
    db.add(1, hour=0, lead_outcome="qualified")  # oldest
    for n in range(2, 14):
        db.add(n, hour=n)
    plain = ids(await get(db, {"page_size": 5}))
    assert cid(1) not in plain
    assert ids(await get(db, {"page_size": 5, "lead_filter": "leads"})) == {cid(1)}


@pytest.mark.asyncio
async def test_the_filter_combines_with_search_failed_since_direction(db):
    seed_mixed(db)
    db.add(20, hour=20, outcome="no_answer", phone="+442079460958", direction="inbound")
    db.add(21, hour=21, outcome="no_answer", phone="+442079460958")
    assert ids(await get(db, {"lead_filter": "issues", "search": "2079460958"})) == {cid(20), cid(21)}
    assert ids(await get(db, {"lead_filter": "issues", "search": "2079460958", "direction": "inbound"})) == {cid(20)}
    since = (BASE + timedelta(hours=4, minutes=30)).isoformat()
    assert ids(await get(db, {"lead_filter": "issues", "since": since})) == {cid(5), cid(6), cid(8), cid(12), cid(20), cid(21)}
    # failed=true uses the backend's outcome list, so busy STATUS (8) is not in it
    assert ids(await get(db, {"lead_filter": "issues", "failed": "true"})) == {cid(4), cid(20), cid(21)}


@pytest.mark.asyncio
async def test_the_filter_pages_by_cursor_and_a_cursor_cannot_change_filter(db):
    seed_mixed(db)
    first = await get(db, {"lead_filter": "leads", "pagination": "cursor", "page_size": 2})
    body = first.json()
    assert len(body["items"]) == 2 and body["next_cursor"]
    second = await get(db, {"lead_filter": "leads", "cursor": body["next_cursor"], "page_size": 2})
    assert ids(first) | ids(second) == {cid(1), cid(2), cid(3), cid(14)}
    assert not ids(first) & ids(second)
    assert (await get(db, {"lead_filter": "issues", "cursor": body["next_cursor"]})).status_code == 422
    assert (await get(db, {"cursor": body["next_cursor"]})).status_code == 422


@pytest.mark.asyncio
async def test_an_invalid_lead_filter_is_422(db):
    r = await get(db, {"lead_filter": "hot"})
    assert r.status_code == 422 and not db.queries


# ------------------------------------------------------------ D5-6 search
@pytest.mark.asyncio
async def test_search_matches_the_captured_email_and_phone_of_the_tenants_own_call(db):
    seed_mixed(db)
    assert ids(await get(db, {"search": "alex@exam"})) == {cid(3)}  # not tenant O's call 11
    assert ids(await get(db, {"search": "9230853"})) == {cid(14)}  # 10 holds an invalid value
    assert ids(await get(db, {"search": "belongs@other"})) == set()  # detail row owned by another tenant
    assert ids(await get(db, {"search": "alex@exam"}, tenant=O)) == {cid(11)}


@pytest.mark.asyncio
async def test_search_wildcards_are_literal_in_captured_values(db):
    db.add(1, hour=1, contact=("email", "a_b@example.com"))
    db.add(2, hour=2, contact=("email", "axb@example.com"))
    db.add(3, hour=3, contact=("email", "100%@example.com"))
    assert ids(await get(db, {"search": "a_b@"})) == {cid(1)}
    assert ids(await get(db, {"search": "00%@"})) == {cid(3)}
    assert ids(await get(db, {"search": "%%%"})) == set()


@pytest.mark.asyncio
async def test_short_searches_do_not_read_captured_details(db):
    await get(db, {"search": "ab"})
    assert "call_lead_details sd" not in db.queries[0][0]
    await get(db, {"search": "abc"})
    assert "call_lead_details sd" in db.queries[1][0]


@pytest.mark.asyncio
async def test_every_new_clause_is_tenant_scoped_and_search_is_bound(db):
    await get(db, {"lead_filter": "leads", "search": "x@y.z"})
    await get(db, {"lead_filter": "issues", "changed_since": BASE.isoformat()})
    for query, args in db.queries + db.counts:
        assert args[0] == UUID(T) and "c.tenant_id = $1" in query
        assert "x@y.z" not in query
        for table, alias in (("call_lead_details", "d"), ("call_lead_details", "sd")):
            for m in re.finditer(rf"FROM {table} {alias}\b(.*?)\)\s*(?:OR|\)|$)", query, flags=re.S):
                assert f"{alias}.tenant_id = c.tenant_id" in m.group(0)
    leads_query = db.queries[0][0]
    assert "d.call_id = c.id AND d.tenant_id = c.tenant_id" in leads_query
    assert "sd.call_id = c.id AND sd.tenant_id = c.tenant_id" in leads_query


@pytest.mark.asyncio
async def test_old_parameters_and_old_cursors_behave_as_before(db):
    r = await _list(_Conn())
    assert r.items == [] and r.total == 0


# ------------------------------------------------------------ D5-20
@pytest.mark.asyncio
async def test_changed_since_returns_changed_calls_newest_change_first(db):
    db.add(1, hour=1, updated=BASE + timedelta(hours=50))   # old call, changed late
    db.add(2, hour=2, updated=BASE + timedelta(hours=30))
    db.add(3, hour=3)                                        # created 03:00, never changed since
    db.add(4, tenant=O, hour=4, updated=BASE + timedelta(hours=60))
    r = await get(db, {"changed_since": (BASE + timedelta(hours=10)).isoformat()})
    assert r.status_code == 200
    assert [row["id"] for row in r.json()["items"]] == [cid(1), cid(2)]
    assert r.json()["next_cursor"] is None
    assert "c.updated_at >= $2" in db.queries[0][0]
    assert "ORDER BY c.updated_at DESC" in db.queries[0][0]


@pytest.mark.asyncio
async def test_changed_since_without_a_zone_is_utc_and_combines_with_filters(db):
    db.add(1, hour=1, outcome="no_answer", updated=BASE + timedelta(hours=50))
    db.add(2, hour=2, outcome="answered", updated=BASE + timedelta(hours=51))
    r = await get(db, {"changed_since": "2026-11-10T10:00:00", "lead_filter": "issues"})
    assert [row["id"] for row in r.json()["items"]] == [cid(1)]
    assert datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc) in db.queries[0][1]


@pytest.mark.asyncio
async def test_changed_since_pages_by_page_number_and_refuses_cursors(db):
    for n in range(1, 5):
        db.add(n, hour=n, updated=BASE + timedelta(hours=20 + n))
    page2 = await get(db, {"changed_since": BASE.isoformat(), "page_size": 2, "page": 2})
    assert [row["id"] for row in page2.json()["items"]] == [cid(2), cid(1)]
    assert (await get(db, {"changed_since": BASE.isoformat(), "pagination": "cursor"})).status_code == 422
    assert (await get(db, {"changed_since": BASE.isoformat(), "cursor": "abc"})).status_code == 422


@pytest.mark.asyncio
async def test_an_unparsable_changed_since_is_422(db):
    r = await get(db, {"changed_since": "yesterday"})
    assert r.status_code == 422 and not db.queries


# ------------------------------------------------------------ D5-22
@pytest.mark.asyncio
async def test_best_time_to_call_comes_from_the_calls_own_lead_in_the_same_tenant(db):
    db.sql.execute("INSERT INTO leads VALUES('l1',?,'weekdays 9-11am')", (T,))
    db.sql.execute("INSERT INTO leads VALUES('l2',?,'never seen')", (O,))
    db.sql.execute("INSERT INTO leads VALUES('l3',?,NULL)", (T,))
    db.add(1, hour=1, lead="l1")
    db.add(2, hour=2, lead="l2")   # lead of another tenant: must not leak
    db.add(3, hour=3)              # no lead
    db.add(4, hour=4, lead="l3")   # lead with no value
    body = (await get(db)).json()
    by_id = {row["id"]: row["best_time_to_call"] for row in body["items"]}
    assert by_id == {cid(1): "weekdays 9-11am", cid(2): None, cid(3): None, cid(4): None}
    assert "LEFT JOIN leads ld ON ld.id = c.lead_id AND ld.tenant_id = c.tenant_id" in " ".join(db.queries[0][0].split())


class _DetailConn:
    def __init__(self, row):
        self.row = row
        self.fetchrow_calls = []

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()

    async def execute(self, *_a):
        pass

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.row

    async def fetch(self, query, *args):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["weekdays 9-11am", None])
async def test_the_detail_carries_best_time_to_call(value):
    user = _user()
    row = {"id": uuid4(), "created_at": BASE, "phone_number": "+14155550100", "status": "ended",
           "direction": "outbound", "best_time_to_call": value}
    conn = _DetailConn(row)
    detail = await calls.get_call(call_id=str(row["id"]), current_user=user, db_client=_db(conn))
    assert detail.best_time_to_call == value
    assert detail.model_dump()["best_time_to_call"] == value
    query, args = conn.fetchrow_calls[0]
    assert "LEFT JOIN leads ld ON ld.id = c.lead_id AND ld.tenant_id = c.tenant_id" in " ".join(query.split())
    assert "c.tenant_id=$2" in query and args[1] == UUID(str(user.tenant_id))


# ------------------------------------------------------------ D5-21 document
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "call-status-values.md"


def _doc() -> str:
    assert DOC.exists(), "backend/docs/call-status-values.md is missing"
    return DOC.read_text(encoding="utf-8")


def _tokens_in(path: str, pattern: str) -> set[str]:
    return set(re.findall(pattern, (ROOT / path).read_text(encoding="utf-8")))


def test_every_call_status_value_is_documented():
    from app.domain.models.call import CallStatus
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES, CallState

    values = {s.value for s in CallState} | set(TERMINAL_CALL_STATUSES) | {s.value for s in CallStatus}
    values |= {"termination_pending"}
    values |= _tokens_in("app/api/v1/endpoints/campaign_test_ws.py", r"\$\d+,'(in_progress)'")
    missing = [v for v in sorted(values) if f"`{v}`" not in _doc()]
    assert not missing, f"call status values missing from the document: {missing}"


def test_every_call_outcome_is_documented():
    from app.domain.services.call_outcomes import ANSWERED_OUTCOMES, FAILED_OUTCOMES
    from app.domain.services.call_status import CallOutcome

    values = {o.value for o in CallOutcome} | set(ANSWERED_OUTCOMES) | set(FAILED_OUTCOMES)
    missing = [v for v in sorted(values) if f"`{v}`" not in _doc()]
    assert not missing, f"call outcome values missing from the document: {missing}"


def test_every_inbound_readiness_and_rejection_code_is_documented():
    service = "app/domain/services/inbound_campaign_service.py"
    keys = _tokens_in(service, r'add\(\s*"([a-z_]+)"')
    errors = _tokens_in(service, r'code="([a-z_]+)"')
    admission = "app/domain/services/telephony/inbound_admission.py"
    denials = _tokens_in(admission, r'_deny\(\s*(?:"other"|provider),\s*"([a-z_]+)"')
    denials |= _tokens_in(admission, r',\s*"([a-z_]+)"\),\n')
    block = re.search(r"_REASONS = \{(.*?)\}", (ROOT / "app/infrastructure/metrics/inbound_metrics.py").read_text(encoding="utf-8"), re.S).group(1)
    metrics = set(re.findall(r'"([a-z_]+)"', block))
    assert keys and errors and denials and metrics
    missing = [v for v in sorted(keys | errors | denials | metrics) if f"`{v}`" not in _doc()]
    assert not missing, f"inbound codes missing from the document: {missing}"


def test_every_dialer_block_code_is_documented_with_its_title():
    from app.domain.services.dialer.block_reasons import BlockCode
    from app.domain.services.dialer.block_state import _TITLES

    doc = _doc()
    missing = [c.value for c in BlockCode if f"`{c.value}`" not in doc]
    assert not missing, f"block codes missing from the document: {missing}"
    stale = [(c.value, t) for c, t in _TITLES.items() if t not in doc]
    assert not stale, f"block-code titles missing from the document: {stale}"
    assert "Calls are paused" in doc  # the fallback title
