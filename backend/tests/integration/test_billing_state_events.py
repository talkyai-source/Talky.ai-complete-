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
        await conn.execute(f'GRANT SELECT,INSERT ON invoice_snapshots TO "{fixture.role}"')
        await conn.execute(f'GRANT USAGE ON SEQUENCE invoice_snapshots_id_seq TO "{fixture.role}"')
        await conn.execute(f'GRANT SELECT ON user_profiles,tenant_users,roles TO "{fixture.role}"')
        # A won dispute first looks for a disputed top-up order.
        await conn.execute(f'GRANT SELECT,UPDATE ON topup_orders TO "{fixture.role}"')
        await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON billing_webhook_notifications,processed_webhook_events TO "{fixture.role}"')
    fixture.objects, fixture.reads, fixture.notification_users = {}, [], []

    async def read(resource, method, identity, **kwargs):
        assert method == "retrieve", "No provider mutation in webhook state application"
        fixture.reads.append((resource, identity))
        return deepcopy(fixture.objects[(resource, identity)])

    fixture.billing = SimpleNamespace(billing_mode="test", _stripe_call=read)
    try:
        yield fixture
    finally:
        # Retained immutable invoice observations must not leave synthetic
        # pending email work for a later test's deliberately global drain.
        # Only this fixture's mutable tenant-scoped delivery/receipt rows go.
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute(
                "DELETE FROM billing_webhook_notifications WHERE tenant_id=ANY($1::uuid[])", fixture.tenants,
            )
            await conn.execute(
                """DELETE FROM billing_webhook_review_log WHERE event_id IN
                   (SELECT event_id FROM processed_webhook_events WHERE tenant_id=ANY($1::uuid[]))""", fixture.tenants,
            )
            await conn.execute(
                "DELETE FROM processed_webhook_events WHERE tenant_id=ANY($1::uuid[])", fixture.tenants,
            )
            await conn.execute("DELETE FROM tenant_users WHERE user_id=ANY($1::uuid[])", fixture.notification_users)
            await conn.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[])", fixture.notification_users)


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
            "id": "il_" + receipt["request_id"].replace("-", ""), "invoice": invoice_id, "livemode": False,
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


async def _recipient_profile(fixture, *, profile_role="tenant_admin", membership_role="tenant_admin",
                             membership_status="active", active=True, verified=True,
                             profile_tenant=0, membership_tenant=0, email=None):
    user_id = uuid4()
    email = f"cp06-{user_id.hex}@example.invalid" if email is None else email
    fixture.notification_users.append(user_id)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(
            """INSERT INTO user_profiles(id,email,tenant_id,role,is_active,is_verified,email_verified_at)
               VALUES($1,$2,$3,$4,$5,$6,CASE WHEN $6 THEN NOW() ELSE NULL END)""",
            user_id, email, fixture.tenants[profile_tenant] if profile_tenant is not None else None,
            profile_role, active, verified,
        )
        if membership_role is not None:
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", membership_role)
            assert role_id, "Canonical membership role must exist"
            await conn.execute(
                "INSERT INTO tenant_users(user_id,tenant_id,role_id,status) VALUES($1,$2,$3,$4)",
                user_id, fixture.tenants[membership_tenant], role_id, membership_status,
            )
    return user_id, email


@pytest.mark.parametrize("profile_role", ["tenant_admin", "user"])
@pytest.mark.parametrize("event_type", ["invoice.paid", "invoice.payment_failed"])
async def test_billing_notice_uses_single_current_verified_tenant_admin_membership(state_db, profile_role, event_type):
    fixture = state_db
    _, subscription, _, invoice = await _paid(fixture)
    _user, email = await _recipient_profile(fixture, profile_role=profile_role)
    # An otherwise valid non-admin does not make the administrator ambiguous.
    await _recipient_profile(fixture, membership_role="user")
    if event_type == "invoice.payment_failed":
        fixture.objects[("Subscription", subscription["id"])]["status"] = "past_due"
        fixture.objects[("Invoice", invoice["id"])].update(status="open", amount_paid=0)
    result = await _apply(fixture, event_type, invoice)
    assert result["notifications"][0]["recipient"] == email
    assert result["notifications"][0]["tenant_id"] == str(fixture.tenants[0])


