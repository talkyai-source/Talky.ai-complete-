"""Inbound admission meters minutes as outbound does (QA F13-04, QA-BIL-013).

Real PostgreSQL as a restricted NOSUPERUSER NOBYPASSRLS role. Each case puts
``tenant_inbound_allowance`` (inbound admission and transfer authorization)
next to ``compute_minutes_status`` (outbound admission) for one tenant. Before
the fix inbound read ``tenants.minutes_allocated`` alone: bought minutes were
ignored, an admin zero meant unlimited, and usage restarted on the calendar
month while a paid plan renews from its purchase date.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.payg import PAYG_PLAN_ID
from app.domain.services.telephony.inbound_transfer import tenant_inbound_allowance

pytestmark = pytest.mark.integration


def _dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    target = urlparse(dsn)
    if target.hostname not in {"127.0.0.1", "localhost"} or not target.path.endswith("_test"):
        pytest.fail("Inbound allowance tests require a disposable local *_test database")
    return dsn


@pytest_asyncio.fixture
async def meter_db():
    dsn = _dsn()
    owner = await asyncpg.connect(dsn, timeout=5, command_timeout=20)
    role = "inbound_meter_" + uuid4().hex
    tenants: list = []
    plans: list = []
    campaigns: dict = {}
    pool = None
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "{role}"')

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, timeout=5, command_timeout=20,
                                         init=_register_jsonb_codecs, setup=setup)

        async def tenant(plan_id, minutes, status="active", anchor_sql="NULL"):
            tid = uuid4()
            async with owner.transaction():
                await owner.execute("SET LOCAL app.bypass_rls = 'on'")
                await owner.execute(
                    f"""INSERT INTO tenants (id, business_name, plan_id, minutes_allocated, minutes_used,
                           subscription_status, allowance_anchor_at)
                        VALUES ($1, 'Synthetic inbound meter', $2, $3, 0, $4, {anchor_sql})""",
                    tid, plan_id, minutes, status)
                for direction in ("outbound", "inbound"):
                    campaigns[(tid, direction)] = uuid4()
                    await owner.execute(
                        "INSERT INTO campaigns (id, tenant_id, name, direction) VALUES ($1, $2, 'meter', $3)",
                        campaigns[(tid, direction)], tid, direction)
            tenants.append(tid)
            return tid

        async def call(tid, seconds, at_sql, *, direction="outbound", billing_status="none", reserved=0):
            async with owner.transaction():
                await owner.execute("SET LOCAL app.bypass_rls = 'on'")
                await owner.execute(
                    f"""INSERT INTO calls (id, tenant_id, campaign_id, phone_number, status, duration_seconds,
                           direction, billing_status, reserved_seconds, is_test, created_at)
                        VALUES ($1, $2, $3, '+15550001111', $4, $5, $6, $7, $8, FALSE, {at_sql})""",
                    uuid4(), tid, campaigns[(tid, direction)], "in_progress" if seconds is None else "ended",
                    seconds, direction, billing_status, reserved)

        async def bought(tid, minutes):
            async with owner.transaction():
                await owner.execute("SET LOCAL app.bypass_rls = 'on'")
                await owner.execute(
                    """INSERT INTO billing_ledger (tenant_id, kind, minutes_delta, amount_cents, provider_event_id,
                           created_at)
                       VALUES ($1, 'topup', $2, 0, $3, now() - interval '2 hours')""",
                    tid, minutes, "evt_inbound_meter_" + uuid4().hex)

        async def unlimited_plan():
            plan = "test_unlimited_" + uuid4().hex[:8]
            await owner.execute(
                "INSERT INTO plans (id, name, price, minutes) VALUES ($1, 'Test unlimited', 10, 0)", plan)
            plans.append(plan)
            return plan

        async def both(tid):
            async with acquire_with_tenant(pool, None) as conn:
                inbound = await tenant_inbound_allowance(conn, tenant_id=str(tid))
            async with acquire_with_tenant(pool, str(tid)) as conn:
                outbound = await compute_minutes_status(conn, str(tid))
            return inbound, outbound

        yield SimpleNamespace(tenant=tenant, call=call, bought=bought, unlimited_plan=unlimited_plan, both=both)
    finally:
        if pool is not None:
            await pool.close()
        async with owner.transaction():
            await owner.execute("SET LOCAL app.bypass_rls = 'on'")
            await owner.execute("ALTER TABLE billing_ledger DISABLE TRIGGER billing_ledger_immutable")
            await owner.execute("DELETE FROM calls WHERE tenant_id = ANY($1)", tenants)
            await owner.execute("DELETE FROM campaigns WHERE tenant_id = ANY($1)", tenants)
            await owner.execute("DELETE FROM billing_ledger WHERE tenant_id = ANY($1)", tenants)
            await owner.execute("DELETE FROM tenants WHERE id = ANY($1)", tenants)
            await owner.execute("DELETE FROM plans WHERE id = ANY($1)", plans)
            await owner.execute("ALTER TABLE billing_ledger ENABLE TRIGGER billing_ledger_immutable")
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE IF EXISTS "{role}"')
        await owner.close()


async def test_bought_minutes_are_admitted_inbound_as_outbound(meter_db):
    tid = await meter_db.tenant("basic", 300)
    await meter_db.bought(tid, 250)
    await meter_db.call(tid, 300 * 60, "now() - interval '1 hour'")
    inbound, outbound = await meter_db.both(tid)
    assert outbound.remaining_minutes == 250
    assert inbound.remaining_seconds == 250 * 60


async def test_usage_is_counted_from_the_purchase_anchored_month(meter_db):
    # One call that falls on the other side of the month start for the two
    # windows: inside this calendar month but before the anchored window
    # started, or (first day of the month) the other way round.
    month_start = "date_trunc('month', now())"
    early = f"now() - {month_start} >= interval '1 day'"
    window_start = f"(CASE WHEN {early} THEN {month_start} + interval '12 hours' ELSE {month_start} - interval '12 hours' END)"
    call_at = f"(CASE WHEN {early} THEN {month_start} + interval '6 hours' ELSE {month_start} - interval '6 hours' END)"
    tid = await meter_db.tenant("basic", 300, anchor_sql=f"({window_start} - interval '1 month')")
    await meter_db.call(tid, 300 * 60, call_at)
    inbound, outbound = await meter_db.both(tid)
    assert inbound.remaining_seconds == outbound.remaining_minutes * 60


async def test_admin_zero_on_a_limited_plan_admits_nothing_inbound(meter_db):
    tid = await meter_db.tenant("basic", 0)
    inbound, outbound = await meter_db.both(tid)
    assert outbound.exhausted and not outbound.unlimited
    assert inbound.allocated_minutes == 0 and inbound.remaining_seconds == 0


async def test_pay_as_you_go_minutes_are_admitted_inbound(meter_db):
    tid = await meter_db.tenant(PAYG_PLAN_ID, 0, status="inactive")
    await meter_db.bought(tid, 125)
    inbound, outbound = await meter_db.both(tid)
    assert outbound.remaining_minutes == 125
    assert inbound.remaining_seconds == 125 * 60


async def test_an_unlimited_plan_stays_unlimited_inbound(meter_db):
    # Zero included minutes on a subscription plan is the unlimited sentinel.
    tid = await meter_db.tenant(await meter_db.unlimited_plan(), 0)
    await meter_db.call(tid, 100_000 * 60, "now() - interval '1 hour'")
    inbound, outbound = await meter_db.both(tid)
    assert outbound.unlimited
    assert inbound.allocated_minutes is None and inbound.remaining_seconds is None


async def test_live_inbound_reservations_still_count_in_full(meter_db):
    tid = await meter_db.tenant("basic", 300)
    await meter_db.call(tid, None, "now() - interval '1 minute'", direction="inbound",
                        billing_status="reserved", reserved=600)
    inbound, outbound = await meter_db.both(tid)
    assert outbound.remaining_minutes == 300
    assert inbound.accounted_seconds == 600
    assert inbound.remaining_seconds == 300 * 60 - 600
