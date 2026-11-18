"""PayPal plan routes: checks, error mapping, answers. A fake service, no database, no network."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user
from app.api.v1.endpoints import billing_paypal_subscriptions as bps
from app.core.error_handlers import register_error_handlers
from app.domain.services.billing_checkout import CheckoutError

TENANT = str(uuid4())
REQUEST = str(uuid4())
OPTION = str(uuid4())
CHECKOUT = "/api/v1/billing/paypal/subscriptions/checkout"
ATTEMPT = f"/api/v1/billing/paypal/subscriptions/{REQUEST}"
SECRET_TEXT = "".join(["db", "-", "password", "-", "leak"])


def answer(state="open"):
    return {
        "request_id": REQUEST, "state": state, "session_id": "I-" + "x1",
        "checkout_url": "https://www.sandbox.paypal.com/webapps/billing/subscriptions?ba_token=x1",
        "price_option": {"id": OPTION}, "mock_mode": False, "message": None,
    }


class FakeSvc:
    def __init__(self):
        self.error: Exception | None = None
        self.start_calls: list[dict] = []
        self.get_calls: list[dict] = []

    async def start_checkout(self, **kw):
        self.start_calls.append(kw)
        if self.error:
            raise self.error
        return answer()

    async def get_attempt(self, **kw):
        self.get_calls.append(kw)
        if self.error:
            raise self.error
        return answer("activated")


class FakeAudit:
    def __init__(self):
        self.calls: list[dict] = []

    async def log(self, **kw):
        self.calls.append(kw)


def user(role="tenant_admin", email="buyer@example.com", tenant=TENANT):
    return CurrentUser(id=str(uuid4()), email=email, tenant_id=tenant, role=role)


@pytest.fixture
def world(monkeypatch):
    svc = FakeSvc()
    audit = FakeAudit()
    monkeypatch.setattr(bps, "_service", lambda: svc)
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", "".join(["id", "-", "synthetic"]))
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", "".join(["sec", "-", "synthetic"]))
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "".join(["wh", "-", "synthetic"]))
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "on")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "".join(["sk", "_", "test", "_", "synthetic"]))
    monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    monkeypatch.delenv("STRIPE_TEST_CHECKOUT_EMAILS", raising=False)
    app = FastAPI()
    app.include_router(bps.router, prefix="/api/v1")
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: user()
    app.dependency_overrides[get_audit_logger] = lambda: audit
    return SimpleNamespace(app=app, svc=svc, audit=audit)


async def post(world, body, current=None):
    if current is not None:
        world.app.dependency_overrides[get_current_user] = lambda: current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://test") as c:
        return await c.post(CHECKOUT, json=body)


async def get(world, current=None):
    if current is not None:
        world.app.dependency_overrides[get_current_user] = lambda: current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://test") as c:
        return await c.get(ATTEMPT)


BODY = {"request_id": REQUEST, "price_option_id": OPTION}


# -- permission ------------------------------------------------------------------------


async def test_checkout_needs_the_billing_update_permission(world):
    response = await post(world, BODY, user(role="viewer"))
    assert response.status_code == 403 and world.svc.start_calls == []


async def test_get_needs_the_billing_update_permission(world):
    response = await get(world, user(role="viewer"))
    assert response.status_code == 403 and world.svc.get_calls == []


async def test_both_routes_declare_the_permission_and_a_response_model():
    assert len(bps.router.routes) == 2
    for route in bps.router.routes:
        assert route.response_model is not None
        assert any("permission_checker" in repr(d.call) for d in route.dependant.dependencies)


# -- checkout ----------------------------------------------------------------------------


async def test_checkout_happy_path_passes_the_session_tenant_and_audits(world):
    response = await post(world, BODY)
    assert response.status_code == 200
    assert response.json() == answer()
    (call,) = world.svc.start_calls
    assert call == {
        "tenant_id": TENANT, "request_id": REQUEST, "price_option_id": OPTION, "allow_team_only": False,
    }
    (entry,) = world.audit.calls
    assert entry["action"] == "paypal_plan_checkout_started"
    assert entry["tenant_id"] == TENANT
    assert entry["metadata"] == {"request_id": REQUEST, "price_option_id": OPTION}


async def test_checkout_team_rule_sets_allow_team_only(world):
    await post(world, BODY, user(role="platform_admin"))
    assert world.svc.start_calls[0]["allow_team_only"] is True


@pytest.mark.parametrize("extra", [{"tenant_id": TENANT}, {"amount": 1}, {"plan_id": "x"}])
async def test_checkout_body_with_an_extra_field_is_refused(world, extra):
    response = await post(world, {**BODY, **extra})
    assert response.status_code == 422 and world.svc.start_calls == []


@pytest.mark.parametrize("body", [{}, {"request_id": REQUEST}, {"request_id": "nope", "price_option_id": OPTION}])
async def test_checkout_body_must_be_complete_and_valid(world, body):
    response = await post(world, body)
    assert response.status_code == 422 and world.svc.start_calls == []


async def test_closed_switch_is_503_and_the_service_is_not_called(world, monkeypatch):
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "off")
    response = await post(world, BODY)
    assert response.status_code == 503
    assert world.svc.start_calls == [] and world.audit.calls == []
    assert "paypal_unavailable" in response.text and "request_not_started" in response.text


async def test_team_only_switch_is_closed_for_a_customer(world, monkeypatch):
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "team")
    response = await post(world, BODY)
    assert response.status_code == 503 and world.svc.start_calls == []


async def test_test_mode_gate_refuses_a_stranger(world, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    response = await post(world, BODY)
    assert response.status_code == 403 and world.svc.start_calls == []
    assert "test_billing_team_only" in response.text


@pytest.mark.parametrize("status_code,not_started,existing", [
    (409, True, {"request_id": str(uuid4())}),
    (400, True, None),
    (503, False, None),
])
async def test_checkout_error_keeps_code_status_and_flags(world, status_code, not_started, existing):
    error = CheckoutError(
        "some_code", "Some message", status_code, request_not_started=not_started, existing_attempt=existing
    )
    world.svc.error = error
    response = await post(world, BODY)
    assert response.status_code == status_code
    text = response.text
    assert "some_code" in text and "Some message" in text
    assert ("request_not_started" in text) is not_started
    if existing is not None:
        assert existing["request_id"] in text
    assert world.audit.calls == []


async def test_unexpected_error_is_503_unconfirmed_and_leaks_nothing(world):
    world.svc.error = RuntimeError(SECRET_TEXT)
    response = await post(world, BODY)
    assert response.status_code == 503
    assert "checkout_unconfirmed" in response.text
    assert SECRET_TEXT not in response.text


async def test_plain_value_error_is_503_unconfirmed_and_leaks_nothing(world):
    world.svc.error = ValueError(SECRET_TEXT)
    response = await post(world, BODY)
    assert response.status_code == 503 and SECRET_TEXT not in response.text


# -- get ---------------------------------------------------------------------------------


async def test_get_happy_path_uses_the_session_tenant(world):
    response = await get(world)
    assert response.status_code == 200 and response.json()["state"] == "activated"
    assert world.svc.get_calls == [{"tenant_id": TENANT, "request_id": REQUEST}]


async def test_get_not_found_is_404_with_its_code(world):
    world.svc.error = CheckoutError("checkout_not_found", "No such checkout", 404)
    response = await get(world)
    assert response.status_code == 404 and "checkout_not_found" in response.text


async def test_get_unexpected_error_is_503_and_leaks_nothing(world):
    world.svc.error = RuntimeError(SECRET_TEXT)
    response = await get(world)
    assert response.status_code == 503 and "checkout_unconfirmed" in response.text
    assert SECRET_TEXT not in response.text


async def test_get_does_not_check_the_switch(world, monkeypatch):
    monkeypatch.setenv("PAYPAL_PLANS_ENABLED", "off")
    monkeypatch.delenv("PAYPAL_ENV")
    response = await get(world)
    assert response.status_code == 200 and len(world.svc.get_calls) == 1
