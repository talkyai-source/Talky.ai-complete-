"""Readers that only knew Stripe now also see a plan held through PayPal.

The card answers must not change: every PayPal case has a card twin here.
"""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import billing as api
from app.domain.services import billing_mode, paypal_mode
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.minutes_quota import _status_from

TENANT = str(uuid4())
USER = SimpleNamespace(id="u-1", tenant_id=TENANT, role="tenant_admin", email="owner@example.com")
START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 12, 1, tzinfo=UTC)
OFFER = {"id": str(uuid4()), "plan_id": "starter", "interval": "month", "amount_minor": 2900}


class FakeService:
    """Stands in for PayPalSubscriptionService; answers are set per test."""

    answers: dict = {}
    calls: list = []

    def __init__(self, pool):
        self.pool = pool

    async def _answer(self, name, *args, **kwargs):
        FakeService.calls.append((name, args, kwargs))
        value = FakeService.answers.get(name)
        if isinstance(value, Exception):
            raise value
        return value

    async def current_for_tenant(self, *args, **kwargs):
        return await self._answer("current_for_tenant", *args, **kwargs)

    async def payments_for_tenant(self, *args, **kwargs):
        return await self._answer("payments_for_tenant", *args, **kwargs)

    async def cancel_for_tenant(self, *args, **kwargs):
        return await self._answer("cancel_for_tenant", *args, **kwargs)


@pytest.fixture
def paypal(monkeypatch):
    FakeService.answers, FakeService.calls = {}, []
    monkeypatch.setattr(
        "app.domain.services.paypal_subscriptions.PayPalSubscriptionService", FakeService
    )
    return FakeService


@pytest.fixture
def quota(monkeypatch):
    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        yield object()

    async def status(conn, tenant_id):
        return _status_from(300, 60 * 60, unlimited=False)

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.minutes_quota.compute_minutes_status", status)


def card_row(**changes):
    return {
        "status": "active", "plan_id": "starter", "plans": {"name": "Starter"},
        "current_period_start": "2026-10-01", "current_period_end": "2026-11-01",
        "metadata": {"cancel_at_period_end": False}, "stripe_subscription_id": "sub_synthetic",
        "purchased_price_option": {"id": "card-offer"}, "billing_portal_available": True,
        **changes,
    }


async def subscription(row):
    billing = SimpleNamespace(get_subscription=AsyncMock(return_value=row))
    return (await api.get_subscription(USER, billing, object())).model_dump()


def paypal_held(**changes):
    return {
        "provider": "paypal", "subscription_status": "active", "current_period_start": START,
        "current_period_end": END, "cancel_at_period_end": True, "purchased_price_option": OFFER,
        **changes,
    }


# ---- /subscription -------------------------------------------------------


async def test_a_card_subscriber_sees_exactly_the_old_answer_plus_the_provider(paypal, quota):
    result = await subscription(card_row())
    assert result["current_period_start"] == "2026-10-01"
    assert result["current_period_end"] == "2026-11-01"
    assert result["cancel_at_period_end"] is False
    assert result["purchased_price_option"] == {"id": "card-offer"}
    assert result["billing_portal_available"] is True
    assert result["billing_provider"] == "stripe"
    assert result["minutes_allocated"] == 300 and result["minutes_used"] == 60


async def test_an_account_with_no_stripe_id_and_no_paypal_plan_has_no_provider(paypal, quota):
    assert (await subscription(card_row(stripe_subscription_id=None)))["billing_provider"] is None


async def test_no_subscription_at_all_has_no_provider_even_if_paypal_answers(paypal, quota):
    paypal.answers["current_for_tenant"] = paypal_held()
    result = await subscription(None)
    assert result["status"] == "inactive" and result["billing_provider"] is None


async def test_a_paypal_subscriber_sees_paypals_dates_flag_and_offer(paypal, quota):
    paypal.answers["current_for_tenant"] = paypal_held()
    result = await subscription(card_row(stripe_subscription_id=None, billing_portal_available=True))
    assert result["current_period_start"] == START.isoformat()
    assert result["current_period_end"] == END.isoformat()
    assert result["cancel_at_period_end"] is True
    assert result["purchased_price_option"] == OFFER
    assert result["billing_portal_available"] is False
    assert result["billing_provider"] == "paypal"
    assert result["plan_name"] == "Starter" and result["status"] == "active"


