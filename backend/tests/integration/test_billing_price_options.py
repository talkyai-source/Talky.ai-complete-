"""CP02 constraints on a migrated disposable DB, including real NOBYPASSRLS.

No provider is called. Public-table tests use the actual Alembic schema. The
backfill case executes the migration's SQL in a private namespace cloned from
the real legacy table shapes, so its pre-migration rows can be inspected.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _scoped(fixture, tenant=None, *, platform=False):
    async with fixture.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{fixture.role}"')
            await conn.execute(
                "SELECT set_config('app.bypass_rls',$1,true)", "on" if platform else "off"
            )
            await conn.execute(
                "SELECT set_config('app.current_tenant_id',$1,true)", str(tenant) if tenant else ""
            )
            yield conn


@pytest_asyncio.fixture
async def billing_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("CP02 integration accepts only a disposable localhost *_test database")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6, timeout=5, command_timeout=10)
    suffix = uuid4().hex
    fixture = SimpleNamespace(
        pool=pool,
        role="cp02_" + suffix,
        plan="cp02_" + suffix,
        tenants=[uuid4(), uuid4()],
        option=uuid4(),
    )
    try:
        async with acquire_with_tenant(pool, None) as conn:
            await conn.execute(f'CREATE ROLE "{fixture.role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{fixture.role}"')
            await conn.execute(
                f'GRANT SELECT,INSERT,UPDATE,DELETE ON plan_price_options,billing_checkout_attempts TO "{fixture.role}"'
            )
            await conn.execute(
                "INSERT INTO plans(id,name,price,minutes) VALUES($1,'Synthetic CP02',19,120)",
                fixture.plan,
            )
            await conn.executemany(
                "INSERT INTO tenants(id,business_name,plan_id) VALUES($1,'Synthetic CP02',$2)",
                [(tenant, fixture.plan) for tenant in fixture.tenants],
            )
            await conn.execute(
                """INSERT INTO plan_price_options
                (id,plan_id,stripe_price_id,stripe_product_id,kind,interval,amount_minor,
                 currency,currency_exponent,provider_mode,active,verified_at)
                VALUES($1,$2,$3,$4,'stripe','month',1900,'usd',2,'test',TRUE,NOW())""",
                fixture.option,
                fixture.plan,
                "price_" + suffix,
                "prod_" + suffix,
            )
        yield fixture
    finally:
        async with acquire_with_tenant(pool, None) as conn:
            await conn.execute(
                "DELETE FROM billing_checkout_attempts WHERE tenant_id=ANY($1::uuid[])",
                fixture.tenants,
            )
            await conn.execute(
                "DELETE FROM subscriptions WHERE tenant_id=ANY($1::uuid[])", fixture.tenants
            )
            # The signed ledger intentionally cannot be deleted, even by an
            # owner. Keep synthetic ledger rows in this disposable database;
            # UUID-scoped tests never reuse their tenant identities.
            await conn.execute("DELETE FROM plan_price_options WHERE plan_id=$1", fixture.plan)
            # Invoice observations are deliberately immutable, including during
            # parent deletion. Retain only their UUID-scoped synthetic parents
            # in this disposable DB instead of disabling evidence protection.
            has_snapshots = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM invoice_snapshots WHERE tenant_id=ANY($1::uuid[]))", fixture.tenants,
            ) if await conn.fetchval("SELECT to_regclass('public.invoice_snapshots') IS NOT NULL") else False
            if not has_snapshots:
                await conn.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", fixture.tenants)
                await conn.execute("DELETE FROM plans WHERE id=$1", fixture.plan)
            await conn.execute(f'DROP OWNED BY "{fixture.role}"')
            await conn.execute(f'DROP ROLE "{fixture.role}"')
        await pool.close()


async def _insert_attempt(conn, fixture, tenant, *, status="creating", identity=None):
    identity = identity or uuid4()
    snapshot = {
        "price_option_id": str(fixture.option),
        "amount_minor": 1900,
        "currency": "usd",
        "interval": "month",
    }
    await conn.execute(
        """INSERT INTO billing_checkout_attempts
        (id,tenant_id,price_option_id,request_hash,snapshot,status)
        VALUES($1,$2,$3,$4,$5::jsonb,$6)""",
        identity,
        tenant,
        fixture.option,
        "a" * 64,
        json.dumps(snapshot),
        status,
    )
    return identity


async def test_actual_tables_force_rls_and_tenants_cannot_read_or_write_other_attempts(billing_db):
    fixture = billing_db
    first, second = fixture.tenants
    async with _scoped(fixture, first) as conn:
        flags = await conn.fetchrow(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert not flags["rolsuper"] and not flags["rolbypassrls"]
        identity = await _insert_attempt(conn, fixture, first)
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_checkout_attempts WHERE id=$1", identity
            )
            == 1
        )
    async with _scoped(fixture, second) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_checkout_attempts WHERE id=$1", identity
            )
            == 0
        )
        assert (
            await conn.execute(
                "UPDATE billing_checkout_attempts SET status='completed' WHERE id=$1", identity
            )
            == "UPDATE 0"
        )
        assert (
            await conn.execute("DELETE FROM billing_checkout_attempts WHERE id=$1", identity)
            == "DELETE 0"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await _insert_attempt(conn, fixture, first)
    async with _scoped(fixture) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_checkout_attempts WHERE id=$1", identity
            )
            == 0
        )
    async with fixture.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE oid IN ('plan_price_options'::regclass,'billing_checkout_attempts'::regclass)"""
        )
        assert len(rows) == 2 and all(
            row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows
        )


