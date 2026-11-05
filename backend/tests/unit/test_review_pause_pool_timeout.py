"""C3: the operational pause cannot hang behind a saturated pool."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core import db
from app.core.error_handlers import register_error_handlers
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardCheck
from app.domain.services.platform_runtime_controls import set_outbound_call_pause


class ExhaustedPool:
    @asynccontextmanager
    async def acquire(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover — the pool never has a lease


async def test_emergency_pause_read_blocks_dial_within_pool_bound(monkeypatch):
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 0.02)
    guard = CallGuard(ExhaustedPool())
    for name in ("_get_tenant_limits", "_get_partner_limits", "_get_partner_id"):
        monkeypatch.setattr(guard, name, AsyncMock(return_value=None))
    dial = AsyncMock()
    result = await asyncio.wait_for(
        guard.evaluate("11111111-1111-4111-8111-111111111111", "+14155550123"),
        timeout=0.5,
    )
    if result.decision == GuardDecision.ALLOW:
        await dial()
    assert result.decision == GuardDecision.BLOCK
    assert GuardCheck.PLATFORM_CALLS_ENABLED in result.failed_checks
    dial.assert_not_awaited()


async def test_emergency_pause_toggle_returns_503_within_pool_bound(monkeypatch):
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 0.02)
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/synthetic-pause")
    async def toggle():
        return await set_outbound_call_pause(
            ExhaustedPool(), paused=True,
            actor_id="22222222-2222-4222-8222-222222222222",
            reason="synthetic operator pause",
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://synthetic",
    ) as client:
        response = await asyncio.wait_for(client.post("/synthetic-pause"), timeout=0.5)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["error"]["message"]
