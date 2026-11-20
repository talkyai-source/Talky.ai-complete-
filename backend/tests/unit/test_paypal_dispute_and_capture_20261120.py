"""Item 1: an INQUIRY-stage dispute must not take minutes or end a plan.
Item 2: CHECKOUT.ORDER.APPROVED captures our pending order without a browser.
No database, no network."""
from __future__ import annotations

import json

import pytest

from tests.unit import test_paypal_topups as tt
from tests.unit import test_paypal_webhooks as tw

pytestmark = pytest.mark.asyncio

world = tw.world  # fixture re-export
env = tt.env


def dispute(stage, *ids, dispute_id="PP-D-1"):
    res = tw.dispute(*ids, dispute_id=dispute_id, stage=None)
    if stage is not None:
        res["dispute_life_cycle_stage"] = stage
    return res


# -- item 1 ----------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["INQUIRY", None, "SOMETHING_NEW", ""])
async def test_inquiry_created_takes_no_minutes_and_ends_no_plan(world, stage):
    out = await tw.run(world, tw.body("CUSTOMER.DISPUTE.CREATED", dispute(stage, tw.CAPTURE)))
    assert out == {"status": "needs_review"}
    assert world.svc.reversed == [] and world.plans.ended == []
    r = tw.row(world)
    assert r["detail"] == "dispute_inquiry" and r["order_id"] == tw.ORDER


async def test_inquiry_on_a_plan_payment_ends_no_plan(world):
    world.svc.reverse_answers = "unknown"
    world.plans.owned = {tw.SALE}
    out = await tw.run(world, tw.body("CUSTOMER.DISPUTE.CREATED", dispute("INQUIRY", tw.SALE)))
    assert out == {"status": "needs_review"}
    assert tw.row(world)["detail"] == "dispute_inquiry"
    assert world.svc.reversed == [] and world.plans.ended == []


@pytest.mark.parametrize("stage", ["CHARGEBACK", "PRE_ARBITRATION", "ARBITRATION"])
async def test_escalated_created_reverses_as_before(world, stage):
    await tw.run(world, tw.body("CUSTOMER.DISPUTE.CREATED", dispute(stage, "C1")))
    assert world.svc.reversed == [dict(capture_id="C1", kind="dispute", event_id="paypal:dispute:PP-D-1:C1")]


@pytest.mark.parametrize("stage", ["CHARGEBACK", "PRE_ARBITRATION", "ARBITRATION"])
async def test_updated_to_escalated_reverses_with_the_same_keys(world, stage):
    world.svc.reverse_answers = ["unknown"]
    world.plans.end_answer = "ended"
    out = await tw.run(world, tw.body("CUSTOMER.DISPUTE.UPDATED", dispute(stage, "C1")))
    assert out == {"status": "completed"}
    assert world.svc.reversed == [dict(capture_id="C1", kind="dispute", event_id="paypal:dispute:PP-D-1:C1")]
    assert world.plans.ended == [
        dict(transaction_id="C1", kind="dispute", event_id="paypal:plan-dispute:PP-D-1:C1")]


@pytest.mark.parametrize("stage", ["INQUIRY", None])
async def test_updated_still_inquiry_is_review_only(world, stage):
    out = await tw.run(world, tw.body("CUSTOMER.DISPUTE.UPDATED", dispute(stage, tw.CAPTURE)))
    assert out == {"status": "needs_review"} and world.svc.reversed == [] and world.plans.ended == []
    assert tw.row(world)["detail"] == "dispute_update"


async def test_resolved_never_reverses_even_at_chargeback_stage(world):
    out = await tw.run(world, tw.body("CUSTOMER.DISPUTE.RESOLVED", dispute("CHARGEBACK", tw.CAPTURE)))
    assert out == {"status": "needs_review"} and world.svc.reversed == [] and world.plans.ended == []


# -- item 2: webhook dispatch -----------------------------------------------------------


def approved(resource_id=tw.PP_ORDER):
    return tw.body("CHECKOUT.ORDER.APPROVED", {"id": resource_id}, resource_type="checkout-order")


async def test_approved_event_captures_our_order(world):
    captured = []

    async def capture_approved_order(*, paypal_order_id):
        captured.append(paypal_order_id)
        return tw.SimpleNamespace(order_id=tw.ORDER, status="paid")

    world.svc.capture_approved_order = capture_approved_order
    assert await tw.run(world, approved()) == {"status": "completed"}
    assert captured == [tw.PP_ORDER] and tw.row(world)["order_id"] == tw.ORDER


async def test_approved_event_for_foreign_order_is_ignored(world):
    async def none(*, paypal_order_id):
        return None

    world.svc.capture_approved_order = none
    assert await tw.run(world, approved()) == {"status": "ignored"}


async def test_approved_event_without_id_is_ignored(world):
    assert await tw.run(world, tw.body("CHECKOUT.ORDER.APPROVED", {}, resource_type="checkout-order")) == {
        "status": "ignored"}


async def test_approved_event_verification_failure_is_needs_review(world):
    async def review(*, paypal_order_id):
        raise tw.PayPalTopupError("review", "amount_mismatch")

    world.svc.capture_approved_order = review
    assert await tw.run(world, approved()) == {"status": "needs_review"}


# -- item 2: service ---------------------------------------------------------------------


def ready(env):
    svc, client, topups, store = env
    order = tt.add_order(store)
    client.capture_answer = tt.pp_order(order)
    return svc, client, topups, store, order


async def test_webhook_first_captures_once_then_browser_does_not_recapture(env):
    svc, client, topups, store, order = ready(env)
    out = await svc.capture_approved_order(paypal_order_id=tt.PP_ORDER)
    assert out.status == "paid" and len(topups.credits) == 1
    assert client.calls == [("capture_order", tt.PP_ORDER, f"capture-{order['id']}")]
    again = await svc.capture_for_tenant(tenant_id=tt.TENANT, order_id=order["id"])
    assert again.status == "paid" and len(topups.credits) == 1 and client.names() == ["capture_order"]


async def test_browser_first_then_webhook_makes_no_second_capture(env):
    svc, client, topups, store, order = ready(env)
    await svc.capture_for_tenant(tenant_id=tt.TENANT, order_id=order["id"])
    out = await svc.capture_approved_order(paypal_order_id=tt.PP_ORDER)
    assert out.status == "paid" and len(topups.credits) == 1 and client.names() == ["capture_order"]


async def test_unknown_paypal_order_is_none_and_calls_nothing(env):
    svc, client, topups, store = env
    assert await svc.capture_approved_order(paypal_order_id="nope") is None
    assert client.calls == []


async def test_stripe_order_is_none(env):
    svc, client, topups, store = env
    tt.add_order(store, provider="stripe")
    assert await svc.capture_approved_order(paypal_order_id=tt.PP_ORDER) is None


async def test_webhook_capture_runs_for_the_orders_own_tenant(env):
    svc, client, topups, store, order = ready(env)
    await svc.capture_approved_order(paypal_order_id=tt.PP_ORDER)
    assert store["acquired"][-1] == tt.TENANT


async def test_verification_failure_raises_review_and_credits_nothing(env):
    svc, client, topups, store, order = ready(env)
    client.capture_answer = tt.pp_order(order, value="1.00")
    with pytest.raises(tt.PayPalTopupError) as exc:
        await svc.capture_approved_order(paypal_order_id=tt.PP_ORDER)
    assert exc.value.code == "review" and topups.credits == []
