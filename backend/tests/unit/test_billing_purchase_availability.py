"""G7: whether a purchase will be refused is known before the click.

Before: with BILLING_TOPUPS_LIVE_ENABLED off the packages list still offered
bundles and checkout answered 503; in production Stripe test mode a non-team
user saw checkout_available: true and got 403 test_billing_team_only only
after clicking. /billing/config exposed only the mode.
"""
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import billing as billing_ep
from app.api.v1.endpoints import billing_topups as topups_ep
from app.domain.services import billing_mode

TEAM = SimpleNamespace(id="u-team", tenant_id="t-1", role="tenant_admin", email="team@talkleeai.com")
CUSTOMER = SimpleNamespace(id="u-cust", tenant_id="t-2", role="tenant_admin", email="customer@example.com")
PACKAGE = {"code": "payg_125", "name": "125 minutes", "minutes": 125, "price_cents": 5000,
           "currency": "USD", "expires_days": None, "audience": "payg"}


@pytest.fixture
def production_test_mode(monkeypatch):
    monkeypatch.setattr(billing_mode, "get_billing_mode", lambda **_kwargs: "test")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STRIPE_TEST_CHECKOUT_EMAILS", "team@talkleeai.com")
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "true")


@pytest.fixture
def packages(monkeypatch):
    calls = []

    async def audience(_tenant_id):
        return "payg"

    class Service:
        async def packages(self, aud):
            calls.append(aud)
            return [dict(PACKAGE)]

    monkeypatch.setattr(topups_ep, "_purchase_audience", audience)
    monkeypatch.setattr(topups_ep, "_service", lambda: Service())
    return calls


async def test_with_topups_switched_off_no_bundle_is_offered(production_test_mode, packages, monkeypatch):
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "false")
    assert await topups_ep.list_packages(current_user=TEAM) == []
    assert packages == [], "nothing is read when nothing can be bought"


async def test_a_non_team_user_in_production_test_mode_is_offered_no_bundle(production_test_mode, packages):
    assert await topups_ep.list_packages(current_user=CUSTOMER) == []
    listed = await topups_ep.list_packages(current_user=TEAM)
    assert [p.code for p in listed] == ["payg_125"]


async def test_config_says_before_the_click_whether_checkout_and_topups_are_open(production_test_mode, monkeypatch):
    customer = await billing_ep.get_billing_config(current_user=CUSTOMER)
    team = await billing_ep.get_billing_config(current_user=TEAM)
    assert customer["checkout_open"] is False and "test mode" in customer["checkout_unavailable_reason"]
    assert customer["topups_open"] is False and customer["topups_unavailable_reason"]
    assert team["checkout_open"] is True and team["checkout_unavailable_reason"] is None
    assert team["topups_open"] is True and team["topups_unavailable_reason"] is None
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "false")
    off = await billing_ep.get_billing_config(current_user=TEAM)
    assert off["topups_open"] is False and off["topups_unavailable_reason"] == "Minute top-ups are not available yet."
    assert off["checkout_open"] is True
    assert set(customer) >= {"stripe_configured", "mock_mode", "billing_mode", "publishable_key"}


async def test_an_unusable_billing_mode_closes_checkout_with_a_reason(monkeypatch):
    monkeypatch.setattr(billing_mode, "get_billing_mode", lambda **_kwargs: "unconfigured")
    config = await billing_ep.get_billing_config(current_user=TEAM)
    assert config["checkout_open"] is False and config["checkout_unavailable_reason"]


async def test_plans_mark_checkout_unavailable_for_a_user_who_would_be_refused(production_test_mode, monkeypatch):
    async def catalog(_pool, include_team_only):
        return [{"id": "starter", "price_options": [
            {"plan_id": "starter", "checkout_available": True, "unavailable_reason": None},
            {"plan_id": "starter", "checkout_available": False, "unavailable_reason": "provider says no"}]}]

    monkeypatch.setattr("app.domain.services.billing_catalog.list_plan_catalog", catalog)
    db = SimpleNamespace(pool=object())
    customer = await billing_ep.list_billing_plans(db_client=db, current_user=CUSTOMER)
    team = await billing_ep.list_billing_plans(db_client=db, current_user=TEAM)
    first, second = customer[0]["price_options"]
    assert first["checkout_available"] is False and "test mode" in first["unavailable_reason"]
    assert second["unavailable_reason"] == "provider says no", "a provider reason is kept"
    assert team[0]["price_options"][0]["checkout_available"] is True
