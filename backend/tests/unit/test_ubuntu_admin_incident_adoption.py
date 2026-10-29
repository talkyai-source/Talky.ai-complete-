"""Real rollback-only PostgreSQL proof for all supported incident bootstrap shapes.

The shared guard requires an explicit empty, disposable loopback database and
a restricted owner login. These cases never access the pinned Admin fixtures.
"""
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests.integration import test_legacy_incidents_adoption as adoption
from tests.unit.test_incidents_bootstrap_migration import CANONICAL_SQL, MIGRATION
from tests.unit.test_stream_events_bootstrap_migration import (
    _upgrade_sql,
    conn,  # noqa: F401 -- explicit guarded empty-database fixture.
)


@pytest_asyncio.fixture(params=["legacy_manual", "ubuntu_forward", "canonical_floor"])
async def source(conn, request):
    if request.param == "legacy_manual":
        await adoption.legacy(conn)
    elif request.param == "canonical_floor":
        await conn.execute(CANONICAL_SQL)
    else:
        for statement in _upgrade_sql(MIGRATION):
            await conn.execute(statement)
    return conn


async def metadata(conn):
    return await conn.fetchrow("SELECT oid,relowner,relacl::text FROM pg_class WHERE oid='public.incidents'::regclass")


async def policies(conn):
    return await conn.fetch("SELECT to_jsonb(p)::text AS value FROM pg_policy p WHERE polrelid='public.incidents'::regclass ORDER BY polname")


async def test_fresh_admin_creation_uses_same_durable_contract(conn):
    await adoption.upgrade(conn)
    await adoption.assert_contract(conn)


async def test_supported_history_preserves_identifiers_acl_owner_and_protected_policy(source):
    timestamp = datetime(2026, 10, 28, 12, 3, 4, 567890, tzinfo=timezone.utc)
    for status, acknowledged in [("open", False), ("acknowledged", True), ("resolved", False), ("resolved", True)]:
        await source.execute("""INSERT INTO incidents(id,title,severity,status,description,triggered_at,created_at,
            acknowledged_at,acknowledged_by,resolved_at,resolved_by)
            VALUES($1,'Preserved bootstrap history','warning',$2,'Original description',$3,$3,$4,$5,$6,$7)""",
            str(uuid4()), status, timestamp, timestamp if acknowledged else None,
            str(uuid4()) if acknowledged else None, timestamp if status == "resolved" else None,
            str(uuid4()) if status == "resolved" else None)
    await source.execute("GRANT SELECT ON incidents TO PUBLIC")
    original_metadata, original_policies, original_rows = await metadata(source), await policies(source), await adoption.rows(source)
    await adoption.upgrade(source)
    assert await metadata(source) == original_metadata
    assert await adoption.rows(source) == original_rows
    if original_policies:
        assert await policies(source) == original_policies  # Includes the original policy OID.
    await adoption.assert_contract(source)
    assert await adoption.rows(source) == original_rows
    await source.execute("SET LOCAL app.bypass_rls='false'")
    assert await source.fetchval("SELECT count(*) FROM incidents") == 0
    assert await source.execute("UPDATE incidents SET title='unprivileged edit'") == "UPDATE 0"
    assert await source.execute("DELETE FROM incidents") == "DELETE 0"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with source.transaction():
            await source.execute("INSERT INTO incidents(title,severity) VALUES('Unprivileged insert','info')")


@pytest.mark.parametrize("bad", ["id", "actor", "duplicate_uuid", "triggered_at", "severity", "status",
    "ack_time_only", "ack_actor_only", "resolve_time_only", "resolve_actor_only", "ack_receipt", "resolve_receipt"])