async def test_paypal_dates_that_are_missing_stay_none(paypal, quota):
    paypal.answers["current_for_tenant"] = paypal_held(current_period_start=None, current_period_end=None)
    result = await subscription(card_row(stripe_subscription_id=None))
    assert result["current_period_start"] is None and result["current_period_end"] is None


async def test_a_failing_paypal_read_leaves_the_card_answer_intact(paypal, quota):
    paypal.answers["current_for_tenant"] = RuntimeError("synthetic")
    result = await subscription(card_row())
    assert result["billing_provider"] == "stripe" and result["billing_portal_available"] is True
    assert result["current_period_end"] == "2026-11-01"


# ---- /cancel -------------------------------------------------------------


def cancel_deps():
    billing = SimpleNamespace(cancel_subscription=AsyncMock(return_value={
        "status": "active", "cancel_at_period_end": True, "mock_mode": False, "message": "card"}))
    audit = SimpleNamespace(log=AsyncMock())
    request = SimpleNamespace(client=None, headers={})
    return request, billing, audit


async def test_cancel_uses_paypal_when_it_answers_and_never_calls_stripe(paypal):
    request, billing, audit = cancel_deps()
    paypal.answers["cancel_for_tenant"] = {
        "status": "active", "cancel_at_period_end": True, "current_period_end": END,
        "mock_mode": False, "message": "paypal"}
    result = await api.cancel_subscription(request, USER, billing, audit, object())
    assert result.message == "paypal" and result.cancel_at_period_end is True
    billing.cancel_subscription.assert_not_awaited()
    audit.log.assert_awaited_once()


async def test_cancel_falls_through_to_stripe_when_paypal_has_no_plan(paypal):
    request, billing, audit = cancel_deps()
    paypal.answers["cancel_for_tenant"] = None
    result = await api.cancel_subscription(request, USER, billing, audit, object())
    assert result.message == "card"
    billing.cancel_subscription.assert_awaited_once_with(tenant_id=TENANT, cancel_at_period_end=True)
    audit.log.assert_awaited_once()


async def test_cancel_keeps_the_status_code_and_message_of_a_checkout_error(paypal):
    request, billing, audit = cancel_deps()
    paypal.answers["cancel_for_tenant"] = CheckoutError("paypal_busy", "Try again shortly.", 503)
    with pytest.raises(HTTPException) as error:
        await api.cancel_subscription(request, USER, billing, audit, object())
    # The status is the service's; the text is the route's own, never the exception's.
    assert error.value.status_code == 503
    assert error.value.detail == "PayPal could not cancel the subscription. Please try again."
    billing.cancel_subscription.assert_not_awaited()


# ---- /invoices -----------------------------------------------------------


def stripe_invoice(created):
    return {"id": uuid4(), "tenant_id": uuid4(), "stripe_invoice_id": "in_synthetic", "status": "paid",
            "amount_due": 800, "amount_paid": 800, "currency": "usd", "projection": None,
            "created_at": created}


def paypal_payment(created, **extra):
    return {"id": str(uuid4()), "created_at": created, **extra}


@pytest.fixture
def stripe_rows(monkeypatch):
    rows = []

    async def fake(pool, tenant_id, *, invoice_id=None, limit=10):
        return rows

    monkeypatch.setattr(api, "_invoice_rows", fake)
    return rows


async def test_the_invoice_list_merges_sorts_newest_first_and_cuts_to_the_limit(paypal, stripe_rows):
    stripe_rows.append(stripe_invoice(datetime(2026, 9, 1, tzinfo=UTC)))
    newest = paypal_payment("2026-11-01T00:00:00+00:00")
    middle = paypal_payment("2026-10-01T00:00:00+00:00")
    paypal.answers["payments_for_tenant"] = [newest, middle]
    listed = await api.list_invoices(2, USER, object())
    assert [item["id"] for item in listed["invoices"]] == [newest["id"], middle["id"]]
    assert listed["count"] == 2
    name, args, kwargs = paypal.calls[-1]
    assert name == "payments_for_tenant" and args == (TENANT,) and kwargs == {"limit": 2}


async def test_a_payment_without_a_date_sorts_last(paypal, stripe_rows):
    stripe_rows.append(stripe_invoice(datetime(2026, 9, 1, tzinfo=UTC)))
    undated = paypal_payment(None)
    paypal.answers["payments_for_tenant"] = [undated]
    listed = await api.list_invoices(10, USER, object())
    assert listed["invoices"][-1]["id"] == undated["id"] and listed["count"] == 2


