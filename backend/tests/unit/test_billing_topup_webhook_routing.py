"""One webhook stream, two products — and the routing that keeps them apart.

Stripe delivers subscription checkouts and minute-top-up checkouts down the SAME
``checkout.session.completed`` event type, to the SAME endpoint. The existing
subscription handler reads ``metadata.tenant_id`` and then writes
``plan_id`` and ``stripe_subscription_id`` from the session. A one-time payment
session carries neither, so letting a top-up reach that handler would blank out
the plan of a customer at the exact moment they paid us.

That is the bug this file exists to prevent, and it is invisible until a real
customer buys minutes while holding a subscription.
"""
from __future__ import annotations

import pytest

from app.domain.services.billing_service import BillingService


class _StubClient:
    """BillingService only touches ``.pool`` on the paths under test."""
    pool = None


def _svc() -> BillingService:
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


# ── the separation itself ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_topup_checkout_is_recognised_as_a_topup():
    assert await _svc()._is_topup_event(
        "checkout.session.completed", _topup_session()
    ) is True


@pytest.mark.asyncio
async def test_a_subscription_checkout_is_not_routed_to_the_topup_handler():
    """THE REGRESSION GUARD. If this ever returns True, subscriptions stop
    being provisioned — the top-up handler would look for an order that does
    not exist and no plan would be activated."""
    assert await _svc()._is_topup_event(
        "checkout.session.completed", _subscription_session()
    ) is False


@pytest.mark.asyncio
async def test_a_session_with_no_metadata_at_all_is_not_a_topup():
    svc = _svc()
    assert await svc._is_topup_event("checkout.session.completed", {}) is False
    assert await svc._is_topup_event(
        "checkout.session.completed", {"metadata": None}
    ) is False


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


# ── completing a checkout is not the same as paying for it ──────────────────

@pytest.mark.asyncio
async def test_an_unsettled_payment_defers_instead_of_crediting(monkeypatch):
    """A checkout can complete with the payment still processing (delayed
    methods do this routinely). Crediting there hands out minutes for a payment
    that may still fail."""
    svc = _svc()
    called = {"credit": 0}

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            called["credit"] += 1
            return True

    monkeypatch.setattr(
        "app.domain.services.topup_service.TopupService", _Topups
    )

    result = await svc._handle_topup_event(
        "checkout.session.completed",
        _topup_session(payment_status="unpaid"),
        "evt_1",
    )

    assert result["status"] == "deferred"
    assert called["credit"] == 0, "minutes were credited before the money arrived"


@pytest.mark.asyncio
async def test_a_settled_payment_credits_with_the_stripe_event_id(monkeypatch):
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            seen.update(kw)
            return True

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    result = await svc._handle_topup_event(
        "checkout.session.completed", _topup_session(), "evt_abc",
    )

    assert result["status"] == "handled"
    assert seen["event_id"] == "evt_abc", (
        "the ledger's idempotency key must be Stripe's event id — anything "
        "generated locally differs on every redelivery and dedupes nothing"
    )
    assert seen["session_id"] == "cs_test_1"
    assert seen["payment_id"] == "pi_test_1"


@pytest.mark.asyncio
async def test_a_missing_event_id_still_produces_a_stable_key(monkeypatch):
    """A NULL idempotency key is not deduped by a partial unique index, so a
    missing event id must fall back to something stable per payment rather than
    to nothing."""
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            seen.update(kw)
            return True

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    await svc._handle_topup_event("checkout.session.completed", _topup_session(), None)

    assert seen["event_id"]
    assert "cs_test_1" in seen["event_id"], "the fallback key must vary per payment"


# ── reversals ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_partial_refund_does_not_claw_back_the_whole_bundle(monkeypatch):
    """Refunding £5 of a £25 bundle is not a reason to take 250 minutes away.
    Splitting a bundle is a judgement call, so it is flagged, not guessed."""
    svc = _svc()
    called = {"reverse": 0}

    class _Topups:
        def __init__(self, pool): pass
        async def reverse(self, **kw):
            called["reverse"] += 1
            return True

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    result = await svc._handle_topup_event(
        "charge.refunded",
        {"id": "ch_1", "payment_intent": "pi_1", "refunded": False,
         "amount": 2500, "amount_refunded": 500},
        "evt_r",
    )

    assert result["status"] == "ignored"
    assert called["reverse"] == 0


