"""Paid-only billing (owner decision 2026-10-27).

No free minutes for anyone from now on, without taking away what existing
accounts already have; Basic and Business monthly or yearly at 17% off;
production may run Stripe test mode only when the operator says so.
"""
from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import billing_topups as bt
from app.core import prod_gate
from app.domain.services import billing_catalog as catalog
from app.domain.services.billing_checkout import CheckoutAttempts, CheckoutError
from app.domain.services.minutes_quota import compute_minutes_status

TENANT = "11111111-1111-1111-1111-111111111111"


# ── production boot ─────────────────────────────────────────────────────────

@pytest.fixture
def stripe_env(monkeypatch):
    for name in ("STRIPE_SECRET_KEY", "STRIPE_BILLING_DISABLED", "STRIPE_MOCK_MODE",
                 "STRIPE_TEST_MODE_IN_PRODUCTION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fixture")
    monkeypatch.setenv("JWT_SECRET", "a-sufficiently-random-secret-value")
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "a-sufficiently-random-token")
    if importlib.util.find_spec("stripe") is None:
        pytest.skip("stripe SDK not installed in this environment")
    return monkeypatch


def _stripe_rules(violations):
    return {v.rule for v in violations if "stripe" in (v.rule + v.detail).lower()}


def test_a_test_key_still_refuses_production_without_the_opt_in(stripe_env):
    stripe_env.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    assert "STRIPE_LIVE_KEY" in _stripe_rules(prod_gate._check_required_secrets())


@pytest.mark.parametrize("flag", ["true", "1", "on"])
def test_an_explicit_opt_in_lets_production_run_stripe_test_mode(stripe_env, flag):
    stripe_env.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    stripe_env.setenv("STRIPE_TEST_MODE_IN_PRODUCTION", flag)
    assert _stripe_rules(prod_gate._check_required_secrets()) == set()


@pytest.mark.parametrize("key", ["sk_test_", "rk_test_abc123", "garbage"])
def test_the_opt_in_never_accepts_something_that_is_not_a_test_key(stripe_env, key):
    stripe_env.setenv("STRIPE_SECRET_KEY", key)
    stripe_env.setenv("STRIPE_TEST_MODE_IN_PRODUCTION", "true")
    assert "STRIPE_LIVE_KEY" in _stripe_rules(prod_gate._check_required_secrets())


def test_the_opt_in_does_not_excuse_a_missing_signing_secret(stripe_env):
    stripe_env.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    stripe_env.setenv("STRIPE_TEST_MODE_IN_PRODUCTION", "true")
    stripe_env.delenv("STRIPE_WEBHOOK_SECRET")
    assert "STRIPE_WEBHOOK_SECRET" in _stripe_rules(prod_gate._check_required_secrets())


# ── no free plan ────────────────────────────────────────────────────────────

def _free_option():
    return {
        "id": "00000000-0000-0000-0000-000000000001", "plan_id": "free", "kind": "free",
        "interval": "month", "interval_count": 1, "amount_minor": 0, "minutes": 30,
        "currency": "usd", "currency_exponent": 2, "provider_mode": "free",
        "stripe_price_id": None, "stripe_product_id": None,
        "active": True, "verified_at": "2026-10-01T00:00:00Z",
    }


@pytest.mark.parametrize("mode", ["test", "live", "disabled"])
def test_an_active_free_option_still_left_in_the_database_is_refused(mode):
    with pytest.raises(catalog.BillingCatalogUnavailable, match="no longer offered"):
        catalog.validate_option(_free_option(), mode)


async def test_a_free_request_saved_before_the_change_never_grants_minutes():
    class Saved(CheckoutAttempts):
        async def _get(self, tenant_id, request_id, *, conn=None):
            return {"id": request_id, "tenant_id": tenant_id, "status": "creating",
                    "price_option_id": _free_option()["id"], "stripe_session_id": None,
                    "snapshot": {"option": _free_option()}}

    with pytest.raises(CheckoutError) as raised:
        await Saved(pool=None, billing=SimpleNamespace(billing_mode="test")).create(
            tenant_id=TENANT, request_id="22222222-2222-2222-2222-222222222222",
            price_option_id=_free_option()["id"], email="owner@example.com",
        )
    assert raised.value.code == "free_plan_retired"