async def test_a_card_only_tenant_gets_the_old_list(paypal, stripe_rows):
    stripe_rows.append(stripe_invoice(datetime(2026, 9, 1, tzinfo=UTC)))
    paypal.answers["payments_for_tenant"] = []
    listed = await api.list_invoices(10, USER, object())
    assert listed["count"] == 1 and listed["invoices"][0]["stripe_invoice_id"] == "in_synthetic"


async def test_an_incomplete_history_is_not_shown_as_complete(paypal, stripe_rows):
    paypal.answers["payments_for_tenant"] = RuntimeError("synthetic")
    with pytest.raises(HTTPException) as error:
        await api.list_invoices(10, USER, object())
    assert error.value.status_code == 503 and error.value.detail["code"] == "invoices_unavailable"


# ---- /invoices/{id} ------------------------------------------------------


async def test_a_single_invoice_falls_back_to_a_paypal_payment(paypal, stripe_rows):
    payment = paypal_payment("2026-11-01T00:00:00+00:00")
    paypal.answers["payments_for_tenant"] = [payment]
    result = await api.get_invoice(payment["id"], USER, object())
    assert result == payment
    name, args, kwargs = paypal.calls[-1]
    assert str(kwargs["payment_id"]) == payment["id"] and kwargs["limit"] == 1


async def test_a_stripe_invoice_is_returned_without_asking_paypal(paypal, stripe_rows):
    row = stripe_invoice(datetime(2026, 9, 1, tzinfo=UTC))
    stripe_rows.append(row)
    result = await api.get_invoice(str(row["id"]), USER, object())
    assert result["stripe_invoice_id"] == "in_synthetic" and paypal.calls == []


async def test_an_unknown_invoice_is_still_404(paypal, stripe_rows):
    paypal.answers["payments_for_tenant"] = []
    with pytest.raises(HTTPException) as error:
        await api.get_invoice(str(uuid4()), USER, object())
    assert error.value.status_code == 404


async def test_a_failing_paypal_lookup_is_a_503_not_a_404(paypal, stripe_rows):
    paypal.answers["payments_for_tenant"] = RuntimeError("synthetic")
    with pytest.raises(HTTPException) as error:
        await api.get_invoice(str(uuid4()), USER, object())
    assert error.value.status_code == 503 and error.value.detail["code"] == "invoice_unavailable"


# ---- /plans --------------------------------------------------------------

PRICED, UNPRICED, CLOSED = str(uuid4()), str(uuid4()), str(uuid4())


@pytest.fixture
def catalog(monkeypatch):
    async def listing(_pool, include_team_only):
        return [{"id": "starter", "price_options": [
            {"id": PRICED, "checkout_available": True},
            {"id": UNPRICED, "checkout_available": True},
            {"id": CLOSED, "checkout_available": False, "unavailable_reason": "provider says no"},
        ]}]

    monkeypatch.setattr("app.domain.services.billing_catalog.list_plan_catalog", listing)
    monkeypatch.setattr(billing_mode, "get_billing_mode", lambda **_kwargs: "test")
    monkeypatch.delenv("ENVIRONMENT", raising=False)


async def plans(user=USER):
    result = await api.list_billing_plans(db_client=SimpleNamespace(pool=object()), current_user=user)
    return {option["id"]: option for option in result[0]["price_options"]}


async def test_only_priced_options_with_open_checkout_are_marked_paypal_available(catalog, monkeypatch):
    seen = {}

    async def ids(pool, *, is_platform_admin, email):
        seen.update(is_platform_admin=is_platform_admin, email=email)
        return {PRICED, CLOSED}

    monkeypatch.setattr(api, "_paypal_priced_option_ids", ids)
    options = await plans()
    assert options[PRICED]["paypal_available"] is True
    assert options[UNPRICED]["paypal_available"] is False
    assert options[CLOSED]["paypal_available"] is False, "never when checkout is unavailable"
    assert options[CLOSED]["unavailable_reason"] == "provider says no"
    assert seen == {"is_platform_admin": False, "email": "owner@example.com"}


async def test_an_anonymous_visitor_is_asked_as_a_non_admin_without_email(catalog, monkeypatch):
    seen = {}

    async def ids(pool, *, is_platform_admin, email):
        seen.update(is_platform_admin=is_platform_admin, email=email)
        return set()

    monkeypatch.setattr(api, "_paypal_priced_option_ids", ids)
    options = await plans(user=None)
    assert seen == {"is_platform_admin": False, "email": None}
    assert all(option["paypal_available"] is False for option in options.values())


