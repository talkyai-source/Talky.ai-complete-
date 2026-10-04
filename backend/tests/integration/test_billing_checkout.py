"""Actual checkout SQL/RLS with synthetic Stripe SDK objects, never API calls."""

from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
import stripe

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.services import billing_catalog as catalog
from app.domain.services import billing_checkout as checkout
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration


class SyntheticStripe:
    """Models provider idempotency and SDK shapes, not a provider sandbox proof."""

    billing_mode = "test"

    def __init__(self, offers):
        self.prices = {
            offer["stripe_price_id"]: stripe.Price.construct_from(
                {
                    "id": offer["stripe_price_id"],
                    "object": "price",
                    "active": True,
                    "type": "recurring",
                    "billing_scheme": "per_unit",
                    "livemode": False,
                    "unit_amount": offer["amount_minor"],
                    "currency": offer["currency"],
                    "product": {"id": offer["stripe_product_id"], "object": "product"},
                    "recurring": {
                        "interval": offer["interval"],
                        "interval_count": 1,
                        "usage_type": "licensed",
                    },
                },
                "sk_test_synthetic",
            )
            for offer in offers
        }
        self.customers, self.sessions, self.session_params = {}, {}, {}
        self.calls = []
        self.lose_next_session_response = False

    async def _stripe_call(self, resource, method, *args, **kwargs):
        self.calls.append((resource, method, args, deepcopy(kwargs)))
        if resource == "Price" and method == "retrieve":
            assert isinstance(args[0], str)
            return self.prices[args[0]]
        if resource == "Customer":
            if method == "retrieve":
                return self.customers[args[0]]
            identity = "cus_" + kwargs["idempotency_key"].split(":")[1].replace("-", "")
            self.customers.setdefault(
                identity,
                stripe.Customer.construct_from(
                    {
                        "id": identity,
                        "object": "customer",
                        "livemode": False,
                        "metadata": kwargs["metadata"],
                    },
                    "sk_test_synthetic",
                ),
            )
            return self.customers[identity]
        if resource == "checkout.Session":
            if method == "retrieve":
                return self.sessions[args[0]]
            key = kwargs["idempotency_key"]
            if key in self.session_params:
                assert kwargs == self.session_params[key], "Provider retry parameters changed"
            else:
                self.session_params[key] = deepcopy(kwargs)
            identity = "cs_" + kwargs["client_reference_id"].replace("-", "")
            price = self.prices[kwargs["line_items"][0]["price"]]
            self.sessions.setdefault(
                identity,
                stripe.checkout.Session.construct_from(
                    {
                        "id": identity,
                        "object": "checkout.session",
                        "mode": "subscription",
                        "status": "open",
                        "customer": kwargs["customer"],
                        "livemode": False,
                        "currency": price["currency"],
                        "amount_subtotal": price["unit_amount"],
                        "client_reference_id": kwargs["client_reference_id"],
                        "expires_at": kwargs["expires_at"],
                        "url": "https://checkout.stripe.com/synthetic/" + identity,
                        "metadata": kwargs["metadata"],
                    },
                    "sk_test_synthetic",
                ),
            )
            if self.lose_next_session_response:
                self.lose_next_session_response = False
                raise TimeoutError("Synthetic accepted session response lost")
            return self.sessions[identity]
        raise AssertionError((resource, method))

    def paid_subscription(self, receipt):
        session = self.sessions[receipt["session_id"]]
        params = self.session_params["subscription-checkout:" + receipt["request_id"]]
        price = self.prices[params["line_items"][0]["price"]]
        identity = "sub_" + receipt["request_id"].replace("-", "")
        session.update({"status": "complete", "payment_status": "paid", "subscription": identity})
        now = int(datetime.now(UTC).timestamp())
        return (
            stripe.Subscription.construct_from(
                {
                    "id": identity,
                    "object": "subscription",
                    "customer": {"id": session["customer"], "object": "customer"},
                    "livemode": False,
                    "status": "active",
                    "metadata": session["metadata"],
                    "items": {
                        "object": "list",
                        "data": [
                            {
                                "object": "subscription_item",
                                "id": "si_fixture",
                                "price": price,
                                "quantity": 1,
                                "current_period_start": now,
                                "current_period_end": now
                                + (365 if price["recurring"]["interval"] == "year" else 30) * 86400,
                            }
                        ],
                    },
                },
                "sk_test_synthetic",
            ),
            session,
        )


