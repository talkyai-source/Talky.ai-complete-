"""The team-only $1 payment check (owner decision 2026-10-27).

A real $1 charge proves the live money flow before customers pay. Nobody
outside the team may see or buy it: to them it would be 2 minutes for $1.
Provider and database calls are fakes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import billing as billing_endpoint
from app.domain.services import billing_catalog as catalog
from app.domain.services.billing_mode import TEAM_ONLY_PLAN_IDS, is_billing_team

OPTION = "00000000-0000-0000-0000-000000000009"
STRANGER = SimpleNamespace(role="tenant_admin", email="someone@else.com")
OWNER = SimpleNamespace(role="tenant_admin", email="owner+test2@example.com")
PLATFORM_ADMIN = SimpleNamespace(role="platform_admin", email="anyone@else.com")


@pytest.fixture(autouse=True)
def team(monkeypatch):
    monkeypatch.setenv("STRIPE_TEST_CHECKOUT_EMAILS", "Owner@Example.com")


def _fake_db(monkeypatch, *, row=None, plans=(), options=()):
    class Conn:
        async def fetchrow(self, sql, *args):
            return row

        async def fetch(self, sql, *args):
            return list(plans) if "plan_price_options" not in sql else list(options)

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(catalog, "acquire_with_tenant", lambda pool, tenant: Acquire())


# ── who the team is ──────────────────────────────────────────────────────────

def test_the_team_is_platform_admins_and_the_listed_addresses():
    assert is_billing_team(is_platform_admin=True, email="anyone@else.com") is True
    assert is_billing_team(is_platform_admin=False, email="owner@example.com") is True
    assert is_billing_team(is_platform_admin=False, email="owner+test2@example.com") is True
    assert is_billing_team(is_platform_admin=False, email="someone@else.com") is False
    assert is_billing_team(is_platform_admin=False, email="owner@example.com.evil") is False
    assert is_billing_team(is_platform_admin=False, email=None) is False


# ── checkout refuses the $1 offer to everyone else ──────────────────────────

def _option_row(plan_id, amount, minutes):
    return {"id": OPTION, "plan_id": plan_id, "plan_name": plan_id, "kind": "stripe", "interval": "month",
            "interval_count": 1, "amount_minor": amount, "minutes": minutes, "currency": "usd",
            "currency_exponent": 2, "provider_mode": "live", "active": True, "verified_at": "2026-10-27",
            "stripe_price_id": f"price_{plan_id}", "stripe_product_id": f"prod_{plan_id}"}


@pytest.fixture
def live_mode(monkeypatch):
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "live")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")


async def test_a_customer_cannot_buy_the_one_dollar_plan(monkeypatch, live_mode):
    _fake_db(monkeypatch, row=_option_row("payment_test", 100, 2))
    with pytest.raises(catalog.BillingCatalogUnavailable, match="Unknown price option"):
        await catalog.resolve_checkout_option(None, OPTION)


async def test_the_team_can_buy_the_one_dollar_plan(monkeypatch, live_mode):
    _fake_db(monkeypatch, row=_option_row("payment_test", 100, 2))
    option = await catalog.resolve_checkout_option(None, OPTION, allow_team_only=True)
    assert (option["plan_id"], option["amount_minor"], option["minutes"]) == ("payment_test", 100, 2)


async def test_a_customer_can_still_buy_the_public_plans(monkeypatch, live_mode):
    _fake_db(monkeypatch, row=_option_row("basic", 9900, 300))
    option = await catalog.resolve_checkout_option(None, OPTION)
    assert option["plan_id"] == "basic"


async def test_the_refusal_is_a_pre_insert_rejection(monkeypatch):
    """Nothing is saved, so the customer may choose another offer at once."""
    from app.domain.services.billing_checkout import CheckoutAttempts, CheckoutError

    seen = {}

    async def resolve(pool, option_id, *, allow_team_only=False):
        seen["allow_team_only"] = allow_team_only
        raise catalog.BillingCatalogUnavailable("Unknown price option.")

    async def no_row(*_a):
        return None

    async def reject(tenant_id, request_id, message):
        raise CheckoutError("billing_offer_unavailable", message, 400, request_not_started=True)

    monkeypatch.setattr(catalog, "resolve_checkout_option", resolve)
    attempts = CheckoutAttempts(None, SimpleNamespace(billing_mode="live"))
    attempts._get = no_row
    attempts._existing_or_reject_offer = reject
    with pytest.raises(CheckoutError) as refused:
        await attempts.create(tenant_id="11111111-1111-1111-1111-111111111111",
                              request_id="22222222-2222-2222-2222-222222222222",
                              price_option_id=OPTION, email="someone@else.com")
    assert refused.value.code == "billing_offer_unavailable" and refused.value.request_not_started
    assert seen == {"allow_team_only": False}


@pytest.mark.parametrize("user, expected", [(STRANGER, False), (OWNER, True), (PLATFORM_ADMIN, True)])
async def test_the_checkout_endpoint_says_who_is_buying(monkeypatch, user, expected):
    monkeypatch.setattr(billing_endpoint, "_require_checkout_permitted_in_test_mode", lambda current: None)
    calls = []

    async def create_checkout_session(**kwargs):
        calls.append(kwargs)
        return {"request_id": "22222222-2222-2222-2222-222222222222", "state": "open", "price_option": {}}

    async def log(**kwargs):
        pass

    body = billing_endpoint.CreateCheckoutRequest(request_id="22222222-2222-2222-2222-222222222222",
                                                  price_option_id=OPTION)
    current = SimpleNamespace(**vars(user), tenant_id="t", id="u", business_name=None)
    await billing_endpoint.create_checkout_session(
        body, SimpleNamespace(client=None, headers={}), current,
        SimpleNamespace(create_checkout_session=create_checkout_session), SimpleNamespace(log=log))
    assert calls[0]["allow_team_only"] is expected


# ── only the team sees it in the catalog ─────────────────────────────────────

def _catalog_rows():
    plans = [
        {"id": "payment_test", "name": "Payment test", "price": 1, "description": None, "minutes": 2,
         "agents": 1, "concurrent_calls": 1, "features": [], "not_included": [], "popular": False},
        {"id": "basic", "name": "Basic", "price": 99, "description": None, "minutes": 300, "agents": 2,
         "concurrent_calls": 3, "features": [], "not_included": [], "popular": False},
    ]
    options = [
        {"id": f"00000000-0000-0000-0000-00000000000{n}", "plan_id": plan_id, "plan_name": plan_id,
         "kind": "stripe", "interval": "month", "interval_count": 1, "amount_minor": amount, "minutes": minutes,
         "currency": "usd", "currency_exponent": 2, "provider_mode": "live", "active": True,
         "verified_at": "2026-10-27", "stripe_price_id": f"price_{plan_id}", "stripe_product_id": f"prod_{plan_id}"}
        for n, (plan_id, amount, minutes) in enumerate([("payment_test", 100, 2), ("basic", 9900, 300)], 1)
    ]
    return plans, options


async def _listed(monkeypatch, **kwargs):
    import stripe

    plans, options = _catalog_rows()
    _fake_db(monkeypatch, plans=plans, options=options)
    catalog._price_cache.clear()
    prices = {o["stripe_price_id"]: {
        "id": o["stripe_price_id"], "active": True, "type": "recurring", "billing_scheme": "per_unit",
        "livemode": True, "unit_amount": o["amount_minor"], "currency": "usd", "product": o["stripe_product_id"],
        "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}} for o in options}

    class Http:
        def __init__(self, **kw):
            pass

        async def close_async(self):
            pass

        def close(self):
            pass

    async def retrieve(price_id):
        return prices[price_id]

    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "live")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_" + "live_" + "synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    monkeypatch.setattr(stripe, "HTTPXClient", Http)
    monkeypatch.setattr(stripe, "StripeClient", lambda *a, **k: SimpleNamespace(
        v1=SimpleNamespace(prices=SimpleNamespace(retrieve_async=retrieve))))
    try:
        return await catalog.list_plan_catalog(None, **kwargs)
    finally:
        catalog._price_cache.clear()


async def test_the_public_catalog_never_lists_the_one_dollar_plan(monkeypatch):
    listed = await _listed(monkeypatch)
    assert [plan["id"] for plan in listed] == ["basic"]


async def test_the_team_catalog_lists_it_as_buyable(monkeypatch):
    listed = await _listed(monkeypatch, include_team_only=True)
    assert [plan["id"] for plan in listed] == ["payment_test", "basic"]
    assert listed[0]["price_options"][0]["amount_minor"] == 100
    assert listed[0]["price_options"][0]["checkout_available"] is True


@pytest.mark.parametrize("user, expected", [(None, False), (STRANGER, False), (OWNER, True), (PLATFORM_ADMIN, True)])
async def test_billing_plans_shows_team_only_plans_to_the_team_alone(monkeypatch, user, expected):
    seen = []

    async def list_plan_catalog(pool, *, include_team_only=False):
        seen.append(include_team_only)
        return []

    monkeypatch.setattr(catalog, "list_plan_catalog", list_plan_catalog)
    await billing_endpoint.list_billing_plans(SimpleNamespace(pool=None), user)
    assert seen == [expected]


# ── the plan itself ──────────────────────────────────────────────────────────

def test_the_migration_adds_a_one_dollar_plan_with_real_minutes():
    path = Path(__file__).resolve().parents[2] / "Alembic" / "versions" / "0073_payment_test_plan.py"
    spec = importlib.util.spec_from_file_location("m0073", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0072_allowance_anchor"
    insert = module.STATEMENTS[1]
    assert "'payment_test', 'Payment test', 1," in insert
    # zero minutes would read as an unlimited plan to the meter and the UI
    assert "\n                2, 1, 1," in insert
    assert TEAM_ONLY_PLAN_IDS == {"payment_test"}
