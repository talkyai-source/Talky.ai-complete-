"""0072 adopts only the documented July table, using rollback-only real DDL."""
from datetime import datetime, timezone
from importlib import import_module
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import protected_snapshot, reviewed_database

MIGRATION = import_module("Alembic.versions.0072_admin_incidents")
LEGACY_SQL = (Path(__file__).resolve().parents[2] / "database/migrations/20260706_add_admin_health_tables.sql").read_text(
    encoding="utf-8"
).split("-- 2) webhook_endpoints", 1)[0]
pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def adoption_db():
    configured = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not configured:
        pytest.skip("Explicit disposable migration database required")
    conn = await asyncpg.connect(reviewed_database(configured, os.getenv("GITHUB_ACTIONS")), command_timeout=30)
    before = None
    tx = None
    report = {"protected_unchanged": False}
    RUN_EVIDENCE.append(report)
    try:
        before = await protected_snapshot(conn)
        tx = conn.transaction()
        await tx.start()
        await conn.execute("SET LOCAL lock_timeout='5s'")
        await conn.execute("SET LOCAL app.bypass_rls='on'")
        original = await conn.fetchval("SELECT to_regclass('public.incidents')::oid")
        if original is not None:
            # Preserve the existing table and every index in this transaction;
            # rollback restores their original names/OIDs/data even on failure.
            suffix = uuid4().hex
            indexes = await conn.fetch("SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid=$1", original)
            for index, row in enumerate(indexes):
                quoted = row["relname"].replace('"', '""')
                await conn.execute(f'ALTER INDEX public."{quoted}" RENAME TO "saved_0072_{suffix}_{index}"')
            await conn.execute(f'ALTER TABLE public.incidents RENAME TO "saved_0072_{suffix}"')
        yield SimpleNamespace(conn=conn, report=report)
    finally:
        try:
            if tx is not None:
                await tx.rollback()
            if before is not None:
                report["protected_unchanged"] = await protected_snapshot(conn) == before
                assert report["protected_unchanged"]
        finally:
            await conn.close()


async def upgrade(conn):
    for statement in MIGRATION.UPGRADE_STATEMENTS:
        await conn.execute(statement)


async def legacy(conn):
    await conn.execute(LEGACY_SQL)


async def saved(conn):
    return await conn.fetchval("""SELECT jsonb_build_object(
        'table', (SELECT to_jsonb(c) FROM pg_class c WHERE oid='public.incidents'::regclass),
        'columns', (SELECT jsonb_agg(to_jsonb(a) ORDER BY attnum) FROM pg_attribute a
            WHERE attrelid='public.incidents'::regclass AND attnum>0),
        'defaults', (SELECT jsonb_agg(to_jsonb(d) ORDER BY oid) FROM pg_attrdef d
            WHERE adrelid='public.incidents'::regclass),
        'constraints', (SELECT jsonb_agg(to_jsonb(c) ORDER BY oid) FROM pg_constraint c
            WHERE conrelid='public.incidents'::regclass),
        'indexes', (SELECT jsonb_agg(to_jsonb(i) ORDER BY indexrelid) FROM pg_index i
            WHERE indrelid='public.incidents'::regclass),
        'policies', (SELECT jsonb_agg(to_jsonb(p) ORDER BY oid) FROM pg_policy p
            WHERE polrelid='public.incidents'::regclass),
        'triggers', (SELECT jsonb_agg(to_jsonb(t) ORDER BY oid) FROM pg_trigger t
            WHERE tgrelid='public.incidents'::regclass),
        'rows', (SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM public.incidents t))""")


async def rows(conn):
    return await conn.fetchval("SELECT jsonb_agg(to_jsonb(t) ORDER BY id::text) FROM public.incidents t")


async def assert_contract(conn):
    result = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.incidents'::regclass")
    assert tuple(result.values()) == (True, True)
    assert await conn.fetchval("SELECT count(*) FROM pg_policy WHERE polrelid='public.incidents'::regclass AND polname='incidents_platform_only'") == 1
    assert await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conrelid='public.incidents'::regclass AND contype='c'") == 6
    assert await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid='public.incidents'::regclass AND attname=ANY($1::text[]) AND atttypid='uuid'::regtype", ["id", "acknowledged_by", "resolved_by"]) == 3
    identifier = await conn.fetchval("INSERT INTO public.incidents(title,severity) VALUES('Synthetic default receipt','info') RETURNING id")
    assert identifier is not None
    await conn.execute("DELETE FROM public.incidents WHERE id=$1", identifier)


async def test_fresh_path_retains_durable_contract(adoption_db):
    await upgrade(adoption_db.conn)
    await assert_contract(adoption_db.conn)
    assert not await adoption_db.conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.incidents'::regclass AND attname='created_at')")


async def test_empty_july_table_is_adopted_in_place(adoption_db):
    conn = adoption_db.conn
    await legacy(conn)
    before = await conn.fetchrow("SELECT oid,relowner FROM pg_class WHERE oid='public.incidents'::regclass")
    await upgrade(conn)
    assert await conn.fetchrow("SELECT oid,relowner FROM pg_class WHERE oid='public.incidents'::regclass") == before
    assert await conn.fetchval("SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname=ANY($1::text[])", ["idx_incidents_status", "idx_incidents_severity", "idx_incidents_triggered_at"]) == 3
    await assert_contract(conn)
    assert await conn.fetchval("SELECT count(*) FROM incidents") == 0


