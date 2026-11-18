"""PayPal settings module."""
from __future__ import annotations

import pytest

from app.domain.services import paypal_mode as pm

_ALL = (
    "PAYPAL_ENV", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET",
    "PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET",
    "PAYPAL_WEBHOOK_ID", "PAYPAL_TOPUPS_ENABLED", "ENVIRONMENT",
    "STRIPE_TEST_CHECKOUT_EMAILS",
)
ID = "client-" + "id" * 4
SECRET = "client-" + "sec" * 4
SB_ID = "sandbox-" + "id" * 4
SB_SECRET = "sandbox-" + "sec" * 4


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)


def _live(monkeypatch, webhook=True):
    monkeypatch.setenv("PAYPAL_ENV", "live")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", ID)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", SECRET)
    if webhook:
        monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "wh" + "1" * 6)


def test_unset_everything():
    assert pm.paypal_env() is None
    assert pm.paypal_api_base() is None
    assert pm.paypal_provider_mode() is None
    assert pm.paypal_credentials() is None
    assert pm.paypal_webhook_id() is None
    assert pm.paypal_configured() is False
    assert pm.paypal_topups_switch() == "off"
    assert pm.paypal_topup_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)


@pytest.mark.parametrize(
    "raw,env,base,mode",
    [
        (" LIVE ", "live", pm.PAYPAL_LIVE_API, "live"),
        ("Sandbox", "sandbox", pm.PAYPAL_SANDBOX_API, "test"),
        ("production", None, None, None),
        ("", None, None, None),
    ],
)
def test_env_values(monkeypatch, raw, env, base, mode):
    monkeypatch.setenv("PAYPAL_ENV", raw)
    assert pm.paypal_env() == env
    assert pm.paypal_api_base() == base
    assert pm.paypal_provider_mode() == mode


def test_live_credentials(monkeypatch):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", SB_ID)
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", SB_SECRET)
    assert pm.paypal_credentials() == (ID, SECRET)


def test_live_credentials_need_both(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENV", "live")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", ID)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "  ")
    assert pm.paypal_credentials() is None


def test_sandbox_uses_only_its_own_pair(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "wh")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", ID)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", SECRET)
    assert pm.paypal_credentials() is None  # the live pair is never sent to the sandbox
    assert pm.paypal_configured() is False
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", SB_ID)
    assert pm.paypal_credentials() is None  # half a pair is not used
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", SB_SECRET)
    assert pm.paypal_credentials() == (SB_ID, SB_SECRET)
    assert pm.paypal_configured() is True


def test_webhook_id_trimmed(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "  abc  ")
    assert pm.paypal_webhook_id() == "abc"
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "   ")
    assert pm.paypal_webhook_id() is None


def test_configured_needs_webhook_id(monkeypatch):
    _live(monkeypatch, webhook=False)
    assert pm.paypal_configured() is False
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "wh")
    assert pm.paypal_configured() is True


@pytest.mark.parametrize(
    "raw,expected",
    [("team", "team"), (" TEAM ", "team"), ("true", "on"), ("1", "on"), ("on", "on"),
     ("YES", "on"), ("off", "off"), ("false", "off"), ("maybe", "off"), ("", "off")],
)
def test_switch(monkeypatch, raw, expected):
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", raw)
    assert pm.paypal_topups_switch() == expected


def test_availability_off_and_unconfigured(monkeypatch):
    _live(monkeypatch)
    assert pm.paypal_topup_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "on")
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID")
    assert pm.paypal_topup_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)


def test_availability_on_is_open(monkeypatch):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "on")
    assert pm.paypal_topup_availability(is_platform_admin=False, email="x@y.com") == (True, None)


def test_availability_team_mode(monkeypatch):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "team")
    monkeypatch.setenv("STRIPE_TEST_CHECKOUT_EMAILS", "mate@example.com")
    assert pm.paypal_topup_availability(is_platform_admin=False, email="mate@example.com") == (True, None)
    assert pm.paypal_topup_availability(is_platform_admin=True, email=None) == (True, None)
    assert pm.paypal_topup_availability(is_platform_admin=False, email="stranger@example.com") == (
        False, pm.PAYPAL_UNAVAILABLE)


