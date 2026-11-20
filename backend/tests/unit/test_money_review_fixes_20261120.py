"""Review fixes 20261120: stuck Stripe receipts can be dismissed (item 4), a PayPal
plan payment disputed at the inquiry stage is flagged (item 5), a RESOLVED dispute
never reverses (item 6), a PayPal plan purchase never lifts a suspension (item 8),
a refused capture is a final state (item 3). No database, no network."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.services import paypal_subscriptions as ps
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.paypal_client import PayPalError
from tests.unit import test_paypal_subscriptions as tps
from tests.unit import test_paypal_webhooks as tw
from tests.unit import test_review_payment_receipts as rr

pytestmark = pytest.mark.asyncio

world = tw.world  # fixture re-export
store = rr.store  # fixture re-export
env = tps.env  # fixture re-export


# -- item 4 ------------------------------------------------------------------------------


def _age(store, event_id, hours):
    store.rows[event_id]["received_at"] = datetime.now(timezone.utc) - timedelta(hours=hours)


@pytest.mark.parametrize("state", ["failed", "pending"])
async def test_a_stuck_failed_or_pending_receipt_can_be_dismissed(store, state):
    store.rows["evt_failed"]["state"] = state
    _age(store, "evt_failed", 7)
    result = await rr.recon().dismiss("evt_failed", operator="op", reason=rr.REASON)
    assert result == {"event_id": "evt_failed", "state": "dismissed", "changed": True,
                      "financial_effects_applied": False}
    assert store.rows["evt_failed"]["state"] == "dismissed"
    assert store.log == [{"event_id": "evt_failed", "operator": "op", "reason": rr.REASON}]
    assert store.updates == 1


@pytest.mark.parametrize("state", ["failed", "pending"])
async def test_a_young_failed_or_pending_receipt_is_refused_and_changes_nothing(store, state):
    store.rows["evt_failed"]["state"] = state
    _age(store, "evt_failed", 1)
    before = store.snapshot()
    with pytest.raises(ValueError, match="younger than 6 hours"):
        await rr.recon().dismiss("evt_failed", operator="op", reason=rr.REASON)
    assert store.snapshot() == before


async def test_a_stuck_receipt_needs_the_same_operator_and_reason(store):
    _age(store, "evt_failed", 7)
    before = store.snapshot()
    with pytest.raises(ValueError, match="operator and substantive"):
        await rr.recon().dismiss("evt_failed", operator="op", reason="short")
    assert store.snapshot() == before


async def test_a_dismissed_stuck_receipt_leaves_the_list(store):
    _age(store, "evt_failed", 7)
    listed = await rr.recon().list_unresolved()
    assert "evt_failed" in {r["event_id"] for r in listed["events"]}  # the operator can find it
    await rr.recon().dismiss("evt_failed", operator="op", reason=rr.REASON)
    listed = await rr.recon().list_unresolved()
    assert "evt_failed" not in {r["event_id"] for r in listed["events"]}


async def test_needs_review_is_dismissed_as_before(store):
    result = await rr.recon().dismiss("evt_a", operator="op", reason=rr.REASON)
    assert result["changed"] is True and store.rows["evt_a"]["state"] == "dismissed"


# -- item 3 ------------------------------------------------------------------------------


def _approved():
    return tw.body("CHECKOUT.ORDER.APPROVED", {"id": tw.PP_ORDER}, resource_type="checkout-order")


async def test_a_capture_refused_for_good_is_a_final_state_not_a_retry(world):
    async def refused(*, paypal_order_id):
        return SimpleNamespace(order_id=tw.ORDER, status="not_approved")

    world.svc.capture_approved_order = refused
    assert await tw.run(world, _approved()) == {"status": "completed"}
    assert tw.row(world)["detail"] == "approved_not_captured"
    assert tw.row(world)["order_id"] == tw.ORDER


async def test_a_transient_paypal_error_still_retries(world):
    async def down(*, paypal_order_id):
        raise PayPalError(status=503, name="UNAVAILABLE", retryable=True)

    world.svc.capture_approved_order = down
    with pytest.raises(tw.pw.PayPalWebhookRetry):
        await tw.run(world, _approved())


# -- item 5 ------------------------------------------------------------------------------


def _dispute(stage, *ids, **extra):
    res = tw.dispute(*ids, stage=None)
    if stage is not None:
        res["dispute_life_cycle_stage"] = stage
    res.update(extra)
    return res


async def test_inquiry_on_a_plan_payment_is_flagged_for_review(world):
    world.plans.owned = {tw.SALE}
    out = await tw.run(world, tw.body(
        "CUSTOMER.DISPUTE.CREATED", _dispute("INQUIRY", tw.SALE), resource_type="dispute"))
    assert out == {"status": "needs_review"}
    assert tw.row(world)["detail"] == "dispute_inquiry"
    assert world.svc.reversed == [] and world.plans.ended == []


async def test_inquiry_on_a_foreign_transaction_is_still_ignored(world):
    out = await tw.run(world, tw.body(
        "CUSTOMER.DISPUTE.CREATED", _dispute("INQUIRY", tw.SALE), resource_type="dispute"))
    assert out == {"status": "ignored"} and tw.row(world)["detail"] == "not_our_order"


async def test_owns_transaction_is_a_read_only_lookup(env):
    env.store["payments"]["TX-1"] = dict(id="p1", tenant_id=tps.TENANT, subscription_id="s1",
                                         paypal_transaction_id="TX-1", status="completed")
    before = len(env.store["sql"])
    assert await env.svc.owns_transaction("TX-1") is True
    assert await env.svc.owns_transaction("TX-2") is False
    assert env.store["sql"][before:] == [ps._Q_PAYMENT_BY_TXN] * 2


# -- item 6 ------------------------------------------------------------------------------


@pytest.mark.parametrize("event", ["CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.CREATED"])
@pytest.mark.parametrize("extra", [
    {"status": "RESOLVED"},
    {"dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"}},
])
async def test_an_escalated_dispute_already_resolved_never_reverses(world, event, extra):
    out = await tw.run(world, tw.body(event, _dispute("CHARGEBACK", tw.CAPTURE, **extra), resource_type="dispute"))
    assert out == {"status": "needs_review"}
    assert world.svc.reversed == [] and world.plans.ended == []


async def test_an_escalated_open_dispute_still_reverses(world):
    await tw.run(world, tw.body("CUSTOMER.DISPUTE.UPDATED",
                                _dispute("CHARGEBACK", "C1", status="UNDER_REVIEW"), resource_type="dispute"))
    assert [r["capture_id"] for r in world.svc.reversed] == ["C1"]


# -- item 8 ------------------------------------------------------------------------------


def _run_tenant_update(sql, row, args):
    """Evaluates a tenant statement from its own text: every WHERE predicate must hold
    for the row and args, then the SET clauses apply. Unknown text fails loudly."""
    m = re.fullmatch(r"UPDATE tenants SET (.+?) WHERE (.+)", " ".join(sql.split()))
    assert m, sql
    sets, where = m.groups()
    for pred in where.split(" AND "):
        if pred == "id = $1::uuid":
            ok = True
        elif pred == "stripe_subscription_id IS NULL":
            ok = row["stripe_subscription_id"] is None
        elif pred == "plan_id = $3":
            ok = row["plan_id"] == args[2]
        elif pred == "subscription_status IS DISTINCT FROM 'suspended'":
            ok = row["subscription_status"] != "suspended"
        else:
            raise AssertionError(f"unknown predicate {pred!r}")
        if not ok:
            return
    for clause in re.split(r", (?=\w+ = )", sets):
        col, expr = (x.strip() for x in clause.split(" = ", 1))
        if col == "plan_id" and expr == "$2":
            row["plan_id"] = args[1]
        elif col == "subscription_status" and expr in ("$2", "$3"):
            row["subscription_status"] = args[int(expr[1]) - 1]
        elif col == "subscription_status" and expr == (
                "CASE WHEN subscription_status = 'suspended' THEN subscription_status ELSE $3 END"):
            if row["subscription_status"] != "suspended":
                row["subscription_status"] = args[2]
        else:
            raise AssertionError(f"unknown SET {clause!r}")


def _row(status):
    return {"stripe_subscription_id": None, "plan_id": "pro", "subscription_status": status}


async def test_the_grant_statement_keeps_a_suspension_and_still_writes_the_plan():
    row = _row("suspended")
    _run_tenant_update(ps._Q_TENANT_GRANT, row, ("t", "pro2", "active"))
    assert row == {"stripe_subscription_id": None, "plan_id": "pro2", "subscription_status": "suspended"}


async def test_the_project_statement_keeps_a_suspension():
    row = _row("suspended")
    _run_tenant_update(ps._Q_TENANT_PROJECT, row, ("t", "past_due", "pro"))
    assert row["subscription_status"] == "suspended"


async def test_the_statements_still_write_for_a_normal_tenant():
    row = _row("active")
    _run_tenant_update(ps._Q_TENANT_PROJECT, row, ("t", "past_due", "pro"))
    assert row["subscription_status"] == "past_due"
    _run_tenant_update(ps._Q_TENANT_GRANT, row, ("t", "pro2", "active"))
    assert (row["plan_id"], row["subscription_status"]) == ("pro2", "active")


async def test_starting_a_paypal_plan_is_refused_for_a_suspended_tenant(env):
    tps.tenant(env)["subscription_status"] = tps.SUSPENDED
    before = len(env.store["subs"])
    with pytest.raises(CheckoutError) as raised:
        await tps.start(env)
    assert str(raised.value) == "This account is suspended and cannot buy a plan. Contact support."
    assert (raised.value.code, raised.value.status_code) == ("tenant_suspended", 403)
    assert raised.value.request_not_started is True
    assert len(env.store["subs"]) == before and env.client.names().count("create") == 0
