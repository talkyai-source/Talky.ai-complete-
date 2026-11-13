"""F2-03: bypassed call projections must scope their tenant-bearing children.

The mounted routes supply their real SQL to a small asyncpg protocol double.
The double executes the actual scalar/EXISTS subqueries and campaign join on
SQLite, with an owning call plus adversarial child rows. It does not infer
visibility by searching for a desired predicate. PostgreSQL RLS, full-query
syntax and migration constraints are not exercised here. Recording/campaign
mismatches model legacy data for defense in depth: migration 0041 already adds
composite ownership foreign keys for those relationships. Transcripts and
feedback instead have separate call/tenant foreign keys in the inspected schema.
"""
from collections import defaultdict
from contextlib import asynccontextmanager
import re
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import dependencies
from app.api.v1.endpoints import calls


OWNER = "a1111111-bbbb-4111-8111-ccccccccccc1"
OTHER = "a2222222-bbbb-4222-8222-ccccccccccc2"
CALL = "c3333333-bbbb-4333-8333-ddddddddddd3"
CAMPAIGN = "d4444444-bbbb-4444-8444-eeeeeeeeeee4"
OWN_RECORDING = "e5555555-bbbb-4555-8555-fffffffffff5"
OTHER_RECORDING = "f6666666-bbbb-4666-8666-aaaaaaaaaaa6"


def subquery(sql, selected):
    """Extract one actual parenthesized SELECT, retaining its SQL predicates."""
    start = re.search(r"\(\s*(SELECT\s+" + selected + r"\b)", sql, re.I)
    assert start, f"Expected projection absent: {selected}"
    depth, quoted, index = 1, False, start.start(1)
    while index < len(sql):
        character = sql[index]
        if character == "'":
            if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            depth += character == "("
            depth -= character == ")"
            if depth == 0:
                return sql[start.start(1):index]
        index += 1
    raise AssertionError("Unbalanced projection SQL")


class ProjectionConnection:
    def __init__(self):
        self.sql = sqlite3.connect(":memory:")
        self.sql.row_factory = sqlite3.Row
        self.executed = []
        self.bypass = False
        self.sql.executescript("""
            CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT, campaign_id TEXT,
                                created_at TEXT, status TEXT, direction TEXT, phone_number TEXT);
            CREATE TABLE campaigns (id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT);
            CREATE TABLE transcripts (id TEXT PRIMARY KEY, tenant_id TEXT, call_id TEXT,
                                      full_text TEXT, created_at TEXT);
            CREATE TABLE recordings_s3 (id TEXT PRIMARY KEY, tenant_id TEXT, call_id TEXT,
                                       status TEXT, created_at TEXT);
            CREATE TABLE call_feedback (id TEXT PRIMARY KEY, tenant_id TEXT, call_id TEXT);
        """)
        self.sql.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)", (
            CALL, OWNER, CAMPAIGN, "2026-11-06T00:00:00+00:00", "completed", "outbound", "+14155550123",
        ))

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        finally:
            self.bypass = False

    async def execute(self, query, *args):
        self.executed.append((query, args))
        assert re.fullmatch(r"SET LOCAL app\.bypass_rls\s*=\s*'true'", query.strip(), re.I)
        self.bypass = True
        return "SET"

    def _scalar(self, expression, call_id, tenant_id):
        return self.sql.execute(
            f"SELECT {expression} FROM calls c WHERE c.id = ? AND c.tenant_id = ?",
            (call_id, tenant_id),
        ).fetchone()[0]

    def _project(self, query, call_id, tenant_id, *, detail):
        assert self.bypass, "Exercise the real bypass path, not an RLS-filtered fake"
        parent = self.sql.execute(
            "SELECT * FROM calls WHERE id = ? AND tenant_id = ?", (call_id, tenant_id),
        ).fetchone()
        if parent is None:
            return None
        # Parent authorization is already present. Check that the protocol
        # double is receiving that unchanged binding from the real route.
        assert re.search(r"c\.tenant_id\s*=\s*\$[12]", query)
        row = defaultdict(lambda: None, dict(parent))
        join = re.search(
            r"LEFT\s+JOIN\s+campaigns\s+camp\s+ON\s+(.*?)\s+WHERE\s+c\.", query, re.I | re.S,
        )
        assert join, "The campaign projection must be exercised"
        row["campaign_name"] = self.sql.execute(
            "SELECT camp.name FROM calls c LEFT JOIN campaigns camp ON " + join.group(1)
            + " WHERE c.id = ? AND c.tenant_id = ?", (call_id, tenant_id),
        ).fetchone()[0]
        for field, selected in (("recording_id", r"r\.id"), ("recording_status", r"r\.status")):
            row[field] = self._scalar("(" + subquery(query, selected) + ")", call_id, tenant_id)
        row["has_transcript"] = bool(self._scalar(
            "EXISTS (" + subquery(query, r"1\s+FROM\s+transcripts\s+tr") + ")", call_id, tenant_id,
        ))
        if detail:
            row["persisted_transcript"] = self._scalar(
                "(" + subquery(query, r"tr\.full_text") + ")", call_id, tenant_id,
            )
        else:
            row["has_feedback"] = bool(self._scalar(
                "EXISTS (" + subquery(query, r"1\s+FROM\s+call_feedback\s+f") + ")", call_id, tenant_id,
            ))
        return row

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        assert "AS persisted_transcript" in query
        return self._project(query, str(args[0]), str(args[1]), detail=True)

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        if "FROM call_legs" in query and "AS recording_id" not in query:
            # call_legs has no tenant_id; the already-owned parent is its owner.
            assert str(args[0]) == CALL
            return []
        assert "AS recording_id" in query
        row = self._project(query, CALL, str(args[0]), detail=False)
        return [row] if row else []

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        assert re.search(r"SELECT COUNT\(\*\) FROM calls c WHERE c\.tenant_id = \$1", query)
        return self.sql.execute("SELECT COUNT(*) FROM calls WHERE tenant_id = ?", (str(args[0]),)).fetchone()[0]