@pytest.mark.parametrize("changes", [
    {"membership_role": None},
    {"membership_status": "removed"},
    {"membership_status": "suspended"},
    {"membership_status": "pending"},
    {"active": False},
    {"verified": False},
    {"membership_tenant": 1},
    {"profile_tenant": 1},
    {"profile_tenant": None},
    {"membership_role": "user"},
    {"membership_role": "platform_admin"},
    {"profile_role": "platform_admin"},
    {"email": "   "},
], ids=["no-membership", "removed", "suspended", "pending", "inactive", "unverified",
        "foreign-membership", "foreign-profile", "no-current-tenant", "wrong-role",
        "global-membership", "global-profile", "empty-email"])
async def test_billing_notice_never_uses_ineligible_or_foreign_recipient(state_db, changes):
    fixture = state_db
    _, _, _, invoice = await _paid(fixture)
    await _recipient_profile(fixture, **changes)
    result = await _apply(fixture, "invoice.paid", invoice)
    assert result["notifications"][0]["recipient"] is None


async def test_multiple_eligible_admins_persist_missing_recipient_without_arbitrary_choice(state_db):
    fixture = state_db
    _, _, _, invoice = await _paid(fixture)
    await _recipient_profile(fixture)
    await _recipient_profile(fixture)
    event_id = "evt_cp06_" + uuid4().hex
    async with acquire_with_tenant(fixture.service_pool, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id,event_type,state,tenant_id) VALUES($1,'invoice.paid','completed',$2)",
                           event_id, fixture.tenants[0])
        result = await apply_billing_event(conn, fixture.billing, "invoice.paid", invoice)
        await store_notifications(conn, event_id, "invoice.paid", result["notifications"])
        saved = await conn.fetchrow("SELECT recipient,status FROM billing_webhook_notifications WHERE event_id=$1", event_id)
    assert dict(saved) == {"recipient": None, "status": "recipient_missing"}
    assert (await _tenant(fixture))["subscription_status"] == "active"


async def test_repeated_paid_webhooks_preserve_captured_recipient_and_financial_effects(state_db):
    fixture = state_db
    receipt, _, _, invoice = await _paid(fixture)
    first_user, first_email = await _recipient_profile(fixture)
    event_ids = ["evt_cp06_" + uuid4().hex for _ in range(2)]

    async def apply_and_store(event_id):
        async with acquire_with_tenant(fixture.service_pool, None) as conn:
            await conn.execute("INSERT INTO processed_webhook_events(event_id,event_type,state,tenant_id) VALUES($1,'invoice.paid','completed',$2) ON CONFLICT DO NOTHING",
                               event_id, fixture.tenants[0])
            result = await apply_billing_event(conn, fixture.billing, "invoice.paid", invoice)
            await store_notifications(conn, event_id, "invoice.paid", result["notifications"])
            return result

    first = await apply_and_store(event_ids[0])
    assert first["notifications"][0]["recipient"] == first_email
    async with acquire_with_tenant(fixture.pool, None) as conn:
        before_ledger = await conn.fetchval("SELECT count(*) FROM billing_ledger WHERE tenant_id=$1", fixture.tenants[0])
        await conn.execute("UPDATE tenants SET minutes_used=37 WHERE id=$1", fixture.tenants[0])
        await conn.execute("UPDATE tenant_users SET status='removed' WHERE user_id=$1", first_user)
    _new_user, second_email = await _recipient_profile(fixture)
    for event_id in (event_ids[0], event_ids[1]):
        repeated = await apply_and_store(event_id)
        assert repeated["notifications"][0]["recipient"] == second_email
    async with acquire_with_tenant(fixture.pool, None) as conn:
        notifications = await conn.fetch("SELECT recipient,status FROM billing_webhook_notifications WHERE tenant_id=$1", fixture.tenants[0])
        assert [dict(row) for row in notifications] == [{"recipient": first_email, "status": "pending"}]
        assert await conn.fetchval("SELECT count(*) FROM invoices WHERE stripe_invoice_id=$1", invoice["id"]) == 1
        assert await conn.fetchval("SELECT count(*) FROM billing_ledger WHERE tenant_id=$1", fixture.tenants[0]) == before_ledger
    tenant = await _tenant(fixture)
    assert tenant["minutes_allocated"] == 120 and tenant["minutes_used"] == 37
    assert (await fixture.service._get(fixture.tenants[0], receipt["request_id"]))["status"] == "completed"


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
    assert tenant["minutes_allocated"] == 120 and tenant["minutes_used"] == 37
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
    with pytest.raises(BillingStateBusy if wrong == "truncated_lines" else BillingStateReviewRequired):
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
        await conn.execute("UPDATE invoices SET notification_history_known=FALSE WHERE stripe_invoice_id=$1", invoice["id"])
    repeated = await _apply(fixture, event_type, invoice)
    assert repeated["notifications"][0]["prior_delivery_unknown"] is True
    assert repeated["notifications"][0]["delivery_key"] == first["notifications"][0]["delivery_key"]


