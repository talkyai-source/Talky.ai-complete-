"""Raw signed HTTP delivery through the real Stripe verifier and CSRF middleware.

Only post-verification processing is mocked here. No provider, database or
notification is contacted. Actual transaction behavior has separate PG tests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import billing as api
from app.core.error_handlers import register_error_handlers
from app.core.security.csrf import CSRFMiddleware
from app.domain.services.billing_service import BillingService

SECRET = "synthetic-cp03-signing-secret"
PATH = "/api/v1/billing/webhooks"


def event_bytes():
    # Deliberate whitespace and non-ASCII content: reserializing before
    # verification would invalidate this signature.
    return json.dumps({
        "id": "evt_cp03_signed_synthetic", "object": "event", "type": "invoice.paid",
        "livemode": False, "created": int(time.time()),
        "data": {"object": {"id": "in_cp03_synthetic", "description": "Synthetic café"}},
    }, ensure_ascii=False, indent=2).encode("utf-8")


def signature(body, *, secret=SECRET, timestamp=None):
    stamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


@pytest.fixture
def signed_app(monkeypatch):
    from app.domain.services.billing_webhooks import BillingWebhookProcessor
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic_cp03_not_a_provider_key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    process = AsyncMock(return_value={"status": "handled", "event_id": "evt_cp03_signed_synthetic", "event_type": "invoice.paid"})
    monkeypatch.setattr(BillingWebhookProcessor, "process", process)
    provider = AsyncMock(side_effect=AssertionError("HTTP signature test must not contact a provider"))
    monkeypatch.setattr(BillingService, "_stripe_call", provider)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    register_error_handlers(app)
    app.add_middleware(CSRFMiddleware, allowed_origins=["https://synthetic.example"])
    app.dependency_overrides[api.get_db_client] = lambda: SimpleNamespace(pool=object())
    app.dependency_overrides[api.get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())
    return app, process, provider


async def post(app, body, sig=None, *, path=PATH, method="POST"):
    headers = {"content-type": "application/json"}
    if sig is not None:
        headers["stripe-signature"] = sig
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, content=body, headers=headers)


@pytest.mark.asyncio
async def test_valid_stripe_signature_passes_without_browser_origin(signed_app):
    app, process, provider = signed_app
    body = event_bytes()
    response = await post(app, body, signature(body))
    assert response.status_code == 200 and response.json()["status"] == "handled"
    process.assert_awaited_once()
    verified = process.await_args.args[0]
    assert verified["id"] == "evt_cp03_signed_synthetic"
    assert verified["data"]["object"]["description"] == "Synthetic café"
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_signed_delivery_passes_the_full_application_middleware_without_auth(signed_app):
    from app.core.app_bootstrap import configure_middleware

    subset_app, process, provider = signed_app
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides.update(subset_app.dependency_overrides)
    configure_middleware(app)
    body = event_bytes()
    # Loopback deliberately avoids the production global Redis limiter. All
    # configured middleware still run; no Origin, cookie or auth is supplied.
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43210))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(PATH, content=body, headers={
            "content-type": "application/json", "stripe-signature": signature(body),
        })
    assert response.status_code == 200 and response.json()["status"] == "handled"
    assert response.headers.get("x-request-id")
    process.assert_awaited_once()
    assert process.await_args.args[0]["data"]["object"]["description"] == "Synthetic café"
    provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "wrong_secret", "tampered_body", "expired", "malformed", "v0_only"])
async def test_invalid_signature_never_reaches_processing(signed_app, case):
    app, process, provider = signed_app
    body = event_bytes()
    sig = signature(body)
    if case == "missing":
        sig = None
    elif case == "wrong_secret":
        sig = signature(body, secret="different-synthetic-secret")
    elif case == "tampered_body":
        body += b" "
    elif case == "expired":
        sig = signature(body, timestamp=int(time.time()) - 3600)
    elif case == "malformed":
        sig = "invalid-signature-header"
    elif case == "v0_only":
        sig = sig.replace("v1=", "v0=")
    response = await post(app, body, sig)
    assert response.status_code == 400
    process.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_signed_invalid_json_is_rejected_before_processing(signed_app):
    app, process, _ = signed_app
    body = b'{"malformed":'
    response = await post(app, body, signature(body))
    assert response.status_code == 400
    process.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", [
    ("/api/v1/billing/create-checkout-session", "POST"),
    ("/api/v1/billing/portal", "POST"),
    ("/api/v1/billing/webhooks/", "POST"),
    ("/api/v1/billing/webhooks/other", "POST"),
    ("/api/v1/billing/webhooks-forged", "POST"),
    (PATH, "PUT"),
])
async def test_only_exact_webhook_post_is_exempt_from_csrf(signed_app, path, method):
    app, process, _ = signed_app
    body = event_bytes()
    response = await post(app, body, signature(body), path=path, method=method)
    assert response.status_code == 403
    process.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["BillingWebhookRetryable", "BillingWebhookReviewRequired"])
async def test_unfinished_processing_returns_retryable_non_success(signed_app, error_name):
    from app.domain.services import billing_webhooks
    app, process, _ = signed_app
    process.side_effect = getattr(billing_webhooks, error_name)("synthetic_processing_unfinished")
    body = event_bytes()
    response = await post(app, body, signature(body))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    process.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_duplicate_acknowledgment_preserves_server_result(signed_app):
    app, process, _ = signed_app
    process.return_value = {"status": "duplicate", "event_id": "evt_cp03_signed_synthetic", "event_type": "invoice.paid"}
    body = event_bytes()
    response = await post(app, body, signature(body))
    assert response.status_code == 200 and response.json() == process.return_value


@pytest.mark.asyncio
async def test_oversized_delivery_is_rejected_before_signature_or_processing(signed_app):
    app, process, _ = signed_app
    body = b" " * (512 * 1024 + 1)
    response = await post(app, body, signature(body))
    assert response.status_code == 413
    process.assert_not_awaited()
