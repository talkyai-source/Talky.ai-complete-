"""Pay as you go (owner decision 31 Oct 2026): prepaid minutes without a subscription.

Two rules carry the money risk:
- who may buy which minute bundles (subscribers buy top-ups, everyone else whose
  account may place calls buys pay-as-you-go bundles, nobody else buys anything);
- zero included minutes means "unlimited" only on a subscription plan, never on a
  prepaid plan, or a pay-as-you-go account would call for free.
"""
from __future__ import annotations

import pytest

from app.domain.services.minutes_quota import minutes_unlimited
from app.domain.services.payg import PAYG, SUBSCRIBER, PurchaseRefused, purchase_audience


@pytest.mark.parametrize("status", ["active", "trialing", "ACTIVE "])
def test_a_subscriber_in_good_standing_buys_top_ups(status):
    assert purchase_audience("sub_123", status) == SUBSCRIBER


@pytest.mark.parametrize("status", [None, "", "inactive", "active", "incomplete"])
def test_an_account_without_a_subscription_buys_pay_as_you_go(status):
    assert purchase_audience(None, status) == PAYG


@pytest.mark.parametrize("status", ["past_due", "unpaid", "incomplete", "paused"])
def test_a_subscription_that_is_not_paid_up_cannot_buy_minutes(status):
    with pytest.raises(PurchaseRefused) as refused:
        purchase_audience("sub_123", status)
    assert refused.value.code == "subscription_not_in_good_standing"


@pytest.mark.parametrize("status", ["suspended", "cancelled", "canceled", "past_due", "unpaid", "incomplete_expired"])
def test_an_account_that_may_not_place_calls_cannot_buy_minutes(status):
    with pytest.raises(PurchaseRefused) as refused:
        purchase_audience(None, status)
    assert refused.value.code == "account_cannot_buy_minutes"


def test_zero_minutes_is_unlimited_only_on_a_subscription_plan():
    assert minutes_unlimited(0, 0, "subscription") is True
    assert minutes_unlimited(0, 0, None) is True  # rows written before billing_model existed


def test_a_prepaid_plan_with_zero_included_minutes_is_never_unlimited():
    assert minutes_unlimited(0, 0, "prepaid") is False


@pytest.mark.parametrize("allocated,plan_minutes", [(300, 300), (0, None), (0, 300), (None, 0)])
def test_any_other_allowance_is_not_unlimited(allocated, plan_minutes):
    assert minutes_unlimited(allocated, plan_minutes, "subscription") is False


@pytest.mark.parametrize("status", ["active", "trialing"])
def test_a_paypal_plan_in_good_standing_buys_subscriber_top_ups(status):
    assert purchase_audience("paypal", status) == SUBSCRIBER


@pytest.mark.parametrize("status", ["past_due", "suspended", "incomplete"])
def test_a_paypal_plan_not_in_good_standing_cannot_buy_minutes(status):
    with pytest.raises(PurchaseRefused) as refused:
        purchase_audience("paypal", status)
    assert refused.value.code == "subscription_not_in_good_standing"


class _Row(dict):
    pass


async def _audience_with(monkeypatch, stripe_id, paypal_holds, status="active"):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.domain.services import payg

    monkeypatch.setattr(
        payg, "read_call_authority", AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    lookups = []

    async def held(conn, tenant_id):
        lookups.append(tenant_id)
        return paypal_holds

    monkeypatch.setattr("app.domain.services.paypal_subscriptions.held_subscription_exists", held)
    conn = SimpleNamespace(
        fetchrow=AsyncMock(return_value=_Row(stripe_subscription_id=stripe_id, subscription_status=status))
    )
    return await payg.tenant_purchase_audience(conn, "tenant-1"), lookups


async def test_a_held_paypal_plan_makes_the_tenant_a_subscriber(monkeypatch):
    audience, lookups = await _audience_with(monkeypatch, None, True)
    assert audience == SUBSCRIBER and lookups == ["tenant-1"]


async def test_a_held_paypal_plan_that_is_not_in_good_standing_is_refused(monkeypatch):
    with pytest.raises(PurchaseRefused) as refused:
        await _audience_with(monkeypatch, None, True, status="past_due")
    assert refused.value.code == "subscription_not_in_good_standing"


async def test_no_stripe_id_and_no_paypal_plan_stays_pay_as_you_go(monkeypatch):
    audience, lookups = await _audience_with(monkeypatch, None, False)
    assert audience == PAYG and lookups == ["tenant-1"]


async def test_a_stripe_subscriber_never_triggers_the_paypal_lookup(monkeypatch):
    audience, lookups = await _audience_with(monkeypatch, "sub_synthetic", True)
    assert audience == SUBSCRIBER and lookups == []
