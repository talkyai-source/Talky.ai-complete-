"""0091 index adoption/rollback on disposable loopback-only PostgreSQL databases."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy.exc import DBAPIError

from tests.integration.test_campaign_alert_migration import database  # noqa: F401


pytestmark = pytest.mark.integration


async def apply(database, method="upgrade"):
    path = Path(__file__).resolve().parents[2] / "Alembic/versions/0091_call_history_cursor.py"
    spec = importlib.util.spec_from_file_location("qa_history_index", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    async with database.engine.begin() as connection:
        def run(sync):
            with Operations.context(MigrationContext.configure(sync)):
                getattr(revision, method)()
        await connection.run_sync(run)


async def test_index_upgrade_is_idempotent_and_owned_downgrade_preserves_calls(database):
    await apply(database)
    await apply(database)
    async with database.pool.acquire() as conn:
        definition = await conn.fetchval("SELECT pg_get_indexdef(to_regclass('idx_calls_tenant_created_id'))")
        assert "(tenant_id, created_at DESC, id DESC)" in definition
    await apply(database, "downgrade")
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('idx_calls_tenant_created_id')") is None
        assert await conn.fetchval("SELECT to_regclass('calls')") is not None


async def test_matching_preexisting_index_is_adopted_without_claiming_ownership(database):
    async with database.pool.acquire() as conn:
        await conn.execute("CREATE INDEX idx_calls_tenant_created_id ON calls(tenant_id,created_at DESC,id DESC)")
    await apply(database)
    await apply(database, "downgrade")
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('idx_calls_tenant_created_id')") is not None
        assert await conn.fetchval("SELECT obj_description(to_regclass('idx_calls_tenant_created_id'),'pg_class')") is None


async def test_incompatible_existing_index_is_refused_without_replacement(database):
    async with database.pool.acquire() as conn:
        await conn.execute("CREATE INDEX idx_calls_tenant_created_id ON calls(created_at)")
        before = await conn.fetchval("SELECT pg_get_indexdef(to_regclass('idx_calls_tenant_created_id'))")
    with pytest.raises(DBAPIError, match="incompatible call cursor index"):
        await apply(database)
    async with database.pool.acquire() as conn:
        assert await conn.fetchval("SELECT pg_get_indexdef(to_regclass('idx_calls_tenant_created_id'))") == before
