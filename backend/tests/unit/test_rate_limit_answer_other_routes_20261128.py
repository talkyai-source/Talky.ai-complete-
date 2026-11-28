"""The shared 429 handler on a limited route that is NOT Buy Now.

Real limiter, real in-memory store, controlled clock (same approach as the
Buy Now hourly-limit test). Client-errors route: 10 a minute per user.
"""
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded

from app.api.v1.dependencies import get_current_user
from app.api.v1.endpoints.admin import client_errors
from app.api.v1.endpoints.auth import limiter
from app.core.error_handlers import rate_limit_exception_handler

URL = "/api/v1/admin/client-errors"
GENERIC = "Too many requests. Wait a moment and try again."
BODY = {
    "kind": "render", "name": "TypeError", "message": "x is undefined",
    "stack": ["at Foo (app.js:1:2)"], "route": "/campaigns", "app_version": "1.2.3",
    "occurred_at": "2026-11-17T10:00:00Z",
}


@pytest.fixture(autouse=True)
def fresh_limiter():
    limiter.reset()
    yield
    limiter.reset()


def make_app():
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    app.include_router(client_errors.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="12345678-aaaa-bbbb-cccc-ddddeeeeffff", email="a@example.com",
        role="tenant_admin", tenant_id=str(uuid4()))
    return app


async def post(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t.invalid") as c:
        return await c.post(URL, json=BODY)


async def fill_window(app, monkeypatch):
    now = time.time()
    clock = {"now": now}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    for _ in range(10):
        assert (await post(app)).status_code == 204
    return clock


def check_generic(r, seconds):
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) == seconds
    error = r.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["message"] == GENERIC
    assert set(error["details"]) == {"limit", "retry_after_seconds"}
    assert error["details"]["limit"] == "10 per 1 minute"
    assert error["details"]["retry_after_seconds"] == seconds


async def test_remaining_window_not_the_full_minute(monkeypatch):
    app = make_app()
    clock = await fill_window(app, monkeypatch)
    clock["now"] += 30
    check_generic(await post(app), 30)


async def test_window_stats_failure_is_still_a_429_with_the_item_expiry(monkeypatch):
    app = make_app()
    clock = await fill_window(app, monkeypatch)
    clock["now"] += 30

    def broken(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(limiter.limiter, "get_window_stats", broken)
    check_generic(await post(app), 60)


async def test_limiter_missing_from_app_state_is_still_a_429(monkeypatch):
    app = make_app()
    clock = await fill_window(app, monkeypatch)
    clock["now"] += 30
    del app.state.limiter
    check_generic(await post(app), 60)
