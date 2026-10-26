"""Top-up minutes are used once and carry over, proven against real PostgreSQL.

Owner decision 2026-10-27: a bought minute is used only after the month's plan
minutes run out and stays until used. Before this, a top-up inside
``minutes_allocated`` was granted again every calendar month.

The meter and the recorder run as a NOSUPERUSER/NOBYPASSRLS role under tenant
context, the way the application reads. Rows are UUID-scoped and removed.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_checkout import apply_plan_allocation
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.topup_usage import record_topup_usage, topup_minutes_left, usage_event_id

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def usage_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Top-up usage accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    await admin.execute("SELECT set_config('app.bypass_rls','on',false)")
    role, tenant, plan, campaign = "topup_" + uuid4().hex, uuid4(), "topup_" + uuid4().hex, uuid4()
    pool = None
    try:
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT ON plans,calls,call_legs,topup_orders,topup_packages TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON tenants TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT ON billing_ledger TO "{role}"')
        sequence = await admin.fetchval("SELECT pg_get_serial_sequence('billing_ledger','id')")
        await admin.execute(f'GRANT USAGE,SELECT ON SEQUENCE {sequence} TO "{role}"')
        await admin.execute("INSERT INTO plans(id,name,price,minutes) VALUES($1,'Synthetic top-up',99,300)", plan)
        await admin.execute(
            "INSERT INTO tenants(id,business_name,plan_id,minutes_allocated) VALUES($1,'Synthetic top-up',$2,300)",
            tenant, plan,
        )
        await admin.execute(
            "INSERT INTO campaigns(id,tenant_id,name,direction) VALUES($1,$2,'Synthetic top-up','outbound')",
            campaign, tenant,
        )

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, setup=setup, timeout=5, command_timeout=10)
        yield SimpleNamespace(admin=admin, pool=pool, tenant=tenant, plan=plan, campaign=campaign)
    finally:
        if pool:
            await pool.close()
        await admin.execute("DELETE FROM calls WHERE tenant_id=$1", tenant)
        await admin.execute("DELETE FROM campaigns WHERE id=$1", campaign)
        # billing_ledger is append-only: its UUID-scoped synthetic rows remain.
        await admin.execute("DELETE FROM tenants WHERE id=$1", tenant)
        await admin.execute("DELETE FROM plans WHERE id=$1", plan)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


def _month(months_ago: int, day: int = 2) -> str:
    """SQL for a moment inside an earlier calendar month (0 = this month)."""
    return f"date_trunc('month',now()) - interval '{months_ago} month' + interval '{day - 1} day'"


async def buy(db, minutes: int, months_ago: int) -> None:
    """A credited top-up, as credit_paid_order leaves it: ledger row plus allocation."""
    await db.admin.execute(
        f"""INSERT INTO billing_ledger(tenant_id,kind,minutes_delta,provider_event_id,created_at)
            VALUES($1,'topup',$2,$3,{_month(months_ago)})""",
        db.tenant, minutes, "evt_topup_" + uuid4().hex,
    )
    await db.admin.execute(
        "UPDATE tenants SET minutes_allocated=minutes_allocated+$2 WHERE id=$1", db.tenant, minutes,
    )


async def talk(db, minutes: int, months_ago: int) -> None:
    await db.admin.execute(
        f"""INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,duration_seconds,
            direction,billing_status,is_test,created_at)
            VALUES($1,$2,$3,'+15550001111','ended',$4,'outbound','none',FALSE,{_month(months_ago, 3)})""",
        uuid4(), db.tenant, db.campaign, minutes * 60,
    )


async def meter(db):
    async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
        return await compute_minutes_status(conn, str(db.tenant))


async def record(db) -> int:
    async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
        return await record_topup_usage(conn, str(db.tenant))


async def allocated(db) -> int:
    return await db.admin.fetchval("SELECT minutes_allocated FROM tenants WHERE id=$1", db.tenant)


async def test_a_closed_month_uses_bought_minutes_once(usage_db):
    db = usage_db
    await buy(db, 250, months_ago=1)
    await talk(db, 400, months_ago=1)  # 100 beyond the 300 plan minutes

    status = await meter(db)
    assert (status.allocated, status.remaining_minutes) == (450, 450), (
        "last month's 100 top-up minutes were granted again this month"
    )

    assert await record(db) == 100
    assert await allocated(db) == 450
    month = await db.admin.fetchval(f"SELECT to_char({_month(1)}, 'YYYY-MM')")
    row = await db.admin.fetchrow(
        "SELECT kind,minutes_delta FROM billing_ledger WHERE provider_event_id=$1",
        usage_event_id(db.tenant, month),
    )
    assert (row["kind"], row["minutes_delta"]) == ("adjustment", -100)
    assert (await meter(db)).allocated == 450, "a recorded month was subtracted twice"
    assert await record(db) == 0, "recording is idempotent"


async def test_unused_bought_minutes_carry_into_later_months(usage_db):
    db = usage_db
    await buy(db, 250, months_ago=2)
    await talk(db, 350, months_ago=2)  # 50 beyond the plan
    await talk(db, 200, months_ago=1)  # within the plan

    assert (await meter(db)).allocated == 500
    assert await record(db) == 50
    assert await allocated(db) == 500
    assert await db.admin.fetchval(
        "SELECT COUNT(*) FROM billing_ledger WHERE tenant_id=$1 AND provider_event_id LIKE 'topup-usage:%'", db.tenant,
    ) == 2, "both closed months are marked, the quiet one with a zero row"


async def test_minutes_bought_later_never_cover_an_earlier_month(usage_db):
    db = usage_db
    await buy(db, 50, months_ago=2)
    await talk(db, 400, months_ago=1)  # 100 over, only 50 bought by then
    await buy(db, 250, months_ago=0)

    assert (await meter(db)).allocated == 550  # 300 plan + 250 still unused
    assert await record(db) == 50


async def test_this_months_overage_comes_out_of_the_balance_shown(usage_db):
    db = usage_db
    await buy(db, 250, months_ago=1)
    await talk(db, 400, months_ago=1)
    await talk(db, 350, months_ago=0)  # 50 beyond the plan this month

    status = await meter(db)
    assert (status.allocated, status.used_minutes, status.remaining_minutes) == (450, 350, 100)
    async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
        assert await topup_minutes_left(conn, str(db.tenant), status.used_minutes) == 100


async def test_a_plan_change_measures_closed_months_against_the_old_plan(usage_db):
    db = usage_db
    await buy(db, 250, months_ago=1)
    await talk(db, 400, months_ago=1)

    async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
        await apply_plan_allocation(conn, str(db.tenant), 1000)

    assert await allocated(db) == 1150, "the new plan plus the 150 bought minutes still unused"
    assert (await meter(db)).allocated == 1150


async def test_an_unlimited_account_is_left_alone(usage_db):
    db = usage_db
    await db.admin.execute("UPDATE plans SET minutes=0 WHERE id=$1", db.plan)
    await db.admin.execute("UPDATE tenants SET minutes_allocated=0 WHERE id=$1", db.tenant)
    await db.admin.execute(
        f"""INSERT INTO billing_ledger(tenant_id,kind,minutes_delta,provider_event_id,created_at)
            VALUES($1,'topup',250,$2,{_month(1)})""",
        db.tenant, "evt_topup_" + uuid4().hex,
    )
    await talk(db, 400, months_ago=1)

    assert await record(db) == 0
    assert (await meter(db)).unlimited is True


async def test_an_account_with_no_plan_has_zero_minutes_not_an_unknown_allowance(usage_db):
    db = usage_db
    await db.admin.execute("UPDATE tenants SET plan_id=NULL,minutes_allocated=0 WHERE id=$1", db.tenant)

    status = await meter(db)
    assert (status.state, status.allocated, status.remaining_minutes) == ("known", 0, 0)
    assert status.exhausted is True and status.unlimited is False
