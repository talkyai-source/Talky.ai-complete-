"""Pay as you go against a migrated PostgreSQL, as a restricted NOSUPERUSER NOBYPASSRLS role.

Owner decision 31 Oct 2026: prepaid minutes without a subscription, prices kept in
the database so they can change without a release, and no plan limits beyond
what the account pays for.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_pool
from app.api.v1.endpoints import plans
from app.api.v1.endpoints.admin import billing_packages
from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.core.error_handlers import register_error_handlers
from app.domain.services.audit_logger import AuditLogger
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.payg import PAYG, PAYG_PLAN_ID, SUBSCRIBER, PurchaseRefused, tenant_purchase_audience
from app.domain.services.topup_service import TopupError, TopupService
from app.domain.services.topup_usage import record_topup_usage

pytestmark = pytest.mark.integration


def _dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    target = urlparse(dsn)
    if target.hostname not in {"127.0.0.1", "localhost"} or not target.path.endswith("_test"):
        pytest.fail("Pay-as-you-go PostgreSQL tests require a disposable local *_test database")
    return dsn


@pytest_asyncio.fixture
async def payg_db():
    dsn = _dsn()
    owner = await asyncpg.connect(dsn, timeout=5, command_timeout=20)
    role = "payg_app_" + uuid4().hex
    tenants = {key: uuid4() for key in ("new", "subscriber", "suspended", "unlimited")}
    users = {"staff": uuid4(), "member": uuid4()}
    unlimited_plan = "test_unlimited_" + uuid4().hex[:8]
    pool = audit = None
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        await owner.execute("SET app.bypass_rls = 'on'")
        await owner.execute(
            "INSERT INTO plans (id, name, price, minutes) VALUES ($1, 'Test unlimited', 10, 0)", unlimited_plan)
        rows = {
            "new": (None, 0, "inactive", None),
            "subscriber": ("basic", 300, "active", "sub_" + uuid4().hex[:12]),
            "suspended": (None, 0, "suspended", None),
            "unlimited": (unlimited_plan, 0, "active", "sub_" + uuid4().hex[:12]),
        }
        for key, (plan_id, minutes, status, subscription) in rows.items():
            await owner.execute(
                """INSERT INTO tenants (id, business_name, plan_id, minutes_allocated, minutes_used,
                       subscription_status, stripe_subscription_id)
                   VALUES ($1, 'Synthetic pay as you go', $2, $3, 0, $4, $5)""",
                tenants[key], plan_id, minutes, status, subscription)
        for key, profile_role, tenant_key in (("staff", "platform_admin", None), ("member", "user", "new")):
            await owner.execute(
                """INSERT INTO user_profiles (id, email, name, tenant_id, role, is_active, is_verified, email_verified_at)
                   VALUES ($1, $2, 'Synthetic', $3, $4, TRUE, TRUE, now())""",
                users[key], f"{users[key]}@example.invalid", tenants[tenant_key] if tenant_key else None, profile_role)

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6, timeout=5, command_timeout=20,
                                         init=_register_jsonb_codecs, setup=setup)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            assert tuple(flags.values()) == (False, False)
        audit = AuditLogger(pool)
        current = {"user": None}
        app = FastAPI()
        app.include_router(plans.router, prefix="/api/v1")
        app.include_router(billing_packages.router, prefix="/api/v1/admin")
        register_error_handlers(app)
        app.dependency_overrides[get_db_pool] = lambda: pool
        app.dependency_overrides[get_audit_logger] = lambda: audit
        app.dependency_overrides[get_current_user] = lambda: current["user"]

        async def http(who, method, path, **kwargs):
            current["user"] = None if who is None else CurrentUser(
                id=str(users[who]), email="x@example.invalid",
                role="platform_admin" if who == "staff" else "user",
                tenant_id=None if who == "staff" else str(tenants["new"]), name="Synthetic")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.request(method, f"/api/v1{path}", **kwargs)

        yield SimpleNamespace(owner=owner, pool=pool, tenants=tenants, users=users, http=http,
                              service=TopupService(pool))
    finally:
        if audit is not None:
            try:
                await audit.aclose()
            except Exception:
                pass
        if pool is not None:
            await pool.close()
        ids = list(tenants.values())
        async with owner.transaction():
            await owner.execute("SET LOCAL app.bypass_rls = 'on'")
            # billing_ledger is append-only by an ALWAYS trigger. Only this
            # disposable-database cleanup lifts it, inside one transaction that
            # restores it before commit (a failure rolls the change back too).
            await owner.execute("ALTER TABLE billing_ledger DISABLE TRIGGER billing_ledger_immutable")
            await owner.execute("DELETE FROM call_events WHERE call_id IN (SELECT id FROM calls WHERE tenant_id = ANY($1))", ids)
            await owner.execute("DELETE FROM call_legs WHERE call_id IN (SELECT id FROM calls WHERE tenant_id = ANY($1))", ids)
            await owner.execute("DELETE FROM crm_deliveries WHERE tenant_id = ANY($1)", ids)
            await owner.execute("DELETE FROM calls WHERE tenant_id = ANY($1)", ids)
            await owner.execute("DELETE FROM campaigns WHERE tenant_id = ANY($1)", ids)
            await owner.execute("DELETE FROM billing_ledger WHERE tenant_id = ANY($1)", ids)
            await owner.execute("DELETE FROM topup_orders WHERE tenant_id = ANY($1)", ids)
            await owner.execute("DELETE FROM audit_logs WHERE actor_id = ANY($1::uuid[]) OR tenant_id = ANY($2)",
                                list(users.values()), ids)
            await owner.execute("DELETE FROM topup_packages WHERE code LIKE 'test\\_%'")
            await owner.execute("UPDATE topup_packages SET price_cents = 5000, name = '125 minutes', is_active = TRUE "
                                "WHERE code = 'payg_125'")
            await owner.execute("DELETE FROM user_profiles WHERE id = ANY($1::uuid[])", list(users.values()))
            await owner.execute("DELETE FROM tenants WHERE id = ANY($1)", ids)
            await owner.execute("DELETE FROM plans WHERE id = $1", unlimited_plan)
            await owner.execute("ALTER TABLE billing_ledger ENABLE ALWAYS TRIGGER billing_ledger_immutable")
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


async def _audience(db, key):
    async with acquire_with_tenant(db.pool, str(db.tenants[key])) as conn:
        return await tenant_purchase_audience(conn, str(db.tenants[key]))


async def _minutes(db, key):
    async with acquire_with_tenant(db.pool, str(db.tenants[key])) as conn:
        return await compute_minutes_status(conn, str(db.tenants[key]))


async def _buy(db, key, code, audience):
    order = await db.service.create_order(tenant_id=str(db.tenants[key]), user_id=None, package_code=code,
                                          audience=audience)
    session = "cs_test_" + uuid4().hex
    await db.service.attach_session(str(order["id"]), session)
    assert await db.service.credit_paid_order(session_id=session, event_id="evt_" + uuid4().hex)
    return order


async def _plan_of(db, key):
    return await db.owner.fetchval("SELECT plan_id FROM tenants WHERE id = $1", db.tenants[key])


async def test_migration_seeds_a_prepaid_plan_and_pay_as_you_go_bundles(payg_db):
    db = payg_db
    plan = await db.owner.fetchrow("SELECT price, minutes, billing_model FROM plans WHERE id = $1", PAYG_PLAN_ID)
    assert (float(plan["price"]), plan["minutes"], plan["billing_model"]) == (0.0, 0, "prepaid")
    assert {p["code"] for p in await db.service.packages(audience=PAYG)} >= {"payg_125", "payg_175", "payg_250"}
    assert all(p["audience"] == SUBSCRIBER for p in await db.service.packages(audience=SUBSCRIBER))


async def test_who_buys_which_bundles_comes_from_the_account(payg_db):
    db = payg_db
    assert await _audience(db, "new") == PAYG
    assert await _audience(db, "subscriber") == SUBSCRIBER
    with pytest.raises(PurchaseRefused):
        await _audience(db, "suspended")


async def test_paying_for_a_bundle_moves_a_new_account_to_pay_as_you_go_with_exactly_those_minutes(payg_db):
    db = payg_db
    assert (await _minutes(db, "new")).remaining_minutes == 0

    await _buy(db, "new", "payg_125", PAYG)

    assert await _plan_of(db, "new") == PAYG_PLAN_ID
    status = await _minutes(db, "new")
    assert (status.state, status.unlimited, status.remaining_minutes) == ("known", False, 125)


async def test_calls_use_up_pay_as_you_go_minutes_and_the_month_close_records_them(payg_db):
    db = payg_db
    await _buy(db, "new", "payg_125", PAYG)
    campaign = uuid4()
    await db.owner.execute("SET app.bypass_rls = 'on'")
    await db.owner.execute("INSERT INTO campaigns (id, tenant_id, name, status, direction) "
                           "VALUES ($1, $2, 'Synthetic', 'running', 'outbound')", campaign, db.tenants["new"])
    await db.owner.execute("INSERT INTO calls (id, tenant_id, campaign_id, phone_number, status, duration_seconds, "
                           "direction) VALUES ($1, $2, $3, '+15550003001', 'completed', 120, 'outbound')",
                           uuid4(), db.tenants["new"], campaign)

    assert (await _minutes(db, "new")).remaining_minutes == 123
    async with acquire_with_tenant(db.pool, str(db.tenants["new"])) as conn:
        assert await record_topup_usage(conn, str(db.tenants["new"]), close_current=True) == 2


async def test_bundles_are_sold_only_to_their_own_audience(payg_db):
    db = payg_db
    with pytest.raises(TopupError):
        await db.service.create_order(tenant_id=str(db.tenants["subscriber"]), user_id=None,
                                      package_code="payg_125", audience=SUBSCRIBER)
    with pytest.raises(TopupError):
        await db.service.create_order(tenant_id=str(db.tenants["new"]), user_id=None,
                                      package_code="usd_125", audience=PAYG)


async def test_a_subscriber_top_up_keeps_its_plan(payg_db):
    db = payg_db
    await _buy(db, "subscriber", "usd_125", SUBSCRIBER)

    assert await _plan_of(db, "subscriber") == "basic"
    assert (await _minutes(db, "subscriber")).remaining_minutes == 425


async def test_an_unlimited_subscription_plan_stays_unlimited(payg_db):
    status = await _minutes(payg_db, "unlimited")

    assert status.unlimited is True


async def test_a_price_change_applies_to_new_orders_and_never_to_existing_ones(payg_db):
    db = payg_db
    before = await db.service.create_order(tenant_id=str(db.tenants["new"]), user_id=None,
                                           package_code="payg_125", audience=PAYG)

    response = await db.http("staff", "PATCH", "/admin/billing/packages/payg_125", json={"price_cents": 6250})

    assert response.status_code == 200, response.text
    assert response.json()["price_cents"] == 6250
    after = await db.service.create_order(tenant_id=str(db.tenants["new"]), user_id=None,
                                          package_code="payg_125", audience=PAYG)
    assert (before["price_cents"], after["price_cents"]) == (5000, 6250)
    stored = await db.owner.fetchval("SELECT price_cents FROM topup_orders WHERE id = $1", before["id"])
    assert stored == 5000
    logged = await db.owner.fetchval(
        "SELECT count(*) FROM audit_logs WHERE actor_id = $1 AND action = 'billing.package.update'",
        db.users["staff"])
    assert logged == 1


async def test_an_admin_can_add_a_bundle_and_take_one_off_sale(payg_db):
    db = payg_db
    created = await db.http("staff", "POST", "/admin/billing/packages", json={
        "code": "test_payg_500", "name": "500 minutes", "minutes": 500, "price_cents": 18000,
        "currency": "USD", "audience": "payg", "sort_order": 40})
    assert created.status_code == 201, created.text
    assert "test_payg_500" in {p["code"] for p in await db.service.packages(audience=PAYG)}

    off = await db.http("staff", "PATCH", "/admin/billing/packages/test_payg_500", json={"is_active": False})

    assert off.status_code == 200, off.text
    assert "test_payg_500" not in {p["code"] for p in await db.service.packages(audience=PAYG)}


async def test_only_a_platform_admin_can_change_prices(payg_db):
    db = payg_db
    response = await db.http("member", "PATCH", "/admin/billing/packages/payg_125", json={"price_cents": 1})

    assert response.status_code == 403
    assert await db.owner.fetchval("SELECT price_cents FROM topup_packages WHERE code = 'payg_125'") == 5000


async def test_the_pricing_page_reads_current_pay_as_you_go_prices_without_sign_in(payg_db):
    db = payg_db
    response = await db.http(None, "GET", "/plans/payg")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["id"] == PAYG_PLAN_ID
    assert {p["code"] for p in body["packages"]} >= {"payg_125", "payg_175", "payg_250"}
    assert all(p["minutes"] > 0 and p["price_cents"] > 0 for p in body["packages"])
