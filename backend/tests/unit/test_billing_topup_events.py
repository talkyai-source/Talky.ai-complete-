"""Current Stripe object validation; PostgreSQL proves the actual transaction."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
import stripe

from app.domain.services import billing_topup_events as events
from tests.unit.test_topup_service import FakeConn, make_store, paid_order


class EventConn(FakeConn):
    locked = True

    def is_in_transaction(self):
        return True

    async def fetchval(self, sql, *args):
        if "pg_try_advisory" in sql:
            return self.locked
        if "SELECT id FROM topup_orders" in sql:
            return (
                self.store["order"]["id"]
                if self.store["order"].get("provider_payment_id") == args[0]
                else None
            )
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        if "FROM tenants" in sql:
            return {
                "stripe_customer_id": "cus_fixture",
                "minutes_allocated": self.store["allocated"],
            }
        if "COUNT(*) AS count" in sql:
            rows = [row for row in self.store["ledger"] if row["kind"] == "topup"]
            return {
                "count": len(rows),
                "currency_mismatches": sum(row["currency"].lower() != args[1] for row in rows),
                "minutes": sum(row["minutes_delta"] for row in rows),
                "amount": sum(row["amount_cents"] for row in rows),
            }
        if "FROM topup_orders WHERE id=" in sql and args[0] != self.store["order"]["id"]:
            return None
        result = await super().fetchrow(sql, *args)
        if "INSERT INTO billing_ledger" in sql and result:
            self.store["ledger"][-1]["amount_cents"] = args[3] if "'topup'" in sql else args[4]
            self.store["ledger"][-1]["currency"] = args[4] if "'topup'" in sql else args[5]
        return result

    async def fetch(self, sql, *args):
        if "FROM billing_ledger" in sql:
            return [row for row in self.store["ledger"] if row["kind"] in {"refund", "dispute"}]
        return []


def fixture_data():
    order_id, tenant_id = uuid4(), uuid4()
    order = {
        **paid_order(),
        "id": order_id,
        "tenant_id": tenant_id,
        "provider": "stripe",
        "provider_session_id": "cs_fixture",
        "provider_payment_id": None,
    }
    metadata = {
        "purpose": "minute_topup",
        "order_id": str(order_id),
        "tenant_id": str(tenant_id),
        "minutes": "999999",
    }
    session = {
        "id": "cs_fixture",
        "mode": "payment",
        "livemode": False,
        "customer": "cus_fixture",
        "payment_intent": "pi_fixture",
        "status": "complete",
        "payment_status": "paid",
        "amount_total": 2500,
        "currency": "gbp",
        "client_reference_id": str(order_id),
        "metadata": metadata,
        "customer_details": {"email": "buyer@example.com"},
    }
    payment = {
        "id": "pi_fixture",
        "status": "succeeded",
        "livemode": False,
        "customer": "cus_fixture",
        "amount": 2500,
        "amount_received": 2500,
        "currency": "gbp",
        "metadata": metadata,
        "latest_charge": "ch_fixture",
    }
    charge = {
        "id": "ch_fixture",
        "payment_intent": "pi_fixture",
        "livemode": False,
        "customer": "cus_fixture",
        "paid": True,
        "status": "succeeded",
        "amount": 2500,
        "amount_captured": 2500,
        "currency": "gbp",
        "amount_refunded": 0,
        "refunded": False,
        "disputed": False,
    }
    dispute = {
        "id": "dp_fixture",
        "charge": "ch_fixture",
        "payment_intent": "pi_fixture",
        "livemode": False,
        "amount": 2500,
        "currency": "gbp",
        "status": "needs_response",
    }
    objects = {
        "checkout.Session": session,
        "PaymentIntent": payment,
        "Charge": charge,
        "Dispute": dispute,
    }
    calls = []

    async def retrieve(resource, method, identity):
        calls.append((resource, method, identity))
        assert method == "retrieve" and identity == objects[resource]["id"]
        return stripe.StripeObject.construct_from(deepcopy(objects[resource]), "sk_test_synthetic")

    store = make_store(allocated=1000, enforced=1000, order=order)
    return SimpleNamespace(
        conn=EventConn(store),
        store=store,
        provider=SimpleNamespace(billing_mode="test", _stripe_call=retrieve),
        objects=objects,
        calls=calls,
        session=session,
        payment=payment,
        charge=charge,
        dispute=dispute,
    )


async def apply(fixture, event_type="checkout.session.completed", event_id="evt_fixture"):
    data = (
        fixture.dispute
        if "dispute" in event_type
        else fixture.charge if event_type.startswith("charge.") else fixture.session
    )
    return await events.apply_topup_event(
        fixture.conn, fixture.provider, event_type, deepcopy(data), event_id
    )


@pytest.mark.asyncio
async def test_distinct_success_events_credit_once_and_recover_same_receipt_intent():
    fixture = fixture_data()
    first = await apply(fixture)
    second = await apply(fixture, "checkout.session.async_payment_succeeded", "evt_late")
    assert fixture.store["allocated"] == fixture.store["enforced"] == 1250
    assert len(fixture.store["ledger"]) == 1
    assert first["notifications"][0]["delivery_key"] == second["notifications"][0]["delivery_key"]
    assert first["notifications"][0]["prior_delivery_unknown"] is False
    assert second["notifications"][0]["prior_delivery_unknown"] is True
    receipt = first["notifications"][0]
    assert receipt["delivery_key"] == f"topup:{fixture.store['order']['id']}:paid"
    assert (
        "250" in receipt["body"]
        and "GBP 25.00" in receipt["body"]
        and "999999" not in receipt["body"]
    )


@pytest.mark.asyncio
async def test_credit_refund_then_late_credit_has_one_positive_and_one_negative_entry():
    fixture = fixture_data()
    await apply(fixture)
    fixture.charge.update(amount_refunded=2500, refunded=True)
    await apply(fixture, "charge.refunded", "evt_refund")
    late = await apply(fixture, event_id="evt_late")
    assert late["notifications"] == [] and fixture.store["allocated"] == 1000
    assert [row["minutes_delta"] for row in fixture.store["ledger"]] == [250, -250]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource,field,value",
    [
        ("checkout.Session", "mode", "subscription"),
        ("checkout.Session", "customer", "cus_other"),
        ("checkout.Session", "livemode", True),
        ("checkout.Session", "amount_total", 1),
        ("checkout.Session", "currency", "usd"),
        ("checkout.Session", "client_reference_id", "other"),
        ("PaymentIntent", "customer", "cus_other"),
        ("PaymentIntent", "amount_received", 1),
        ("PaymentIntent", "currency", "usd"),
        ("PaymentIntent", "metadata", {}),
        ("Charge", "customer", "cus_other"),
        ("Charge", "livemode", True),
        ("Charge", "paid", False),
        ("Charge", "amount_captured", 1),
        ("Charge", "currency", "usd"),
    ],
)
async def test_mismatched_current_evidence_cannot_change_money(resource, field, value):
    fixture = fixture_data()
    fixture.objects[resource][field] = value
    with pytest.raises(events.BillingTopupReviewRequired):
        await apply(fixture)
    assert fixture.store["ledger"] == [] and fixture.store["allocated"] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("refunded", [1, 333, 2499])
async def test_partial_refund_is_explicit_review_without_invented_rounding(refunded):
    fixture = fixture_data()
    await apply(fixture)
    fixture.charge["amount_refunded"] = refunded
    with pytest.raises(events.BillingTopupReviewRequired, match="partial_refund"):
        await apply(fixture, "charge.refunded", "evt_partial")
    assert fixture.store["allocated"] == 1250 and len(fixture.store["ledger"]) == 1


@pytest.mark.asyncio
async def test_precredit_refund_is_review_and_later_credit_cannot_ignore_current_refund():
    fixture = fixture_data()
    fixture.charge.update(refunded=True, amount_refunded=2500)
    with pytest.raises(events.BillingTopupReviewRequired, match="before_recorded_credit"):
        await apply(fixture, "charge.refunded")
    with pytest.raises(events.BillingTopupReviewRequired, match="reversed_before_credit"):
        await apply(fixture, event_id="evt_late")
    assert fixture.store["ledger"] == [] and fixture.store["allocated"] == 1000


@pytest.mark.asyncio
async def test_current_dispute_reverses_once_without_inventing_a_refund():
    fixture = fixture_data()
    await apply(fixture)
    fixture.charge["disputed"] = True
    await apply(fixture, "charge.dispute.created", "evt_dispute")
    await apply(fixture, "charge.dispute.created", "evt_duplicate_dispute")
    assert [row["kind"] for row in fixture.store["ledger"]] == ["topup", "dispute"]
    assert fixture.store["allocated"] == 1000


@pytest.mark.asyncio
async def test_missing_recipient_is_retained_as_a_nullable_intent():
    fixture = fixture_data()
    fixture.session["customer_details"] = {}
    assert (await apply(fixture))["notifications"][0]["recipient"] is None


@pytest.mark.asyncio
async def test_unsettled_payment_is_deferred_without_credit():
    fixture = fixture_data()
    fixture.session["payment_status"] = "unpaid"
    fixture.payment["status"] = "processing"
    assert (await apply(fixture))["status"] == "deferred"
    assert fixture.store["ledger"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "async_payment_failed"])
async def test_authoritative_nonpayment_changes_only_the_pending_order(failure):
    fixture = fixture_data()
    fixture.session["payment_status"] = "unpaid"
    if failure == "expired":
        fixture.session["status"] = "expired"
    else:
        fixture.payment["status"] = "requires_payment_method"
    assert (await apply(fixture, "checkout.session." + failure))["status"] == "handled"
    assert fixture.store["order"]["status"] == ("cancelled" if failure == "expired" else "failed")
    assert fixture.store["ledger"] == []


@pytest.mark.asyncio
async def test_concurrent_aggregate_owner_causes_retry_before_provider_read():
    fixture = fixture_data()
    fixture.conn.locked = False
    with pytest.raises(events.BillingTopupBusy):
        await apply(fixture)
    assert fixture.calls == [] and fixture.store["ledger"] == []


@pytest.mark.asyncio
async def test_provider_timeout_is_retryable_and_missing_object_needs_review():
    fixture = fixture_data()

    async def timeout(*args):
        raise TimeoutError("synthetic")

    fixture.provider._stripe_call = timeout
    with pytest.raises(TimeoutError):
        await apply(fixture)

    async def absent(*args):
        raise stripe.InvalidRequestError("synthetic missing", "id", http_status=404)

    fixture.provider._stripe_call = absent
    with pytest.raises(events.BillingTopupReviewRequired, match="provider_object_not_found"):
        await apply(fixture)
    assert fixture.store["ledger"] == []


@pytest.mark.asyncio
async def test_missing_signed_event_identity_never_invents_a_ledger_key():
    fixture = fixture_data()
    with pytest.raises(events.BillingTopupReviewRequired, match="event_identity_missing"):
        await apply(fixture, event_id=None)
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_unpaid_event_can_discover_settled_payment_after_aggregate_lock():
    fixture = fixture_data()
    earlier = deepcopy(fixture.session)
    earlier.update(payment_intent=None, payment_status="unpaid")
    result = await events.apply_topup_event(
        fixture.conn, fixture.provider, "checkout.session.completed", earlier, "evt_earlier"
    )
    assert result["status"] == "handled" and fixture.store["allocated"] == 1250
    assert [call[0] for call in fixture.calls] == [
        "checkout.Session",
        "checkout.Session",
        "PaymentIntent",
        "Charge",
    ]


@pytest.mark.asyncio
async def test_discovered_payment_is_reread_and_changed_binding_cannot_credit():
    fixture = fixture_data()
    earlier = deepcopy(fixture.session)
    earlier["payment_intent"] = None
    retrieve = fixture.provider._stripe_call

    async def drift(resource, *args):
        result = await retrieve(resource, *args)
        if resource == "checkout.Session":
            fixture.session["payment_intent"] = "pi_changed"
        return result

    fixture.provider._stripe_call = drift
    with pytest.raises(events.BillingTopupReviewRequired, match="payment_binding_changed"):
        await events.apply_topup_event(
            fixture.conn, fixture.provider, "checkout.session.completed", earlier, "evt_earlier"
        )
    assert fixture.store["ledger"] == []


@pytest.mark.asyncio
async def test_paid_unlimited_tenant_retains_sentinel_and_duplicate_ledger():
    fixture = fixture_data()
    fixture.store.update(allocated=0, enforced=0)
    await apply(fixture)
    await apply(fixture, event_id="evt_repeat")
    assert fixture.store["allocated"] == fixture.store["enforced"] == 0
    assert len(fixture.store["ledger"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["topup", "refund"])
async def test_existing_ledger_currency_conflict_requires_review(kind):
    fixture = fixture_data()
    await apply(fixture)
    if kind == "refund":
        fixture.charge.update(amount_refunded=2500, refunded=True)
        await apply(fixture, "charge.refunded", "evt_refund")
    next(row for row in fixture.store["ledger"] if row["kind"] == kind)["currency"] = "usd"
    with pytest.raises(events.BillingTopupReviewRequired, match="recorded_.*_conflict"):
        await apply(fixture, event_id="evt_repeat")


@pytest.mark.asyncio
async def test_nullable_dispute_payment_binds_through_current_charge_and_rereads():
    fixture = fixture_data()
    await apply(fixture)
    fixture.calls.clear()
    fixture.dispute["payment_intent"] = None
    fixture.charge["disputed"] = True
    await apply(fixture, "charge.dispute.created", "evt_dispute")
    assert [call[0] for call in fixture.calls][:4] == ["Dispute", "Charge", "Dispute", "Charge"]
    assert [row["minutes_delta"] for row in fixture.store["ledger"]] == [250, -250]


@pytest.mark.asyncio
async def test_nullable_dispute_binding_drift_cannot_reverse():
    fixture = fixture_data()
    await apply(fixture)
    fixture.dispute["payment_intent"] = None
    fixture.charge["disputed"] = True
    retrieve = fixture.provider._stripe_call

    async def drift(resource, *args):
        result = await retrieve(resource, *args)
        if resource == "Charge":
            fixture.charge["payment_intent"] = "pi_changed"
        return result

    fixture.provider._stripe_call = drift
    with pytest.raises(events.BillingTopupReviewRequired, match="charge_payment_mismatch"):
        await apply(fixture, "charge.dispute.created", "evt_dispute")
    assert len(fixture.store["ledger"]) == 1 and fixture.store["allocated"] == 1250


@pytest.mark.asyncio
async def test_valid_credit_with_stale_nonpaid_order_is_review_not_completed():
    fixture = fixture_data()
    await apply(fixture)
    fixture.store["order"]["status"] = "pending"
    with pytest.raises(events.BillingTopupReviewRequired, match="order_ledger_state_conflict"):
        await apply(fixture, event_id="evt_stale")
    assert len(fixture.store["ledger"]) == 1 and fixture.store["allocated"] == 1250