async def test_valid_populated_history_preserves_identifiers_and_receipts(adoption_db):
    conn = adoption_db.conn
    await legacy(conn)
    timestamp = datetime(2026, 7, 6, 12, 3, 4, 567890, tzinfo=timezone.utc)
    for status, acknowledged in [("open", False), ("acknowledged", True), ("resolved", False), ("resolved", True)]:
        await conn.execute("""INSERT INTO incidents(id,title,severity,status,description,triggered_at,created_at,
            acknowledged_at,acknowledged_by,resolved_at,resolved_by)
            VALUES($1,'Preserved July history','warning',$2,'Original description',$3,$3,$4,$5,$6,$7)""",
            str(uuid4()), status, timestamp, timestamp if acknowledged else None,
            str(uuid4()) if acknowledged else None, timestamp if status == "resolved" else None,
            str(uuid4()) if status == "resolved" else None)
    before = await rows(conn)
    await upgrade(conn)
    assert await rows(conn) == before
    await assert_contract(conn)
    assert await rows(conn) == before
    adoption_db.report["four_saved_history_states_unchanged"] = True


@pytest.mark.parametrize("bad", ["id", "actor", "duplicate_uuid", "triggered_at", "severity", "status",
    "ack_time_only", "ack_actor_only", "resolve_time_only", "resolve_actor_only", "ack_receipt", "resolve_receipt"])
async def test_incompatible_history_rejects_atomically(adoption_db, bad):
    conn = adoption_db.conn
    await legacy(conn)
    identifier = str(uuid4())
    await conn.execute("INSERT INTO incidents(id,title) VALUES($1,'Preserved rejected history')", identifier)
    updates = {
        "id": "id='not-a-uuid'", "actor": "acknowledged_by='not-a-uuid',acknowledged_at=now()",
        "triggered_at": "triggered_at=NULL", "severity": "severity='unsupported'", "status": "status='unsupported'",
        "ack_time_only": "acknowledged_at=now()", "ack_actor_only": "acknowledged_by=gen_random_uuid()::text",
        "resolve_time_only": "resolved_at=now()", "resolve_actor_only": "resolved_by=gen_random_uuid()::text",
        "ack_receipt": "status='acknowledged'", "resolve_receipt": "status='resolved'",
    }
    if bad == "duplicate_uuid":
        await conn.execute("INSERT INTO incidents(id,title) VALUES($1,'Equivalent UUID rejected')", "{" + identifier + "}")
    else:
        await conn.execute("UPDATE incidents SET " + updates[bad])
    before = await saved(conn)
    with pytest.raises((asyncpg.InvalidTextRepresentationError, asyncpg.UniqueViolationError,
                        asyncpg.NotNullViolationError, asyncpg.CheckViolationError)):
        async with conn.transaction():
            await upgrade(conn)
    assert await saved(conn) == before
    assert await conn.fetchval("SELECT to_regclass('pg_temp.admin_0072_incidents_reference')") is None


@pytest.mark.parametrize("unknown", ["policy", "column", "default", "check", "index", "rls", "trigger"])
async def test_unknown_shape_rejects_without_discarding_objects(adoption_db, unknown):
    conn = adoption_db.conn
    await legacy(conn)
    changes = {
        "policy": "CREATE POLICY unreviewed ON incidents USING(TRUE)",
        "column": "ALTER TABLE incidents ADD COLUMN unreviewed text",
        "default": "ALTER TABLE incidents ALTER COLUMN severity SET DEFAULT 'warning'",
        "check": "ALTER TABLE incidents ADD CONSTRAINT unreviewed CHECK(length(title)>0)",
        "index": "CREATE INDEX unreviewed ON incidents(title)",
        "rls": "ALTER TABLE incidents ENABLE ROW LEVEL SECURITY",
        "trigger": """CREATE FUNCTION pg_temp.incidents_noop() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
            CREATE TRIGGER unreviewed BEFORE INSERT ON incidents FOR EACH ROW EXECUTE FUNCTION pg_temp.incidents_noop()""",
    }
    await conn.execute(changes[unknown])
    before = await saved(conn)
    with pytest.raises(asyncpg.RaiseError, match="0072 refuses unknown legacy"):
        async with conn.transaction():
            await upgrade(conn)
    assert await saved(conn) == before


async def test_downgrade_retains_even_empty_adopted_table(adoption_db, monkeypatch):
    conn = adoption_db.conn
    await legacy(conn)
    await upgrade(conn)
    before = await saved(conn)
    statements = []
    monkeypatch.setattr(MIGRATION, "op", SimpleNamespace(execute=statements.append))
    MIGRATION.downgrade()
    with pytest.raises(asyncpg.RaiseError, match="pre-existing July incidents"):
        async with conn.transaction():
            for statement in statements:
                await conn.execute(statement)
    assert await saved(conn) == before