async def test_the_catalog_lists_only_plans_with_a_paid_price_in_this_mode(monkeypatch):
    plans = [
        {"id": "free", "name": "Free Trial", "price": 0, "description": None, "minutes": 30, "agents": 1,
         "concurrent_calls": 1, "features": [], "not_included": [], "popular": False},
        {"id": "basic", "name": "Basic", "price": 99, "description": None, "minutes": 300, "agents": 2,
         "concurrent_calls": 3, "features": [], "not_included": [], "popular": False},
        {"id": "professional", "name": "Professional", "price": 79, "description": None, "minutes": 1500,
         "agents": 3, "concurrent_calls": 3, "features": [], "not_included": [], "popular": True},
    ]
    options = [
        dict(_free_option(), plan_name="Free Trial"),
        {"id": "00000000-0000-0000-0000-000000000002", "plan_id": "basic", "plan_name": "Basic",
         "kind": "stripe", "interval": "year", "interval_count": 1, "amount_minor": 98600, "minutes": 300,
         "currency": "usd", "currency_exponent": 2, "provider_mode": "test", "active": True,
         "verified_at": "2026-10-27T00:00:00Z", "stripe_price_id": "price_basicyear",
         "stripe_product_id": "prod_basic"},
    ]

    class Conn:
        async def fetch(self, sql, *args):
            return plans if "FROM plans" in sql and "plan_price_options" not in sql else options

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(catalog, "acquire_with_tenant", lambda pool, tenant: Acquire())
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "disabled")
    listed = await catalog.list_plan_catalog(pool=None)
    assert listed == [], "with billing off nothing is purchasable, so no plan is listed"

    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "test")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    listed = await catalog.list_plan_catalog(pool=None)
    assert [plan["id"] for plan in listed] == ["basic"]
    assert [option["kind"] for option in listed[0]["price_options"]] == ["stripe"]


# ── no free minutes for a new account ───────────────────────────────────────

class _Meter:
    def __init__(self, **row):
        self.row = row

    async def fetchrow(self, sql, *args):
        return self.row

    async def fetchval(self, sql, *args):
        return 0


async def test_a_new_account_with_no_plan_has_zero_known_minutes():
    status = await compute_minutes_status(_Meter(minutes_allocated=0, plan_minutes=None, no_plan=True), TENANT)
    assert (status.state, status.allocated, status.remaining_minutes) == ("known", 0, 0)
    assert status.exhausted is True and status.unlimited is False


async def test_a_bound_plan_that_cannot_be_read_is_still_unknown():
    status = await compute_minutes_status(_Meter(minutes_allocated=0, plan_minutes=None, no_plan=False), TENANT)
    assert status.state == "unavailable"


async def test_existing_accounts_keep_their_minutes():
    status = await compute_minutes_status(_Meter(minutes_allocated=30, plan_minutes=30, no_plan=False), TENANT)
    assert (status.state, status.remaining_minutes) == ("known", 30)


# ── top-ups add to a paid plan ──────────────────────────────────────────────

class _Orders:
    def __init__(self):
        self.created = 0

    async def create_order(self, **_kw):
        self.created += 1
        raise AssertionError("an order was created for an account without a paid plan")


@pytest.mark.parametrize("row", [
    None,
    {"stripe_subscription_id": None, "subscription_status": "active"},
    {"stripe_subscription_id": "sub_1", "subscription_status": "past_due"},
    {"stripe_subscription_id": "sub_1", "subscription_status": "canceled"},
])
async def test_top_ups_need_an_active_paid_plan_before_any_order(monkeypatch, row):
    orders = _Orders()

    class Conn:
        async def fetchrow(self, sql, *args):
            return row

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "true")
    monkeypatch.setattr(bt, "_service", lambda: orders)
    monkeypatch.setattr(bt, "acquire_with_tenant", lambda pool, tenant: Acquire())
    monkeypatch.setattr(bt, "get_container", lambda: SimpleNamespace(db_pool=None, is_initialized=True))
    monkeypatch.setattr(bt, "BillingService", lambda *a, **k: SimpleNamespace(mock_mode=False, billing_mode="test"))

    with pytest.raises(HTTPException) as raised:
        await bt.create_topup_checkout(
            body=bt.CheckoutIn(package_code="usd_125"),
            request=SimpleNamespace(client=None, headers={}),
            current_user=CurrentUser(id="u1", email="owner@example.com", tenant_id=TENANT, role="tenant_admin"),
            db_client=SimpleNamespace(), audit_logger=SimpleNamespace(),
        )
    assert raised.value.status_code == 409
    assert "plan" in str(raised.value.detail).lower()
    assert orders.created == 0


async def test_an_active_subscription_is_a_paid_plan(monkeypatch):
    class Conn:
        async def fetchrow(self, sql, *args):
            return {"stripe_subscription_id": "sub_1", "subscription_status": "active"}

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(bt, "acquire_with_tenant", lambda pool, tenant: Acquire())
    monkeypatch.setattr(bt, "get_container", lambda: SimpleNamespace(db_pool=None))
    assert await bt._has_paid_plan(TENANT) is True


# ── annual prices ───────────────────────────────────────────────────────────

def test_yearly_prices_are_seventeen_percent_off_twelve_months():
    from scripts.stripe_catalog_setup import CATALOG

    assert set(CATALOG) == {"basic", "business"}
    for _name, prices in CATALOG.values():
        discount = 1 - prices["year"] / (12 * prices["month"])
        assert round(discount * 100, 1) == 17.0
    assert CATALOG["basic"][1] == {"month": 9900, "year": 98600}
    assert CATALOG["business"][1] == {"month": 29900, "year": 297800}
