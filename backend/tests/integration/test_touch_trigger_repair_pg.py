"""0092 on disposable loopback PostgreSQL: touch triggers without their column (QA F14-02).

LINEAGE is production's shape (2 June 2026 baseline plus migrations), read from
the production catalog on 11 Nov 2026: BEFORE UPDATE triggers that set
NEW.updated_at on assistant_actions and reminders, neither of which has it.
"""
import importlib.util
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
TOUCH = """CREATE FUNCTION update_updated_at_column() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$"""
LINEAGE = (
    TOUCH,
    "CREATE TABLE assistant_actions (id uuid PRIMARY KEY, tenant_id uuid, status text)",
    """CREATE TRIGGER update_assistant_actions_updated_at BEFORE UPDATE ON assistant_actions
       FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()""",
    "CREATE TABLE reminders (id uuid PRIMARY KEY, tenant_id uuid, status text)",
    """CREATE TRIGGER update_reminders_updated_at BEFORE UPDATE ON reminders
       FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()""",
)


def load(name):
    path = BACKEND / "Alembic/versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"qa_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def database():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("explicit loopback TEST_DATABASE_URL required; real PostgreSQL not run")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if (parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.query or parsed.fragment
            or not re.search(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", parsed.path.lstrip("/"), re.I)):
        pytest.fail("schema repair tests require a loopback test DSN without routing overrides", pytrace=False)
    name = f"qa_schema_repair_{uuid4().hex}_test"
    admin = await asyncpg.connect(urlunsplit(parsed), timeout=5, command_timeout=60)
    engine = pool = None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        target = urlunsplit(parsed._replace(path=f"/{name}"))
        engine = create_async_engine("postgresql+asyncpg://", connect_args={"dsn": target, "timeout": 5, "command_timeout": 60},
                                     poolclass=NullPool)
        pool = await asyncpg.create_pool(target, min_size=1, max_size=2, timeout=5, command_timeout=30)

        async def build(statements):
            async with pool.acquire() as conn:
                for statement in statements:
                    await conn.execute(statement)

        async def apply(module, method="upgrade"):
            async with engine.begin() as conn:
                def run(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        getattr(module, method)()
                await conn.run_sync(run)

        yield SimpleNamespace(engine=engine, pool=pool, build=build, apply=apply)
    finally:
        if pool is not None:
            await pool.close()
        if engine is not None:
            await engine.dispose()
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{name}"')
        finally:
            await admin.close()


def migration():
    module = load("0092_drop_broken_touch_triggers")
    assert (module.revision, module.down_revision) == ("0092_drop_broken_touch_triggers", "0091_call_history_cursor")
    return module


async def touch_triggers(conn):
    return await conn.fetchval("""SELECT count(*) FROM pg_trigger
        WHERE tgname IN ('update_assistant_actions_updated_at','update_reminders_updated_at')""")


async def test_production_lineage_updates_work_once_the_broken_triggers_are_gone(database):
    await database.build(LINEAGE)
    async with database.pool.acquire() as conn:
        action, reminder = uuid4(), uuid4()
        await conn.execute("INSERT INTO assistant_actions (id, status) VALUES ($1, 'pending')", action)
        await conn.execute("INSERT INTO reminders (id, status) VALUES ($1, 'pending')", reminder)
        for table, row in (("assistant_actions", action), ("reminders", reminder)):
            with pytest.raises(asyncpg.UndefinedColumnError, match='has no field "updated_at"'):
                await conn.execute(f"UPDATE {table} SET status='done' WHERE id=$1", row)

    await database.apply(migration())
    await database.apply(migration())  # idempotent

    async with database.pool.acquire() as conn:
        assert await touch_triggers(conn) == 0
        assert await conn.execute("UPDATE assistant_actions SET status='completed' WHERE id=$1", action) == "UPDATE 1"
        assert await conn.execute("UPDATE reminders SET status='sent' WHERE id=$1", reminder) == "UPDATE 1"


async def test_a_trigger_whose_table_has_the_column_is_kept(database):
    await database.build((TOUCH,
        "CREATE TABLE reminders (id uuid PRIMARY KEY, status text, updated_at timestamptz DEFAULT now())",
        """CREATE TRIGGER update_reminders_updated_at BEFORE UPDATE ON reminders
           FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()"""))
    await database.apply(migration())
    async with database.pool.acquire() as conn:
        assert await touch_triggers(conn) == 1


async def test_missing_tables_are_skipped_and_downgrade_never_restores_the_triggers(database):
    await database.build(LINEAGE[:3])  # no reminders table at all
    await database.apply(migration())
    await database.apply(migration(), "downgrade")
    async with database.pool.acquire() as conn:
        assert await touch_triggers(conn) == 0
