"""Current-state billing mutations on real PostgreSQL; provider reads are synthetic."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import stripe

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_state_events import (
    BillingStateBusy,
    BillingStateReviewRequired,
    apply_billing_event,
)
from app.domain.services.billing_webhook_notifications import store_notifications
from tests.integration.test_billing_checkout import _create, _tenant, checkout_db  # noqa: F401
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def state_db(checkout_db):  # noqa: F811
    fixture = checkout_db
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON invoices TO "{fixture.role}"')
        await conn.execute(f'GRANT SELECT ON user_profiles TO "{fixture.role}"')
        await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON billing_webhook_notifications,processed_webhook_events TO "{fixture.role}"')
    fixture.objects, fixture.reads = {}, []

    async def read(resource, method, identity, **kwargs):
        assert method == "retrieve", "No provider mutation in webhook state application"
        fixture.reads.append((resource, identity))
        return deepcopy(fixture.objects[(resource, identity)])

    fixture.billing = SimpleNamespace(billing_mode="test", _stripe_call=read)
    yield fixture


async def _paid(fixture):
    receipt = await _create(fixture)
    subscription, session = fixture.provider.paid_subscription(receipt)
    invoice_id = "in_" + receipt["request_id"].replace("-", "")
    price = subscription["items"]["data"][0]["price"]
    invoice = stripe.Invoice.construct_from({
        "id": invoice_id, "object": "invoice", "livemode": False,
        "customer": session["customer"], "status": "paid", "metadata": {},
        "parent": {"type": "subscription_details", "subscription_details": {"subscription": subscription["id"]}},
        "amount_due": price["unit_amount"], "amount_paid": price["unit_amount"], "currency": "usd",
        "status_transitions": {"paid_at": 1700000000},
        "lines": {"has_more": False, "data": [{
            "parent": {"type": "subscription_item_details", "subscription_item_details": {}},
            "pricing": {"type": "price_details", "price_details": {"price": price["id"]}},
            "quantity": 1, "currency": "usd",
        }]},
    }, "sk_test_synthetic")
    fixture.objects[("Subscription", subscription["id"])] = subscription
    fixture.objects[("checkout.Session", session["id"])] = session
    fixture.objects[("Invoice", invoice_id)] = invoice
    return receipt, subscription, session, invoice


async def _apply(fixture, kind, data):
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        return await apply_billing_event(conn, fixture.billing, kind, data)


async def test_paid_invoice_first_recovers_missing_session_then_checkout_never_resets_usage(state_db):
    fixture = state_db
    receipt, subscription, session, invoice = await _paid(fixture)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE billing_checkout_attempts SET stripe_session_id=NULL,status='unknown' WHERE id=$1", UUID(receipt["request_id"]))
    first = await _apply(fixture, "invoice.paid", {"id": invoice["id"], "parent": invoice["parent"]})
    assert first["status"] == "handled" and (await _tenant(fixture))["subscription_status"] == "active"
    assert first["notifications"][0]["delivery_key"] == f"invoice:{invoice['id']}:paid"
    assert first["notifications"][0]["recipient"] is None
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET minutes_used=37 WHERE id=$1", fixture.tenants[0])
    await _apply(fixture, "checkout.session.completed", session)
    duplicate = await _apply(fixture, "invoice.paid", invoice)
    assert duplicate["notifications"][0]["delivery_key"] == first["notifications"][0]["delivery_key"]
    tenant = await _tenant(fixture)
    assert tenant["minutes_allocated"] == 163 and tenant["minutes_used"] == 37
    saved = await fixture.service._get(fixture.tenants[0], receipt["request_id"])
    assert saved["status"] == "completed" and saved["stripe_session_id"] == session["id"]
    async with acquire_with_tenant(fixture.pool, None) as conn:
        stored = await conn.fetchrow("SELECT * FROM invoices WHERE stripe_invoice_id=$1", invoice["id"])
        assert stored["tenant_id"] == fixture.tenants[0] and stored["stripe_subscription_id"] == subscription["id"]


async def test_stale_same_second_subscription_update_cannot_restore_canceled_access(state_db):
    fixture = state_db
    _, subscription, session, _ = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    for kind in ("customer.subscription.deleted", "customer.subscription.updated"):
        await _apply(fixture, kind, {"id": subscription["id"], "status": "active", "created": 1700000000})
        tenant = await _tenant(fixture)
        assert tenant["subscription_status"] == "cancelled" and tenant["stripe_subscription_id"] is None


async def test_old_subscription_deletion_does_not_clear_its_replacement(state_db):
    fixture = state_db
    _, subscription, session, _ = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET stripe_subscription_id='sub_replacement',subscription_status='active' WHERE id=$1", fixture.tenants[0])
    before = await _tenant(fixture)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    result = await _apply(fixture, "customer.subscription.deleted", subscription)
    assert result["reason"] == "subscription_replaced"
    assert await _tenant(fixture) == before


async def test_completed_old_purchase_cannot_replace_new_free_access_when_stripe_id_cleared(state_db):
    fixture = state_db
    _, subscription, session, _ = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", subscription)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET subscription_status='active',minutes_used=29 WHERE id=$1", fixture.tenants[0])
    before = await _tenant(fixture)
    assert before["stripe_subscription_id"] is None
    result = await _apply(fixture, "customer.subscription.updated", {"id": subscription["id"], "status": "active"})
    assert result["reason"] == "subscription_replaced"
    assert await _tenant(fixture) == before


async def test_old_invoice_cannot_mark_replacement_subscription_past_due(state_db):
    fixture = state_db
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET stripe_subscription_id='sub_replacement',subscription_status='active' WHERE id=$1", fixture.tenants[0])
    before = await _tenant(fixture)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "past_due"
    fixture.objects[("Invoice", invoice["id"])].update({"status": "open", "amount_paid": 0})
    result = await _apply(fixture, "invoice.payment_failed", invoice)
    assert result["reason"] == "subscription_replaced"
    assert await _tenant(fixture) == before


@pytest.mark.parametrize("kind,terminal", [
    ("customer.subscription.deleted", "canceled"),
    ("customer.subscription.updated", "incomplete_expired"),
])
async def test_terminal_first_subscription_frees_attempt_without_changing_free_access(state_db, kind, terminal):
    fixture = state_db
    receipt, subscription, _, _ = await _paid(fixture)
    fixture.objects[("Subscription", subscription["id"])]["status"] = terminal
    before = await _tenant(fixture)
    result = await _apply(fixture, kind, {"id": subscription["id"]})
    assert result["status"] == "handled" and result["reason"] == "initial_subscription_terminal"
    assert await _tenant(fixture) == before
    saved = await fixture.service._get(fixture.tenants[0], receipt["request_id"])
    assert saved["status"] == "failed"
    replacement = await _create(fixture)
    assert replacement["state"] == "open" and replacement["request_id"] != receipt["request_id"]


async def test_old_payment_failure_uses_current_paid_invoice_and_active_subscription(state_db):
    fixture = state_db
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    result = await _apply(fixture, "invoice.payment_failed", {
        "id": invoice["id"], "subscription": subscription["id"], "status": "open", "created": 1700000000,
    })
    assert (await _tenant(fixture))["subscription_status"] == "active"
    assert [item["kind"] for item in result["notifications"]] == ["invoice_paid"]


@pytest.mark.parametrize("wrong", ["customer", "price", "mode", "truncated_lines"])
async def test_first_paid_invoice_cannot_loosen_frozen_purchase_binding(state_db, wrong):
    fixture = state_db
    receipt, _, _, invoice = await _paid(fixture)
    current = fixture.objects[("Invoice", invoice["id"])]
    if wrong == "customer":
        current["customer"] = "cus_unrelated"
    elif wrong == "price":
        current["lines"]["data"][0]["pricing"]["price_details"]["price"] = "price_unrelated"
    elif wrong == "mode":
        current["livemode"] = True
    else:
        current["lines"]["has_more"] = True
    before = await _tenant(fixture)
    with pytest.raises(BillingStateReviewRequired):
        await _apply(fixture, "invoice.paid", invoice)
    assert await _tenant(fixture) == before
    assert (await fixture.service._get(fixture.tenants[0], receipt["request_id"]))["status"] == "ready"


async def test_state_and_invoice_roll_back_if_outer_receipt_completion_fails(state_db):
    fixture = state_db
    receipt, _, _, invoice = await _paid(fixture)
    before = await _tenant(fixture)
    with pytest.raises(RuntimeError, match="receipt write failed"):
        async with acquire_with_tenant(fixture.service_pool, None) as conn:
            result = await apply_billing_event(conn, fixture.billing, "invoice.paid", invoice)
            assert result["notifications"]
            raise RuntimeError("Synthetic receipt write failed")
    assert await _tenant(fixture) == before
    assert (await fixture.service._get(fixture.tenants[0], receipt["request_id"]))["status"] == "ready"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM invoices WHERE stripe_invoice_id=$1", invoice["id"]) == 0


async def test_concurrent_current_state_reads_are_serialized_before_provider_retrieval(state_db):
    fixture = state_db
    _, subscription, session, _ = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    entered, release = asyncio.Event(), asyncio.Event()
    read = fixture.billing._stripe_call

    async def blocked_read(resource, method, identity, **kwargs):
        entered.set()
        await release.wait()
        return await read(resource, method, identity, **kwargs)

    fixture.billing._stripe_call = blocked_read
    fixture.reads.clear()
    first = asyncio.create_task(_apply(fixture, "customer.subscription.updated", subscription))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        with pytest.raises(BillingStateBusy):
            await _apply(fixture, "customer.subscription.deleted", subscription)
        assert fixture.reads == []
    finally:
        release.set()
        await first
    fixture.billing._stripe_call = read
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", subscription)
    assert (await _tenant(fixture))["subscription_status"] == "cancelled"


@pytest.mark.parametrize("prior_status", ["pending", "failed_before_send", "recipient_missing", "sending", "unknown", "accepted"])
async def test_paid_invoice_supersedes_only_proved_unsent_failure_notice_atomically(state_db, prior_status):
    fixture = state_db
    _, _, _, invoice = await _paid(fixture)
    event_id = "evt_" + uuid4().hex
    key = f"invoice:{invoice['id']}:payment_failed"
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id,event_type,state) VALUES($1,'invoice.payment_failed','completed')", event_id)
        await conn.execute(
            """INSERT INTO billing_webhook_notifications(delivery_key,event_id,event_type,tenant_id,kind,subject,body,status)
               VALUES($1,$2,'invoice.payment_failed',$3,'invoice_payment_failed','Synthetic failure','Synthetic',$4)""",
            key, event_id, fixture.tenants[0], prior_status,
        )
    try:
        with pytest.raises(RuntimeError, match="receipt completion"):
            async with acquire_with_tenant(fixture.service_pool, None) as conn:
                await apply_billing_event(conn, fixture.billing, "invoice.paid", invoice)
                raise RuntimeError("Synthetic receipt completion failure")
        async with acquire_with_tenant(fixture.pool, None) as conn:
            assert await conn.fetchval("SELECT status FROM billing_webhook_notifications WHERE delivery_key=$1", key) == prior_status
        for event_type in ("invoice.paid", "invoice.payment_failed"):
            async with acquire_with_tenant(fixture.service_pool, None) as conn:
                result = await apply_billing_event(conn, fixture.billing, event_type, invoice)
                await store_notifications(conn, event_id, event_type, result["notifications"])
        async with acquire_with_tenant(fixture.pool, None) as conn:
            expected = "superseded" if prior_status in {"pending", "failed_before_send", "recipient_missing"} else prior_status
            assert await conn.fetchval("SELECT status FROM billing_webhook_notifications WHERE delivery_key=$1", key) == expected
            assert await conn.fetchval("SELECT count(*) FROM billing_webhook_notifications WHERE delivery_key=$1", f"invoice:{invoice['id']}:paid") == 1
        assert (await _tenant(fixture))["subscription_status"] == "active"
    finally:
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute("DELETE FROM billing_webhook_notifications WHERE event_id=$1", event_id)
            await conn.execute("DELETE FROM processed_webhook_events WHERE event_id=$1", event_id)


@pytest.mark.parametrize("prior_status", ["paid", "open"])
async def test_existing_invoice_projection_cannot_prove_old_email_was_unsent(state_db, prior_status):
    fixture = state_db
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    event_type = "invoice.paid"
    if prior_status == "open":
        fixture.objects[("Subscription", subscription["id"])]["status"] = "past_due"
        fixture.objects[("Invoice", invoice["id"])].update({"status": "open", "amount_paid": 0})
        event_type = "invoice.payment_failed"
    first = await _apply(fixture, event_type, invoice)
    assert first["notifications"] and not first["notifications"][0].get("prior_delivery_unknown")
    # The old handler could persist an invoice and send email without retaining
    # an email receipt. A new event ID cannot turn that ambiguity into "unsent".
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM billing_webhook_notifications WHERE tenant_id=$1", fixture.tenants[0]) == 0
    repeated = await _apply(fixture, event_type, invoice)
    assert repeated["notifications"][0]["prior_delivery_unknown"] is True
    assert repeated["notifications"][0]["delivery_key"] == first["notifications"][0]["delivery_key"]
