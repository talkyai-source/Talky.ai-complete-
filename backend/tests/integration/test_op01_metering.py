"""Canonical meter on migrated public tables; synthetic rows, no provider I/O."""
from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.services.minutes_quota import compute_minutes_status

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def meter_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("OP01 accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    await admin.execute("SELECT set_config('app.bypass_rls','on',false)")
    role = "op01_" + uuid4().hex
    tenants, plans, campaigns = [uuid4(), uuid4()], ["op01_" + uuid4().hex for _ in range(2)], [uuid4() for _ in range(4)]
    pool = None
    try:
        # Grants apply only to this new fixture role, never the application role.
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT ON plans,tenants,calls,call_legs TO "{role}"')
        for i in range(2):
            await admin.execute("INSERT INTO plans(id,name,price,minutes) VALUES($1,'Synthetic OP01',1,30)", plans[i])
            await admin.execute("INSERT INTO tenants(id,business_name,plan_id,minutes_allocated) VALUES($1,'Synthetic OP01',$2,30)", tenants[i], plans[i])
            for offset, direction in enumerate(("outbound", "inbound")):
                await admin.execute("INSERT INTO campaigns(id,tenant_id,name,direction) VALUES($1,$2,'Synthetic OP01',$3)", campaigns[i * 2 + offset], tenants[i], direction)

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, setup=setup,
                                         timeout=5, command_timeout=10)
        yield SimpleNamespace(admin=admin, pool=pool, role=role, tenants=tenants, plans=plans, campaigns=campaigns)
    finally:
        if pool:
            await pool.close()
        # Only exact UUID-scoped fixture rows; no financial ledger or receipt is created.
        await admin.execute("DELETE FROM call_legs WHERE call_id IN (SELECT id FROM calls WHERE tenant_id=ANY($1::uuid[]))", tenants)
        await admin.execute("DELETE FROM calls WHERE tenant_id=ANY($1::uuid[])", tenants)
        await admin.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaigns)
        await admin.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", tenants)
        await admin.execute("DELETE FROM plans WHERE id=ANY($1::text[])", plans)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def status(db, tenant=None):
    tenant = tenant or db.tenants[0]
    async with acquire_with_tenant(db.pool, str(tenant)) as conn:
        flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert not flags["rolsuper"] and not flags["rolbypassrls"]
        return await compute_minutes_status(conn, str(tenant))


async def call(db, *, tenant_index=0, duration=119, direction="outbound", billing="none", test=False, previous_month=False):
    identifier = uuid4()
    await db.admin.execute(
        """INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,duration_seconds,
           direction,billing_status,is_test,created_at)
           VALUES($1,$2,$3,'+15550001111','ended',$4,$5,$6,$7,
             CASE WHEN $8 THEN date_trunc('month',now())-interval '1 second' ELSE now() END)""",
        identifier, db.tenants[tenant_index], db.campaigns[tenant_index * 2 + (direction == "inbound")],
        duration, direction, billing, test, previous_month,
    )
    return identifier


async def test_finite_meter_uses_current_month_and_existing_floor(meter_db):
    await call(meter_db, duration=119)
    await call(meter_db, duration=3600, previous_month=True)
    result = await status(meter_db)
    assert (result.state, result.allocated, result.used_minutes, result.remaining_minutes) == ("known", 30, 1, 29)


@pytest.mark.parametrize("plan_minutes,state,exhausted", [(30, "known", True), (0, "unlimited", False)])
async def test_zero_allocation_requires_explicit_zero_plan(meter_db, plan_minutes, state, exhausted):
    await meter_db.admin.execute("UPDATE tenants SET minutes_allocated=0 WHERE id=$1", meter_db.tenants[0])
    await meter_db.admin.execute("UPDATE plans SET minutes=$2 WHERE id=$1", meter_db.plans[0], plan_minutes)
    result = await status(meter_db)
    assert result.state == state and result.exhausted is exhausted
    assert result.unlimited is (state == "unlimited")


@pytest.mark.parametrize("missing", ["tenant", "plan"])
async def test_missing_entitlement_is_unavailable(meter_db, missing):
    if missing == "plan":
        await meter_db.admin.execute("UPDATE tenants SET minutes_allocated=0,plan_id=NULL WHERE id=$1", meter_db.tenants[0])
    result = await status(meter_db, uuid4() if missing == "tenant" else None)
    assert result.state == "unavailable" and not result.unlimited
    assert result.remaining_minutes is None


async def test_schema_rejects_missing_allocation_without_altering_entitlement(meter_db):
    with pytest.raises(asyncpg.NotNullViolationError):
        await meter_db.admin.execute("UPDATE tenants SET minutes_allocated=NULL WHERE id=$1", meter_db.tenants[0])
    assert (await status(meter_db)).allocated == 30


@pytest.mark.parametrize("bypass", ["on", "true"])
async def test_worker_platform_context_keeps_explicit_tenant_metering(meter_db, bypass):
    await call(meter_db, tenant_index=1, duration=125)
    async with acquire_with_tenant(meter_db.pool, None) as conn:
        await conn.execute("SELECT set_config('app.bypass_rls',$1,true)", bypass)
        result = await compute_minutes_status(conn, str(meter_db.tenants[1]))
    assert result.state == "known" and result.used_minutes == 2


async def test_connection_without_scope_cannot_report_a_full_balance(meter_db):
    async with meter_db.pool.acquire() as conn:
        result = await compute_minutes_status(conn, str(meter_db.tenants[0]))
    assert result.state == "unavailable" and result.remaining_minutes is None


async def test_selective_usage_permission_failure_does_not_grant_unlimited(meter_db):
    await meter_db.admin.execute(f'REVOKE SELECT ON calls FROM "{meter_db.role}"')
    async with acquire_with_tenant(meter_db.pool, str(meter_db.tenants[0])) as conn:
        # Entitlement lookup still succeeds; only the usage SELECT is unavailable.
        assert await conn.fetchval("SELECT minutes_allocated FROM tenants WHERE id=$1", meter_db.tenants[0]) == 30
        result = await compute_minutes_status(conn, str(meter_db.tenants[0]))
    assert result.state == "unavailable" and result.remaining_minutes is None
    await meter_db.admin.execute(f'GRANT SELECT ON calls TO "{meter_db.role}"')
    assert (await status(meter_db)).remaining_minutes == 30


async def test_tenant_scoped_parent_and_finalized_transfer_usage(meter_db):
    await call(meter_db, duration=61)
    parent = await call(meter_db, direction="inbound", duration=59, billing="finalized")
    await call(meter_db, direction="inbound", duration=3600, billing="held")
    test_call = await call(meter_db, duration=3600, test=True)
    foreign = await call(meter_db, tenant_index=1, duration=3600)
    for owned_call, seconds, billing in [(parent, 119, "finalized"), (parent, 900, "held"), (test_call, 900, "finalized"), (foreign, 900, "finalized")]:
        await meter_db.admin.execute("INSERT INTO call_legs(call_id,leg_type,duration_seconds,billing_status) VALUES($1,'transfer',$2,$3)", owned_call, seconds, billing)
    result = await status(meter_db)
    assert result.used_minutes == 3 and result.remaining_minutes == 27  # 239 settled seconds.
    async with acquire_with_tenant(meter_db.pool, str(meter_db.tenants[0])) as conn:
        assert await conn.fetchval("SELECT count(*) FROM calls WHERE tenant_id=$1", meter_db.tenants[1]) == 0
        assert (await compute_minutes_status(conn, str(meter_db.tenants[1]))).state == "unavailable"
