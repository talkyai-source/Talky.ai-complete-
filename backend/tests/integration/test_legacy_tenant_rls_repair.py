"""Known legacy policy repair on a disposable migrated DB, always rolled back."""
from importlib import import_module
import os
import re
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import protected_snapshot, reviewed_database

MIGRATION = import_module("Alembic.versions.0077_legacy_tenant_rls_repair")
pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


async def policies(conn):
    return [dict(row) for row in await conn.fetch("""
        SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,p.oid AS policy_oid,
               p.polname,p.polcmd::text,p.polpermissive,p.polroles,
               pg_get_expr(p.polqual,p.polrelid) AS qual,
               pg_get_expr(p.polwithcheck,p.polrelid) AS with_check
        FROM pg_class c JOIN pg_policy p ON p.polrelid=c.oid
        WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY($1::text[])
        ORDER BY c.relname,p.polname
    """, list(MIGRATION.TABLES))]


@pytest_asyncio.fixture
async def repair_db():
    configured = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not configured:
        pytest.skip("Explicit disposable migration database required")
    dsn = reviewed_database(configured, os.getenv("GITHUB_ACTIONS"))
    conn = await asyncpg.connect(dsn, command_timeout=30)
    before = None
    tx = None
    role = "rls0077_" + uuid4().hex
    report = {"role_removed": False, "protected_unchanged": False}
    RUN_EVIDENCE.append(report)
    try:
        database = await conn.fetchval("SELECT current_database()")
        assert (os.getenv("GITHUB_ACTIONS") == "true" and database == "talky_alembic_test") or re.fullmatch(
            r"admin_rls_[0-9a-f]{32}_test", database
        ), "This DDL regression must run only in its owned clone or explicit CI fixture"
        before = await protected_snapshot(conn)
        assert before["head"] == "0083_customer_support_contracts"
        tx = conn.transaction()
        await tx.start()
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        yield SimpleNamespace(conn=conn, role=role, report=report)
    finally:
        try:
            if tx is not None:
                await tx.rollback()
            if before is not None:
                report["protected_unchanged"] = await protected_snapshot(conn) == before
                assert report["protected_unchanged"]
            report["role_removed"] = not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
            assert report["role_removed"]
        finally:
            await conn.close()


async def set_legacy(conn):
    for table in MIGRATION.TABLES:
        await conn.execute(f'DROP POLICY "{table}_tenant_isolation" ON public."{table}"')
        await conn.execute(f'CREATE POLICY "{table}_tenant_isolation" ON public."{table}" FOR ALL USING ({MIGRATION.LEGACY})')
        await conn.execute(f'ALTER TABLE public."{table}" NO FORCE ROW LEVEL SECURITY')


async def seed_pair(conn):
    tenants = [uuid4(), uuid4()]
    ids = {table: [uuid4(), uuid4()] for table in MIGRATION.TABLES}
    for i, tenant in enumerate(tenants):
        await conn.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic0077')", tenant)
        await conn.execute("INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic0077')", ids["campaigns"][i], tenant)
        await conn.execute("INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,'+15555550001','deleted')",
                           ids["leads"][i], tenant, ids["campaigns"][i])
        await conn.execute("""INSERT INTO calls(id,tenant_id,phone_number,status,is_test,direction)
            VALUES($1,$2,'+15555550001','in_call',TRUE,'inbound')""", ids["calls"][i], tenant)
        await conn.execute("INSERT INTO conversations(id,tenant_id,call_id) VALUES($1,$2,$3)",
                           ids["conversations"][i], tenant, ids["calls"][i])
        await conn.execute("INSERT INTO connectors(id,tenant_id,type,provider) VALUES($1,$2,'email','gmail')",
                           ids["connectors"][i], tenant)
    return tenants, ids


async def saved_rows(conn, ids):
    return {table: [row["value"] for row in await conn.fetch(
        f'SELECT to_jsonb(t)::text AS value FROM public."{table}" t WHERE id=ANY($1::uuid[]) ORDER BY id', values
    )] for table, values in ids.items()}


async def scoped_rows(conn, ids):
    return {table: list(await conn.fetchval(
        f'SELECT COALESCE(array_agg(id ORDER BY id),ARRAY[]::uuid[]) FROM public."{table}" WHERE id=ANY($1::uuid[])', values
    )) for table, values in ids.items()}