class ProjectionPool:
    def __init__(self, conn):
        self.conn, self.active = conn, 0

    @asynccontextmanager
    async def acquire(self, **kwargs):
        self.active += 1
        try:
            yield self.conn
        finally:
            self.active -= 1


@pytest.fixture
def projection_db():
    conn = ProjectionConnection()
    try:
        yield conn
    finally:
        conn.sql.close()


async def request(conn, *, detail, tenant=OWNER):
    pool = ProjectionPool(conn)
    user = SimpleNamespace(id=OWNER, tenant_id=tenant, role="tenant_admin")
    app = FastAPI()
    app.include_router(calls.router, prefix="/api/v1")
    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    app.dependency_overrides[dependencies.get_db_client] = lambda: SimpleNamespace(pool=pool)
    app.dependency_overrides[calls._require_calls_read] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.get(f"/api/v1/calls/{CALL}" if detail else "/api/v1/calls/")
    assert pool.active == 0
    return response


async def test_detail_does_not_project_another_tenants_newer_transcript(projection_db):
    projection_db.sql.executemany("INSERT INTO transcripts VALUES (?, ?, ?, ?, ?)", [
        ("own", OWNER, CALL, "Owning tenant transcript", "2026-11-05"),
        ("other", OTHER, CALL, "OTHER TENANT PRIVATE TRANSCRIPT", "2026-11-06"),
    ])
    response = await request(projection_db, detail=True)
    assert response.status_code == 200, response.text
    assert response.json()["transcript"] == "Owning tenant transcript"
    assert "OTHER TENANT" not in response.text


async def test_list_does_not_claim_a_foreign_transcript_is_available(projection_db):
    projection_db.sql.execute("UPDATE calls SET direction = 'inbound' WHERE id = ?", (CALL,))
    projection_db.sql.execute("INSERT INTO transcripts VALUES (?, ?, ?, ?, ?)", (
        "other", OTHER, CALL, "OTHER TENANT PRIVATE TRANSCRIPT", "2026-11-06",
    ))
    response = await request(projection_db, detail=False)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["transcript_status"] == "pending"
    assert not any("OTHER TENANT" in str(value) for value in response.json()["items"][0].values())