def test_sandbox_in_production_is_team_only(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", SB_ID)
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", SB_SECRET)
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "wh")
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "on")
    assert pm.paypal_topup_availability(is_platform_admin=False, email="a@b.com") == (True, None)
    monkeypatch.setenv("ENVIRONMENT", " Production ")
    assert pm.paypal_topup_availability(is_platform_admin=False, email="a@b.com") == (
        False, pm.PAYPAL_UNAVAILABLE)
    assert pm.paypal_topup_availability(is_platform_admin=True, email=None) == (True, None)


def test_live_in_production_is_open_when_on(monkeypatch):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "on")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert pm.paypal_topup_availability(is_platform_admin=False, email=None) == (True, None)


def test_reason_never_names_a_setting():
    assert "PAYPAL" not in pm.PAYPAL_UNAVAILABLE
    assert "_" not in pm.PAYPAL_UNAVAILABLE


def test_webhook_events_unique_and_cover_money_events():
    events = pm.PAYPAL_WEBHOOK_EVENTS
    assert len(events) == len(set(events))
    for name in (
        "PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED",
        "CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.RESOLVED",
    ):
        assert name in events


# ---- plans ------------------------------------------------------------------

def test_webhook_events_include_subscription_events():
    events = pm.PAYPAL_WEBHOOK_EVENTS
    assert len(events) == 19
    assert len(set(events)) == 19
    assert events[:10] == (
        "CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DECLINED",
        "PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.PENDING", "PAYMENT.CAPTURE.REFUNDED",
        "PAYMENT.CAPTURE.REVERSED", "CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED",
        "CUSTOMER.DISPUTE.RESOLVED",
    )
    assert events[10:] == (
        "BILLING.SUBSCRIPTION.ACTIVATED", "BILLING.SUBSCRIPTION.UPDATED",
        "BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED",
        "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
        "PAYMENT.SALE.COMPLETED", "PAYMENT.SALE.REFUNDED", "PAYMENT.SALE.REVERSED",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [("team", "team"), (" TEAM ", "team"), ("true", "on"), ("1", "on"), ("on", "on"),
     ("off", "off"), ("maybe", "off"), ("", "off")],
)
def test_plans_switch(monkeypatch, raw, expected):
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", raw)
    assert pm.paypal_plans_switch() == expected


def test_plans_switch_unset_is_off(monkeypatch):
    monkeypatch.delenv("PAYPAL_PLANS_ENABLED", raising=False)
    assert pm.paypal_plans_switch() == "off"


@pytest.fixture
def billing_live(monkeypatch):
    monkeypatch.setattr(pm, "get_billing_mode", lambda: "live")


def test_plan_availability_off(monkeypatch, billing_live):
    monkeypatch.delenv("PAYPAL_PLANS_ENABLED", raising=False)
    _live(monkeypatch)
    assert pm.paypal_plan_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "on")  # the top-up switch is not the plans switch
    assert pm.paypal_plan_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)


def test_plan_availability_on(monkeypatch, billing_live):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "on")
    assert pm.paypal_plan_availability(is_platform_admin=False, email="x@y.com") == (True, None)


def test_plan_availability_team(monkeypatch, billing_live):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "team")
    monkeypatch.setenv("STRIPE_TEST_CHECKOUT_EMAILS", "mate@example.com")
    assert pm.paypal_plan_availability(is_platform_admin=False, email="mate@example.com") == (True, None)
    assert pm.paypal_plan_availability(is_platform_admin=False, email="stranger@example.com") == (
        False, pm.PAYPAL_UNAVAILABLE)


def test_plan_availability_not_configured(monkeypatch, billing_live):
    _live(monkeypatch, webhook=False)
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "on")
    assert pm.paypal_plan_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)


@pytest.mark.parametrize("other", ["test", "mock", "disabled", "unconfigured"])
def test_plan_availability_mode_mismatch(monkeypatch, other):
    _live(monkeypatch)
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "on")
    monkeypatch.setattr(pm, "get_billing_mode", lambda: other)
    assert pm.paypal_plan_availability(is_platform_admin=True, email=None) == (False, pm.PAYPAL_UNAVAILABLE)
