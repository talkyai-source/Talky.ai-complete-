"""Bughunt telephony-control: runtime policy + concurrency lease routes are admin-only.

telephony-infra-media-1 / outbound-telephony-dialer-11: any tenant member
(agent, billing_user, readonly) could activate/rollback the routing policy and
acquire/release/expire the tenant's concurrency leases. These tests drive the
real routers over HTTP with a synthetic principal of each role.
"""
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

import app.api.v1.dependencies as dependencies
from app.api.v1.endpoints import telephony_concurrency as concurrency
from app.api.v1.endpoints.telephony_runtime import router as runtime_router

NON_ADMIN_ROLES = ["user", "agent", "billing_user", "readonly"]
LEASE_ID = uuid4()
RUNTIME_ROUTES = [
    ("POST", "/telephony/sip/runtime/compile/preview", None),
    ("POST", "/telephony/sip/runtime/activate", {}),
    ("POST", "/telephony/sip/runtime/rollback", {}),
    ("GET", "/telephony/sip/runtime/versions", None),
    ("GET", "/telephony/sip/runtime/metrics/activation", None),
]
LEASE_ROUTES = [
    (
        "POST",
        "/telephony/sip/runtime/concurrency/leases/acquire",
        {"call_id": str(uuid4()), "talklee_call_id": "synthetic-call", "lease_kind": "call"},
    ),
    ("POST", f"/telephony/sip/runtime/concurrency/leases/{LEASE_ID}/release", {"reason": "finished"}),
    ("POST", f"/telephony/sip/runtime/concurrency/leases/{LEASE_ID}/heartbeat", None),
    ("POST", "/telephony/sip/runtime/concurrency/leases/expire", None),
]


class _Conn:
    @asynccontextmanager
    async def transaction(self):
        yield


class _ExplodingLimiter:
    """Any call proves the admin gate let the request through."""

    def __getattr__(self, name):
        raise AssertionError(f"limiter.{name} reached by a non-admin")


class _StatusLimiter:
    async def get_status(self, conn, *, tenant_id):
        return {
            "tenant_id": tenant_id,
            "active_calls": 1,
            "active_transfers": 0,
            "max_active_calls": 5,
            "max_transfer_inflight": 1,
            "lease_ttl_seconds": 60,
            "heartbeat_grace_seconds": 15,
            "policy_name": "default",
        }


def _app(role: str, monkeypatch, limiter=None) -> FastAPI:
    principal = dependencies.CurrentUser(
        id=str(uuid4()), email="synthetic@example.invalid", tenant_id=str(uuid4()), role=role
    )

    @asynccontextmanager
    async def scoped_pool(*_args, **_kwargs):
        yield _Conn()

    async def rls(*_args, **_kwargs):
        return None

    monkeypatch.setattr(concurrency, "acquire_with_tenant", scoped_pool)
    monkeypatch.setattr(concurrency, "apply_tenant_rls_context", rls)
    app = FastAPI()
    app.include_router(runtime_router)
    app.include_router(concurrency.router)
    app.dependency_overrides[dependencies.get_current_user] = lambda: principal
    app.dependency_overrides[dependencies.get_db_pool] = lambda: object()
    app.dependency_overrides[concurrency._get_concurrency_limiter] = lambda: limiter or _ExplodingLimiter()
    return app


async def _call(app, method, path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        return await client.request(method, path, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.parametrize("method,path,body", RUNTIME_ROUTES + LEASE_ROUTES)
async def test_non_admin_cannot_use_runtime_or_lease_routes(role, method, path, body, monkeypatch):
    response = await _call(_app(role, monkeypatch), method, path, body)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", LEASE_ROUTES)
async def test_tenant_admin_passes_the_gate_on_lease_routes(method, path, body, monkeypatch):
    # The exploding limiter is reached only after the gate: AssertionError
    # (surfacing as an exception from the ASGI app) proves it was not a 403.
    with pytest.raises(AssertionError, match="reached by a non-admin"):
        await _call(_app("tenant_admin", monkeypatch), method, path, body)


@pytest.mark.asyncio
async def test_lease_status_stays_readable_by_readonly(monkeypatch):
    app = _app("readonly", monkeypatch, limiter=_StatusLimiter())
    response = await _call(app, "GET", "/telephony/sip/runtime/concurrency/status", None)
    assert response.status_code == 200, response.text
    assert response.json()["active_calls"] == 1
