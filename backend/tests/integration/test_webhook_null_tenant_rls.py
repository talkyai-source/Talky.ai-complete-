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
            await conn.execute(f"CREATE TEMP TABLE {table} (id int PRIMARY KEY, tenant_id uuid) ON COMMIT DROP")
            assert await conn.fetchval(
                "SELECT relnamespace=pg_my_temp_schema() FROM pg_class WHERE oid=to_regclass($1)", table
            )
            await conn.execute(f"INSERT INTO pg_temp.{table} VALUES (1,$1),(2,$2),(3,NULL)", tenant_a, tenant_b)
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
                        await conn.execute(f"INSERT INTO pg_temp.{table} VALUES (4,$1)", owner)
        await conn.execute("SELECT set_config('app.current_tenant_id','',true)")
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 0
        await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
        for table in migration.TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM pg_temp.{table}") == 3
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
        await verify_webhook_rls(conn, migration)
    finally:
        await conn.close()
