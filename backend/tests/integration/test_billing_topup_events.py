"""Actual PostgreSQL billing transactions with synthetic Stripe objects only.

These exercise the production dispatcher/receipt/ledger boundary, not provider
sandbox acceptance or signed HTTP delivery (covered separately). Immutable
synthetic ledger/order rows remain in the disposable database, never deleted.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_service import BillingService
from app.domain.services.billing_webhooks import (
    BillingWebhookProcessor,
    BillingWebhookRetryable,
    BillingWebhookReviewRequired,
)
from app.domain.services.topup_service import TopupService
from tests.integration.test_billing_webhook_migration import webhook_db  # noqa: F401
from tests.unit.test_billing_topup_events import fixture_data

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def topup_db(webhook_db):  # noqa: F811
    db = webhook_db
    data = fixture_data()
    suffix = uuid4().hex
    order = data.store["order"]
    order["tenant_id"] = db.tenant
    for resource, prefix in (
        ("checkout.Session", "cs_"),
        ("PaymentIntent", "pi_"),
        ("Charge", "ch_"),
        ("Dispute", "dp_"),
    ):
        data.objects[resource]["id"] = prefix + suffix
    customer = "cus_" + suffix
    for obj in (data.session, data.payment, data.charge):
        obj["customer"] = customer
    data.session["payment_intent"] = data.charge["payment_intent"] = data.dispute[
        "payment_intent"
    ] = data.payment["id"]
    data.payment["latest_charge"] = data.dispute["charge"] = data.charge["id"]
    data.session["metadata"]["tenant_id"] = str(db.tenant)
    order["provider_session_id"] = data.session["id"]
    plan = "cp03_topup_" + suffix
    async with acquire_with_tenant(db.admin, None) as conn:
        await conn.execute(
            f'GRANT SELECT,INSERT,UPDATE ON tenants,tenant_call_limits,topup_orders,billing_ledger TO "{db.role}"'
        )
        sequence = await conn.fetchval("SELECT pg_get_serial_sequence('billing_ledger','id')")
        if sequence:
            await conn.execute(f'GRANT USAGE,SELECT ON SEQUENCE {sequence} TO "{db.role}"')
        await conn.execute(
            "INSERT INTO plans(id,name,price,minutes) VALUES($1,'Synthetic CP03',10,1000)", plan
        )
        await conn.execute(
            """INSERT INTO tenants(id,business_name,plan_id,stripe_customer_id,minutes_allocated)
            VALUES($1,'Synthetic CP03',$2,$3,1000)""",
            db.tenant,
            plan,
            customer,
        )
        await conn.execute(
            "INSERT INTO tenant_call_limits(tenant_id,monthly_minutes_allocated) VALUES($1,1000)",
            db.tenant,
        )
        await conn.execute(
            """INSERT INTO topup_orders(id,tenant_id,package_code,minutes,price_cents,currency,
            status,provider,provider_session_id) VALUES($1,$2,'mins_250',250,2500,'GBP','pending','stripe',$3)""",
            order["id"],
            db.tenant,
            data.session["id"],
        )
    billing = BillingService.__new__(BillingService)
    billing.billing_mode = "test"
    billing.db_client = SimpleNamespace(pool=db.pool)
    billing._stripe_call = data.provider._stripe_call
    fixture = SimpleNamespace(
        db=db, data=data, billing=billing, processor=BillingWebhookProcessor(db.pool), order=order
    )
    yield fixture
    # Orders and their append-only ledger remain as UUID-scoped synthetic
    # evidence. No trigger is disabled to make test cleanup convenient.


def event(fixture, suffix, event_type="checkout.session.completed"):
    data = (
        fixture.data.dispute
        if "dispute" in event_type
        else fixture.data.charge if event_type.startswith("charge.") else fixture.data.session
    )
    return {
        "id": fixture.db.prefix + "_" + suffix,
        "object": "event",
        "type": event_type,
        "livemode": False,
        "created": 1700000000,
        "api_version": "2025-09-30.clover",
        "data": {"object": deepcopy(data)},
    }


async def process(fixture, envelope):
    return await fixture.processor.process(envelope, fixture.billing._apply_webhook_event)


async def state(fixture):
    async with acquire_with_tenant(fixture.db.admin, None) as conn:
        return {
            "allocated": await conn.fetchval(
                "SELECT minutes_allocated FROM tenants WHERE id=$1", fixture.db.tenant
            ),
            "enforced": await conn.fetchval(
                "SELECT monthly_minutes_allocated FROM tenant_call_limits WHERE tenant_id=$1",
                fixture.db.tenant,
            ),
            "order": await conn.fetchval(
                "SELECT status FROM topup_orders WHERE id=$1", fixture.order["id"]
            ),
            "ledger": [
                dict(row)
                for row in await conn.fetch(
                    "SELECT kind,minutes_delta,amount_cents,currency FROM billing_ledger WHERE order_id=$1 ORDER BY id",
                    fixture.order["id"],
                )
            ],
            "receipts": [
                dict(row)
                for row in await conn.fetch(
                    "SELECT event_id,state,last_error_code FROM processed_webhook_events WHERE event_id LIKE $1 ORDER BY event_id",
                    fixture.db.prefix + "%",
                )
            ],
            "notifications": [
                dict(row)
                for row in await conn.fetch(
                    "SELECT delivery_key,status,body FROM billing_webhook_notifications WHERE tenant_id=$1",
                    fixture.db.tenant,
                )
            ],
        }


async def test_redelivery_and_distinct_success_event_commit_one_credit_and_one_intent(topup_db):
    fixture = topup_db
    first = event(fixture, "first")
    assert (await process(fixture, first))["status"] == "handled"
    assert (await process(fixture, first))["status"] == "duplicate"
    await process(fixture, event(fixture, "distinct", "checkout.session.async_payment_succeeded"))
    result = await state(fixture)
    assert result["allocated"] == result["enforced"] == 1250
    assert result["order"] == "paid" and len(result["ledger"]) == 1
    assert len(result["notifications"]) == 1 and all(
        row["state"] == "completed" for row in result["receipts"]
    )
    assert (
        "GBP 25.00" in result["notifications"][0]["body"]
        and "999999" not in result["notifications"][0]["body"]
    )


@pytest.mark.parametrize("failure_boundary", ["after_credit", "notification_insert"])
async def test_failure_after_financial_sql_rolls_back_then_exact_event_retries_once(
    topup_db, monkeypatch, failure_boundary
):
    fixture = topup_db
    envelope = event(fixture, "rollback")
    failed = False
    if failure_boundary == "after_credit":
        original = TopupService.credit_paid_order

        async def interrupted(self, **kwargs):
            nonlocal failed
            result = await original(self, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("Synthetic crash after financial SQL")
            return result

        monkeypatch.setattr(TopupService, "credit_paid_order", interrupted)
    else:
        from app.domain.services import billing_webhook_notifications as notifications

        original = notifications.store_notifications

        async def interrupted(*args, **kwargs):
            nonlocal failed
            await original(*args, **kwargs)
            if not failed:
                failed = True
                raise RuntimeError("Synthetic crash after notification insert")

        monkeypatch.setattr(notifications, "store_notifications", interrupted)
    with pytest.raises(BillingWebhookRetryable):
        await process(fixture, envelope)
    result = await state(fixture)
    assert result["allocated"] == result["enforced"] == 1000
    assert result["order"] == "pending" and result["ledger"] == result["notifications"] == []
    assert result["receipts"][0]["state"] == "failed"
    await process(fixture, envelope)
    result = await state(fixture)
    assert (
        result["allocated"] == 1250 and len(result["ledger"]) == len(result["notifications"]) == 1
    )
    assert result["receipts"][0]["state"] == "completed"


@pytest.mark.parametrize("reversal", ["refund", "dispute"])
async def test_full_reversal_and_late_credit_preserve_original_ledger(topup_db, reversal):
    fixture = topup_db
    await process(fixture, event(fixture, "paid"))
    if reversal == "refund":
        fixture.data.charge.update(amount_refunded=2500, refunded=True)
        event_type = "charge.refunded"
    else:
        fixture.data.charge["disputed"] = True
        fixture.data.dispute["payment_intent"] = None
        event_type = "charge.dispute.created"
    await process(fixture, event(fixture, "reversed", event_type))
    await process(fixture, event(fixture, "duplicate_reversal", event_type))
    await process(fixture, event(fixture, "late_paid"))
    result = await state(fixture)
    assert result["allocated"] == result["enforced"] == 1000
    assert [row["minutes_delta"] for row in result["ledger"]] == [250, -250]
    assert [row["amount_cents"] for row in result["ledger"]] == [2500, -2500]
    assert result["order"] == ("refunded" if reversal == "refund" else "disputed")
    assert len(result["notifications"]) == 1


async def test_partial_refund_has_review_receipt_and_no_guessed_minutes(topup_db):
    fixture = topup_db
    await process(fixture, event(fixture, "paid"))
    fixture.data.charge["amount_refunded"] = 333
    with pytest.raises(BillingWebhookReviewRequired, match="partial_refund"):
        await process(fixture, event(fixture, "partial", "charge.refunded"))
    result = await state(fixture)
    assert result["allocated"] == 1250 and len(result["ledger"]) == 1
    assert any(row["state"] == "needs_review" for row in result["receipts"])


async def test_precredit_reversal_then_success_is_review_without_financial_mutation(topup_db):
    fixture = topup_db
    fixture.data.charge.update(amount_refunded=2500, refunded=True)
    for suffix, event_type in (
        ("early", "charge.refunded"),
        ("late", "checkout.session.completed"),
    ):
        with pytest.raises(BillingWebhookReviewRequired):
            await process(fixture, event(fixture, suffix, event_type))
    result = await state(fixture)
    assert result["allocated"] == 1000 and result["ledger"] == result["notifications"] == []
    assert all(row["state"] == "needs_review" for row in result["receipts"])


@pytest.mark.parametrize("unlimited", [False, True])
async def test_finite_reversal_cannot_become_unlimited_and_existing_unlimited_is_preserved(
    topup_db, unlimited
):
    fixture = topup_db
    if unlimited:
        async with acquire_with_tenant(fixture.db.admin, None) as conn:
            await conn.execute(
                "UPDATE tenants SET minutes_allocated=0 WHERE id=$1", fixture.db.tenant
            )
            await conn.execute(
                "UPDATE tenant_call_limits SET monthly_minutes_allocated=0 WHERE tenant_id=$1",
                fixture.db.tenant,
            )
    await process(fixture, event(fixture, "paid"))
    await process(fixture, event(fixture, "duplicate_paid"))
    if not unlimited:
        async with acquire_with_tenant(fixture.db.admin, None) as conn:
            await conn.execute(
                "UPDATE tenants SET minutes_allocated=50 WHERE id=$1", fixture.db.tenant
            )
            await conn.execute(
                "UPDATE tenant_call_limits SET monthly_minutes_allocated=50 WHERE tenant_id=$1",
                fixture.db.tenant,
            )
    fixture.data.charge.update(amount_refunded=2500, refunded=True)
    if unlimited:
        await process(fixture, event(fixture, "refund", "charge.refunded"))
    else:
        with pytest.raises(BillingWebhookReviewRequired, match="unlimited_semantics"):
            await process(fixture, event(fixture, "refund", "charge.refunded"))
    result = await state(fixture)
    assert result["allocated"] == result["enforced"] == (0 if unlimited else 50)
    assert len(result["ledger"]) == (2 if unlimited else 1)


async def test_different_events_for_same_payment_serialize_before_provider_read(topup_db):
    fixture = topup_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = fixture.billing._stripe_call
    calls = 0

    async def blocked(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original(*args)

    fixture.billing._stripe_call = blocked
    first = asyncio.create_task(process(fixture, event(fixture, "first")))
    second = event(fixture, "second")
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(BillingWebhookRetryable):
            await process(fixture, second)
        assert calls == 1
    finally:
        release.set()
        await first
    await process(fixture, second)
    result = await state(fixture)
    assert (
        result["allocated"] == 1250 and len(result["ledger"]) == len(result["notifications"]) == 1
    )


async def test_missing_recipient_is_durable_and_provider_timeout_is_retryable(topup_db):
    fixture = topup_db
    fixture.data.session["customer_details"] = {}
    original = fixture.billing._stripe_call

    async def timeout(*args):
        raise TimeoutError("Synthetic provider timeout")

    fixture.billing._stripe_call = timeout
    envelope = event(fixture, "timeout")
    with pytest.raises(BillingWebhookRetryable):
        await process(fixture, envelope)
    assert (await state(fixture))["ledger"] == []
    fixture.billing._stripe_call = original
    await process(fixture, envelope)
    result = await state(fixture)
    assert result["notifications"][0]["status"] == "recipient_missing"
    assert result["receipts"][0]["state"] == "completed"


async def test_unpaid_snapshot_retries_after_payment_binding_is_created(topup_db):
    fixture = topup_db
    envelope = event(fixture, "pending")
    envelope["data"]["object"].update(payment_intent=None, payment_status="unpaid")
    fixture.data.session.update(payment_intent=None, payment_status="unpaid")
    with pytest.raises(BillingWebhookRetryable, match="payment_not_settled"):
        await process(fixture, envelope)
    fixture.data.session.update(payment_intent=fixture.data.payment["id"], payment_status="paid")
    await process(fixture, envelope)
    result = await state(fixture)
    assert result["allocated"] == 1250 and len(result["ledger"]) == 1


@pytest.mark.parametrize(
    "resource,field,value",
    [("checkout.Session", "customer", "cus_other"), ("Charge", "currency", "usd")],
)
async def test_current_provider_binding_conflict_cannot_mutate_tenant(
    topup_db, resource, field, value
):
    fixture = topup_db
    fixture.data.objects[resource][field] = value
    with pytest.raises(BillingWebhookReviewRequired):
        await process(fixture, event(fixture, "mismatch"))
    result = await state(fixture)
    assert result["allocated"] == 1000 and result["ledger"] == result["notifications"] == []
    assert result["receipts"][0]["state"] == "needs_review"


@pytest.mark.parametrize("identifier", ["session", "payment"])
async def test_public_reversal_keeps_single_identifier_compatibility(topup_db, identifier):
    fixture = topup_db
    await process(fixture, event(fixture, "paid"))
    kwargs = (
        {"session_id": fixture.data.session["id"]}
        if identifier == "session"
        else {"payment_id": fixture.data.payment["id"]}
    )
    assert await TopupService(fixture.db.pool).reverse(
        event_id=fixture.db.prefix + "_reverse", **kwargs
    )
    result = await state(fixture)
    assert result["allocated"] == result["enforced"] == 1000
    assert [row["minutes_delta"] for row in result["ledger"]] == [250, -250]


async def test_supplied_payment_and_session_must_identify_the_same_order(topup_db):
    fixture = topup_db
    await process(fixture, event(fixture, "paid"))
    topups = TopupService(fixture.db.pool)
    assert not await topups.reverse(
        event_id=fixture.db.prefix + "_wrong_session",
        payment_id=fixture.data.payment["id"],
        session_id="cs_unrelated",
    )
    assert not await topups.reverse(
        event_id=fixture.db.prefix + "_wrong_payment",
        payment_id="pi_unrelated",
        session_id=fixture.data.session["id"],
    )
    result = await state(fixture)
    assert result["allocated"] == 1250 and len(result["ledger"]) == 1 and result["order"] == "paid"


async def test_duplicate_legacy_payment_binding_does_not_reverse_other_tenant(topup_db):
    fixture = topup_db
    await process(fixture, event(fixture, "paid"))
    other_tenant, other_order = uuid4(), uuid4()
    async with acquire_with_tenant(fixture.db.admin, None) as conn:
        plan = await conn.fetchval("SELECT plan_id FROM tenants WHERE id=$1", fixture.db.tenant)
        await conn.execute(
            """INSERT INTO tenants(id,business_name,plan_id,minutes_allocated)
            VALUES($1,'Synthetic legacy binding conflict',$2,1250)""",
            other_tenant,
            plan,
        )
        await conn.execute(
            "INSERT INTO tenant_call_limits(tenant_id,monthly_minutes_allocated) VALUES($1,1250)",
            other_tenant,
        )
        await conn.execute(
            """INSERT INTO topup_orders(id,tenant_id,package_code,minutes,price_cents,currency,
            status,provider,provider_session_id,provider_payment_id) VALUES($1,$2,'mins_250',250,2500,'GBP',
            'paid','stripe',$3,$4)""",
            other_order,
            other_tenant,
            "cs_" + other_order.hex,
            fixture.data.payment["id"],
        )
        await conn.execute(
            """INSERT INTO billing_ledger(tenant_id,order_id,kind,minutes_delta,amount_cents,currency,provider_event_id)
            VALUES($1,$2,'topup',250,2500,'GBP',$3)""",
            other_tenant,
            other_order,
            fixture.db.prefix + "_legacy",
        )
    fixture.data.charge.update(amount_refunded=2500, refunded=True)
    await process(fixture, event(fixture, "refund", "charge.refunded"))
    result = await state(fixture)
    assert result["allocated"] == 1000 and result["order"] == "refunded"
    async with acquire_with_tenant(fixture.db.admin, None) as conn:
        assert (
            await conn.fetchval("SELECT minutes_allocated FROM tenants WHERE id=$1", other_tenant)
            == 1250
        )
        assert (
            await conn.fetchval("SELECT status FROM topup_orders WHERE id=$1", other_order)
            == "paid"
        )
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM billing_ledger WHERE order_id=$1", other_order
            )
            == 1
        )


async def test_valid_ledger_with_stale_order_status_has_review_receipt(topup_db):
    fixture = topup_db
    await process(fixture, event(fixture, "paid"))
    async with acquire_with_tenant(fixture.db.admin, None) as conn:
        await conn.execute(
            "UPDATE topup_orders SET status='pending' WHERE id=$1", fixture.order["id"]
        )
    with pytest.raises(BillingWebhookReviewRequired, match="order_ledger_state_conflict"):
        await process(fixture, event(fixture, "stale"))
    result = await state(fixture)
    assert result["allocated"] == result["enforced"] == 1250 and len(result["ledger"]) == 1
    assert result["order"] == "pending"
    assert any(row["state"] == "needs_review" for row in result["receipts"])


async def test_legacy_committed_credit_without_delivery_record_never_blindly_resends(topup_db):
    fixture = topup_db
    assert await TopupService(fixture.db.pool).credit_paid_order(
        session_id=fixture.data.session["id"],
        payment_id=fixture.data.payment["id"],
        event_id=fixture.db.prefix + "_legacy_credit",
    )
    assert (await state(fixture))["notifications"] == []
    await process(fixture, event(fixture, "later_event"))
    result = await state(fixture)
    assert result["allocated"] == 1250 and len(result["ledger"]) == 1
    assert result["notifications"][0]["status"] == "unknown"