@pytest_asyncio.fixture
async def checkout_db(billing_db, monkeypatch):  # noqa: F811 - imported pytest fixture
    fixture = billing_db
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    monkeypatch.setattr(
        checkout,
        "get_settings",
        lambda: SimpleNamespace(
            frontend_url="https://app.example", allowed_origins=["https://app.example"]
        ),
    )
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON tenants,subscriptions TO "{fixture.role}"'
        )
        await conn.execute(f'GRANT SELECT ON plans,billing_ledger TO "{fixture.role}"')
        fixture.free = await conn.fetchval(
            """INSERT INTO plan_price_options
            (plan_id,kind,interval,amount_minor,currency,currency_exponent,provider_mode,active,verified_at)
            VALUES($1,'free','month',0,'usd',2,'free',TRUE,NOW()) RETURNING id""",
            fixture.plan,
        )
        fixture.year = await conn.fetchval(
            """INSERT INTO plan_price_options
            (plan_id,kind,interval,amount_minor,currency,currency_exponent,provider_mode,
             stripe_price_id,stripe_product_id,active,verified_at)
            VALUES($1,'stripe','year',19000,'usd',2,'test',$2,$3,TRUE,NOW()) RETURNING id""",
            fixture.plan,
            "price_" + uuid4().hex,
            "prod_" + uuid4().hex,
        )
        await conn.execute(
            """UPDATE tenants SET minutes_allocated=163,minutes_used=17,
            subscription_status='active' WHERE id=ANY($1::uuid[])""",
            fixture.tenants,
        )
        for tenant in fixture.tenants:
            await conn.execute(
                """INSERT INTO billing_ledger(tenant_id,kind,minutes_delta)
                VALUES($1,'topup',50),($1,'refund',-7)""",
                tenant,
            )
        offers = [
            dict(row)
            for row in await conn.fetch(
                "SELECT * FROM plan_price_options WHERE plan_id=$1 AND kind='stripe'", fixture.plan
            )
        ]

    async def role_setup(conn):
        await conn.execute(f'SET ROLE "{fixture.role}"')

    # Actual production JSON codecs and a non-owner, non-bypass service role.
    pool = await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"],
        min_size=1,
        max_size=6,
        timeout=5,
        command_timeout=10,
        init=_register_jsonb_codecs,
        setup=role_setup,
    )
    fixture.provider = SyntheticStripe(offers)
    fixture.service_pool = pool
    fixture.service = checkout.CheckoutAttempts(pool, fixture.provider)
    try:
        async with pool.acquire() as conn:
            role = await conn.fetchrow(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            assert not role["rolsuper"] and not role["rolbypassrls"]
        yield fixture
    finally:
        await pool.close()


async def _create(
    fixture, *, tenant=None, request_id=None, option_id=None, email="buyer@example.com"
):
    return await fixture.service.create(
        tenant_id=tenant or fixture.tenants[0],
        request_id=request_id or uuid4(),
        price_option_id=option_id or fixture.option,
        email=email,
        business_name="Synthetic buyer",
    )


async def _tenant(fixture):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return dict(
            await conn.fetchrow(
                """SELECT plan_id,subscription_status,stripe_subscription_id,
            minutes_allocated,minutes_used FROM tenants WHERE id=$1""",
                fixture.tenants[0],
            )
        )


async def test_free_activation_commits_once_and_preserves_signed_topups(checkout_db):
    fixture = checkout_db
    request_id = uuid4()
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE tenants SET subscription_status='inactive' WHERE id=$1", fixture.tenants[0]
        )
    first = await _create(fixture, request_id=request_id, option_id=fixture.free)
    assert (
        first["state"] == "activated" and first["checkout_url"] is None and not first["mock_mode"]
    )
    assert (await _tenant(fixture))["minutes_allocated"] == 163
    assert (await _tenant(fixture))["minutes_used"] == 0
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET minutes_used=11 WHERE id=$1", fixture.tenants[0])
    replay = await _create(fixture, request_id=request_id, option_id=fixture.free)
    assert replay == first and (await _tenant(fixture))["minutes_used"] == 11
    assert fixture.provider.calls == []


