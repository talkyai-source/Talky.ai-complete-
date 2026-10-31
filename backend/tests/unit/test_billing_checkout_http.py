"""HTTP purchase contract, including the canonical error envelope used by the UI."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user
from app.api.v1.endpoints import billing as api
from app.core.error_handlers import register_error_handlers
from app.domain.services.billing_checkout import CheckoutError


def application(service):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    register_error_handlers(app)
    tenant_id = str(uuid4())
    # Authentication is a separate gate. Use an explicitly privileged synthetic
    # actor here so this test exercises only routing, parsing and serialization.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), tenant_id=tenant_id, email="synthetic@example.com", role="platform_admin"
    )
    app.dependency_overrides[api.get_billing_service] = lambda: service
    app.dependency_overrides[get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())
    return app, tenant_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"plan_id": "forged"},
        {"stripe_price_id": "price_forged"},
        {"amount_minor": 1},
        {"tenant_id": str(uuid4())},
        {"success_url": "https://untrusted.example"},
    ],
)
async def test_http_rejects_untrusted_purchase_terms_before_service(extra):
    service = SimpleNamespace(create_checkout_session=AsyncMock())
    app, _ = application(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/billing/create-checkout-session",
            json={"request_id": str(uuid4()), "price_option_id": str(uuid4()), **extra},
        )
    assert response.status_code == 422
    service.create_checkout_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_provider_outcome_is_503_without_false_rejection_proof():
    service = SimpleNamespace(
        create_checkout_session=AsyncMock(side_effect=TimeoutError("synthetic transport failure"))
    )
    app, _ = application(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/billing/create-checkout-session",
            json={"request_id": str(uuid4()), "price_option_id": str(uuid4())},
        )
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "checkout_unconfirmed"
    assert not (error.get("details") or {}).get("request_not_started")
    assert "transport failure" not in response.text


@pytest.mark.asyncio
async def test_get_attempt_uses_authenticated_tenant_and_preserves_unconfirmed_error():
    service = SimpleNamespace(
        get_checkout_attempt=AsyncMock(
            side_effect=CheckoutError("checkout_not_found", "Checkout request was not found.", 404)
        )
    )
    app, tenant = application(service)
    request_id = str(uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/billing/checkout-attempts/{request_id}?tenant_id={uuid4()}"
        )
    assert response.status_code == 404 and response.json()["error"]["code"] == "checkout_not_found"
    service.get_checkout_attempt.assert_awaited_once_with(tenant_id=tenant, request_id=request_id)


@pytest.mark.asyncio
async def test_preinsert_proof_survives_canonical_http_error_envelope():
    prior = {"request_id": str(uuid4()), "state": "pending", "price_option": {"id": str(uuid4())}}
    service = SimpleNamespace(
        create_checkout_session=AsyncMock(
            side_effect=CheckoutError(
                "checkout_outstanding",
                "Resume the existing purchase.",
                request_not_started=True,
                existing_attempt=prior,
            )
        )
    )
    app, _ = application(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/billing/create-checkout-session",
            json={"request_id": str(uuid4()), "price_option_id": str(uuid4())},
        )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "checkout_outstanding"
    assert error["details"] == {"request_not_started": True, "existing_attempt": prior}
