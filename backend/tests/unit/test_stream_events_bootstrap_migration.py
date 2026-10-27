"""Real PostgreSQL regression for the canonical event-stream bootstrap gap.

Set STREAM_EVENTS_TEST_DATABASE_URL to an EMPTY, disposable loopback database
named talklee_stream_events_test_<suffix>, owned by a non-superuser/non-BYPASSRLS
login. Each test rolls back its own DDL and rows. The fixture refuses populated
schemas and never creates/drops a database, changes roles or reads app rows.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.event_emitter import emit_event


MIGRATION = importlib.import_module("Alembic.versions.0073_stream_events_bootstrap")
BACKEND = Path(__file__).resolve().parents[2]
SCHEMA = (BACKEND / "database" / "complete_schema.sql").read_text(encoding="utf-8")
CANONICAL_SQL = SCHEMA.split("-- BEGIN STREAM_EVENTS BOOTSTRAP", 1)[1].split(
    "-- END STREAM_EVENTS BOOTSTRAP", 1
)[0]


def _upgrade_sql(module):
    statements = []
    original = module.op
    try:
        module.op = SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
        module.upgrade()
    finally:
        module.op = original
    return statements


@pytest_asyncio.fixture
async def conn():
    dsn = os.getenv("STREAM_EVENTS_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit STREAM_EVENTS_TEST_DATABASE_URL required")
    parsed = urlsplit(dsn)
    database = unquote(parsed.path.removeprefix("/"))
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not database.startswith("talklee_stream_events_test_")
        or not database.removeprefix("talklee_stream_events_test_").isalnum()
        or set(parse_qs(parsed.query))
        & {"host", "hostaddr", "port", "dbname", "database", "user", "options", "service"}
    ):
        pytest.fail("event-stream regression requires an explicit isolated local test database")
    connection = await asyncpg.connect(dsn, timeout=10)
    transaction = connection.transaction()
    try:
        assert await connection.fetchval("SELECT current_database()") == database
        role = await connection.fetchrow(
            "SELECT rolsuper, rolbypassrls, current_user = session_user AS own_login "
            "FROM pg_roles WHERE rolname = current_user"
        )
        assert role and not role["rolsuper"] and not role["rolbypassrls"] and role["own_login"], (
            "RLS regression requires a restricted owner login"
        )
        assert not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind IN ('r', 'p', 'v', 'm', 'f'))"
        ), "refusing a nonempty test schema"
        await transaction.start()
        await connection.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        await connection.execute("CREATE TABLE public.tenants (id uuid PRIMARY KEY)")
        await connection.execute("SET LOCAL app.bypass_rls = 'true'")
        yield connection
    finally:
        if connection.is_in_transaction():
            await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture(params=["forward_repair", "canonical_floor"])
async def table(conn, request):
    if request.param == "forward_repair":
        for sql in _upgrade_sql(MIGRATION):
            await conn.execute(sql)
    else:
        await conn.execute(CANONICAL_SQL)
        # The supported floor is stamped 0008, then receives canonical RLS
        # and the category repair before the new migration at head.
        for name in ("0013_canonical_rls_policies", "0065_stream_events_call_category"):
            for sql in _upgrade_sql(importlib.import_module(f"Alembic.versions.{name}")):
                await conn.execute(sql)
        for sql in _upgrade_sql(MIGRATION):
            await conn.execute(sql)
    return conn


async def _tenants(connection):
    own, foreign = uuid.uuid4(), uuid.uuid4()
    await connection.execute("INSERT INTO public.tenants VALUES ($1), ($2)", own, foreign)
    return own, foreign


async def _scope(connection, tenant="", bypass=False):
    await connection.execute(
        "SELECT set_config('app.current_tenant_id', $1, true), "
        "set_config('app.bypass_rls', $2, true)",
        str(tenant), str(bypass).lower(),
    )


async def test_runtime_emitter_accepts_all_categories_and_full_payload(table):
    tenant, _ = await _tenants(table)
    related = [uuid.uuid4() for _ in range(3)]
    await _scope(table, tenant)
    for category in ("campaign", "system", "alert", "user_action", "milestone", "call"):
        await emit_event(
            table, tenant_id=tenant, category=category, title=f"synthetic {category}",
            description="local regression", severity="info", related_campaign_id=related[0],
            related_call_id=related[1], actor_user_id=related[2], metadata={"synthetic": True},
        )
    rows = await table.fetch("SELECT * FROM stream_events ORDER BY category")
    assert len(rows) == 6  # emit_event swallows SQL errors: prove actual rows.
    assert {row["category"] for row in rows} == {"campaign", "system", "alert", "user_action", "milestone", "call"}
    for row in rows:
        assert row["id"] and row["description"] == "local regression"
        assert [row["related_campaign_id"], row["related_call_id"], row["actor_user_id"]] == related
        assert json.loads(row["metadata"]) == {"synthetic": True}
        assert (row["expires_at"] - row["created_at"]).days == 90
    indexes = {r["indexname"] for r in await table.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='stream_events'"
    )}
    assert {"idx_se_tenant_recent", "idx_se_tenant_category", "idx_se_expires"} <= indexes


async def test_populated_tenant_isolation_and_cross_tenant_retention(table):
    own, foreign = await _tenants(table)
    for tenant in (own, foreign):
        await _scope(table, tenant)
        await emit_event(table, tenant_id=tenant, category="call", title="retain")
        await table.execute(
            "INSERT INTO stream_events (tenant_id, category, title, expires_at) "
            "VALUES ($1, 'system', 'expired', now() - interval '1 day')", tenant,
        )
    await _scope(table, own)
    assert await table.fetchval("SELECT count(*) FROM stream_events") == 2
    assert await table.fetchval("SELECT count(*) FROM stream_events WHERE tenant_id=$1", foreign) == 0
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with table.transaction():
            await table.execute(
                "INSERT INTO stream_events (tenant_id, category, title) VALUES ($1, 'call', 'wrong tenant')", foreign,
            )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with table.transaction():
            await table.execute("UPDATE stream_events SET tenant_id=$1", foreign)
    await _scope(table)  # blank tenant setting must deny without UUID-cast failure.
    assert await table.fetchval("SELECT count(*) FROM stream_events") == 0
    await _scope(table, bypass=True)
    assert await table.fetchval("SELECT count(*) FROM stream_events") == 4
    assert await table.execute(
        "DELETE FROM stream_events WHERE id IN "
        "(SELECT id FROM stream_events WHERE expires_at < now() LIMIT 5000)"
    ) == "DELETE 2"
    assert await table.fetchval("SELECT count(*) FROM stream_events WHERE title='retain'") == 2
    flags = await table.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='public.stream_events'::regclass"
    )
    assert flags["relrowsecurity"] and flags["relforcerowsecurity"]


async def test_invalid_event_values_and_tenant_reference_are_rejected(table):
    tenant, _ = await _tenants(table)
    for category, severity in (("invalid", "info"), ("call", "invalid")):
        with pytest.raises(asyncpg.CheckViolationError):
            async with table.transaction():
                await table.execute(
                    "INSERT INTO stream_events (tenant_id, category, title, severity) VALUES ($1, $2, 'invalid', $3)",
                    tenant, category, severity,
                )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with table.transaction():
            await table.execute(
                "INSERT INTO stream_events (tenant_id, category, title) VALUES ($1, 'call', 'missing tenant')", uuid.uuid4(),
            )


async def test_existing_table_rows_and_policies_survive_upgrade_and_downgrade(conn):
    # Exercise the real older lineage, not an approximation of its table.
    for name in ("0003_add_stream_events", "0013_canonical_rls_policies", "0065_stream_events_call_category"):
        for sql in _upgrade_sql(importlib.import_module(f"Alembic.versions.{name}")):
            await conn.execute(sql)
    tenant, _ = await _tenants(conn)
    await emit_event(conn, tenant_id=tenant, category="call", title="retained historical event")
    before = await conn.fetchrow(
        "SELECT oid, relowner, relacl::text, relrowsecurity, relforcerowsecurity "
        "FROM pg_class WHERE oid='public.stream_events'::regclass"
    )
    policies = await conn.fetch("SELECT * FROM pg_policies WHERE schemaname='public' AND tablename='stream_events'")
    for _ in range(2):
        for sql in _upgrade_sql(MIGRATION):
            await conn.execute(sql)
    statements = []
    original = MIGRATION.op
    try:
        MIGRATION.op = SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
        MIGRATION.downgrade()
    finally:
        MIGRATION.op = original
    assert not statements  # Never drop a table already owned by the 0003 contract.
    assert before == await conn.fetchrow(
        "SELECT oid, relowner, relacl::text, relrowsecurity, relforcerowsecurity "
        "FROM pg_class WHERE oid='public.stream_events'::regclass"
    )
    assert policies == await conn.fetch("SELECT * FROM pg_policies WHERE schemaname='public' AND tablename='stream_events'")
    assert await conn.fetchval("SELECT count(*) FROM stream_events WHERE title='retained historical event'") == 1
