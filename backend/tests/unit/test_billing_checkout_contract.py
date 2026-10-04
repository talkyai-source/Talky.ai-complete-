"""Purchase identity and provider receipts, without external billing calls."""
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.billing import CreateCheckoutRequest
from app.domain.services import billing_checkout as checkout
from app.domain.services.billing_service import BillingService


def test_checkout_accepts_only_a_server_option_and_stable_request_identity():
    request_id, option_id = uuid4(), uuid4()
    parsed = CreateCheckoutRequest(request_id=request_id, price_option_id=option_id)
    assert parsed.request_id == request_id
    assert parsed.price_option_id == option_id
    for extra in ({"plan_id": "basic"}, {"amount_minor": 1}, {"success_url": "https://untrusted.example"}):
        with pytest.raises(ValidationError):
            CreateCheckoutRequest(request_id=request_id, price_option_id=option_id, **extra)


def test_legacy_plan_id_alone_cannot_create_a_new_purchase():
    with pytest.raises(ValidationError):
        CreateCheckoutRequest(plan_id="basic")


def option():
    return {"id": str(uuid4()), "plan_id": "starter", "plan_name": "Starter", "kind": "stripe",
            "amount_minor": 2900, "currency": "usd", "currency_exponent": 2,
            "interval": "month", "interval_count": 1, "minutes": 300,
            "provider_mode": "test", "stripe_price_id": "price_synthetic", "stripe_product_id": "prod_synthetic"}


def attempt():
    offer = option()
    return {"id": uuid4(), "tenant_id": uuid4(), "price_option_id": offer["id"], "status": "creating",
            "created_at": datetime.now(UTC), "stripe_session_id": None, "stripe_customer_id": "cus_synthetic",
            "snapshot": {"option": offer, "email": "synthetic@example.com", "business_name": "Synthetic",
                         "success_url": "https://app.example/billing?checkout=success&session_id={CHECKOUT_SESSION_ID}",
                         "cancel_url": "https://app.example/billing?checkout=cancelled", "expires_at": 1999999999}}


def session(row):
    return {"id": "cs_synthetic", "mode": "subscription", "status": "open", "customer": row["stripe_customer_id"],
            "livemode": False, "currency": "usd", "amount_subtotal": 2900,
            "client_reference_id": str(row["id"]), "expires_at": 1999999999,
            "url": "https://checkout.stripe.com/synthetic",
            "metadata": {"tenant_id": str(row["tenant_id"]), "request_id": str(row["id"]),
                         "price_option_id": str(row["price_option_id"])}}


