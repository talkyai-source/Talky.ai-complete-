"""A plan that ends leaves a pay-as-you-go account; bought minutes stay usable (real PostgreSQL, CI only).

The Stripe and PayPal sides are synthetic; this proves the SQL, the order of the allowance close and the
plan move, and the blocks that must stay (admin archive, chargeback, reversal).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
import stripe

from app.core.db_utils import acquire_with_tenant
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.payg import PurchaseRefused, tenant_purchase_audience
from app.domain.services.telephony.call_authority import read_call_authority
from tests.integration.test_billing_checkout import _tenant, checkout_db  # noqa: F401
from tests.integration.test_billing_price_options import billing_db  # noqa: F401
from tests.integration.test_billing_state_events import _apply, _paid, state_db  # noqa: F401
from tests.integration.test_paypal_subscriptions_pg import (  # noqa: F401
    _activated,
    _row,
    pp_db,
)
from tests.integration.test_paypal_subscriptions_pg import _tenant as _pp_tenant

pytestmark = pytest.mark.integration

# The checkout fixture gives the tenant a 120 minute plan and 43 bought minutes (ledger 50 and -7).
BOUGHT = 43


async def _ended(fixture):
    """Pay for the plan, then Stripe deletes the subscription."""
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    await _apply(fixture, "invoice.paid", invoice)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    result = await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    return subscription, session, invoice, result


async def _ledger_rows(fixture):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM billing_ledger WHERE tenant_id=$1", fixture.tenants[0])


async def _may_call_and_buy(fixture):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        tid = str(fixture.tenants[0])
        authority = await read_call_authority(conn, tid)
        audience = await tenant_purchase_audience(conn, tid)
        status = await compute_minutes_status(conn, tid)
    return authority, audience, status


async def test_stripe_deleted_keeps_bought_minutes_usable(state_db):
    fixture = state_db
    await _ended(fixture)
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"], tenant["minutes_allocated"]) == ("payg", "inactive", 0)
    assert tenant["stripe_subscription_id"] is None
    authority, audience, status = await _may_call_and_buy(fixture)
    assert authority.allowed is True and audience == "payg"
    assert status.allocated == BOUGHT and not status.unlimited


async def test_plan_end_never_grants_minutes(state_db):
    fixture = state_db
    receipt, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    await _apply(fixture, "invoice.paid", invoice)
    before_rows = await _ledger_rows(fixture)
    before = await _tenant(fixture)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    after = await _tenant(fixture)
    assert after["minutes_allocated"] == 0 <= before["minutes_allocated"]
    # No credit row: only a usage row (a non-positive adjustment) may appear.
    async with acquire_with_tenant(fixture.pool, None) as conn:
        credits = await conn.fetchval(
            "SELECT COUNT(*) FROM billing_ledger WHERE tenant_id=$1 AND minutes_delta>0", fixture.tenants[0])
    assert credits == 1  # the one bought top-up of the fixture
    assert await _ledger_rows(fixture) >= before_rows


async def test_redelivered_deleted_event_changes_nothing(state_db):
    fixture = state_db
    subscription, _, _, _ = await _ended(fixture)
    first = await _tenant(fixture)
    again = await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    assert again["reason"] == "subscription_replaced"
    assert await _tenant(fixture) == first


async def test_archived_account_stays_blocked_through_plan_end(state_db):
    fixture = state_db
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET subscription_status='cancelled' WHERE id=$1", fixture.tenants[0])
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    tenant = await _tenant(fixture)
    assert tenant["subscription_status"] == "cancelled" and tenant["plan_id"] == "payg"
    authority = await read_call_authority_for(fixture)
    assert authority.allowed is False and authority.reason == "tenant_cancelled"


async def read_call_authority_for(fixture):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        return await read_call_authority(conn, str(fixture.tenants[0]))


async def _dispute(fixture, invoice, session, status="needs_response"):
    charge_id, payment_id, dispute_id = "ch_" + uuid4().hex, "pi_" + uuid4().hex, "dp_" + uuid4().hex
    fixture.objects[("Charge", charge_id)] = stripe.Charge.construct_from({
        "id": charge_id, "object": "charge", "livemode": False, "invoice": invoice["id"],
        "customer": session["customer"], "payment_intent": payment_id, "currency": "usd",
    }, "sk_test_synthetic")
    dispute = {"id": dispute_id, "object": "dispute", "livemode": False, "charge": charge_id,
               "payment_intent": payment_id, "status": status}
    fixture.objects[("Dispute", dispute_id)] = stripe.Dispute.construct_from(dispute, "sk_test_synthetic")
    return dispute


async def test_dispute_opened_after_plan_end_suspends(state_db):
    fixture = state_db
    _, session, invoice, _ = await _ended(fixture)
    dispute = await _dispute(fixture, invoice, session)
    result = await _apply(fixture, "charge.dispute.created", dispute)
    assert result["reason"] == "subscription_dispute_suspended"
    assert (await _tenant(fixture))["subscription_status"] == "suspended"


async def test_dispute_won_after_end_restores_payg_not_the_plan(state_db):
    fixture = state_db
    _, session, invoice, _ = await _ended(fixture)
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    fixture.objects[("Dispute", dispute["id"])] = stripe.Dispute.construct_from(
        dict(dispute, status="won"), "sk_test_synthetic")
    fixture.objects[("Subscription", _subscription_id(fixture))] = dict(
        fixture.objects[("Subscription", _subscription_id(fixture))], status="canceled")
    result = await _apply(fixture, "charge.dispute.closed", dispute)
    assert result["reason"] == "subscription_dispute_won"
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"], tenant["minutes_allocated"]) == ("payg", "inactive", 0)


def _subscription_id(fixture):
    return next(identity for (resource, identity) in fixture.objects if resource == "Subscription")


async def test_dispute_lost_after_end_stays_suspended(state_db):
    fixture = state_db
    _, session, invoice, _ = await _ended(fixture)
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    fixture.objects[("Dispute", dispute["id"])] = stripe.Dispute.construct_from(
        dict(dispute, status="lost"), "sk_test_synthetic")
    await _apply(fixture, "charge.dispute.closed", dispute)
    assert (await _tenant(fixture))["subscription_status"] == "suspended"


# -- PayPal ---------------------------------------------------------------------------


async def test_paypal_reversal_ends_and_blocks(pp_db):
    fixture = pp_db
    rid, sid = await _activated(fixture)
    await fixture.svc.get_attempt(tenant_id=str(fixture.tenants[0]), request_id=str(rid))
    out = await fixture.svc.end_subscription_for_reversal(paypal_subscription_id=sid, kind="reversal", event_id="e1")
    assert out == "ended"
    state = await _pp_tenant(fixture)
    assert (state["plan_id"], state["subscription_status"], state["minutes_allocated"]) == ("payg", "cancelled", 0)


async def test_paypal_reversal_after_plan_end_reblocks(pp_db):
    fixture = pp_db
    rid, sid = await _activated(fixture)
    await fixture.svc.get_attempt(tenant_id=str(fixture.tenants[0]), request_id=str(rid))
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE paypal_subscriptions SET cancel_at_period_end=TRUE, "
                           "current_period_end = NOW() - INTERVAL '1 minute' WHERE id=$1", rid)
    assert (await fixture.svc.sweep())["ended"] == 1
    assert (await _pp_tenant(fixture))["subscription_status"] == "inactive"
    out = await fixture.svc.end_subscription_for_reversal(paypal_subscription_id=sid, kind="dispute", event_id="e2")
    assert out == "already"
    state = await _pp_tenant(fixture)
    assert state["subscription_status"] == "cancelled" and state["plan_id"] == "payg"
    assert (await _row(fixture, rid))["status"] == "cancelled"


# -- fixes from the money review (21 Nov 2026) -----------------------------------------


@pytest_asyncio.fixture
async def paypal_read(state_db):
    """Rights the plan-end path needs, and removal of the rows these cases add.

    A won dispute looks for a live PayPal plan (SELECT on paypal_subscriptions); a plan end that closes
    a window with usage beyond the plan writes the top-up usage row (INSERT on billing_ledger, as the
    top-up fixture grants in test_billing_topup_events.py). Rows added by the cases (a bare PayPal row,
    a campaign and a call) are deleted before the shared fixture's own cleanup, which would otherwise trip
    over their foreign keys.
    """
    fixture = state_db
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute(f'GRANT SELECT ON paypal_subscriptions TO "{fixture.role}"')
        await conn.execute(f'GRANT INSERT ON billing_ledger TO "{fixture.role}"')
        await conn.execute(f'GRANT USAGE ON SEQUENCE billing_ledger_id_seq TO "{fixture.role}"')  # id is BIGSERIAL
    try:
        yield fixture
    finally:
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute("DELETE FROM paypal_subscriptions WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            await conn.execute("DELETE FROM calls WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)
            await conn.execute("DELETE FROM campaigns WHERE tenant_id=ANY($1::uuid[])", fixture.tenants)


async def test_won_dispute_before_the_deleted_event_ends_the_plan_allowance(paypal_read):
    # F1: Stripe cancelled the plan, our deleted receipt has not applied, the dispute is won.
    fixture = paypal_read
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    await _apply(fixture, "invoice.paid", invoice)
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    fixture.objects[("Dispute", dispute["id"])] = stripe.Dispute.construct_from(
        dict(dispute, status="won"), "sk_test_synthetic")
    await _apply(fixture, "charge.dispute.closed", dispute)
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"], tenant["minutes_allocated"]) == ("payg", "inactive", 0)


async def test_won_dispute_never_ends_a_paypal_plan_bought_in_between(paypal_read):
    # F2: sub ended -> a PayPal plan is bought -> a dispute on the old invoice -> PayPal activates -> won.
    fixture = paypal_read
    _, session, invoice, _ = await _ended(fixture)
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    assert (await _tenant(fixture))["subscription_status"] == "suspended"
    async with acquire_with_tenant(fixture.pool, None) as conn:  # the PayPal activation
        await conn.execute("UPDATE tenants SET plan_id=$2, minutes_allocated=120 WHERE id=$1",
                           fixture.tenants[0], fixture.plan)
        await conn.execute(
            """INSERT INTO paypal_subscriptions
            (id, tenant_id, price_option_id, plan_id, provider_mode, paypal_plan_id, snapshot, status, activated_at)
            VALUES ($1, $2, $3, $4, 'test', $5, '{}'::jsonb, 'active', NOW())""",
            uuid4(), fixture.tenants[0], fixture.option, fixture.plan, "P-" + uuid4().hex[:14])
    fixture.objects[("Dispute", dispute["id"])] = stripe.Dispute.construct_from(
        dict(dispute, status="won"), "sk_test_synthetic")
    fixture.objects[("Subscription", _subscription_id(fixture))] = dict(
        fixture.objects[("Subscription", _subscription_id(fixture))], status="canceled")
    await _apply(fixture, "charge.dispute.closed", dispute)
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"], tenant["minutes_allocated"]) == (fixture.plan, "active", 120)


async def test_an_archive_is_not_lifted_by_an_old_dispute(paypal_read):
    # F3 (an admin archive; a PayPal reversal leaves the same state).
    fixture = paypal_read
    _, session, invoice, _ = await _ended(fixture)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET subscription_status='cancelled' WHERE id=$1", fixture.tenants[0])
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    assert (await _tenant(fixture))["subscription_status"] == "cancelled"
    fixture.objects[("Dispute", dispute["id"])] = stripe.Dispute.construct_from(
        dict(dispute, status="won"), "sk_test_synthetic")
    await _apply(fixture, "charge.dispute.closed", dispute)
    assert (await _tenant(fixture))["subscription_status"] == "cancelled"


async def test_a_deleted_subscription_with_an_open_chargeback_stays_suspended(paypal_read):
    # F5: an admin resumed the account while the chargeback is open; then the plan ends.
    fixture = paypal_read
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    await _apply(fixture, "invoice.paid", invoice)
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET subscription_status='active' WHERE id=$1", fixture.tenants[0])
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"]) == ("payg", "suspended")


async def test_usage_beyond_the_plan_is_not_taken_twice(paypal_read):
    # Plan 120, bought 43 (fixture). 150 minutes used: 30 beyond the plan come from the bought minutes.
    fixture = paypal_read
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    campaign = uuid4()
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("INSERT INTO campaigns(id,tenant_id,name,direction) VALUES($1,$2,'Synthetic','outbound')",
                           campaign, fixture.tenants[0])
        await conn.execute(
            "INSERT INTO calls (id, tenant_id, campaign_id, phone_number, status, duration_seconds, direction) "
            "VALUES ($1, $2, $3, '+15550003002', 'completed', 9000, 'outbound')", uuid4(), fixture.tenants[0], campaign)
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    _, _, status = await _may_call_and_buy(fixture)
    assert status.allocated == BOUGHT - 30
    again = await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    assert again["reason"] == "subscription_replaced"
    assert (await _may_call_and_buy(fixture))[2].allocated == BOUGHT - 30


async def test_a_new_stripe_plan_after_the_fallback(paypal_read):
    fixture = paypal_read
    await _ended(fixture)
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    tenant = await _tenant(fixture)
    assert tenant["plan_id"] == fixture.plan and tenant["subscription_status"] == "active"
    assert tenant["minutes_allocated"] == 120
    assert (await _may_call_and_buy(fixture))[2].allocated == 120 + BOUGHT


async def test_incoming_calls_are_admitted_after_the_fallback(paypal_read):
    # inbound_admission.py:795-806 admits `prepaid` + a status that is not in the blocked set.
    fixture = paypal_read
    await _ended(fixture)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        row = await conn.fetchrow(
            "SELECT t.subscription_status, p.billing_model FROM tenants t JOIN plans p ON p.id=t.plan_id "
            "WHERE t.id=$1", fixture.tenants[0])
    assert (row["billing_model"], row["subscription_status"]) == ("prepaid", "inactive")


async def test_resume_after_a_plan_ended_while_suspended_gives_no_plan_minutes(paypal_read):
    fixture = paypal_read
    _, subscription, session, invoice = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET subscription_status='suspended' WHERE id=$1", fixture.tenants[0])
    fixture.objects[("Subscription", subscription["id"])]["status"] = "canceled"
    await _apply(fixture, "customer.subscription.deleted", {"id": subscription["id"], "status": "active"})
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"], tenant["minutes_allocated"]) == ("payg", "suspended", 0)
    async with acquire_with_tenant(fixture.pool, None) as conn:  # the admin Resume writes 'active'
        await conn.execute("UPDATE tenants SET subscription_status='active' WHERE id=$1", fixture.tenants[0])
    assert (await _may_call_and_buy(fixture))[2].allocated == BOUGHT


@pytest.mark.parametrize("status", ["past_due", "unpaid"])
async def test_past_due_and_unpaid_stay_refused(paypal_read, status):
    fixture = paypal_read
    _, subscription, session, _ = await _paid(fixture)
    await _apply(fixture, "checkout.session.completed", session)
    fixture.objects[("Subscription", subscription["id"])]["status"] = status
    await _apply(fixture, "customer.subscription.updated", {"id": subscription["id"], "status": "active"})
    tenant = await _tenant(fixture)
    assert tenant["subscription_status"] == status and tenant["plan_id"] == fixture.plan
    async with acquire_with_tenant(fixture.pool, None) as conn:
        with pytest.raises(PurchaseRefused):
            await tenant_purchase_audience(conn, str(fixture.tenants[0]))


async def test_paypal_suspended_then_cancelled_moves_to_payg(pp_db):
    fixture = pp_db
    rid, sid = await _activated(fixture)
    await fixture.svc.get_attempt(tenant_id=str(fixture.tenants[0]), request_id=str(rid))
    fixture.client.remote[sid]["status"] = "SUSPENDED"
    await fixture.svc.sync(sid)
    assert (await _pp_tenant(fixture))["subscription_status"] == "unpaid"
    await fixture.svc.cancel_for_tenant(str(fixture.tenants[0]))
    state = await _pp_tenant(fixture)
    assert (state["plan_id"], state["subscription_status"], state["minutes_allocated"]) == ("payg", "inactive", 0)


async def test_won_dispute_keeps_calls_off_for_a_paypal_plan_suspended_at_paypal(paypal_read):
    # The status comes from the PayPal module's mapping (suspended at PayPal -> unpaid), never a blanket active.
    fixture = paypal_read
    _, session, invoice, _ = await _ended(fixture)
    dispute = await _dispute(fixture, invoice, session)
    await _apply(fixture, "charge.dispute.created", dispute)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("UPDATE tenants SET plan_id=$2, minutes_allocated=120 WHERE id=$1",
                           fixture.tenants[0], fixture.plan)
        await conn.execute(
            """INSERT INTO paypal_subscriptions
            (id, tenant_id, price_option_id, plan_id, provider_mode, paypal_plan_id, snapshot, status, activated_at)
            VALUES ($1, $2, $3, $4, 'test', $5, '{}'::jsonb, 'suspended', NOW())""",
            uuid4(), fixture.tenants[0], fixture.option, fixture.plan, "P-" + uuid4().hex[:14])
    fixture.objects[("Dispute", dispute["id"])] = stripe.Dispute.construct_from(
        dict(dispute, status="won"), "sk_test_synthetic")
    fixture.objects[("Subscription", _subscription_id(fixture))] = dict(
        fixture.objects[("Subscription", _subscription_id(fixture))], status="canceled")
    await _apply(fixture, "charge.dispute.closed", dispute)
    tenant = await _tenant(fixture)
    assert (tenant["plan_id"], tenant["subscription_status"], tenant["minutes_allocated"]) == (fixture.plan, "unpaid", 120)
