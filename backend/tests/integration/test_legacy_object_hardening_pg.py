"""0094 on disposable loopback PostgreSQL: owner-rights views and the legacy definer function.

LINEAGE reproduces production's arrangement: tables with forced row-level
security, plain views over them owned by a superuser, and the broken
SECURITY DEFINER log_audit_event function.
"""
import os
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.test_touch_trigger_repair_pg import database, load  # noqa: F401

pytestmark = pytest.mark.integration
SCOPED = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
LINEAGE = (
    "CREATE TABLE suspension_events (id uuid PRIMARY KEY, tenant_id uuid, is_active boolean)",
    "CREATE TABLE tenant_secrets (id uuid PRIMARY KEY, tenant_id uuid, encrypted_value text, is_active boolean)",
    "CREATE TABLE security_events (id uuid PRIMARY KEY, tenant_id uuid, status text)",
    "CREATE TABLE calls (id uuid PRIMARY KEY, tenant_id uuid, is_test boolean)",
    *(statement for table in ("suspension_events", "tenant_secrets", "security_events", "calls") for statement in (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_rows ON {table} USING ({SCOPED})",
    )),
    "CREATE VIEW active_suspensions AS SELECT id, tenant_id FROM suspension_events WHERE is_active",
    "CREATE VIEW expiring_secrets AS SELECT id, tenant_id, encrypted_value FROM tenant_secrets WHERE is_active",
    "CREATE VIEW open_security_events AS SELECT id, tenant_id FROM security_events WHERE status = 'open'",
    "CREATE VIEW billable_calls AS SELECT id, tenant_id FROM calls WHERE NOT is_test",
    """CREATE FUNCTION log_audit_event(p_user_id uuid, p_tenant_id uuid, p_action_type character varying,
           p_target_type character varying, p_target_id uuid, p_description text, p_metadata jsonb,
           p_ip_address inet, p_user_agent text) RETURNS uuid
       LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN RETURN NULL; END; $$""",
)
VIEWS = ("active_suspensions", "billable_calls", "expiring_secrets", "open_security_events")


def migration():
    module = load("0094_harden_legacy_db_objects")
    assert (module.revision, module.down_revision) == ("0094_harden_legacy_db_objects", "0093_abuse_event_columns")
    return module


async def seed(db):
    """Two tenants' rows in every table, and a restricted reader scoped to the first."""
    mine, other = uuid4(), uuid4()
    reader = "legacy_reader_" + uuid4().hex
    async with db.pool.acquire() as conn:
        for tenant in (mine, other):
            await conn.execute("INSERT INTO suspension_events VALUES ($1, $2, true)", uuid4(), tenant)
            await conn.execute("INSERT INTO tenant_secrets VALUES ($1, $2, 'ciphertext', true)", uuid4(), tenant)
            await conn.execute("INSERT INTO security_events VALUES ($1, $2, 'open')", uuid4(), tenant)
            await conn.execute("INSERT INTO calls VALUES ($1, $2, false)", uuid4(), tenant)
        await conn.execute(f'CREATE ROLE "{reader}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await conn.execute(f'GRANT SELECT ON suspension_events, tenant_secrets, security_events, calls, {", ".join(VIEWS)} TO "{reader}"')
    return mine, reader


async def visible_tenants(db, reader, tenant, view):
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{reader}"')
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
            return {row["tenant_id"] for row in await conn.fetch(f"SELECT tenant_id FROM {view}")}


async def test_views_stop_bypassing_row_level_security_and_the_definer_function_is_gone(database):  # noqa: F811
    await database.build(LINEAGE)
    mine, reader = await seed(database)
    for view in VIEWS:
        assert len(await visible_tenants(database, reader, mine, view)) == 2, f"{view} should leak before 0094"

    await database.apply(migration())
    await database.apply(migration())  # idempotent

    for view in VIEWS:
        assert await visible_tenants(database, reader, mine, view) == {mine}, view
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pg_proc WHERE proname='log_audit_event'") == 0
        options = await conn.fetch("SELECT relname, reloptions FROM pg_class WHERE relname = ANY($1::text[])", list(VIEWS))
        assert {row["relname"]: row["reloptions"] for row in options} == {view: ["security_invoker=true"] for view in VIEWS}


async def test_downgrade_returns_views_to_owner_rights_and_never_restores_the_function(database):  # noqa: F811
    await database.build(LINEAGE)
    mine, reader = await seed(database)
    await database.apply(migration())
    await database.apply(migration(), "downgrade")
    assert len(await visible_tenants(database, reader, mine, "active_suspensions")) == 2
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pg_proc WHERE proname='log_audit_event'") == 0


async def test_a_fresh_install_without_the_legacy_objects_is_unchanged(database):  # noqa: F811
    await database.build(("CREATE TABLE security_events (id uuid PRIMARY KEY)",
                          "CREATE TABLE open_security_events (id uuid PRIMARY KEY)"))  # a table, not a view
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT reloptions FROM pg_class WHERE relname='open_security_events'") is None
    with pytest.raises(asyncpg.UndefinedTableError):
        async with database.pool.acquire() as conn:
            await conn.execute("SELECT 1 FROM active_suspensions")


async def test_every_view_of_the_migrated_database_runs_with_the_readers_rights():
    """Guard: a view added by a later migration must set security_invoker too."""
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit loopback TEST_DATABASE_URL required; real PostgreSQL not run")
    conn = await asyncpg.connect(dsn, timeout=5)
    try:
        owner_rights = await conn.fetch("""SELECT relname FROM pg_class
            WHERE relkind = 'v' AND relnamespace = 'public'::regnamespace
              AND NOT coalesce(reloptions @> ARRAY['security_invoker=true'], false) ORDER BY relname""")
    finally:
        await conn.close()
    assert [row["relname"] for row in owner_rights] == []