@pytest.mark.asyncio
async def test_a_full_refund_reverses(monkeypatch):
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def reverse(self, **kw):
            seen.update(kw)
            return True

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    await svc._handle_topup_event(
        "charge.refunded",
        {"id": "ch_1", "payment_intent": "pi_1", "refunded": True, "amount": 2500},
        "evt_r",
    )

    assert seen["kind"] == "refund"
    assert seen["payment_id"] == "pi_1"


@pytest.mark.asyncio
async def test_a_dispute_reverses_as_a_dispute_not_a_refund(monkeypatch):
    """They are different events in a reconciliation: one is us giving money
    back, the other is the bank taking it."""
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def reverse(self, **kw):
            seen.update(kw)
            return True

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    await svc._handle_topup_event(
        "charge.dispute.created", {"id": "dp_1", "payment_intent": "pi_1"}, "evt_d",
    )

    assert seen["kind"] == "dispute"


# ── expiry ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_expired_checkout_is_cancelled_not_failed(monkeypatch):
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def mark_failed(self, **kw):
            seen.update(kw)

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    await svc._handle_topup_event(
        "checkout.session.expired", _topup_session(), "evt_x",
    )

    assert seen["status"] == "cancelled"


@pytest.mark.asyncio
async def test_an_async_payment_failure_marks_the_order_failed(monkeypatch):
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def mark_failed(self, **kw):
            seen.update(kw)

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    await svc._handle_topup_event(
        "checkout.session.async_payment_failed", _topup_session(), "evt_f",
    )

    assert seen["status"] == "failed"


# ── the claim, and why it has to be releasable ──────────────────────────────

def _fake_event(event_type, data, event_id="evt_boom"):
    return {"id": event_id, "type": event_type, "data": {"object": data}}


def _arm_for_real_webhook(svc, monkeypatch, event):
    """Drive ``handle_webhook`` end to end without a Stripe signature."""
    monkeypatch.setattr(svc, "mock_mode", False)
    svc.webhook_secret = "whsec_test"

    import app.domain.services.billing_service as bs

    class _Webhook:
        @staticmethod
        def construct_event(payload, signature, secret):
            return event

    monkeypatch.setattr(bs.stripe, "Webhook", _Webhook)

    claimed = []

    async def _claim(event_id, event_type):
        claimed.append(event_id)
        return True

    monkeypatch.setattr(svc, "_claim_webhook_event", _claim)
    return claimed


@pytest.mark.asyncio
async def test_a_failed_credit_releases_the_claim_so_stripe_can_retry(monkeypatch):
    """THE SUBTLE ONE, driven through the real ``handle_webhook``.

    The event id is claimed BEFORE the handler runs. A transient database error
    while crediting would therefore be permanent: Stripe's redelivery is
    discarded as a duplicate and the customer never receives what they paid
    for. Releasing the claim turns a lost payment into a retry.
    """
    svc = _svc()
    event = _fake_event("checkout.session.completed", _topup_session())
    claimed = _arm_for_real_webhook(svc, monkeypatch, event)

    released = []

    async def _release(event_id):
        released.append(event_id)

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            raise RuntimeError("connection reset")

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)
    monkeypatch.setattr(svc, "_release_webhook_claim", _release)

    with pytest.raises(RuntimeError):
        await svc.handle_webhook(b"{}", "sig")

    assert claimed == ["evt_boom"], "the event should have been claimed first"
    assert released == ["evt_boom"], (
        "the claim was not released, so Stripe's retry will be discarded as a "
        "duplicate and these minutes are lost for good"
    )