async def test_only_explicit_platform_context_can_read_or_write_price_options(billing_db):
    fixture = billing_db
    async with _scoped(fixture, fixture.tenants[0]) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM plan_price_options WHERE id=$1", fixture.option
            )
            == 0
        )
        assert (
            await conn.execute(
                "UPDATE plan_price_options SET active=FALSE WHERE id=$1", fixture.option
            )
            == "UPDATE 0"
        )
        assert (
            await conn.execute("DELETE FROM plan_price_options WHERE id=$1", fixture.option)
            == "DELETE 0"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO plan_price_options
                    (plan_id,kind,interval,amount_minor,currency,currency_exponent,provider_mode)
                    VALUES($1,'free','month',0,'usd',2,'free')""",
                    fixture.plan,
                )
    async with _scoped(fixture, platform=True) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM plan_price_options WHERE id=$1", fixture.option
            )
            == 1
        )
        assert (
            await conn.execute(
                "UPDATE plan_price_options SET active=FALSE WHERE id=$1", fixture.option
            )
            == "UPDATE 1"
        )
        free = await conn.fetchval(
            """INSERT INTO plan_price_options
            (plan_id,kind,interval,amount_minor,currency,currency_exponent,provider_mode)
            VALUES($1,'free','month',0,'usd',2,'free') RETURNING id""",
            fixture.plan,
        )
        assert free is not None


@pytest.mark.parametrize("status", ["creating", "ready", "unknown"])
async def test_each_outstanding_state_blocks_a_second_purchase(billing_db, status):
    fixture = billing_db
    async with _scoped(fixture, fixture.tenants[0]) as conn:
        await _insert_attempt(conn, fixture, fixture.tenants[0], status=status)
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await _insert_attempt(conn, fixture, fixture.tenants[0])


async def test_concurrent_requests_create_only_one_outstanding_attempt(billing_db):
    fixture = billing_db
    start = asyncio.Event()
    ready = 0

    async def create():
        nonlocal ready
        async with _scoped(fixture, fixture.tenants[0]) as conn:
            ready += 1
            if ready == 6:
                start.set()
            await start.wait()
            return await _insert_attempt(conn, fixture, fixture.tenants[0])

    tasks = [asyncio.create_task(create()) for _ in range(6)]
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=15)
    assert sum(isinstance(item, asyncpg.UniqueViolationError) for item in results) == 5
    assert sum(not isinstance(item, Exception) for item in results) == 1
    async with _scoped(fixture, fixture.tenants[0]) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_checkout_attempts WHERE tenant_id=$1",
                fixture.tenants[0],
            )
            == 1
        )


@pytest.mark.parametrize("terminal", ["completed", "expired", "failed"])
async def test_terminal_attempt_preserves_purchase_history_and_unlocks_new_attempt(
    billing_db, terminal
):
    fixture = billing_db
    async with _scoped(fixture, fixture.tenants[0]) as conn:
        old = await _insert_attempt(conn, fixture, fixture.tenants[0], status="ready")
        await conn.execute(
            """UPDATE billing_checkout_attempts SET stripe_customer_id='cus_fixture',
            stripe_session_id=$2,checkout_url='https://checkout.stripe.com/synthetic',
            provider_expires_at=NOW()+INTERVAL '1 hour' WHERE id=$1""",
            old,
            "cs_" + old.hex,
        )
        history = "request_hash,snapshot,created_at,stripe_customer_id,stripe_session_id,checkout_url,provider_expires_at"
        before = await conn.fetchrow(
            f"SELECT {history} FROM billing_checkout_attempts WHERE id=$1", old
        )
        await conn.execute(
            "UPDATE billing_checkout_attempts SET status=$2 WHERE id=$1", old, terminal
        )
        new = await _insert_attempt(conn, fixture, fixture.tenants[0])
        after = await conn.fetchrow(
            f"SELECT {history} FROM billing_checkout_attempts WHERE id=$1", old
        )
        assert old != new and dict(after) == dict(before)
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_checkout_attempts WHERE tenant_id=$1",
                fixture.tenants[0],
            )
            == 2
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_count": 12},
        {"interval": "week"},
        {"amount_minor": -1},
        {"currency": "USD"},
        {"currency_exponent": 1},
        {"kind": "free"},
        {"amount_minor": 0},
        {"stripe_price_id": None},
        {"stripe_product_id": None},
        {"provider_mode": "free"},
        {"verified_at": None},
    ],
)
async def test_invalid_catalog_shapes_fail_database_constraints(billing_db, changes):
    # Column names below are test-owned, never caller input.
    fixture = billing_db
    column, value = next(iter(changes.items()))
    async with _scoped(fixture, platform=True) as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    f'UPDATE plan_price_options SET "{column}"=$2 WHERE id=$1',
                    fixture.option,
                    value,
                )


async def test_migration_seeds_only_explicit_free_and_preserves_legacy_rows(
    billing_db, monkeypatch
):
    fixture = billing_db
    schema = "cp02_migration_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(
        "cp02_price_migration",
        Path(__file__).resolve().parents[2] / "Alembic/versions/0053_billing_price_options.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(execute=lambda statement: statements.append(str(statement))),
    )
    migration.upgrade()
    async with fixture.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            try:
                await conn.execute(f'SET LOCAL search_path TO "{schema}", public')
                for table in ("plans", "tenants", "subscriptions"):
                    await conn.execute(
                        f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)'
                    )
                await conn.execute(
                    """INSERT INTO plans(id,name,price,minutes,stripe_price_id,billing_period) VALUES
                    ('free','Explicit free',0,30,NULL,'monthly'),
                    ('paid','Paid legacy',19,120,'price_legacy','monthly'),
                    ('annual','Annual legacy',190,120,'price_annual','yearly'),
                    ('zero_stripe','Zero Stripe price',0,30,'price_zero','monthly')"""
                )
                tenant = uuid4()
                await conn.execute(
                    """INSERT INTO tenants(id,business_name,plan_id,minutes_allocated,minutes_used)
                    VALUES($1,'Legacy subscription fixture','annual',153,17)""",
                    tenant,
                )
                await conn.execute(
                    """INSERT INTO subscriptions
                    (tenant_id,stripe_subscription_id,stripe_customer_id,plan_id,status,current_period_start,current_period_end)
                    VALUES($1,'sub_fixture','cus_fixture','annual','active','2026-01-01Z','2027-01-01Z')""",
                    tenant,
                )
                before = {
                    table: await conn.fetch(
                        f"SELECT to_jsonb(row) AS data FROM {table} row ORDER BY id"
                    )
                    for table in ("plans", "tenants", "subscriptions")
                }
                for statement in statements:
                    await conn.execute(statement.replace("public.", f'"{schema}".'))
                after = {
                    table: await conn.fetch(
                        f"SELECT to_jsonb(row) AS data FROM {table} row ORDER BY id"
                    )
                    for table in ("plans", "tenants", "subscriptions")
                }
                assert before == after
                options = await conn.fetch(
                    "SELECT plan_id,kind,interval,amount_minor,provider_mode,active,verified_at FROM plan_price_options"
                )
                assert len(options) == 1
                option = options[0]
                assert (
                    option["plan_id"],
                    option["kind"],
                    option["interval"],
                    option["amount_minor"],
                    option["provider_mode"],
                ) == ("free", "free", "month", 0, "free")
                assert option["active"] and option["verified_at"] is not None
                # Exercise the CURRENT source migration, even if the disposable
                # public schema was migrated before a reviewed constraint fix.
                paid_id = await conn.fetchval(
                    """INSERT INTO plan_price_options
                    (plan_id,kind,interval,amount_minor,currency,currency_exponent,
                     provider_mode,stripe_price_id,stripe_product_id)
                    VALUES('paid','stripe','month',1900,'usd',2,'test','price_fixture','prod_fixture')
                    RETURNING id"""
                )
                for column, invalid in (
                    ("stripe_price_id", "pricexbad"),
                    ("stripe_price_id", "price_"),
                    ("stripe_price_id", "price_bad_suffix"),
                    ("stripe_product_id", "prodxwrong"),
                    ("stripe_product_id", "prod_"),
                    ("stripe_product_id", "prod_bad_suffix"),
                ):
                    with pytest.raises(asyncpg.CheckViolationError):
                        async with conn.transaction():
                            await conn.execute(
                                f'UPDATE plan_price_options SET "{column}"=$2 WHERE id=$1',
                                paid_id,
                                invalid,
                            )
                with pytest.raises(RuntimeError, match="retained"):
                    migration.downgrade()
            finally:
                await conn.execute("SET LOCAL search_path TO public")
                await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize("provider_fails", [False, True])
async def test_real_catalog_raw_json_uuid_sdk_shapes_and_provider_cleanup(
    billing_db, monkeypatch, provider_fails
):
    """Raw asyncpg intentionally returns JSON strings; SDK returns StripeObject."""
    import stripe
    from app.domain.services import billing_catalog as catalog

    fixture = billing_db
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE plans SET features=$2::jsonb,not_included=NULL WHERE id=$1",
            fixture.plan,
            json.dumps(["Synthetic feature"]),
        )
        free_id = await conn.fetchval(
            """INSERT INTO plan_price_options
            (plan_id,kind,interval,amount_minor,currency,currency_exponent,provider_mode,active,verified_at)
            VALUES($1,'free','month',0,'usd',2,'free',TRUE,NOW()) RETURNING id""",
            fixture.plan,
        )
        raw = await conn.fetchval("SELECT features FROM plans WHERE id=$1", fixture.plan)
        assert isinstance(raw, str)
    resolved = await catalog.resolve_checkout_option(fixture.pool, fixture.option)
    free = await catalog.resolve_checkout_option(fixture.pool, free_id)
    assert resolved["id"] == str(fixture.option) and resolved["free"] is False
    assert free["id"] == str(free_id) and free["free"] is True
    clients = []

    class HttpClient:
        def __init__(self, **kwargs):
            self.async_closed = self.sync_closed = False
            clients.append(self)

        async def close_async(self):
            self.async_closed = True

        def close(self):
            self.sync_closed = True

    async def retrieve(price_id):
        if provider_fails:
            raise TimeoutError("synthetic provider response lost")
        assert price_id == resolved["stripe_price_id"]
        return stripe.Price.construct_from(
            {
                "id": price_id,
                "active": True,
                "type": "recurring",
                "billing_scheme": "per_unit",
                "livemode": False,
                "unit_amount": 1900,
                "currency": "usd",
                "product": {"id": resolved["stripe_product_id"]},
                "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"},
            },
            "sk_test_synthetic",
        )

    monkeypatch.setattr(stripe, "HTTPXClient", HttpClient)
    monkeypatch.setattr(
        stripe,
        "StripeClient",
        lambda *args, **kwargs: SimpleNamespace(
            v1=SimpleNamespace(prices=SimpleNamespace(retrieve_async=retrieve))
        ),
    )
    plans = await catalog.list_plan_catalog(fixture.pool)
    plan = next(item for item in plans if item["id"] == fixture.plan)
    assert plan["features"] == ["Synthetic feature"] and plan["not_included"] == []
    paid = next(item for item in plan["price_options"] if item["id"] == str(fixture.option))
    assert paid["checkout_available"] is not provider_fails
    assert bool(paid["unavailable_reason"]) is provider_fails
    assert next(item for item in plan["price_options"] if item["id"] == str(free_id))[
        "checkout_available"
    ]
    assert "stripe_price_id" not in paid and "provider_mode" not in paid
    assert clients and all(client.async_closed and client.sync_closed for client in clients)
