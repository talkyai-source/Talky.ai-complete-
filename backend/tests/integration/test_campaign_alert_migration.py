"""Opt-in0090 PostgreSQL proof in UUID databases on explicit loopback TEST_DATABASE_URL.

No existing database tables are changed. The one role used for the RLS test is
UUID named and removed in finally. Requires local provisioning/CREATEROLE rights.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.db_utils import acquire_with_tenant
from app.domain.services import campaign_alerts

pytestmark = pytest.mark.integration
REVISION = Path(__file__).resolve().parents[2] / "Alembic" / "versions" / "0090_campaign_alerts.py"
BASE = (
    "CREATE TABLE tenants(id uuid PRIMARY KEY)",
    "CREATE TABLE campaigns(id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenants(id),name text,status text)",
    """CREATE TABLE calls(id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenants(id),campaign_id uuid,
       lead_id uuid,status text,outcome text,direction text DEFAULT 'outbound',is_test boolean DEFAULT false,
       terminal_settled_at timestamptz,created_at timestamptz DEFAULT now(),ended_at timestamptz DEFAULT now())""",
    """CREATE TABLE leads(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,campaign_id uuid,is_lead boolean DEFAULT false,
       qualified_call_id uuid,qualified_at timestamptz)""",
    """CREATE TABLE user_profiles(id uuid PRIMARY KEY,tenant_id uuid NOT NULL,email text,role text,is_active boolean DEFAULT true)""",
    """CREATE TABLE security_events(event_id uuid PRIMARY KEY,tenant_id uuid REFERENCES tenants(id),event_type text,
       severity text,status text DEFAULT 'open',title text,description text,evidence jsonb,alert_type text,
       detection_source text,created_at timestamptz DEFAULT now())""",
)


def migration():
    assert REVISION.is_file(), "0090 durable campaign alert migration is missing"
    spec = importlib.util.spec_from_file_location("campaign_alert_migration", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def database():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("explicit loopback TEST_DATABASE_URL required; real PostgreSQL not run")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if (parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.query or parsed.fragment
            or not re.search(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", parsed.path.lstrip("/"), re.I)):
        pytest.fail("campaign alert migration requires loopback test DSN without routing overrides", pytrace=False)
    name = f"qa_campaign_alert_{uuid4().hex}_test"
    admin = await asyncpg.connect(urlunsplit(parsed), timeout=5, command_timeout=60)
    engine = pool = None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        target = urlunsplit(parsed._replace(path=f"/{name}"))
        engine = create_async_engine("postgresql+asyncpg://", connect_args={"dsn": target, "timeout": 5, "command_timeout": 60}, poolclass=NullPool)
        async with engine.begin() as conn:
            for statement in BASE:
                await conn.execute(text(statement))
        pool = await asyncpg.create_pool(target, min_size=1, max_size=3, timeout=5, command_timeout=30)
        yield SimpleNamespace(engine=engine, pool=pool, admin=admin)
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


async def apply(database, method="upgrade"):
    module = migration()
    assert module.revision == "0090_campaign_alerts"
    assert module.down_revision == "0089_provider_key_validation"
    async with database.engine.begin() as conn:
        await conn.execute(text("SET LOCAL app.bypass_rls='true'"))
        def run(sync):
            with Operations.context(MigrationContext.configure(sync)):
                getattr(module, method)()
        await conn.run_sync(run)


async def seed(database):
    tenant, other, campaign = uuid4(), uuid4(), uuid4()
    async with database.pool.acquire() as conn:
        await conn.executemany("INSERT INTO tenants VALUES($1)", [(tenant,), (other,)])
        await conn.execute("INSERT INTO campaigns VALUES($1,$2,'Synthetic','running')", campaign, tenant)
        await conn.execute("INSERT INTO user_profiles VALUES($1,$2,'owner@example.invalid','tenant_admin',true)", uuid4(), tenant)
    return tenant, other, campaign


async def test_real_pg_duplicate_terminal_claim_and_atomic_alert_intent(database):
    await apply(database)
    tenant, _, campaign = await seed(database)
    call_id = uuid4()
    async with database.pool.acquire() as conn:
        await conn.execute("INSERT INTO calls(id,tenant_id,campaign_id,status,outcome) VALUES($1,$2,$3,'completed','failed')", call_id, tenant, campaign)
    async def record():
        async with acquire_with_tenant(database.pool, str(tenant)) as conn:
            await campaign_alerts.record_terminal_call(conn, str(tenant), call_id, threshold=1)
    import asyncio
    await asyncio.gather(record(), record())
    async with acquire_with_tenant(database.pool, str(tenant)) as conn:
        assert await conn.fetchval("SELECT failure_count FROM campaign_alert_state WHERE tenant_id=$1", tenant) == 1
        assert await conn.fetchval("SELECT count(*) FROM security_events WHERE tenant_id=$1", tenant) == 1
        assert await conn.fetchval("SELECT count(*) FROM campaign_alert_outbox WHERE tenant_id=$1 AND status='pending'", tenant) == 1
        assert await conn.fetchval("SELECT campaign_alert_processed_at IS NOT NULL FROM calls WHERE id=$1 AND tenant_id=$2", call_id, tenant)
        # Execute the new registry predicates in PostgreSQL as well as the
        # isolated SQLite ownership fixture; array and JSON operators are real.
        from app.domain.services.dsar_service import DsarService
        for table in ("security_events", "campaign_alert_outbox"):
            rows = await conn.fetch(DsarService._sql(table), tenant, False, None, None, [], [call_id], 10)
            assert len(rows) == 1
            data = json.loads(rows[0]["data"]) if isinstance(rows[0]["data"], str) else dict(rows[0]["data"])
            assert data["tenant_id"] == str(tenant)
            unrelated = await conn.fetch(DsarService._sql(table), tenant, False, None, None, [], [uuid4()], 10)
            assert unrelated == []


async def test_real_pg_owner_fks_and_forced_rls_reject_cross_tenant_access(database):
    await apply(database)
    tenant, other, campaign = await seed(database)
    async with acquire_with_tenant(database.pool, str(tenant)) as conn:
        await conn.execute("INSERT INTO campaign_alert_state(tenant_id,campaign_id) VALUES($1,$2)", tenant, campaign)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with acquire_with_tenant(database.pool, str(other)) as conn:
            await conn.execute("INSERT INTO campaign_alert_state(tenant_id,campaign_id) VALUES($1,$2)", other, campaign)
    role = f"qa_alert_reader_{uuid4().hex}"
    role_created = False
    try:
        await database.admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        role_created = True
        async with database.pool.acquire() as conn:
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            await conn.execute(f'GRANT SELECT,INSERT ON campaign_alert_state,tenant_alert_state,campaign_alert_outbox TO "{role}"')
            async with conn.transaction():
                await conn.execute(f'SET LOCAL ROLE "{role}"')
                await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(other))
                await conn.execute("SET LOCAL app.bypass_rls='false'")
                assert await conn.fetchval("SELECT count(*) FROM campaign_alert_state") == 0
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(f'SET LOCAL ROLE "{role}"')
                    await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(other))
                    await conn.execute("SET LOCAL app.bypass_rls='false'")
                    await conn.execute("INSERT INTO tenant_alert_state(tenant_id) VALUES($1)", tenant)
            await conn.execute(f'DROP OWNED BY "{role}"')
    finally:
        if role_created:
            # Revoke only this generated role's grants, including after failure.
            async with database.pool.acquire() as conn:
                await conn.execute(f'DROP OWNED BY "{role}"')
            await database.admin.execute(f'DROP ROLE "{role}"')


async def test_populated_alert_downgrade_refuses_without_losing_delivery_state(database):
    await apply(database)
    tenant, _, campaign = await seed(database)
    async with acquire_with_tenant(database.pool, str(tenant)) as conn:
        await conn.execute("INSERT INTO campaign_alert_state(tenant_id,campaign_id) VALUES($1,$2)", tenant, campaign)
    with pytest.raises(DBAPIError, match="alert data exists"):
        await apply(database, "downgrade")
    async with acquire_with_tenant(database.pool, str(tenant)) as conn:
        assert await conn.fetchval("SELECT count(*) FROM campaign_alert_state WHERE tenant_id=$1", tenant) == 1


async def test_empty_alert_downgrade_removes_only_owned_objects(database):
    await apply(database)
    await apply(database, "downgrade")
    async with database.pool.acquire() as conn:
        for table in ("campaign_alert_state", "tenant_alert_state", "campaign_alert_outbox"):
            assert await conn.fetchval("SELECT to_regclass($1)", table) is None
        assert await conn.fetchval("SELECT to_regclass('calls')") is not None
        assert await conn.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name='calls' AND column_name='campaign_alert_processed_at'") == 0