@pytest.mark.asyncio
async def test_a_topup_never_reaches_the_subscription_handler(monkeypatch):
    """Driven through the real dispatch, because the routing IS the fix.

    ``_handle_checkout_completed`` would write the session's absent
    ``plan_id``/``subscription`` over the tenant's real ones.
    """
    svc = _svc()
    event = _fake_event("checkout.session.completed", _topup_session(), "evt_ok")
    _arm_for_real_webhook(svc, monkeypatch, event)

    subscription_handler_ran = []

    async def _boom(session):
        subscription_handler_ran.append(session)

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            return True

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)
    monkeypatch.setattr(svc, "_handle_checkout_completed", _boom)

    result = await svc.handle_webhook(b"{}", "sig")

    assert result["status"] == "handled"
    assert subscription_handler_ran == [], (
        "a minute top-up was processed as a subscription checkout — this blanks "
        "out plan_id and stripe_subscription_id for a paying customer"
    )


@pytest.mark.asyncio
async def test_a_subscription_checkout_still_reaches_its_own_handler(monkeypatch):
    """The other half of the guard: routing must not swallow subscriptions."""
    svc = _svc()
    event = _fake_event("checkout.session.completed", _subscription_session(), "evt_sub")
    _arm_for_real_webhook(svc, monkeypatch, event)

    ran = []

    async def _handler(session):
        ran.append(session)

    monkeypatch.setattr(svc, "_handle_checkout_completed", _handler)

    result = await svc.handle_webhook(b"{}", "sig")

    assert result["status"] == "handled"
    assert len(ran) == 1


# ── delayed payment methods: the pair, not just the failure half ────────────
#
# Stripe's delayed-payment pair is async_payment_succeeded / async_payment_failed.
# Only the FAILURE half was routed, so a top-up paid by Bacs/SEPA/Klarna charged
# the customer and never credited them: the success event fell through to the
# subscription handler table (which has no entry for it), the order sat 'pending'
# forever, and nothing reaps it.


def _async_succeeded_session():
    """The same Checkout Session object, redelivered as the settled event."""
    return _topup_session(id="cs_test_1", payment_status="paid")


@pytest.mark.asyncio
async def test_a_delayed_payment_success_is_claimed_for_the_topup_path():
    assert await _svc()._is_topup_event(
        "checkout.session.async_payment_succeeded", _async_succeeded_session()
    ) is True


@pytest.mark.asyncio
async def test_a_delayed_payment_success_credits_the_minutes(monkeypatch):
    """THE MONEY BUG. Without this the customer is charged and never credited."""
    svc = _svc()
    seen = {}

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            seen.update(kw)
            return True
        async def mark_failed(self, **kw):
            raise AssertionError(
                "a SUCCESSFUL delayed payment was marked failed — the customer "
                "paid and the order was closed as a failure"
            )

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    result = await svc._handle_topup_event(
        "checkout.session.async_payment_succeeded",
        _async_succeeded_session(),
        "evt_async",
    )

    assert result["status"] == "handled"
    assert seen["session_id"] == "cs_test_1"
    assert seen["event_id"] == "evt_async"
    assert seen["payment_id"] == "pi_test_1"


@pytest.mark.asyncio
async def test_completed_then_async_succeeded_credit_the_same_session_once(monkeypatch):
    """Stripe can deliver BOTH for one delayed payment.

    They are different event ids, so the ledger's uniqueness guard does not
    separate them — the order's own paid state does. This asserts the routing
    calls the one credit path for both, which is what makes that guard apply.
    """
    svc = _svc()
    calls = []

    class _Topups:
        def __init__(self, pool): pass
        async def credit_paid_order(self, **kw):
            calls.append(kw)
            # models the real service: the order is 'paid' after the first
            return len(calls) == 1

    monkeypatch.setattr("app.domain.services.topup_service.TopupService", _Topups)

    first = await svc._handle_topup_event(
        "checkout.session.completed", _topup_session(), "evt_1")
    second = await svc._handle_topup_event(
        "checkout.session.async_payment_succeeded",
        _async_succeeded_session(), "evt_2")

    assert first["status"] == "handled"
    assert second["status"] == "duplicate"
    assert [c["session_id"] for c in calls] == ["cs_test_1", "cs_test_1"]


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
