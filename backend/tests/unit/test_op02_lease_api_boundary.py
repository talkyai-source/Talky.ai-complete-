"""Actual HTTP release route + limiter; synthetic auth and SQL/Redis seams."""
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

import app.api.v1.dependencies as dependencies
from app.api.v1.endpoints import telephony_concurrency as endpoint
from app.domain.services.telephony_concurrency_limiter import LeaseKind
from tests.unit.test_telephony_concurrency_limiter import _ctx, _redis_count


@pytest.mark.asyncio
async def test_foreign_http_release_cannot_release_owned_lease(monkeypatch):
    conn, limiter, redis, owner = _ctx()
    lease = await limiter.acquire_lease(
        conn, tenant_id=owner, call_id=str(uuid4()), talklee_call_id="synthetic-owned-call",
        lease_kind=LeaseKind.CALL,
    )
    assert lease.accepted
    contexts = []

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def scoped_pool(_pool, tenant_id, **_kwargs):
        contexts.append(tenant_id)
        yield conn

    async def rls(_conn, tenant_id, *_args, **_kwargs):
        assert tenant_id == contexts[-1]

    conn.transaction = transaction
    monkeypatch.setattr(endpoint, "acquire_with_tenant", scoped_pool)
    monkeypatch.setattr(endpoint, "apply_tenant_rls_context", rls)
    principal = dependencies.CurrentUser(id=str(uuid4()), email="synthetic@example.invalid", tenant_id=str(uuid4()))
    app = FastAPI()
    app.include_router(endpoint.router)
    app.dependency_overrides[dependencies.get_current_user] = lambda: principal
    app.dependency_overrides[dependencies.get_db_pool] = lambda: object()
    app.dependency_overrides[endpoint._get_concurrency_limiter] = lambda: limiter
    url = f"/telephony/sip/runtime/concurrency/leases/{lease.lease_id}/release"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        heartbeat_before = conn.leases[0].last_heartbeat_at
        heartbeat = await client.post(url.removesuffix("release") + "heartbeat")
        assert heartbeat.status_code == 404
        assert conn.leases[0].last_heartbeat_at == heartbeat_before
        response = await client.post(url, json={"reason": "finished"})
        assert response.status_code == 404
        assert contexts[-1] == principal.tenant_id != owner
        assert _redis_count(redis, owner) == 1
        assert conn.leases[0].released_at is None

        forged = await client.post(url, json={"reason": "finished", "tenant_id": owner})
        assert forged.status_code == 422
        assert _redis_count(redis, owner) == 1

        principal.tenant_id = owner
        response = await client.post(url, json={"reason": "finished"})
        assert response.status_code == 200 and response.json()["released"] is True
        assert _redis_count(redis, owner) == 0
        assert conn.leases[0].released_at is not None


@pytest.mark.asyncio
async def test_foreign_raw_hangup_stops_before_provider_or_settlement(monkeypatch):
    from fastapi import HTTPException, Request
    from unittest.mock import AsyncMock
    from app.api.v1.endpoints import telephony_bridge
    from tests.unit.test_telephony_bridge_auth import _patch_container, CallerContext

    _patch_container(monkeypatch, {"tenant_id": "tenant-B"})
    monkeypatch.setattr(telephony_bridge, "_require_call_control", AsyncMock(return_value=CallerContext(is_internal=False, tenant_id="tenant-A")))
    settlement = AsyncMock()
    provider_hangup = AsyncMock()
    monkeypatch.setattr(telephony_bridge, "mark_termination_pending_and_load_context", settlement)
    monkeypatch.setattr(telephony_bridge, "request_confirmed_hangup", provider_hangup)
    with pytest.raises(HTTPException) as exc:
        await telephony_bridge.hangup_call("provider-B", Request({"type": "http", "headers": []}))
    assert exc.value.status_code == 403
    settlement.assert_not_awaited()
    provider_hangup.assert_not_awaited()
