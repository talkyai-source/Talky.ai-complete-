"""Execute the mounted history route's real filter/order/window clauses."""
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import dependencies
from app.api.v1.endpoints import calls

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
IDS = [f"33333333-3333-4333-8333-{i:012d}" for i in range(1, 6)]
CAMPAIGNS = [f"44444444-4444-4444-8444-{i:012d}" for i in range(1, 4)]


class HistoryDatabase:
    def __init__(self):
        self.sql = sqlite3.connect(":memory:")
        self.sql.row_factory = sqlite3.Row
        self.sql.executescript("""
            CREATE TABLE campaigns(id TEXT,tenant_id TEXT,name TEXT);
            CREATE TABLE calls(id TEXT,tenant_id TEXT,created_at TEXT,phone_number TEXT,
                caller_ani TEXT,called_did TEXT,talklee_call_id TEXT,status TEXT,direction TEXT,campaign_id TEXT);
        """)
        self.sql.executemany("INSERT INTO campaigns VALUES(?,?,?)", [
            (CAMPAIGNS[0], TENANT, "Rate100% renewal"), (CAMPAIGNS[1], TENANT, "London"),
            (CAMPAIGNS[2], OTHER, "London"),
        ])
        for index, tenant, hour, phone, campaign in [
            (0, TENANT, "09", "+14155552671", CAMPAIGNS[0]),
            (1, TENANT, "10", "+442079460958", CAMPAIGNS[1]),
            (2, TENANT, "10", "+61293744000", CAMPAIGNS[1]),
            (3, OTHER, "11", "+442079460958", CAMPAIGNS[2]),
        ]:
            self.insert(index, tenant, hour, phone, campaign)
        self.reads, self.counts = [], []
        self.pool = self

    def insert(self, index, tenant, hour, phone, campaign):
        self.sql.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?)", (
            IDS[index], tenant, f"2026-11-06T{hour}:00:00.000000+00:00", phone,
            None, None, None, "ended", "outbound", campaign,
        ))

    @asynccontextmanager
    async def acquire(self, **kwargs):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, *_args):
        pass  # PostgreSQL transaction-local GUC; filtering executes below.

    def query(self, query, args, *, count=False):
        # Existing projections have separate tests. Execute the actual main
        # predicate/order/limit, adapting only PG placeholders, ILIKE and casts.
        tail = query.rsplit("FROM calls c", 1)[1]
        select = "COUNT(*)" if count else "c.*, camp.name AS campaign_name"
        if not count and "JOIN campaigns" not in tail:
            raise AssertionError("Missing tenant-owned campaign projection")
        statement = "SELECT " + select + " FROM calls c" + tail
        statement = re.sub(r"::(?:uuid|text|timestamptz)", "", statement)
        statement = re.sub(r"\bILIKE\b", "LIKE", statement)
        # PostgreSQL DESC defaults to NULLS FIRST; SQLite defaults to LAST.
        # Preserve explicit source ordering, and spell out PG's default when
        # omitted so this harness cannot hide nullable cursor boundaries.
        statement = re.sub(
            r"\b(c\.(?:created_at|id))\s+(ASC|DESC)\b(?!\s+NULLS\b)",
            lambda match: f"{match[1]} {match[2]} NULLS {'FIRST' if match[2].upper() == 'DESC' else 'LAST'}",
            statement, flags=re.IGNORECASE,
        )
        bound = {str(i): value.isoformat(timespec="microseconds") if isinstance(value, datetime)
                 else str(value) if isinstance(value, UUID) else value for i, value in enumerate(args, 1)}
        return self.sql.execute(statement, bound)

    async def fetch(self, query, *args):
        self.reads.append((query, args))
        return [defaultdict(lambda: None, dict(row)) for row in self.query(query, args)]

    async def fetchval(self, query, *args):
        self.counts.append((query, args))
        return self.query(query, args, count=True).fetchone()[0]


@pytest.fixture
def database():
    db = HistoryDatabase()
    yield db
    db.sql.close()


async def request(db, params=None, tenant=TENANT):
    user = SimpleNamespace(id=tenant, tenant_id=tenant, role="tenant_admin")
    app = FastAPI()
    app.include_router(calls.router, prefix="/api/v1")
    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    app.dependency_overrides[calls._require_calls_read] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        return await client.get("/api/v1/calls/", params=params)


