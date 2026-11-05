"""F2-05: real HTTP routes bound pool waits without changing auth or transactions."""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import dependencies
from app.api.v1.endpoints import calls, sessions, telephony_bridge, telephony_providers
from app.api.v1.endpoints.rbac import permissions, roles
from app.core import container, db
from app.core.error_handlers import db_pool_timeout_exception_handler


TENANT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
CALL = "33333333-3333-4333-8333-333333333333"


class Connection:
    def __init__(self):
        self.events = []
        self.row = None

    @asynccontextmanager
    async def transaction(self):
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    async def execute(self, sql, *args):
        self.events.append(sql)
        return "OK"

    async def fetch(self, sql, *args):
        self.events.append(sql)
        return []

    async def fetchval(self, sql, *args):
        self.events.append(sql)
        return 0

    async def fetchrow(self, sql, *args):
        self.events.append(sql)
        return self.row


class Pool:
    def __init__(self):
        self.slot = asyncio.Semaphore(1)
        self.conn = Connection()
        self.attempts = 0
        self.active = 0

    @asynccontextmanager
    async def acquire(self):
        self.attempts += 1
        await self.slot.acquire()
        self.active += 1
        try:
            yield self.conn
        finally:
            self.active -= 1
            self.slot.release()


ROUTES = [
    ("sessions", "GET", "/api/v1/sessions/active", 200),
    ("roles", "GET", "/api/v1/rbac/roles", 200),
    ("permissions", "GET", "/api/v1/rbac/permissions", 200),
    ("calls", "GET", "/api/v1/calls/", 200),
    ("calls", "GET", f"/api/v1/calls/{CALL}", 404),
    ("providers", "GET", "/api/v1/telephony/providers", 200),
    ("bridge", "POST", f"/api/v1/sip/telephony/hangup/{CALL}", 403),
]


def mounted_app(monkeypatch, pool, family, *, authenticated=True):
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 0.02)
    app = FastAPI()
    app.add_exception_handler(db.DatabasePoolTimeoutError, db_pool_timeout_exception_handler)
    user = SimpleNamespace(id=USER, tenant_id=TENANT, role="tenant_admin")
    auth_calls = []

    async def authenticated_user():
        auth_calls.append(True)
        return user

    app.dependency_overrides[dependencies.get_db_client] = lambda: SimpleNamespace(pool=pool)
    app.dependency_overrides[dependencies.get_db_pool] = lambda: pool
    if authenticated:
        app.dependency_overrides[dependencies.get_current_user] = authenticated_user
        app.dependency_overrides[dependencies.require_admin] = authenticated_user
        app.dependency_overrides[calls._require_calls_read] = authenticated_user
    routers = {
        "sessions": sessions.router, "roles": roles.router,
        "permissions": permissions.router, "calls": calls.router,
        "providers": telephony_providers.router, "bridge": telephony_bridge.router,
    }
    prefix = "/api/v1/rbac" if family in {"roles", "permissions"} else "/api/v1"
    app.include_router(routers[family], prefix=prefix)
    if family == "bridge":
        async def authorized_control(request):
            auth_calls.append(True)
            return telephony_bridge.CallerContext(is_internal=False, tenant_id=TENANT)

        monkeypatch.setattr(telephony_bridge, "_require_call_control", authorized_control)
        monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(
            is_initialized=True, db_pool=pool,
        ))
    return app, auth_calls


@pytest.mark.parametrize("family,method,path,recovered_status", ROUTES)
async def test_authenticated_routes_timeout_then_recover_without_skipping_ownership(
    monkeypatch, family, method, path, recovered_status,
):
    pool = Pool()
    app, auth_calls = mounted_app(monkeypatch, pool, family)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://synthetic",
    ) as client:
        async with pool.acquire():
            response = await asyncio.wait_for(client.request(method, path), 0.5)
            assert auth_calls, "Route must finish authentication before the saturated DB operation"
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "5"
            assert response.json()["error"]["code"] == "service_unavailable"
            assert pool.conn.events == [], "An expired acquisition must not execute SQL"
        response = await asyncio.wait_for(client.request(method, path), 0.5)
    assert response.status_code == recovered_status
    assert pool.active == 0
    if family in {"calls", "providers", "bridge"}:
        assert pool.conn.events[0] == "begin"
        assert any("SET LOCAL app.bypass_rls" in sql for sql in pool.conn.events)
    else:
        assert "begin" not in pool.conn.events, "Acquisition must not add an RLS/transaction wrapper"
    if family == "bridge":
        assert response.json()["detail"]["error"] == "call_not_owned"


@pytest.mark.parametrize("family,path", [
    ("sessions", "/api/v1/sessions/active"),
    ("roles", "/api/v1/rbac/roles"),
    ("providers", "/api/v1/telephony/providers"),
])
async def test_unauthenticated_routes_still_reject_before_pool_wait(monkeypatch, family, path):
    pool = Pool()
    app, _ = mounted_app(monkeypatch, pool, family, authenticated=False)
    async with pool.acquire():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://synthetic",
        ) as client:
            response = await asyncio.wait_for(client.get(path), 0.5)
        assert response.status_code == 401
        assert pool.attempts == 1
        assert pool.conn.events == []


async def test_campaign_hangup_timeout_retains_truthful_failure_summary(monkeypatch):
    pool = Pool()
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 0.02)
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=pool))
    async with pool.acquire():
        result = await asyncio.wait_for(telephony_bridge.hangup_calls_for_campaign(CALL), 0.5)
    assert result["status"] == "lookup_failed"
    assert result["lookup_error"] == "database_lookup_failed"
    assert pool.conn.events == []
    assert pool.active == 0


async def test_provider_result_survives_bounded_best_effort_persistence(monkeypatch):
    pool = Pool()
    pool.conn.row = {"credentials_encrypted": "synthetic", "from_number": None}
    app, _ = mounted_app(monkeypatch, pool, "providers")
    monkeypatch.setattr(telephony_providers, "get_encryption_service", lambda: SimpleNamespace(
        decrypt=lambda value: json.dumps({"account_sid": "synthetic", "auth_token": "synthetic"}),
    ))
    blocker = None
    checks = []

    async def synthetic_provider_check():
        nonlocal blocker
        checks.append(True)
        blocker = pool.acquire()
        await blocker.__aenter__()
        return {"ok": True, "latency_ms": 1}

    adapter_module = ModuleType("app.infrastructure.telephony.twilio_provider_adapter")
    adapter_module.TwilioProviderAdapter = lambda **kwargs: SimpleNamespace(
        ping_with_detail=synthetic_provider_check,
    )
    monkeypatch.setitem(sys.modules, adapter_module.__name__, adapter_module)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://synthetic",
        ) as client:
            response = await asyncio.wait_for(client.post("/api/v1/telephony/providers/twilio/test"), 0.5)
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["check_scope"] == "provider_account"
        assert checks == [True]
    finally:
        if blocker is not None:
            await blocker.__aexit__(None, None, None)
    assert pool.active == 0
