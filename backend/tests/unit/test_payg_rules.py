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