@pytest.mark.parametrize("status,expected", [("past_due", "past_due"), ("unpaid", "unpaid"), ("canceled", "cancelled")])
async def test_after_purchase_a_changed_price_never_blocks_a_status_change(state_db, status, expected):
    """A provider-side price/item change after the purchase used to reject
    every later event as a purchase mismatch, so a cancelled or unpaid
    tenant stayed active. Status changes must always land; minutes never do."""
    fixture = state_db
    receipt, subscription, session, _invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    before = await _tenant(fixture)
    assert before["subscription_status"] == "active"
    changed = deepcopy(subscription)
    changed["items"]["data"][0]["price"] = dict(changed["items"]["data"][0]["price"], id="price_changedelsewhere", unit_amount=1)
    changed["status"] = status
    fixture.objects[("Subscription", subscription["id"])] = changed
    result = await _apply(fixture, "customer.subscription.updated", changed)
    after = await _tenant(fixture)
    assert result["status"] == "handled"
    assert after["subscription_status"] == expected
    assert after["minutes_allocated"] == before["minutes_allocated"], "no minutes from a lifecycle event"


async def test_a_chargeback_on_a_plan_suspends_calls_until_the_dispute_is_won(state_db):
    fixture = state_db
    receipt, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "invoice.paid", invoice)
    assert (await _tenant(fixture))["subscription_status"] == "active"
    charge_id, payment_id, dispute_id = "ch_" + uuid4().hex, "pi_" + uuid4().hex, "dp_" + uuid4().hex
    fixture.objects[("Charge", charge_id)] = stripe.Charge.construct_from({
        "id": charge_id, "object": "charge", "livemode": False, "invoice": invoice["id"],
        "customer": session["customer"], "payment_intent": payment_id, "currency": "usd",
    }, "sk_test_synthetic")
    dispute = {"id": dispute_id, "object": "dispute", "livemode": False, "charge": charge_id,
               "payment_intent": payment_id, "status": "needs_response"}
    fixture.objects[("Dispute", dispute_id)] = stripe.Dispute.construct_from(dispute, "sk_test_synthetic")

    result = await _apply(fixture, "charge.dispute.created", dispute)
    assert result["reason"] == "subscription_dispute_suspended"
    assert (await _tenant(fixture))["subscription_status"] == "suspended"
    # An ordinary 'active' update while the dispute is open keeps calls off.
    await _apply(fixture, "customer.subscription.updated", subscription)
    assert (await _tenant(fixture))["subscription_status"] == "suspended"

    fixture.objects[("Dispute", dispute_id)] = stripe.Dispute.construct_from(dict(dispute, status="lost"), "sk_test_synthetic")
    assert (await _apply(fixture, "charge.dispute.closed", dispute))["reason"] == "dispute_not_won"
    assert (await _tenant(fixture))["subscription_status"] == "suspended"
    fixture.objects[("Dispute", dispute_id)] = stripe.Dispute.construct_from(dict(dispute, status="won"), "sk_test_synthetic")
    result = await _apply(fixture, "charge.dispute.closed", dispute)
    assert result["reason"] == "subscription_dispute_won"
    assert (await _tenant(fixture))["subscription_status"] == "active"