@pytest.mark.parametrize("detail", [False, True])
async def test_legacy_null_tenant_transcript_remains_visible_through_owned_call(projection_db, detail):
    projection_db.sql.execute("UPDATE calls SET direction = 'inbound' WHERE id = ?", (CALL,))
    projection_db.sql.executemany("INSERT INTO transcripts VALUES (?, ?, ?, ?, ?)", [
        ("legacy", None, CALL, "Legacy transcript belonging to this call", "2026-11-05"),
        ("other", OTHER, CALL, "OTHER TENANT PRIVATE TRANSCRIPT", "2026-11-06"),
    ])
    response = await request(projection_db, detail=detail)
    assert response.status_code == 200, response.text
    item = response.json() if detail else response.json()["items"][0]
    assert item["transcript_status"] == "done"
    if detail:
        assert item["transcript"] == "Legacy transcript belonging to this call"
    assert "OTHER TENANT" not in response.text


@pytest.mark.parametrize("detail", [False, True])
async def test_legacy_null_tenant_transcript_does_not_grant_parent_access(projection_db, detail):
    projection_db.sql.execute("INSERT INTO transcripts VALUES (?, ?, ?, ?, ?)", (
        "legacy", None, CALL, "Legacy transcript belonging to this call", "2026-11-05",
    ))
    response = await request(projection_db, detail=detail, tenant=OTHER)
    if detail:
        assert response.status_code == 404
    else:
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert response.json()["total"] == 0
    assert "Legacy transcript" not in response.text


@pytest.mark.parametrize("detail", [False, True])
async def test_legacy_mismatched_recording_is_not_projected(projection_db, detail):
    projection_db.sql.executemany("INSERT INTO recordings_s3 VALUES (?, ?, ?, ?, ?)", [
        (OWN_RECORDING, OWNER, CALL, "uploaded", "2026-11-05"),
        (OTHER_RECORDING, OTHER, CALL, "uploaded", "2026-11-06"),
    ])
    response = await request(projection_db, detail=detail)
    assert response.status_code == 200, response.text
    item = response.json() if detail else response.json()["items"][0]
    assert item["recording_id"] == OWN_RECORDING
    assert OTHER_RECORDING not in response.text


@pytest.mark.parametrize("detail", [False, True])
async def test_legacy_mismatched_campaign_name_is_not_projected(projection_db, detail):
    projection_db.sql.execute("INSERT INTO campaigns VALUES (?, ?, ?)", (CAMPAIGN, OTHER, "OTHER TENANT CAMPAIGN"))
    response = await request(projection_db, detail=detail)
    assert response.status_code == 200, response.text
    item = response.json() if detail else response.json()["items"][0]
    assert item["campaign_name"] is None
    assert "OTHER TENANT CAMPAIGN" not in response.text


async def test_list_does_not_project_another_tenants_feedback(projection_db):
    projection_db.sql.execute("INSERT INTO call_feedback VALUES (?, ?, ?)", ("other-feedback", OTHER, CALL))
    response = await request(projection_db, detail=False)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["has_feedback"] is False


@pytest.mark.parametrize("detail", [False, True])
async def test_owning_tenant_keeps_its_media_and_campaign(projection_db, detail):
    projection_db.sql.execute("INSERT INTO campaigns VALUES (?, ?, ?)", (CAMPAIGN, OWNER, "Own campaign"))
    projection_db.sql.execute("INSERT INTO transcripts VALUES (?, ?, ?, ?, ?)", (
        "own", OWNER, CALL, "Own transcript", "2026-11-06",
    ))
    projection_db.sql.execute("INSERT INTO recordings_s3 VALUES (?, ?, ?, ?, ?)", (
        OWN_RECORDING, OWNER, CALL, "uploaded", "2026-11-06",
    ))
    projection_db.sql.execute("INSERT INTO call_feedback VALUES (?, ?, ?)", ("own-feedback", OWNER, CALL))
    response = await request(projection_db, detail=detail)
    assert response.status_code == 200, response.text
    item = response.json() if detail else response.json()["items"][0]
    assert item["campaign_name"] == "Own campaign"
    assert item["recording_id"] == OWN_RECORDING
    if detail:
        assert item["transcript"] == "Own transcript"
    else:
        assert item["has_feedback"] is True


@pytest.mark.parametrize("detail", [False, True])
async def test_other_tenant_cannot_read_the_parent_call(projection_db, detail):
    response = await request(projection_db, detail=detail, tenant=OTHER)
    if detail:
        assert response.status_code == 404
    else:
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert response.json()["total"] == 0