async def test_free_activation_failure_rolls_back_allowance_and_can_resume(
    checkout_db, monkeypatch
):
    fixture = checkout_db
    request_id = uuid4()
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            "UPDATE tenants SET subscription_status='inactive' WHERE id=$1", fixture.tenants[0]
        )
    before = await _tenant(fixture)
    original = checkout.apply_plan_allocation

    async def fail_after_allocation(*args):
        await original(*args)
        raise RuntimeError("Synthetic failure before activation commit")

    monkeypatch.setattr(checkout, "apply_plan_allocation", fail_after_allocation)
    with pytest.raises(RuntimeError, match="before activation"):
        await _create(fixture, request_id=request_id, option_id=fixture.free)
    assert await _tenant(fixture) == before
    row = await fixture.service._get(fixture.tenants[0], request_id)
    assert row["status"] == "creating"
    monkeypatch.setattr(checkout, "apply_plan_allocation", original)
    assert (await _create(fixture, request_id=request_id, option_id=fixture.free))[
        "state"
    ] == "activated"


async def test_new_identity_for_already_active_free_plan_does_not_reset_usage(checkout_db):
    fixture = checkout_db
    await _create(fixture, option_id=fixture.free)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET minutes_used=71 WHERE id=$1", fixture.tenants[0])
    before = await _tenant(fixture)
    await _create(fixture, option_id=fixture.free)
    assert await _tenant(fixture) == before


@pytest.mark.parametrize("interval", ["month", "year"])
async def test_paid_receipt_uses_frozen_terms_and_duplicate_completion_preserves_usage(
    checkout_db, interval
):
    fixture = checkout_db
    option_id = fixture.year if interval == "year" else fixture.option
    receipt = await _create(fixture, option_id=option_id)
    assert receipt["state"] == "open" and receipt["price_option"]["interval"] == interval
    assert receipt["price_option"]["amount_minor"] == (19000 if interval == "year" else 1900)
    saved = await fixture.service._get(fixture.tenants[0], receipt["request_id"])
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE plans SET minutes=6000 WHERE id=$1", fixture.plan)
        await conn.execute(
            "UPDATE plan_price_options SET amount_minor=9999,active=FALSE WHERE id=$1", option_id
        )
    subscription, session = fixture.provider.paid_subscription(receipt)
    # Browser-return retrieval is evidence of completion, not permission to grant.
    pending = await fixture.service.get(
        tenant_id=fixture.tenants[0], request_id=receipt["request_id"]
    )
    assert pending["state"] == "pending" and (await _tenant(fixture))["minutes_used"] == 17
    await fixture.service.sync_subscription(subscription, checkout_session=session)
    state = await _tenant(fixture)
    assert (
        state["minutes_allocated"] == 163
    )  # Annual payment never multiplies monthly allowance by 12.
    assert state["minutes_used"] == 0 and state["subscription_status"] == "active"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET minutes_used=29 WHERE id=$1", fixture.tenants[0])
    await fixture.service.sync_subscription(subscription, checkout_session=session)
    await fixture.service.sync_subscription(subscription)
    assert (await _tenant(fixture))["minutes_used"] == 29
    after = await fixture.service._get(fixture.tenants[0], receipt["request_id"])
    assert after["status"] == "completed" and after["snapshot"] == saved["snapshot"]
    async with acquire_with_tenant(fixture.pool, None) as conn:
        stored = await conn.fetchval(
            "SELECT metadata FROM subscriptions WHERE tenant_id=$1", fixture.tenants[0]
        )
        assert (
            json.loads(stored)["purchase"]["amount_minor"]
            == receipt["price_option"]["amount_minor"]
        )
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE tenant_id=$1", fixture.tenants[0]
            )
            == 1
        )