def provider_price():
    return {"id": "price_synthetic", "active": True, "type": "recurring", "billing_scheme": "per_unit",
            "livemode": False, "unit_amount": 2900, "currency": "usd", "product": "prod_synthetic",
            "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}


@pytest.mark.asyncio
async def test_lost_provider_response_reuses_exact_session_parameters_and_identity(monkeypatch):
    row = attempt()
    calls = []

    async def sdk(resource, method, *args, **kwargs):
        if resource == "Price":
            return provider_price()
        if resource == "Customer":
            return {"id": "cus_synthetic", "livemode": False, "metadata": {"tenant_id": str(row["tenant_id"])}}
        calls.append((resource, method, kwargs))
        raise TimeoutError("response lost after possible acceptance")

    svc = checkout.CheckoutAttempts(None, SimpleNamespace(billing_mode="test", _stripe_call=sdk))
    monkeypatch.setattr(svc, "_get", AsyncMock(return_value=row))
    monkeypatch.setattr(svc, "_save_customer", AsyncMock())
    states = AsyncMock()
    monkeypatch.setattr(svc, "_state", states)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await svc.create(tenant_id=row["tenant_id"], request_id=row["id"], price_option_id=row["price_option_id"], email="changed@example.com")
    assert len(calls) == 2 and calls[0] == calls[1]
    params = calls[0][2]
    assert params["expires_at"] == 1999999999
    assert params["adaptive_pricing"] == {"enabled": False}
    assert params["line_items"] == [{"price": "price_synthetic", "quantity": 1}]
    assert params["payment_method_types"] == ["card"]
    assert params["idempotency_key"] == f"subscription-checkout:{row['id']}"
    assert all(call.args[2] == "unknown" for call in states.await_args_list)


@pytest.mark.asyncio
async def test_old_unknown_attempt_never_recreates_a_provider_session(monkeypatch):
    row = attempt()
    row.update(status="unknown", created_at=datetime.now(UTC) - timedelta(hours=24))
    sdk = AsyncMock()
    svc = checkout.CheckoutAttempts(None, SimpleNamespace(billing_mode="test", _stripe_call=sdk))
    monkeypatch.setattr(svc, "_get", AsyncMock(return_value=row))
    monkeypatch.setattr(svc, "_state", AsyncMock())
    result = await svc.create(tenant_id=row["tenant_id"], request_id=row["id"], price_option_id=row["price_option_id"], email="synthetic@example.com")
    assert result["state"] == "pending" and result["checkout_url"] is None
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_selection_under_existing_identity_is_rejected_before_provider(monkeypatch):
    row = attempt()
    sdk = AsyncMock()
    svc = checkout.CheckoutAttempts(None, SimpleNamespace(billing_mode="test", _stripe_call=sdk))
    monkeypatch.setattr(svc, "_get", AsyncMock(return_value=row))
    with pytest.raises(checkout.CheckoutError, match="different offer"):
        await svc.create(tenant_id=row["tenant_id"], request_id=row["id"], price_option_id=uuid4(), email="synthetic@example.com")
    sdk.assert_not_awaited()


@pytest.mark.parametrize("change", [{"customer": "cus_other"}, {"mode": "payment"}, {"livemode": True},
                                  {"currency": "gbp"}, {"amount_subtotal": 1}, {"client_reference_id": "other"},
                                  {"metadata": {}}])
def test_session_receipt_requires_matching_customer_mode_selection_and_price(change):
    row = attempt()
    with pytest.raises(checkout.CheckoutError):
        checkout.CheckoutAttempts.check_session({**session(row), **change}, row)


def test_subscription_period_accepts_actual_item_shape_and_legacy_shape():
    expected = (datetime.fromtimestamp(1700000000, UTC), datetime.fromtimestamp(1700086400, UTC))
    period = {"current_period_start": 1700000000, "current_period_end": 1700086400}
    assert checkout.subscription_period(period) == expected
    assert checkout.subscription_period({"items": {"data": [period]}}) == expected
    with pytest.raises(checkout.CheckoutError):
        checkout.subscription_period({"items": {"data": [{}, {}]}})


@pytest.mark.asyncio
async def test_unpaid_checkout_event_reads_current_state_without_activating():
    from app.domain.services.billing_state_events import apply_billing_event

    row = attempt()
    current_session = {**session(row), "status": "complete", "payment_status": "unpaid", "subscription": "sub_synthetic"}
    current_sub = {"id": "sub_synthetic", "livemode": False, "customer": "cus_synthetic", "status": "incomplete", "metadata": current_session["metadata"]}
    conn = SimpleNamespace(fetchval=AsyncMock(return_value=True), fetchrow=AsyncMock(side_effect=[
        row, None, row, {"id": row["tenant_id"], "stripe_customer_id": "cus_synthetic", "stripe_subscription_id": None},
    ]), execute=AsyncMock())
    billing = SimpleNamespace(billing_mode="test", _stripe_call=AsyncMock(side_effect=[current_session, current_sub]))
    result = await apply_billing_event(conn, billing, "checkout.session.completed", current_session)
    assert result["status"] == "deferred" and result["reason"] == "payment_unconfirmed"
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize("origin", ["https://bad.example/path", "https://user:pass@app.example", "https://app.example?redirect=bad", "javascript:alert(1)"])
def test_server_return_origin_must_be_an_allowed_origin(monkeypatch, origin):
    monkeypatch.setattr(checkout, "get_settings", lambda: SimpleNamespace(frontend_url=origin, allowed_origins=["https://app.example"]))
    with pytest.raises(checkout.CheckoutError):
        checkout.billing_return_urls(uuid4())


def test_server_return_query_keeps_request_and_provider_session_placeholders(monkeypatch):
    monkeypatch.setattr(checkout, "get_settings", lambda: SimpleNamespace(frontend_url="https://app.example", allowed_origins=["https://app.example"]))
    identity = uuid4()
    success, cancel = checkout.billing_return_urls(identity)
    assert success == f"https://app.example/billing?checkout=success&request_id={identity}&session_id={{CHECKOUT_SESSION_ID}}"
    assert cancel == f"https://app.example/billing?checkout=cancelled&request_id={identity}"


def test_a_second_session_with_copied_metadata_cannot_replace_the_saved_session():
    row = attempt()
    row["stripe_session_id"] = "cs_original"
    with pytest.raises(checkout.CheckoutError):
        checkout.CheckoutAttempts.check_session(session(row), row)


@pytest.mark.asyncio
async def test_subscription_minutes_projection_includes_purchased_allocation(monkeypatch):
    from contextlib import asynccontextmanager

    from app.api.v1.endpoints.billing import get_subscription
    from app.core import db_utils
    from app.domain.services import minutes_quota

    @asynccontextmanager
    async def acquire(*args, **kwargs):
        yield object()

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(minutes_quota, "compute_minutes_status", AsyncMock(return_value=minutes_quota.MinutesStatus(
        allocated=800, used_minutes=50, remaining_minutes=750, unlimited=False, exhausted=False,
    )))
    billing = SimpleNamespace(get_subscription=AsyncMock(return_value={
        "status": "active", "plan_id": "starter", "plans": {"name": "Starter", "minutes": 300},
        "purchased_price_option": None, "billing_portal_available": False,
    }))
    result = await get_subscription(current_user=SimpleNamespace(tenant_id=str(uuid4())), billing=billing, db_pool=object())
    assert result.minutes_allocated == 800 and result.minutes_used == 50 and result.minutes_remaining == 750


@pytest.mark.asyncio
async def test_installed_stripe_sdk_sends_exact_checkout_form_and_idempotency_header(monkeypatch):
    """Exercise the installed SDK and its encoder, replacing only HTTP transport."""
    import stripe
    seen, closed = [], []

    class Transport(stripe.HTTPClient):
        name = "synthetic-offline"

        def __init__(self, timeout):
            super().__init__()
            assert timeout == 5

        def request(self, method, url, headers, post_data=None, **kwargs):
            seen.append((method, url, headers, post_data))
            result = ({"object": "price", **provider_price()} if method.lower() == "get"
                      else {"id": "cs_synthetic", "object": "checkout.session", "url": "https://checkout.stripe.com/synthetic"})
            return json.dumps(result), 200, {"request-id": "req_synthetic"}

        def close(self):
            closed.append(True)

    monkeypatch.setattr(stripe, "RequestsClient", Transport)
    svc = BillingService.__new__(BillingService)
    svc.billing_mode = "test"
    svc._stripe_api_key = "sk_test_" + "synthetic"
    receipt = await svc._stripe_call("checkout.Session", "create", customer="cus_synthetic", mode="subscription",
                                     payment_method_types=["card"],
                                     line_items=[{"price": "price_synthetic", "quantity": 1}],
                                     adaptive_pricing={"enabled": False}, expires_at=1999999999,
                                     success_url="https://app.example/billing?checkout=success&session_id={CHECKOUT_SESSION_ID}",
                                     cancel_url="https://app.example/billing?checkout=cancelled",
                                     idempotency_key="subscription-checkout:synthetic")
    assert receipt.id == "cs_synthetic"
    price = await svc._stripe_call("Price", "retrieve", "price_synthetic")
    assert price.id == "price_synthetic"
    method, url, headers, body = seen[0]
    assert method.lower() == "post" and url == "https://api.stripe.com/v1/checkout/sessions"
    assert headers["Idempotency-Key"] == "subscription-checkout:synthetic"
    encoded = parse_qs(body)
    assert encoded["adaptive_pricing[enabled]"] == ["false"]
    assert encoded["line_items[0][price]"] == ["price_synthetic"]
    assert encoded["payment_method_types[0]"] == ["card"]
    assert encoded["expires_at"] == ["1999999999"]
    assert encoded["success_url"] == ["https://app.example/billing?checkout=success&session_id={CHECKOUT_SESSION_ID}"]
    assert seen[1][1] == "https://api.stripe.com/v1/prices/price_synthetic"
    assert closed == [True, True]


@pytest.mark.asyncio
async def test_another_outstanding_request_returns_its_durable_recovery_receipt(monkeypatch):
    row = attempt()
    row["status"] = "unknown"
    row["snapshot"] = json.dumps(row["snapshot"])
    conn = SimpleNamespace(
        fetchrow=AsyncMock(side_effect=[
            {"id": row["tenant_id"], "status": "active", "stripe_customer_id": None, "stripe_subscription_id": None},
            None, row,
        ]),
        fetchval=AsyncMock(return_value=False), execute=AsyncMock(),
    )

    @asynccontextmanager
    async def acquire(*args):
        yield conn

    monkeypatch.setattr(checkout, "acquire_with_tenant", acquire)
    monkeypatch.setattr(checkout, "billing_return_urls", lambda _: ("https://app.example/billing", "https://app.example/billing"))
    svc = checkout.CheckoutAttempts(None, SimpleNamespace())
    with pytest.raises(checkout.CheckoutError) as rejected:
        await svc._claim(row["tenant_id"], uuid4(), option(), "synthetic@example.com", None)
    assert rejected.value.code == "checkout_outstanding"
    assert rejected.value.request_not_started is True
    receipt = rejected.value.existing_attempt
    assert receipt["request_id"] == str(row["id"])
    assert receipt["state"] == "pending" and receipt["checkout_url"] is None
    assert receipt["price_option"]["id"] == str(row["price_option_id"])
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_receipt", [False, True])
async def test_unavailable_offer_cannot_unlock_a_concurrently_saved_request(monkeypatch, concurrent_receipt):
    from app.domain.services import billing_catalog

    row = attempt()
    row.update(status="unknown", created_at=datetime.now(UTC) - timedelta(hours=24))
    conn = SimpleNamespace(fetchrow=AsyncMock(side_effect=[{"id": row["tenant_id"]}, row if concurrent_receipt else None]))

    @asynccontextmanager
    async def acquire(*args):
        yield conn

    monkeypatch.setattr(checkout, "acquire_with_tenant", acquire)
    monkeypatch.setattr(billing_catalog, "resolve_checkout_option", AsyncMock(side_effect=billing_catalog.BillingCatalogUnavailable("Offer unavailable.")))
    sdk = AsyncMock()
    svc = checkout.CheckoutAttempts(None, SimpleNamespace(billing_mode="test", _stripe_call=sdk))
    monkeypatch.setattr(svc, "_get", AsyncMock(side_effect=[None, row]))
    monkeypatch.setattr(svc, "_state", AsyncMock())
    if concurrent_receipt:
        result = await svc.create(tenant_id=row["tenant_id"], request_id=row["id"], price_option_id=row["price_option_id"], email="synthetic@example.com")
        assert result["state"] == "pending"
    else:
        with pytest.raises(checkout.CheckoutError) as rejected:
            await svc.create(tenant_id=row["tenant_id"], request_id=row["id"], price_option_id=row["price_option_id"], email="synthetic@example.com")
        assert rejected.value.code == "billing_offer_unavailable"
        assert rejected.value.request_not_started is True
    sdk.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_preinsert_rejection_provides_metadata_for_canonical_error_handler():
    from fastapi import HTTPException

    from app.api.v1.endpoints.billing import create_checkout_session

    row = attempt()
    receipt = checkout.CheckoutAttempts.response(row)
    failure = checkout.CheckoutError("checkout_outstanding", "Resume the saved request.", request_not_started=True, existing_attempt=receipt)
    billing = SimpleNamespace(create_checkout_session=AsyncMock(side_effect=failure))
    user = SimpleNamespace(tenant_id=str(row["tenant_id"]), email="synthetic@example.com", business_name=None)
    with pytest.raises(HTTPException) as rejected:
        await create_checkout_session(CreateCheckoutRequest(request_id=uuid4(), price_option_id=uuid4()), None, user, billing, None)
    assert rejected.value.status_code == 409
    assert rejected.value.detail["request_not_started"] is True
    assert rejected.value.detail["existing_attempt"] == receipt
    assert "details" not in rejected.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unconfigured", "disabled"])
@pytest.mark.parametrize("operation", ["customer", "topup", "cancel", "webhook"])
async def test_unavailable_mode_never_implicitly_mocks_financial_operations(monkeypatch, mode, operation):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    if mode == "disabled":
        monkeypatch.setenv("STRIPE_BILLING_DISABLED", "true")
        monkeypatch.setenv("STRIPE_MOCK_MODE", "true")
    db = MagicMock()
    svc = BillingService(db)
    assert svc.billing_mode == mode
    assert svc.mock_mode is False
    from app.domain.services.billing_webhooks import BillingWebhookRetryable
    error_type = BillingWebhookRetryable if operation == "webhook" else ValueError
    with pytest.raises(error_type, match="unavailable"):
        if operation == "customer":
            await svc.create_or_get_customer(str(uuid4()), "synthetic@example.com")
        elif operation == "topup":
            await svc.create_topup_checkout_session(tenant_id=str(uuid4()), email="synthetic@example.com", order_id=str(uuid4()),
                                                  minutes=100, price_cents=1000, currency="usd", product_name="Synthetic",
                                                  success_url="https://app.example/billing", cancel_url="https://app.example/billing")
        elif operation == "cancel":
            await svc.cancel_subscription(str(uuid4()))
        else:
            await svc.handle_webhook(b"{}", "")
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_only_explicit_mock_may_ignore_unsigned_webhook(monkeypatch):
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    monkeypatch.setenv("STRIPE_MOCK_MODE", "true")
    svc = BillingService(MagicMock())
    assert svc.billing_mode == "mock" and svc.mock_mode is True
    assert await svc.handle_webhook(b"{}", "") == {"status": "ignored", "reason": "mock_mode"}