async def test_incompatible_history_rolls_back_with_policy_and_data_intact(source, bad):
    identifier = str(uuid4())
    await source.execute("INSERT INTO incidents(id,title) VALUES($1,'Preserved rejected bootstrap history')", identifier)
    updates = {
        "id": "id='not-a-uuid'", "actor": "acknowledged_by='not-a-uuid',acknowledged_at=now()",
        "triggered_at": "triggered_at=NULL", "severity": "severity='unsupported'", "status": "status='unsupported'",
        "ack_time_only": "acknowledged_at=now()", "ack_actor_only": "acknowledged_by=gen_random_uuid()::text",
        "resolve_time_only": "resolved_at=now()", "resolve_actor_only": "resolved_by=gen_random_uuid()::text",
        "ack_receipt": "status='acknowledged'", "resolve_receipt": "status='resolved'",
    }
    if bad == "duplicate_uuid":
        await source.execute("INSERT INTO incidents(id,title) VALUES($1,'Equivalent UUID rejected')", "{" + identifier + "}")
    else:
        await source.execute("UPDATE incidents SET " + updates[bad])
    before = await adoption.saved(source)
    with pytest.raises((asyncpg.InvalidTextRepresentationError, asyncpg.UniqueViolationError,
                        asyncpg.NotNullViolationError, asyncpg.CheckViolationError)):
        async with source.transaction():
            await adoption.upgrade(source)
    assert await adoption.saved(source) == before
    assert await source.fetchval("SELECT to_regclass('pg_temp.admin_0072_incidents_reference')") is None


@pytest.mark.parametrize("unknown", ["using", "with_check", "name", "command", "roles", "permissive",
    "extra_policy", "rls", "force", "column", "default", "check", "index", "trigger", "dependency"])
async def test_unknown_protected_shapes_reject_atomically(conn, unknown):
    for statement in _upgrade_sql(MIGRATION):
        await conn.execute(statement)
    predicate = "COALESCE(NULLIF(current_setting('app.bypass_rls', true), '')::boolean, false)"
    changes = {
        "using": "ALTER POLICY incidents_platform_only ON incidents USING(TRUE)",
        "with_check": "ALTER POLICY incidents_platform_only ON incidents WITH CHECK(TRUE)",
        "name": "ALTER POLICY incidents_platform_only ON incidents RENAME TO unreviewed",
        "command": "DROP POLICY incidents_platform_only ON incidents; CREATE POLICY incidents_platform_only ON incidents FOR UPDATE "
                   f"USING ({predicate}) WITH CHECK ({predicate})",
        "roles": "ALTER POLICY incidents_platform_only ON incidents TO CURRENT_USER",
        "permissive": "DROP POLICY incidents_platform_only ON incidents; CREATE POLICY incidents_platform_only ON incidents AS RESTRICTIVE "
                      f"USING ({predicate}) WITH CHECK ({predicate})",
        "extra_policy": "CREATE POLICY unreviewed ON incidents USING(TRUE)",
        "rls": "ALTER TABLE incidents DISABLE ROW LEVEL SECURITY",
        "force": "ALTER TABLE incidents NO FORCE ROW LEVEL SECURITY",
        "column": "ALTER TABLE incidents ADD COLUMN unreviewed text",
        "default": "ALTER TABLE incidents ALTER COLUMN severity SET DEFAULT 'warning'",
        "check": "ALTER TABLE incidents ADD CONSTRAINT unreviewed CHECK(length(title)>0)",
        "index": "CREATE INDEX unreviewed ON incidents(title)",
        "trigger": """CREATE FUNCTION pg_temp.incidents_noop() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
            CREATE TRIGGER unreviewed BEFORE INSERT ON incidents FOR EACH ROW EXECUTE FUNCTION pg_temp.incidents_noop()""",
        "dependency": "CREATE TABLE incident_external_reference(id text REFERENCES incidents(id))",
    }
    await conn.execute(changes[unknown])
    before = await adoption.saved(conn)
    with pytest.raises(asyncpg.RaiseError, match="0072 refuses unknown legacy"):
        async with conn.transaction():
            await adoption.upgrade(conn)
    assert await adoption.saved(conn) == before
    assert await conn.fetchval("SELECT to_regclass('pg_temp.admin_0072_incidents_reference')") is None
