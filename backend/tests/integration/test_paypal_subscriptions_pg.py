"""PayPal plan service against real PostgreSQL (CI only: needs a disposable *_test database).

The PayPal client is a fake that models PayPal's answers; this proves the SQL,
the locks, the partial unique index and row security, not PayPal itself.
"""

from __future__ import annotations

import asyncio
import copy
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.services import billing_catalog as catalog
from app.domain.services import billing_checkout as checkout
from app.domain.services import paypal_subscriptions as ps
from app.domain.services.billing_checkout import CheckoutError
from tests.integration.test_billing_price_options import _scoped
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration


def _now():
    return datetime.now(UTC)


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakePayPal:
    """PayPal's side: subscriptions by id, idempotent on the request id."""

    def __init__(self):
        self.remote, self.txns, self.calls, self._by_request = {}, {}, [], {}
        self.create_delay = 0.0

    async def create_subscription(self, **kw):
        self.calls.append("create")
        await asyncio.sleep(self.create_delay)
        sid = self._by_request.setdefault(kw["request_id"], "".join(["I", "-", "pg", uuid4().hex[:12]]))
        self.remote[sid] = {
            "id": sid, "status": "APPROVAL_PENDING", "plan_id": kw["plan_id"], "custom_id": kw["custom_id"],
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/subscribe?x=1"}],
        }
        return copy.deepcopy(self.remote[sid])

    async def get_subscription(self, sid):
        self.calls.append("get")
        return copy.deepcopy(self.remote[sid])

    async def cancel_subscription(self, sid, *, reason):
        self.calls.append("cancel")
        self.remote[sid]["status"] = "CANCELLED"

    async def list_subscription_transactions(self, sid, *, start_time, end_time):
        self.calls.append("list")
        return copy.deepcopy(self.txns.get(sid, []))

    def pay(self, sid, *, value="19.00"):
        """The customer approved and the first payment went through."""
        sub = self.remote[sid]
        sub["status"] = "ACTIVE"
        sub["billing_info"] = {
            "failed_payments_count": 0,
            "next_billing_time": _iso(_now() + timedelta(days=30)),
            "last_payment": {"amount": {"currency_code": "USD", "value": value}, "time": _iso(_now())},
        }
        self.txns[sid] = [{
            "id": "".join(["T", "-", "pg", sid[-8:]]), "status": "COMPLETED", "time": _iso(_now()),
            "amount_with_breakdown": {"gross_amount": {"currency_code": "USD", "value": value}},
        }]


def _approval_link(sub, *, env):
    for link in sub.get("links") or []:
        if link.get("rel") == "approve":
            return link["href"]
    return None


