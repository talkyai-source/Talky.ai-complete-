"""A plan that ends leaves a pay-as-you-go account; bought minutes stay usable (real PostgreSQL, CI only).

The Stripe and PayPal sides are synthetic; this proves the SQL, the order of the allowance close and the
plan move, and the blocks that must stay (admin archive, chargeback, reversal).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import stripe

from app.core.db_utils import acquire_with_tenant
from app.domain.services.minutes_quota import compute_minutes_status
from app.domain.services.payg import tenant_purchase_audience
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