async def test_early_subscription_event_preserves_existing_free_access(checkout_db):
    fixture = checkout_db
    receipt = await _create(fixture)
    before = await _tenant(fixture)
    subscription, _ = fixture.provider.paid_subscription(receipt)
    await fixture.service.sync_subscription(subscription)
    assert await _tenant(fixture) == before
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM subscriptions WHERE tenant_id=$1", fixture.tenants[0]
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT status FROM billing_checkout_attempts WHERE id=$1",
                UUID(receipt["request_id"]),
            )
            == "ready"
        )


@pytest.mark.parametrize("same_identity", [True, False])
async def test_concurrent_service_requests_have_one_durable_purchase(checkout_db, same_identity):
    fixture = checkout_db
    identity = uuid4()
    start = asyncio.Event()

    async def create():
        await start.wait()
        return await _create(fixture, request_id=identity if same_identity else uuid4())

    tasks = [asyncio.create_task(create()) for _ in range(6)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=15)
    successes = [item for item in results if isinstance(item, dict)]
    errors = [item for item in results if isinstance(item, Exception)]
    if same_identity:
        assert len(successes) == 6 and not errors
        assert all(item == successes[0] for item in successes)
    else:
        assert len(successes) == 1 and len(errors) == 5
        assert all(
            isinstance(item, checkout.CheckoutError) and item.code == "checkout_outstanding"
            for item in errors
        )
        assert all(item.request_not_started is True for item in errors)
        assert all(
            item.existing_attempt["request_id"] == successes[0]["request_id"] for item in errors
        )
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_checkout_attempts WHERE tenant_id=$1",
                fixture.tenants[0],
            )
            == 1
        )
    assert len(fixture.provider.sessions) == len(fixture.provider.session_params) == 1


async def test_accepted_session_timeout_retries_frozen_identity_and_parameters(checkout_db):
    fixture = checkout_db
    identity = uuid4()
    fixture.provider.lose_next_session_response = True
    with pytest.raises(TimeoutError):
        await _create(fixture, request_id=identity)
    before = await fixture.service._get(fixture.tenants[0], identity)
    assert before["status"] == "unknown" and before["stripe_session_id"] is None
    with pytest.raises(checkout.CheckoutError) as blocked:
        await _create(fixture)
    assert blocked.value.code == "checkout_outstanding"
    receipt = await _create(fixture, request_id=identity, email="changed@example.com")
    after = await fixture.service._get(fixture.tenants[0], identity)
    assert receipt["state"] == "open" and before["snapshot"] == after["snapshot"]
    session_calls = [
        item for item in fixture.provider.calls if item[:2] == ("checkout.Session", "create")
    ]
    assert len(session_calls) == 2 and session_calls[0] == session_calls[1]
    assert len(fixture.provider.sessions) == 1


async def test_service_attempt_cannot_be_read_or_replayed_by_another_tenant(checkout_db):
    fixture = checkout_db
    identity = uuid4()
    original = await _create(fixture, request_id=identity)
    count = len(fixture.provider.calls)
    with pytest.raises(checkout.CheckoutError) as read:
        await fixture.service.get(tenant_id=fixture.tenants[1], request_id=identity)
    assert read.value.code == "checkout_not_found"
    with pytest.raises(checkout.CheckoutError) as write:
        await _create(fixture, tenant=fixture.tenants[1], request_id=identity)
    assert write.value.code == "checkout_conflict"
    assert len(fixture.provider.calls) == count
    assert (await fixture.service._get(fixture.tenants[0], identity))[
        "stripe_session_id"
    ] == original["session_id"]


async def test_unpaid_or_mismatched_completion_never_changes_entitlement(checkout_db):
    fixture = checkout_db
    receipt = await _create(fixture)
    before = await _tenant(fixture)
    subscription, session = fixture.provider.paid_subscription(receipt)
    session["payment_status"] = "unpaid"
    with pytest.raises(checkout.CheckoutError) as unpaid:
        await fixture.service.sync_subscription(subscription, checkout_session=session)
    assert unpaid.value.code == "payment_unconfirmed"
    session["payment_status"] = "paid"
    session["metadata"]["tenant_id"] = str(fixture.tenants[1])
    with pytest.raises(checkout.CheckoutError):
        await fixture.service.sync_subscription(subscription, checkout_session=session)
    assert await _tenant(fixture) == before