@pytest_asyncio.fixture
async def pp_db(billing_db, monkeypatch):  # noqa: F811 - imported pytest fixture
    fixture = billing_db
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    monkeypatch.setattr(
        checkout, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://app.example", allowed_origins=["https://app.example"]),
    )
    monkeypatch.setattr(ps, "paypal_provider_mode", lambda: "test")
    monkeypatch.setattr(ps, "paypal_env", lambda: "sandbox")
    monkeypatch.setattr(ps._pc, "subscription_approval_url", _approval_link, raising=False)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        for grant in (
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON tenants,subscriptions,billing_checkout_attempts,'
            f'paypal_subscriptions,paypal_subscription_payments TO "{fixture.role}"',
            f'GRANT SELECT ON plans,billing_ledger,calls,call_legs,paypal_plan_options TO "{fixture.role}"',
            f'GRANT SELECT,INSERT,UPDATE ON paypal_webhook_events TO "{fixture.role}"',
        ):
            await conn.execute(grant)
        await conn.execute(
            "UPDATE tenants SET subscription_status='inactive', minutes_allocated=0, minutes_used=0 "
            "WHERE id=ANY($1::uuid[])", fixture.tenants,
        )
        fixture.paypal_plan = "".join(["P", "-", "pg", uuid4().hex[:14]])
        await conn.execute(
            """INSERT INTO paypal_plan_options
            (price_option_id, provider_mode, paypal_product_id, paypal_plan_id, interval, amount_minor,
             currency, active, verified_at)
            VALUES ($1, 'test', $2, $3, 'month', 1900, 'usd', TRUE, NOW())""",
            fixture.option, "".join(["PROD", "-", "pg", uuid4().hex[:12]]), fixture.paypal_plan,
        )

    async def role_setup(conn):
        await conn.execute(f'SET ROLE "{fixture.role}"')

    pool = await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"], min_size=1, max_size=6, timeout=5, command_timeout=10,
        init=_register_jsonb_codecs, setup=role_setup,
    )
    fixture.client = FakePayPal()
    fixture.service_pool = pool
    fixture.svc = ps.PayPalSubscriptionService(pool, client=fixture.client)
    try:
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
        yield fixture
    finally:
        await pool.close()
        async with acquire_with_tenant(fixture.pool, None) as conn:
            ids = await conn.fetch(
                "SELECT id FROM paypal_subscriptions WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            patterns = [f"local:review:plan:{row['id']}:%" for row in ids]
            await conn.execute(
                "DELETE FROM paypal_subscription_payments WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            await conn.execute("DELETE FROM paypal_subscriptions WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            await conn.execute("DELETE FROM paypal_plan_options WHERE price_option_id=$1", fixture.option)
            await conn.execute("DELETE FROM paypal_webhook_events WHERE event_id LIKE ANY($1::text[])", patterns)


async def _start(fixture, *, tenant=None, request_id=None):
    return await fixture.svc.start_checkout(
        tenant_id=str(tenant or fixture.tenants[0]), request_id=str(request_id or uuid4()),
        price_option_id=str(fixture.option),
    )


async def _tenant(fixture, tenant=None):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return dict(await conn.fetchrow(
            """SELECT plan_id, subscription_status, minutes_allocated, minutes_used, allowance_anchor_at
            FROM tenants WHERE id=$1""", tenant or fixture.tenants[0]))


async def _row(fixture, request_id):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return dict(await conn.fetchrow("SELECT * FROM paypal_subscriptions WHERE id=$1", request_id))


async def _payments(fixture):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM paypal_subscription_payments WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)


async def _activated(fixture):
    rid = uuid4()
    out = await _start(fixture, request_id=rid)
    fixture.client.pay(out["session_id"])
    return rid, out["session_id"]


async def test_start_creates_an_approval_pending_row(pp_db):
    fixture = pp_db
    rid = uuid4()
    out = await _start(fixture, request_id=rid)
    row = await _row(fixture, rid)
    assert out["state"] == "open" and out["checkout_url"].startswith("https://www.sandbox.paypal.com/")
    assert row["status"] == "approval_pending" and row["paypal_subscription_id"] == out["session_id"]
    assert row["paypal_plan_id"] == fixture.paypal_plan and row["activated_at"] is None
    assert (await _tenant(fixture))["plan_id"] == fixture.plan
    assert (await _tenant(fixture))["subscription_status"] == "inactive"
    # a replay of the same request id is the same purchase
    again = await _start(fixture, request_id=rid)
    assert again["session_id"] == out["session_id"] and fixture.client.calls.count("create") == 1


async def test_a_paid_subscription_grants_the_plan_exactly_once(pp_db):
    fixture = pp_db
    rid, sid = await _activated(fixture)
    out = await fixture.svc.get_attempt(tenant_id=str(fixture.tenants[0]), request_id=str(rid))
    assert out["state"] == "activated"
    state = await _tenant(fixture)
    assert state["plan_id"] == fixture.plan and state["subscription_status"] == "active"
    assert state["minutes_allocated"] == 120 and state["allowance_anchor_at"] is not None
    row = await _row(fixture, rid)
    assert row["status"] == "active" and row["activated_at"] is not None and row["current_period_end"] > _now()
    assert await _payments(fixture) == 1
    # usage after the grant must survive a second sync (a second grant would reset it)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET minutes_used=9 WHERE id=$1", fixture.tenants[0])
    again = await fixture.svc.sync(sid)
    assert again.activated and (await _tenant(fixture))["minutes_used"] == 9
    assert await _payments(fixture) == 1


async def test_a_second_purchase_by_the_same_tenant_is_refused(pp_db):
    fixture = pp_db
    await _activated(fixture)
    await fixture.svc.sync(next(iter(fixture.client.remote)))
    with pytest.raises(CheckoutError) as raised:
        await _start(fixture)
    assert raised.value.code == "subscription_exists"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert await ps.held_subscription_exists(conn, fixture.tenants[0])
        assert await ps.has_live_subscription(conn, fixture.tenants[0])
        assert not await ps.has_live_subscription(conn, fixture.tenants[1])


async def test_concurrent_starts_leave_one_live_row(pp_db):
    fixture = pp_db
    fixture.client.create_delay = 0.5
    results = await asyncio.wait_for(
        asyncio.gather(_start(fixture), _start(fixture), return_exceptions=True), timeout=20)
    assert sum(isinstance(item, dict) for item in results) == 1
    errors = [item for item in results if isinstance(item, CheckoutError)]
    assert len(errors) == 1 and errors[0].code == "checkout_outstanding"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM paypal_subscriptions WHERE tenant_id=$1", fixture.tenants[0]) == 1


async def test_the_partial_unique_index_allows_one_live_row_per_tenant(pp_db):
    fixture = pp_db
    await _start(fixture)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO paypal_subscriptions
                    (id, tenant_id, price_option_id, plan_id, provider_mode, paypal_plan_id, snapshot, status)
                    VALUES ($1, $2, $3, $4, 'test', $5, '{}'::jsonb, 'creating')""",
                    uuid4(), fixture.tenants[0], fixture.option, fixture.plan, fixture.paypal_plan)


async def test_cancel_keeps_access_until_the_period_ends_then_the_sweep_ends_it(pp_db):
    fixture = pp_db
    rid, _ = await _activated(fixture)
    await fixture.svc.get_attempt(tenant_id=str(fixture.tenants[0]), request_id=str(rid))
    out = await fixture.svc.cancel_for_tenant(str(fixture.tenants[0]))
    assert out["status"] == "active" and out["cancel_at_period_end"]
    row = await _row(fixture, rid)
    assert row["status"] == "active" and row["cancel_at_period_end"] and row["cancelled_at"] is not None
    assert (await _tenant(fixture))["subscription_status"] == "active"
    assert (await fixture.svc.sweep())["ended"] == 0  # time is left
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE paypal_subscriptions SET current_period_end = NOW() - INTERVAL '1 minute' WHERE id=$1", rid)
    assert (await fixture.svc.sweep())["ended"] == 1
    row = await _row(fixture, rid)
    assert row["status"] == "cancelled" and row["ended_at"] is not None and not row["cancel_at_period_end"]
    state = await _tenant(fixture)
    assert state["subscription_status"] == "cancelled" and state["plan_id"] == fixture.plan
    assert (await fixture.svc.sweep())["ended"] == 0
    # the tenant can buy again once the plan has ended
    assert (await _start(fixture))["state"] == "open"


async def test_activation_is_refused_when_a_stripe_subscription_appeared(pp_db):
    fixture = pp_db
    rid = uuid4()
    out = await _start(fixture, request_id=rid)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET stripe_subscription_id='sub_pg_synthetic' WHERE id=$1",
                           fixture.tenants[0])
    try:
        fixture.client.pay(out["session_id"])
        outcome = await fixture.svc.sync(out["session_id"])
        assert outcome.review and not outcome.activated
        row = await _row(fixture, rid)
        assert row["status"] == "cancelled" and row["activated_at"] is None
        assert fixture.client.remote[out["session_id"]]["status"] == "CANCELLED"
        assert (await _tenant(fixture))["minutes_allocated"] == 0
        async with acquire_with_tenant(fixture.pool, None) as conn:
            assert await conn.fetchval(
                "SELECT state FROM paypal_webhook_events WHERE event_id=$1",
                f"local:review:plan:{rid}:superseded") == "needs_review"
    finally:
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute("UPDATE tenants SET stripe_subscription_id=NULL WHERE id=$1", fixture.tenants[0])


async def test_another_tenant_cannot_see_or_change_the_row(pp_db):
    fixture = pp_db
    first, second = fixture.tenants
    rid, _ = await _activated(fixture)
    async with _scoped(fixture, second) as conn:
        flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert not flags["rolsuper"] and not flags["rolbypassrls"]
        assert await conn.fetchval("SELECT COUNT(*) FROM paypal_subscriptions WHERE id=$1", rid) == 0
        assert await conn.execute("UPDATE paypal_subscriptions SET status='failed' WHERE id=$1", rid) == "UPDATE 0"
        assert await conn.fetchval("SELECT COUNT(*) FROM paypal_plan_options") == 0  # platform-only table
    async with _scoped(fixture, first) as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM paypal_subscriptions WHERE id=$1", rid) == 1
    with pytest.raises(CheckoutError) as raised:
        await fixture.svc.get_attempt(tenant_id=str(second), request_id=str(rid))
    assert raised.value.status_code == 404
    assert await fixture.svc.cancel_for_tenant(str(second)) is None
    assert (await _row(fixture, rid))["status"] == "approval_pending"


async def test_active_without_a_payment_for_two_hours_is_cancelled_and_grants_nothing(pp_db):
    fixture = pp_db
    rid = uuid4()
    out = await _start(fixture, request_id=rid)
    sid = out["session_id"]
    fixture.client.remote[sid]["status"] = "ACTIVE"  # approved, but no payment of the price shows up
    first = await fixture.svc.sync(sid)
    assert not first.review and (await _row(fixture, rid))["status"] == "approval_pending"
    assert (await _row(fixture, rid))["approved_at"] is not None
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE paypal_subscriptions SET approved_at = NOW() - INTERVAL '3 hours' WHERE id=$1", rid)
    before = await _tenant(fixture)
    second = await fixture.svc.sync(sid)
    assert second.review and not second.activated
    row = await _row(fixture, rid)
    assert row["status"] == "cancelled" and row["ended_at"] is not None and row["activated_at"] is None
    assert await _tenant(fixture) == before
    assert fixture.client.remote[sid]["status"] == "CANCELLED"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert await conn.fetchval(
            "SELECT state FROM paypal_webhook_events WHERE event_id=$1",
            f"local:review:plan:{rid}:active_without_payment") == "needs_review"