async def test_legacy_repair_preserves_rows_and_enforces_owner_tenant_boundary(repair_db):
    db = repair_db
    conn = db.conn
    await conn.execute("SET LOCAL app.bypass_rls='on'")
    tenants, ids = await seed_pair(conn)
    before_rows = await saved_rows(conn, ids)
    await set_legacy(conn)
    for table in MIGRATION.TABLES:
        await conn.execute(f'ALTER TABLE public."{table}" OWNER TO "{db.role}"')
    await conn.execute(f'SET LOCAL ROLE "{db.role}"')
    assert tuple((await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).values()) == (False, False)
    await conn.execute("SET LOCAL app.bypass_rls='off'")
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,TRUE)", str(tenants[0]))
    assert all(len(rows) == 2 for rows in (await scoped_rows(conn, ids)).values()), "Legacy owner bypass must be reproduced"
    await conn.execute("RESET ROLE")
    await conn.execute(MIGRATION.REPAIR_SQL)
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in await policies(conn))
    await conn.execute(f'SET LOCAL ROLE "{db.role}"')
    for index in (0, 1):
        await conn.execute("SELECT set_config('app.current_tenant_id',$1,TRUE)", str(tenants[index]))
        assert await scoped_rows(conn, ids) == {table: [values[index]] for table, values in ids.items()}
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,TRUE)", str(tenants[0]))
    # Use valid insert shapes whose existing BEFORE triggers do not need to
    # read tenant B through tenant A's scope. Real inbound calls and deleted
    # leads are explicit supported states, not a trigger/security override.
    rejected_inserts = [
        ("INSERT INTO calls(id,tenant_id,phone_number,status,is_test,direction) VALUES($1,$2,'+15555550001','in_call',TRUE,'inbound')", []),
        ("INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic rejected0077')", []),
        ("INSERT INTO connectors(id,tenant_id,type,provider) VALUES($1,$2,'email','gmail')", []),
        ("INSERT INTO conversations(id,tenant_id,call_id) VALUES($1,$2,$3)", [ids["calls"][1]]),
        ("INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,'+15555550002','deleted')", [ids["campaigns"][1]]),
    ]
    for statement, related in rejected_inserts:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(statement, uuid4(), tenants[1], *related)
    await conn.execute("SET LOCAL app.current_tenant_id=''")
    assert all(not rows for rows in (await scoped_rows(conn, ids)).values())
    await conn.execute("SET LOCAL app.bypass_rls='on'")
    assert all(len(rows) == 2 for rows in (await scoped_rows(conn, ids)).values())
    await conn.execute("RESET ROLE")
    assert await saved_rows(conn, ids) == before_rows
    db.report.update(owner_bypass_reproduced=True, both_tenants_isolated=True,
                     unscoped_denied=True, explicit_service_bypass=True, with_check_rejected=5, rows_unchanged=True)


async def test_canonical_policies_are_exact_noop_on_repeat(repair_db):
    before = await policies(repair_db.conn)
    await repair_db.conn.execute(MIGRATION.REPAIR_SQL)
    await repair_db.conn.execute(MIGRATION.REPAIR_SQL)
    assert await policies(repair_db.conn) == before


async def test_canonical_missing_force_keeps_policy_identity(repair_db):
    conn = repair_db.conn
    before = await policies(conn)
    await conn.execute("ALTER TABLE public.calls NO FORCE ROW LEVEL SECURITY")
    await conn.execute(MIGRATION.REPAIR_SQL)
    assert await policies(conn) == before


async def test_nullable_canonical_policy_is_preserved(repair_db):
    conn = repair_db.conn
    await conn.execute("ALTER TABLE public.calls ALTER COLUMN tenant_id DROP NOT NULL")
    await conn.execute("DROP POLICY calls_tenant_isolation ON public.calls")
    expression = MIGRATION.CANONICAL + " OR tenant_id IS NULL"
    await conn.execute(f"CREATE POLICY calls_tenant_isolation ON public.calls FOR ALL USING ({expression}) WITH CHECK ({expression})")
    before = await policies(conn)
    await conn.execute(MIGRATION.REPAIR_SQL)
    assert await policies(conn) == before


@pytest.mark.parametrize("conflict", ["extra", "expression", "roles", "disabled"])
async def test_unknown_posture_refuses_without_partial_policy_changes(repair_db, conflict):
    conn = repair_db.conn
    await set_legacy(conn)
    if conflict == "extra":
        await conn.execute("CREATE POLICY unexpected_extra ON public.leads FOR SELECT USING (TRUE)")
    elif conflict == "expression":
        await conn.execute("ALTER POLICY leads_tenant_isolation ON public.leads USING (TRUE)")
    elif conflict == "roles":
        await conn.execute(f'ALTER POLICY leads_tenant_isolation ON public.leads TO "{repair_db.role}"')
    else:
        await conn.execute("ALTER TABLE public.leads DISABLE ROW LEVEL SECURITY")
    before = await policies(conn)
    with pytest.raises(asyncpg.RaiseError, match="0077 refuses"):
        async with conn.transaction():
            await conn.execute(MIGRATION.REPAIR_SQL)
    assert await policies(conn) == before
    assert await conn.fetchval("SELECT to_regclass('pg_temp.admin_0077_policy_reference')") is None


def test_downgrade_cannot_reopen_owner_bypass():
    with pytest.raises(RuntimeError, match="forward-only"):
        MIGRATION.downgrade()
