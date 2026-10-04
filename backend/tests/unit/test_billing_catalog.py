from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.services import billing_catalog as catalog


def option(**changes):
    return dict(
        id=str(uuid4()),
        plan_id="synthetic",
        plan_name="Synthetic",
        minutes=100,
        stripe_price_id="price_example",
        stripe_product_id="prod_example",
        kind="stripe",
        interval="month",
        interval_count=1,
        amount_minor=1000,
        currency="usd",
        currency_exponent=2,
        provider_mode="test",
        active=True,
        verified_at=datetime.now(timezone.utc),
        **changes,
    )


def provider_price(terms):
    return dict(
        id=terms["stripe_price_id"],
        product=terms["stripe_product_id"],
        active=True,
        type="recurring",
        billing_scheme="per_unit",
        livemode=False,
        unit_amount=terms["amount_minor"],
        currency=terms["currency"],
        recurring=dict(interval=terms["interval"], interval_count=1, usage_type="licensed"),
    )


@pytest.mark.parametrize("interval", ["month", "year"])
def test_exact_approved_provider_terms_pass(monkeypatch, interval):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    terms = option()
    terms["interval"] = interval
    catalog.validate_option(terms, "test")
    catalog.validate_provider_price(terms, provider_price(terms))


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", False),
        ("verified_at", None),
        ("amount_minor", None),
        ("amount_minor", -1),
        ("amount_minor", True),
        ("minutes", None),
        ("minutes", -1),
        ("currency", "unknown"),
        ("currency_exponent", 0),
        ("provider_mode", "live"),
        ("stripe_price_id", None),
        ("stripe_product_id", None),
        ("interval_count", True),
        ("interval", "week"),
    ],
)
def test_incomplete_or_unapproved_option_fails_closed(monkeypatch, field, value):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    terms = option()
    terms[field] = value
    with pytest.raises(catalog.BillingCatalogUnavailable):
        catalog.validate_option(terms, "test")


@pytest.mark.parametrize("mode", ["mock", "disabled", "unconfigured"])
def test_paid_never_silently_becomes_mock_or_free(monkeypatch, mode):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    with pytest.raises(catalog.BillingCatalogUnavailable):
        catalog.validate_option(option(), mode)


def test_signing_secret_required_for_new_paid_checkout(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    with pytest.raises(catalog.BillingCatalogUnavailable, match="confirmation"):
        catalog.validate_option(option(), "test")


def test_explicit_free_does_not_need_stripe(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    terms = option()
    terms.update(
        kind="free",
        amount_minor=0,
        stripe_price_id=None,
        stripe_product_id=None,
        provider_mode="free",
    )
    catalog.validate_option(terms, "disabled")
    terms["amount_minor"] = None
    with pytest.raises(catalog.BillingCatalogUnavailable):
        catalog.validate_option(terms, "disabled")


@pytest.mark.parametrize(
    "currency,exponent,amount,valid",
    [
        ("jpy", 0, 1000, True),
        ("kwd", 3, 1000, True),
        ("cad", 2, 1000, True),
        ("isk", 2, 1000, True),
        ("isk", 2, 1001, False),
        ("ugx", 2, 101, False),
        ("twd", 0, 1000, False),
    ],
)
def test_currency_conventions(monkeypatch, currency, exponent, amount, valid):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    terms = option()
    terms.update(currency=currency, currency_exponent=exponent, amount_minor=amount)
    if valid:
        catalog.validate_option(terms, "test")
    else:
        with pytest.raises(catalog.BillingCatalogUnavailable):
            catalog.validate_option(terms, "test")


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "price_wrong"),
        ("product", "prod_wrong"),
        ("active", False),
        ("type", "one_time"),
        ("billing_scheme", "tiered"),
        ("livemode", True),
        ("unit_amount", 1001),
        ("unit_amount", None),
        ("currency", "gbp"),
        ("custom_unit_amount", {"enabled": True}),
        ("transform_quantity", {"divide_by": 10}),
        ("recurring", {"interval": "year", "interval_count": 1, "usage_type": "licensed"}),
        ("recurring", {"interval": "month", "interval_count": 1, "usage_type": "metered"}),
    ],
)
def test_provider_mismatch_prevents_purchase(field, value):
    terms = option()
    price = provider_price(terms)
    price[field] = value
    with pytest.raises(catalog.BillingCatalogUnavailable):
        catalog.validate_provider_price(terms, price)


def test_retired_price_can_support_existing_receipt_but_not_new_checkout():
    terms = option()
    price = provider_price(terms)
    price["active"] = False
    catalog.validate_provider_price(terms, price, require_active=False)
    with pytest.raises(catalog.BillingCatalogUnavailable):
        catalog.validate_provider_price(terms, price)
    price["unit_amount"] += 1
    with pytest.raises(catalog.BillingCatalogUnavailable):
        catalog.validate_provider_price(terms, price, require_active=False)


@pytest.mark.asyncio
async def test_catalog_hides_provider_fields_and_preserves_annual_amount(monkeypatch):
    terms = option()
    terms.update(interval="year", amount_minor=11000)
    plan = dict(
        id="synthetic",
        name="Synthetic",
        price=10,
        description="",
        minutes=100,
        agents=1,
        concurrent_calls=1,
        features=[],
        not_included=[],
        popular=False,
    )
    connection = SimpleNamespace(fetch=AsyncMock(side_effect=[[plan], [terms]]))

    @asynccontextmanager
    async def acquire(pool, tenant):
        assert tenant is None
        yield connection

    monkeypatch.setattr(catalog, "acquire_with_tenant", acquire)
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    import stripe

    retrieve = AsyncMock(return_value=provider_price(terms))
    monkeypatch.setattr(
        stripe,
        "StripeClient",
        lambda *a, **kw: SimpleNamespace(
            v1=SimpleNamespace(prices=SimpleNamespace(retrieve_async=retrieve))
        ),
    )
    result = await catalog.list_plan_catalog(object())
    selected = result[0]["price_options"][0]
    assert selected["amount_minor"] == 11000 and selected["checkout_available"] is True
    assert selected["interval"] == "year" and result[0]["minutes"] == 100
    assert "stripe_price_id" not in selected and "stripe_product_id" not in selected
    assert "verified_at" not in selected
    assert retrieve.await_count == 1
