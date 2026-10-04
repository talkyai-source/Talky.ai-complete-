"""Subscription/top-up routing and existing card-only checkout contract.

Trusted transactional financial handling lives in test_billing_topup_events;
receipt claim, crash recovery and HTTP delivery are tested independently.
"""

from __future__ import annotations

import pytest
from app.domain.services.billing_service import BillingService


class _StubClient:
    """BillingService only touches ``.pool`` on the paths under test."""

    pool = None


def _svc() -> BillingService:
    with pytest.MonkeyPatch.context() as configured:
        configured.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
        configured.delenv("STRIPE_MOCK_MODE", raising=False)
        configured.delenv("STRIPE_BILLING_DISABLED", raising=False)
        return BillingService(_StubClient())


def _topup_session(**over):
    base = {
        "id": "cs_test_1",
        "payment_status": "paid",
        "payment_intent": "pi_test_1",
        "metadata": {
            "purpose": "minute_topup",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "order_id": "order-1",
            "minutes": "250",
        },
    }
    base.update(over)
    return base


def _subscription_session():
    return {
        "id": "cs_test_2",
        "payment_status": "paid",
        "metadata": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "plan_id": "plan_growth",
        },
    }


@pytest.mark.asyncio
async def test_a_topup_checkout_is_recognised_as_a_topup():
    assert await _svc()._is_topup_event("checkout.session.completed", _topup_session()) is True


@pytest.mark.asyncio
async def test_a_subscription_checkout_is_not_routed_to_the_topup_handler():
    """THE REGRESSION GUARD. If this ever returns True, subscriptions stop
    being provisioned — the top-up handler would look for an order that does
    not exist and no plan would be activated."""
    assert (
        await _svc()._is_topup_event("checkout.session.completed", _subscription_session()) is False
    )


@pytest.mark.asyncio
async def test_a_session_with_no_metadata_at_all_is_not_a_topup():
    svc = _svc()
    assert await svc._is_topup_event("checkout.session.completed", {}) is False
    assert await svc._is_topup_event("checkout.session.completed", {"metadata": None}) is False


@pytest.mark.asyncio
async def test_charge_events_are_claimed_for_the_topup_path():
    """Refunds and disputes arrive on the charge, which carries no session. The
    subscription handler table has no entry for either, so claiming them costs
    nothing when the charge turns out to belong to a subscription — the order
    lookup finds nothing and the handler no-ops."""
    svc = _svc()
    assert await svc._is_topup_event("charge.refunded", {}) is True
    assert await svc._is_topup_event("charge.dispute.created", {}) is True
    assert await svc._is_topup_event("invoice.paid", {}) is False


def _async_succeeded_session():
    """The same Checkout Session object, redelivered as the settled event."""
    return _topup_session(id="cs_test_1", payment_status="paid")


@pytest.mark.asyncio
async def test_a_delayed_payment_success_is_claimed_for_the_topup_path():
    assert (
        await _svc()._is_topup_event(
            "checkout.session.async_payment_succeeded", _async_succeeded_session()
        )
        is True
    )


@pytest.mark.asyncio
async def test_a_topup_checkout_only_offers_immediate_payment_methods(monkeypatch):
    """Nothing in this product handles a payment that settles days later —
    there is no order reaper and no pending-payment UI. Stripe defaults to
    every method enabled on the account, which on a GBP account includes
    Bacs. Constrain the session to methods that settle at checkout.
    """
    svc = _svc()
    monkeypatch.setattr(svc, "mock_mode", False)

    async def _customer(tenant_id, email, business_name=None):
        return {"customer_id": "cus_1", "created": False}

    monkeypatch.setattr(svc, "create_or_get_customer", _customer)

    import app.domain.services.billing_service as bs

    captured = {}

    class _Created:
        id = "cs_new"
        url = "https://pay.example/cs_new"

    class _Session:
        @staticmethod
        def create(**kw):
            captured.update(kw)
            return _Created()

    monkeypatch.setattr(bs.stripe.checkout, "Session", _Session)

    await svc.create_topup_checkout_session(
        tenant_id="11111111-1111-1111-1111-111111111111",
        email="a@b.co",
        order_id="order-1",
        minutes=250,
        price_cents=2500,
        currency="GBP",
        product_name="250 minutes",
        success_url="https://app/ok",
        cancel_url="https://app/no",
    )

    assert captured.get("payment_method_types") == ["card"], (
        "the checkout can offer a delayed method (Bacs/SEPA/Klarna), which "
        "charges the customer days after the session and leaves the order "
        "pending in the meantime"
    )