async def test_a_refused_caller_gets_no_paypal_even_for_a_priced_option(monkeypatch):
    monkeypatch.setattr(billing_mode, "get_billing_mode", lambda **_kwargs: "test")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STRIPE_TEST_CHECKOUT_EMAILS", "team@talkleeai.com")

    async def listing(_pool, include_team_only):
        return [{"id": "starter", "price_options": [{"id": PRICED, "checkout_available": True}]}]

    monkeypatch.setattr("app.domain.services.billing_catalog.list_plan_catalog", listing)
    monkeypatch.setattr(api, "_paypal_priced_option_ids", AsyncMock(return_value={PRICED}))
    option = (await plans())[PRICED]
    assert option["checkout_available"] is False and option["paypal_available"] is False


class _Conn:
    def __init__(self, rows):
        self.fetch = AsyncMock(return_value=rows)


def patch_pool(monkeypatch, conn):
    calls = []

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        calls.append(tenant_id)
        yield conn

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquire)
    return calls


async def test_the_helper_reads_nothing_when_paypal_plans_are_closed(monkeypatch):
    monkeypatch.setattr(paypal_mode, "paypal_plan_availability", lambda **_k: (False, "closed"))
    calls = patch_pool(monkeypatch, _Conn([]))
    assert await api._paypal_priced_option_ids(object(), is_platform_admin=False, email=None) == set()
    assert calls == []


async def test_the_helper_returns_the_verified_option_ids_for_the_provider_mode(monkeypatch):
    monkeypatch.setattr(paypal_mode, "paypal_plan_availability", lambda **_k: (True, None))
    monkeypatch.setattr(paypal_mode, "paypal_provider_mode", lambda: "test")
    conn = _Conn([{"price_option_id": uuid4()}])
    calls = patch_pool(monkeypatch, conn)
    ids = await api._paypal_priced_option_ids(object(), is_platform_admin=True, email="a@b.c")
    assert len(ids) == 1 and calls == [None]
    sql, mode = conn.fetch.await_args.args
    assert mode == "test"
    for fragment in ("pp.active", "pp.verified_at IS NOT NULL", "pp.amount_minor = o.amount_minor",
                     "pp.currency = o.currency", "pp.interval = o.interval"):
        assert fragment in sql


async def test_the_helper_never_fails_the_plan_list(monkeypatch):
    monkeypatch.setattr(paypal_mode, "paypal_plan_availability", lambda **_k: (True, None))
    monkeypatch.setattr(paypal_mode, "paypal_provider_mode", lambda: "test")
    conn = _Conn([])
    conn.fetch = AsyncMock(side_effect=RuntimeError("synthetic"))
    patch_pool(monkeypatch, conn)
    assert await api._paypal_priced_option_ids(object(), is_platform_admin=False, email=None) == set()


# ---- /config -------------------------------------------------------------


async def test_config_reports_paypal_plans_open(monkeypatch):
    monkeypatch.setattr(billing_mode, "checkout_availability", lambda **_k: (True, None))
    monkeypatch.setattr(paypal_mode, "paypal_plan_availability", lambda **_k: (True, None))
    config = await api.get_billing_config(current_user=USER)
    assert config["paypal_plans_open"] is True and config["paypal_plans_unavailable_reason"] is None


async def test_config_reports_paypal_plans_closed_with_paypals_reason(monkeypatch):
    monkeypatch.setattr(billing_mode, "checkout_availability", lambda **_k: (True, None))
    monkeypatch.setattr(paypal_mode, "paypal_plan_availability", lambda **_k: (False, "paypal reason"))
    config = await api.get_billing_config(current_user=USER)
    assert config["paypal_plans_open"] is False
    assert config["paypal_plans_unavailable_reason"] == "paypal reason"


async def test_config_closes_paypal_plans_when_checkout_is_closed(monkeypatch):
    monkeypatch.setattr(billing_mode, "checkout_availability", lambda **_k: (False, "checkout reason"))
    monkeypatch.setattr(paypal_mode, "paypal_plan_availability", lambda **_k: (True, None))
    config = await api.get_billing_config(current_user=USER)
    assert config["paypal_plans_open"] is False
    assert config["paypal_plans_unavailable_reason"] == "checkout reason"
