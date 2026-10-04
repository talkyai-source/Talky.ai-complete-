"""Signed snapshots are hints; authoritative bound objects govern access."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import stripe

from app.domain.services.billing_state_events import (
    BillingStateBusy,
    BillingStateReviewRequired,
    apply_billing_event,
    invoice_subscription_id,
    validate_paid_invoice_lines,
)


def test_invoice_subscription_accepts_current_sdk_parent_and_legacy_shape():
    current = stripe.Invoice.construct_from({
        "id": "in_synthetic", "object": "invoice", "metadata": {},
        "parent": {"type": "subscription_details", "subscription_details": {
            "subscription": {"id": "sub_synthetic", "object": "subscription"},
        }},
    }, "sk_test_synthetic")
    assert invoice_subscription_id(current) == "sub_synthetic"
    assert invoice_subscription_id({"subscription": "sub_synthetic"}) == "sub_synthetic"
    assert invoice_subscription_id({"parent": None}) is None
    with pytest.raises(BillingStateReviewRequired):
        invoice_subscription_id({**current, "subscription": "sub_different"})


@pytest.mark.asyncio
async def test_busy_subscription_is_retryable_before_any_provider_read_or_mutation():
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=False), execute=AsyncMock())
    billing = SimpleNamespace(billing_mode="test", _stripe_call=AsyncMock())
    with pytest.raises(BillingStateBusy):
        await apply_billing_event(conn, billing, "customer.subscription.updated", {"id": "sub_synthetic"})
    billing._stripe_call.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_mode_mismatch_never_reaches_tenant_mutation():
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=True), execute=AsyncMock(), fetchrow=AsyncMock())
    billing = SimpleNamespace(billing_mode="test", _stripe_call=AsyncMock(return_value={
        "id": "sub_synthetic", "livemode": True, "customer": "cus_synthetic", "status": "active",
    }))
    with pytest.raises(BillingStateReviewRequired):
        await apply_billing_event(conn, billing, "customer.subscription.updated", {"id": "sub_synthetic"})
    conn.execute.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()


@pytest.mark.parametrize("variant", ["legacy", "modern"])
def test_paid_invoice_line_validates_frozen_price_in_both_supported_shapes(variant):
    line = {"currency": "usd", "quantity": 1}
    if variant == "legacy":
        line.update(type="subscription", price={"id": "price_selected"})
    else:
        line.update(parent={"type": "subscription_item_details"}, pricing={"price_details": {"price": "price_selected"}})
    option = {"stripe_price_id": "price_selected", "currency": "usd"}
    validate_paid_invoice_lines({"lines": {"has_more": False, "data": [line]}}, option)
    with pytest.raises(BillingStateReviewRequired):
        validate_paid_invoice_lines({"lines": {"has_more": False, "data": [line, {**line, "quantity": 2}]}}, option)
    for has_more in (True, None):
        with pytest.raises(BillingStateReviewRequired):
            validate_paid_invoice_lines({"lines": {"has_more": has_more, "data": [line]}}, option)


@pytest.mark.asyncio
async def test_unknown_provider_subscription_state_is_reviewed_without_writes():
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=True), execute=AsyncMock(), fetchrow=AsyncMock())
    billing = SimpleNamespace(billing_mode="test", _stripe_call=AsyncMock(return_value={
        "id": "sub_synthetic", "livemode": False, "customer": "cus_synthetic", "status": "unrecognized_new_state",
    }))
    with pytest.raises(BillingStateReviewRequired, match="subscription_status_unknown"):
        await apply_billing_event(conn, billing, "customer.subscription.updated", {"id": "sub_synthetic"})
    conn.execute.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()
