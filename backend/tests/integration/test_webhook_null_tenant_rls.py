"""Exercise real FORCE RLS using temporary tables and the actual migration SQL."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import asyncpg
import pytest


async def verify_webhook_rls(conn, migration):
    statements = []
    migration.op = SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
    migration.upgrade()
    tx = conn.transaction()
    await tx.start()
    try:
        assert not await conn.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ), "RLS verification must use a role subject to row security"
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        for table in migration.TABLES:
            await conn.execute(f"CREATE TEMP TABLE {table} (id int PRIMARY KEY, tenant_id uuid, webhook_id uuid) ON COMMIT DROP")
            assert await conn.fetchval(
                "SELECT relnamespace=pg_my_temp_schema() FROM pg_class WHERE oid=to_regclass($1)", table
            )
            await conn.execute(f"INSERT INTO pg_temp.{table} (id,tenant_id) VALUES (1,$1),(2,$2),(3,NULL)", tenant_a, tenant_b)
            await conn.execute(f"CREATE POLICY {table}_tenant_isolation ON pg_temp.{table} "
                               f"USING ({migration.SCOPE} OR tenant_id IS NULL) "
                               f"WITH CHECK ({migration.SCOPE} OR tenant_id IS NULL)")
            await conn.execute(f"ALTER TABLE pg_temp.{table} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"ALTER TABLE pg_temp.{table} FORCE ROW LEVEL SECURITY")
        await conn.execute("SELECT set_config('app.bypass_rls','false',true)")
        await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(tenant_a))
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 2, "old policy must reproduce unowned-row exposure"
        for statement in statements:
            await conn.execute(statement.replace("public.webhook_", "pg_temp.webhook_"))
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 1
            assert await conn.execute(f"UPDATE pg_temp.{table} SET id=30 WHERE id=3") == "UPDATE 0"
            for owner in (None, tenant_b):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.execute(f"INSERT INTO pg_temp.{table} (id,tenant_id) VALUES (4,$1)", owner)
        await conn.execute("SELECT set_config('app.current_tenant_id','',true)")
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 0
        await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 3
    finally:
        await tx.rollback()


async def verify_fresh_webhook_schema(conn, migration):
    """Run the actual DDL without pre-creating either table or policy."""
    statements = []
    migration.op = SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
    migration.upgrade()
    tx = conn.transaction()
    await tx.start()
    try:
        for table in migration.TABLES:
            assert await conn.fetchval("SELECT to_regclass($1)", f"pg_temp.{table}") is None
        for _ in range(2):  # repeat proves reconciliation preserves existing data
            for statement in statements:
                sql = statement.replace("public.webhook_", "pg_temp.webhook_")
                sql = sql.replace("CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE IF NOT EXISTS")
                await conn.execute(sql)
        await conn.execute("SELECT set_config('app.bypass_rls','false',true)")
        tenant = uuid.uuid4()
        await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(tenant))
        endpoint = await conn.fetchval("INSERT INTO pg_temp.webhook_endpoints (tenant_id,url,events) VALUES ($1,'https://example.invalid','[\"call.ended\"]') RETURNING id", tenant)
        await conn.execute("INSERT INTO pg_temp.webhook_deliveries (tenant_id,webhook_id,event) VALUES ($1,$2,'call.ended')", tenant, endpoint)
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 1
            assert await conn.fetchval("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=to_regclass($1)", f"pg_temp.{table}")
    finally:
        await tx.rollback()


@pytest.mark.asyncio
async def test_webhook_unowned_rows_are_only_visible_to_platform_scope():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires explicit TEST_DATABASE_URL for temporary-table RLS proof")
    path = Path(__file__).parents[2] / "Alembic/versions/0044_webhook_null_tenant_rls.py"
    spec = importlib.util.spec_from_file_location("webhook_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    conn = await asyncpg.connect(dsn)
    try:
        await verify_fresh_webhook_schema(conn, migration)
        await verify_webhook_rls(conn, migration)
    finally:
        await conn.close()
