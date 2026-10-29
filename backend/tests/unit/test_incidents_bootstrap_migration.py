"""Real PostgreSQL coverage for incident bootstrap and platform isolation.

Reuse the explicit empty-loopback/non-superuser fixture: every test rolls back
its DDL and rows, and never changes roles or an existing application database.
"""

import importlib
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from tests.unit.test_stream_events_bootstrap_migration import (
    BACKEND,
    SCHEMA,
    _upgrade_sql,
    conn,  # noqa: F401 -- explicitly imports the guarded pytest fixture.
)

MIGRATION = importlib.import_module("Alembic.versions.0074_incidents_bootstrap")
CANONICAL_SQL = SCHEMA.split("-- BEGIN INCIDENTS BOOTSTRAP", 1)[1].split(
    "-- END INCIDENTS BOOTSTRAP", 1
)[0]


@pytest_asyncio.fixture(params=["forward_repair", "canonical_floor"])
async def table(conn, request):
    if request.param == "canonical_floor":
        await conn.execute(CANONICAL_SQL)
        # The canonical floor is stamped 0008; canonical tenant policies must
        # leave the platform-only incident policy intact during the upgrade.
        canonical_rls = importlib.import_module("Alembic.versions.0013_canonical_rls_policies")
        for sql in _upgrade_sql(canonical_rls):
            await conn.execute(sql)
    for sql in _upgrade_sql(MIGRATION):
        await conn.execute(sql)
    return conn


async def _scope(connection, tenant="", bypass=False):
    await connection.execute(
        "SELECT set_config('app.current_tenant_id', $1, true), "
        "set_config('app.bypass_rls', $2, true)",
        str(tenant), str(bypass).lower(),
    )


async def test_persisted_list_and_acknowledge_resolve_contract(table):
    await table.execute(
        "INSERT INTO incidents (id,title,severity,status,description,triggered_at) "
        "VALUES ('test-critical','Storage unavailable','critical','open','synthetic check',now()), "
        "('test-info','Worker recovered','info','open','synthetic check',now())"
    )
    assert await table.fetchval("SELECT count(*) FROM incidents WHERE status='open'") == 2
    assert await table.fetchval(
        "SELECT count(*) FROM incidents WHERE severity='critical' AND status='open'"
    ) == 1
    listed = await table.fetch(
        "SELECT * FROM incidents WHERE status='open' AND severity='critical' "
        "ORDER BY triggered_at DESC LIMIT 20 OFFSET 0"
    )
    assert len(listed) == 1 and listed[0]["id"] == "test-critical"
    assert listed[0]["created_at"] and listed[0]["description"] == "synthetic check"
    await table.execute(
        "UPDATE incidents SET status='acknowledged',acknowledged_at=now(),"
        "acknowledged_by='operator-test' WHERE id='test-critical'"
    )
    await table.execute(
        "UPDATE incidents SET status='resolved',resolved_at=now(),resolved_by='operator-test' "
        "WHERE id='test-critical'"
    )
    row = await table.fetchrow("SELECT * FROM incidents WHERE id='test-critical'")
    assert row["status"] == "resolved"
    assert row["acknowledged_at"] and row["resolved_at"]
    assert row["acknowledged_by"] == row["resolved_by"] == "operator-test"
    indexes = {r["indexname"] for r in await table.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='incidents'"
    )}
    assert {"idx_incidents_status", "idx_incidents_severity", "idx_incidents_triggered_at"} <= indexes


async def test_populated_incidents_are_invisible_and_unmodifiable_in_tenant_context(table):
    await table.execute("INSERT INTO incidents (id,title) VALUES ('platform-only','Private operator incident')")
    for tenant in (uuid.uuid4(), uuid.uuid4(), ""):
        await _scope(table, tenant)
        assert await table.fetchval("SELECT count(*) FROM incidents") == 0
        assert await table.execute("UPDATE incidents SET title='tenant overwrite'") == "UPDATE 0"
        assert await table.execute("DELETE FROM incidents") == "DELETE 0"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with table.transaction():
                await table.execute("INSERT INTO incidents (id,title) VALUES ('forbidden','tenant insert')")
    await table.execute("SELECT set_config('app.bypass_rls', '', true)")
    assert await table.fetchval("SELECT count(*) FROM incidents") == 0
    await _scope(table, bypass=True)
    assert await table.fetchval("SELECT title FROM incidents WHERE id='platform-only'") == "Private operator incident"
    flags = await table.fetchrow(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.incidents'::regclass"
    )
    assert flags["relrowsecurity"] and flags["relforcerowsecurity"]


@pytest.mark.parametrize("custom_policy", [False, True])
async def test_existing_manual_table_rows_acl_and_policies_are_preserved(conn, custom_policy):
    manual = (BACKEND / "database/migrations/20260706_add_admin_health_tables.sql").read_text(encoding="utf-8")
    legacy = manual.split("CREATE TABLE IF NOT EXISTS incidents", 1)[1].split("-- 2) webhook_endpoints", 1)[0]
    await conn.execute("CREATE TABLE IF NOT EXISTS incidents" + legacy)
    await conn.execute("INSERT INTO incidents (id,title) VALUES ('retained','Existing operator evidence')")
    await conn.execute("GRANT SELECT ON incidents TO PUBLIC")
    if custom_policy:
        await conn.execute("ALTER TABLE incidents ENABLE ROW LEVEL SECURITY")
        await conn.execute("CREATE POLICY retained_operator_policy ON incidents FOR SELECT USING (true)")
    metadata_sql = (
        "SELECT oid,relowner,relacl::text,relrowsecurity,relforcerowsecurity "
        "FROM pg_class WHERE oid='public.incidents'::regclass"
    )
    before = await conn.fetchrow(metadata_sql)
    policies = await conn.fetch("SELECT * FROM pg_policies WHERE schemaname='public' AND tablename='incidents'")
    for _ in range(2):
        for sql in _upgrade_sql(MIGRATION):
            await conn.execute(sql)
    statements, original = [], MIGRATION.op
    try:
        MIGRATION.op = SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
        MIGRATION.downgrade()
    finally:
        MIGRATION.op = original
    assert not statements
    assert before == await conn.fetchrow(metadata_sql)
    assert policies == await conn.fetch("SELECT * FROM pg_policies WHERE schemaname='public' AND tablename='incidents'")
    assert await conn.fetchval("SELECT title FROM incidents WHERE id='retained'") == "Existing operator evidence"
