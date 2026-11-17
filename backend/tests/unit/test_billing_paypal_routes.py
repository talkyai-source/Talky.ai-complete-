"""PayPal top-up routes: checks, error mapping, answers, and /billing/config."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_current_user
from app.api.v1.endpoints import billing as billing_ep
from app.api.v1.endpoints import billing_paypal as bp
from app.core.error_handlers import register_error_handlers
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.paypal_client import PayPalError, PayPalNotConfigured
from app.domain.services.paypal_topups import CaptureOutcome, PayPalTopupError, StartedCheckout
from app.domain.services.topup_service import TopupError

TENANT = str(uuid4())
ORDER = str(uuid4())
CHECKOUT = "/api/v1/billing/topups/paypal/checkout"
CAPTURE = "/api/v1/billing/topups/paypal/capture"
STARTED = StartedCheckout(
    order_id=ORDER, paypal_order_id="PPORD-" + "x1", checkout_url="https://www.sandbox.paypal.com/checkoutnow?token=x1",
    minutes=100, price_cents=1000, currency="USD")


class FakeSvc:
    def __init__(self):
        self.start_error: Exception | None = None
        self.capture_error: Exception | None = None
        self.outcome = CaptureOutcome(ORDER, "paid", 100)
        self.start_calls: list[dict] = []
        self.capture_calls: list[dict] = []

    async def start_checkout(self, **kw):
        self.start_calls.append(kw)
        if self.start_error:
            raise self.start_error
        return STARTED

    async def capture_for_tenant(self, **kw):
        self.capture_calls.append(kw)
        if self.capture_error:
            raise self.capture_error
        return self.outcome


def user(role="tenant_admin", email="buyer@example.com", tenant=TENANT):
    return CurrentUser(id=str(uuid4()), email=email, tenant_id=tenant, role=role)


@pytest.fixture
def world(monkeypatch):
    svc = FakeSvc()
    monkeypatch.setattr(bp, "_service", lambda: svc)
    monkeypatch.setattr(bp, "_purchase_audience", AsyncMock(return_value="subscriber"))
    monkeypatch.setattr(bp, "billing_return_urls", lambda: "https://app.example/billing")
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "true")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", "".join(["id", "-", "synthetic"]))
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", "".join(["sec", "-", "synthetic"]))
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "".join(["wh", "-", "synthetic"]))
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "on")
    monkeypatch.setenv("ENVIRONMENT", "development")
    app = FastAPI()
    app.include_router(bp.router, prefix="/api/v1")
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user()
    svc.app = app
    return svc


async def post(world, path, body, current=None):
    if current is not None:
        world.app.dependency_overrides[get_current_user] = lambda: current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://test") as c:
        return await c.post(path, json=body)


def message(response):
    return response.json()["error"]["message"]


# -- permission ------------------------------------------------------------------------


@pytest.mark.parametrize("path,body", [(CHECKOUT, {"package_code": "m100"}), (CAPTURE, {"order_id": ORDER})])
async def test_billing_update_permission_is_required(world, path, body):
    response = await post(world, path, body, user(role="viewer"))
    assert response.status_code == 403
    assert world.start_calls == [] and world.capture_calls == []


async def test_both_routes_declare_the_permission_and_a_response_model():
    for route in bp.router.routes:
        assert route.response_model is not None
        assert route.dependant.dependencies, route.path
        assert any("permission_checker" in repr(d.call) for d in route.dependant.dependencies)


# -- checkout ----------------------------------------------------------------------------


async def test_checkout_happy_answer(world):
    response = await post(world, CHECKOUT, {"package_code": "m100"})
    assert response.status_code == 200
    assert response.json() == {
        "order_id": ORDER, "paypal_order_id": STARTED.paypal_order_id,
        "checkout_url": STARTED.checkout_url, "minutes": 100, "price_cents": 1000, "currency": "USD"}
    (call,) = world.start_calls
    assert call["tenant_id"] == TENANT and call["package_code"] == "m100"
    assert call["audience"] == "subscriber" and call["billing_base"] == "https://app.example/billing"


async def test_checkout_503_when_topups_are_switched_off(world, monkeypatch):
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "false")
    response = await post(world, CHECKOUT, {"package_code": "m100"})
    assert response.status_code == 503 and message(response) == "Minute top-ups are not available yet"
    assert world.start_calls == []


@pytest.mark.parametrize("closed", ["switch_off", "unconfigured"])
async def test_checkout_503_when_paypal_is_closed_with_its_reason(world, monkeypatch, closed):
    if closed == "switch_off":
        monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "off")
    else:
        monkeypatch.delenv("PAYPAL_ENV")
    response = await post(world, CHECKOUT, {"package_code": "m100"})
    assert response.status_code == 503 and message(response) == "PayPal is not available yet."
    assert world.start_calls == []


async def test_checkout_in_team_mode_is_closed_for_a_customer_and_open_for_the_team(world, monkeypatch):
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "team")
    closed = await post(world, CHECKOUT, {"package_code": "m100"})
    assert closed.status_code == 503
    open_ = await post(world, CHECKOUT, {"package_code": "m100"}, user(role="platform_admin"))
    assert open_.status_code == 200


async def test_checkout_return_destination_failure_is_reported_before_any_order(world, monkeypatch):
    def broken():
        raise CheckoutError("no_destination", "no destination", 503)

    monkeypatch.setattr(bp, "billing_return_urls", broken)
    response = await post(world, CHECKOUT, {"package_code": "m100"})
    assert response.status_code == 503 and world.start_calls == []


@pytest.mark.parametrize("error,status_code,text", [
    (TopupError("Unknown or inactive top-up package 'zz'"), 400, None),
    (PayPalTopupError("unsupported_currency"), 400, "PayPal cannot take this currency. Please pay by card."),
    (PayPalTopupError("provider_failed"), 502, "Could not start the PayPal payment. You have not been charged."),
    (PayPalError(status=500, name="X", retryable=True), 502, "Could not start the PayPal payment. You have not been charged."),
    (PayPalNotConfigured("no"), 502, "Could not start the PayPal payment. You have not been charged."),
])
async def test_checkout_error_mapping(world, error, status_code, text):
    world.start_error = error
    response = await post(world, CHECKOUT, {"package_code": "m100"})
    assert response.status_code == status_code
    if text:
        assert message(response) == text


@pytest.mark.parametrize("body", [
    {"package_code": "m100", "price_cents": 1}, {"package_code": "x" * 65}, {}, {"package_code": 5},
])
async def test_checkout_body_is_strict(world, body):
    response = await post(world, CHECKOUT, body)
    assert response.status_code == 422 and world.start_calls == []


async def test_checkout_without_a_tenant_is_400(world):
    response = await post(world, CHECKOUT, {"package_code": "m100"}, user(tenant=None))
    assert response.status_code == 400


# -- capture -------------------------------------------------------------------------------


async def test_capture_happy_answer_and_tenant_comes_from_the_user(world):
    response = await post(world, CAPTURE, {"order_id": ORDER})
    assert response.status_code == 200
    assert response.json() == {"order_id": ORDER, "status": "paid", "minutes": 100}
    assert world.capture_calls == [{"tenant_id": TENANT, "order_id": ORDER}]


async def test_capture_needs_no_availability_check(world, monkeypatch):
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "false")
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "off")
    response = await post(world, CAPTURE, {"order_id": ORDER})
    assert response.status_code == 200


@pytest.mark.parametrize("state", ["pending", "not_approved", "failed"])
async def test_capture_passes_the_other_outcomes_through(world, state):
    world.outcome = CaptureOutcome(ORDER, state, None)
    response = await post(world, CAPTURE, {"order_id": ORDER})
    assert response.status_code == 200 and response.json()["status"] == state
    assert response.json()["minutes"] is None


@pytest.mark.parametrize("error,status_code,text", [
    (PayPalNotConfigured("no"), 503, "PayPal is not available yet."),
    (PayPalTopupError("not_found"), 404, "Order not found"),
    (PayPalError(status=503, name="X", retryable=True), 502, "We could not confirm the payment yet."),
    (PayPalError(status=422, name="X"), 502, "We could not confirm the payment yet."),
    (PayPalTopupError("review"), 409,
     "This payment needs a manual check. Nothing was added to your minutes yet."),
])
async def test_capture_error_mapping(world, error, status_code, text):
    world.capture_error = error
    response = await post(world, CAPTURE, {"order_id": ORDER})
    assert response.status_code == status_code and message(response) == text


@pytest.mark.parametrize("body", [
    {"order_id": "not-a-uuid"}, {"order_id": ORDER, "tenant_id": TENANT}, {}, {"order_id": 7},
])
async def test_capture_body_is_strict(world, body):
    response = await post(world, CAPTURE, body)
    assert response.status_code == 422 and world.capture_calls == []


# -- /billing/config ---------------------------------------------------------------------------

OLD_KEYS = {"stripe_configured", "mock_mode", "billing_mode", "publishable_key", "checkout_open",
            "checkout_unavailable_reason", "topups_open", "topups_unavailable_reason"}


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("PAYPAL_ENV", "PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET", "PAYPAL_CLIENT_ID",
                 "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID", "PAYPAL_TOPUPS_ENABLED",
                 "BILLING_TOPUPS_LIVE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")


async def test_config_new_keys_are_closed_by_default(clean_env):
    config = await billing_ep.get_billing_config(current_user=None)
    assert config["paypal_topups_open"] is False
    assert config["paypal_topups_unavailable_reason"] == "PayPal is not available yet."
    assert set(config) >= OLD_KEYS


async def test_config_new_keys_open_when_everything_is_on(world):
    config = await billing_ep.get_billing_config(current_user=user())
    assert config["paypal_topups_open"] is True and config["paypal_topups_unavailable_reason"] is None
    assert set(config) >= OLD_KEYS


async def test_config_topups_switched_off_closes_paypal_with_the_paypal_sentence(world, monkeypatch):
    monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", "false")
    config = await billing_ep.get_billing_config(current_user=user())
    assert config["paypal_topups_open"] is False
    assert config["paypal_topups_unavailable_reason"] == "PayPal is not available yet."


async def test_config_paypal_switch_off_closes_it_even_with_topups_on(world, monkeypatch):
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "off")
    config = await billing_ep.get_billing_config(current_user=user())
    assert config["paypal_topups_open"] is False


async def test_config_team_switch_depends_on_the_caller(world, monkeypatch):
    monkeypatch.setenv("PAYPAL_TOPUPS_ENABLED", "team")
    customer = await billing_ep.get_billing_config(current_user=user())
    team = await billing_ep.get_billing_config(current_user=user(role="platform_admin"))
    assert customer["paypal_topups_open"] is False and team["paypal_topups_open"] is True
