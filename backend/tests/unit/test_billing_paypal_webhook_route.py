"""PayPal webhook route: status mapping, size cap, no login, raw bytes untouched."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import billing_paypal_webhook as ep
from app.core.error_handlers import register_error_handlers
from app.domain.services.paypal_webhooks import PayPalWebhookRejected, PayPalWebhookRetry

PATH = "/api/v1/billing/paypal/webhooks"


@pytest.fixture
def world(monkeypatch):
    pool = object()
    handler = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(ep, "handle_paypal_webhook", handler)
    monkeypatch.setattr(ep, "get_container", lambda: SimpleNamespace(is_initialized=True, db_pool=pool))
    app = FastAPI()
    app.include_router(ep.router, prefix="/api/v1")
    register_error_handlers(app)
    return SimpleNamespace(app=app, handler=handler, pool=pool)


async def post(world, content, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://test") as c:
        return await c.post(PATH, content=content, headers=headers or {"content-type": "application/json"})


async def test_success_is_200_and_passes_raw_bytes_and_headers_unchanged(world):
    raw = b'{ "id" :  "E1",\n "caf\xc3\xa9": 1 }'
    response = await post(world, raw, {"content-type": "application/json", "paypal-transmission-id": "t1"})
    assert response.status_code == 200
    assert response.json() == {"received": True, "status": "completed"}
    kw = world.handler.await_args.kwargs
    assert kw["raw_body"] == raw and kw["headers"]["paypal-transmission-id"] == "t1"
    assert world.handler.await_args.args == (world.pool,)


async def test_rejected_is_400(world):
    world.handler.side_effect = PayPalWebhookRejected("x")
    response = await post(world, b"{}")
    assert response.status_code == 400
    assert "Invalid PayPal webhook" in response.text


async def test_retry_is_503(world):
    world.handler.side_effect = PayPalWebhookRetry("x")
    response = await post(world, b"{}")
    assert response.status_code == 503
    assert "PayPal webhook cannot be verified right now" in response.text


async def test_body_over_512_kib_is_413_and_never_handled(world):
    response = await post(world, b"x" * (512 * 1024 + 1))
    assert response.status_code == 413
    world.handler.assert_not_awaited()


async def test_exactly_512_kib_is_accepted(world):
    assert (await post(world, b"x" * (512 * 1024))).status_code == 200


async def test_backend_not_ready_is_503(world, monkeypatch):
    monkeypatch.setattr(ep, "get_container", lambda: SimpleNamespace(is_initialized=False, db_pool=None))
    assert (await post(world, b"{}")).status_code == 503
    world.handler.assert_not_awaited()


def test_route_is_public_and_declares_a_response_model():
    (route,) = [r for r in ep.router.routes if r.path.endswith("/webhooks")]
    assert route.response_model is ep.PayPalWebhookOut
    assert not route.dependant.dependencies