@pytest.mark.asyncio
@pytest.mark.parametrize("search,expected", [
    ("London", {IDS[1], IDS[2]}), ("2079460958", {IDS[1]}),
    ("%", {IDS[0]}), ("nothing-matches", set()), ("' OR true --", set()),
])
async def test_search_filters_rows_and_count_inside_the_authenticated_tenant(database, search, expected):
    response = await request(database, {"search": search})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["id"] for row in body["items"]} == expected
    assert body["total"] == len(expected)


@pytest.mark.asyncio
async def test_cursor_pages_keep_timestamp_ties_and_ignore_new_rows_without_offset_or_count(database):
    first = await request(database, {"pagination": "cursor", "page_size": 2})
    assert first.status_code == 200, first.text
    first = first.json()
    assert [row["id"] for row in first["items"]] == [IDS[2], IDS[1]]
    assert first["total"] is None and first["next_cursor"]
    database.insert(4, TENANT, "12", "+14155552672", CAMPAIGNS[0])
    second = await request(database, {"cursor": first["next_cursor"], "page_size": 2})
    assert second.status_code == 200, second.text
    assert [row["id"] for row in second.json()["items"]] == [IDS[0]]
    assert second.json()["next_cursor"] is None
    assert not database.counts
    assert all(" OFFSET " not in " ".join(sql.split()) for sql, _ in database.reads)


@pytest.mark.asyncio
async def test_cursor_cannot_be_reused_for_a_different_tenant_or_filter(database):
    first = (await request(database, {"pagination": "cursor", "page_size": 1})).json()
    cursor = first["next_cursor"]
    previous_reads = len(database.reads)
    for params, tenant in [({"cursor": cursor}, OTHER), ({"cursor": cursor, "search": "London"}, TENANT)]:
        response = await request(database, params, tenant)
        assert response.status_code == 422
    assert len(database.reads) == previous_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"cursor": "not-a-cursor"}, {"pagination": "cursor", "page": 2},
    {"search": "x" * 201}, {"search": "bad\x00search"},
])
async def test_invalid_cursor_or_search_is_refused_before_storage(database, params):
    response = await request(database, params)
    assert response.status_code == 422, response.text
    assert not database.reads and not database.counts


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [1, 2, 3])
async def test_cursor_preserves_null_timestamp_rows_and_crosses_the_null_boundary(database, page_size):
    null_ids = [f"55555555-5555-4555-8555-{number:012d}" for number in range(1, 4)]
    foreign_null = "55555555-5555-4555-8555-999999999999"
    database.sql.executemany("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?)", [
        (call_id, tenant, None, "+14155552670", None, None, None, "ended", "outbound", campaign)
        for call_id, tenant, campaign in [
            *((call_id, TENANT, CAMPAIGNS[0]) for call_id in null_ids),
            (foreign_null, OTHER, CAMPAIGNS[2]),
        ]
    ])
    expected = list(reversed(null_ids)) + [IDS[2], IDS[1], IDS[0]]
    # Retain PostgreSQL's existing DESC ordering rather than accidentally
    # passing because SQLite placed these nullable rows after every timestamp.
    offset = await request(database, {"page_size": 100})
    assert offset.status_code == 200, offset.text
    assert [item["id"] for item in offset.json()["items"]] == expected
    database.reads.clear()
    database.counts.clear()
    seen, cursors, cursor = [], set(), None
    for _ in range(len(expected) + 1):
        params = {"pagination": "cursor", "page_size": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        response = await request(database, params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] is None
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert cursor not in cursors, "The continuation must advance across NULL timestamps"
        cursors.add(cursor)
    else:
        pytest.fail("Nullable history pagination did not terminate")
    assert seen == expected, "Every owned row must appear exactly once, including NULL timestamp ties"
    assert foreign_null not in seen
    assert not database.counts
    assert all(" OFFSET " not in " ".join(sql.split()) for sql, _ in database.reads)


def test_cursor_index_migration_is_guarded_and_linear():
    path = Path(__file__).resolve().parents[2] / "Alembic/versions/0091_call_history_cursor.py"
    assert path.is_file(), "Call history lacks its tenant/created_at/id cursor index migration"
    spec = importlib.util.spec_from_file_location("qa_call_cursor_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0090_campaign_alerts"
    assert len(migration.revision) <= 32
    statements = []
    migration.op = SimpleNamespace(execute=statements.append)
    migration.upgrade()
    sql = " ".join(" ".join(statements).lower().split())
    assert "create index if not exists" in sql
    assert "(tenant_id, created_at desc, id desc)" in sql
    assert "concurrently" not in sql and "to_regclass(" in sql
    statements.clear()
    migration.downgrade()
    assert "drop index" in " ".join(statements).lower()
