"""QA F7-03/C2: real adapter catches must retain retryable database failures."""
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import iter_route_contexts

from app.api.v1 import dependencies
from app.api.v1.endpoints import analytics, campaigns
from app.api.v1.endpoints.admin import calls as admin_calls
from app.core import postgres_adapter as adapter
from app.core.error_handlers import register_error_handlers
from tests.unit.test_campaign_visibility_w5 import _patch_update_dependencies
from tests.unit.test_postgres_adapter import FakeConn


TENANT = "11111111-1111-4111-8111-111111111111"
CALL = "33333333-3333-4333-8333-333333333333"
DRIVER_ERRORS = (
    asyncpg.QueryCanceledError, asyncpg.LockNotAvailableError,
    asyncpg.ConnectionDoesNotExistError, asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)
ROUTES = (
    ("POST", "/campaigns/"),
    ("GET", "/analytics/best-time"),
    ("GET", "/analytics/retry-effectiveness"),
    ("GET", "/analytics/calls"),
    ("GET", "/analytics/calls/by-campaign"),
    ("GET", "/admin/calls/live"),
    ("GET", "/admin/calls/history"),
    ("GET", f"/admin/calls/{CALL}"),
)


def mounted_app(monkeypatch):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(campaigns.router)
    app.include_router(analytics.router)
    app.include_router(admin_calls.router, prefix="/admin")
    user = dependencies.CurrentUser(id=CALL, email="test@example.invalid",
                                    tenant_id=TENANT, role="platform_admin")
    client = adapter.Client(None)
    app.dependency_overrides[dependencies.get_db_client] = lambda: client
    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    app.dependency_overrides[dependencies.require_platform_admin] = lambda: user
    app.dependency_overrides[campaigns.rate_limit_dependency] = lambda: None
    app.dependency_overrides[campaigns.idempotency_dependency] = lambda: None
    # Authentication/permissions are already exercised elsewhere; the real
    # endpoint, QueryBuilder, asyncpg boundary and central handlers stay intact.
    for route in iter_route_contexts(app.routes):
        if getattr(route, "dependant", None):
            for dependency in route.dependant.dependencies:
                if getattr(dependency.call, "__name__", "") == "permission_checker":
                    app.dependency_overrides[dependency.call] = lambda: user
    _patch_update_dependencies(monkeypatch)
    monkeypatch.setattr(adapter, "_TABLE_COLUMN_TYPES_CACHE", {})
    return app


async def request(app, method, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json={
            "name": "Campaign", "persona_type": "lead_gen", "agent_names": ["Sarah"],
            "company_name": "Example", "voice_id": "voice-1",
        } if method == "POST" else None)


def assert_retryable(response):
    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == "5"
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "private database detail" not in response.text


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("error_type", DRIVER_ERRORS)
async def test_eight_routes_preserve_real_adapter_fetch_failure(monkeypatch, method, path, error_type):
    app = mounted_app(monkeypatch)
    conn = FakeConn()
    failure = error_type("private database detail")
    conn.fetch = AsyncMock(side_effect=failure)
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(adapter.asyncpg, "connect", connect)
    response = await request(app, method, path)
    assert conn.fetch.await_count == 1, "must traverse real adapter SQL/catch, without replay"
    assert conn.closed
    assert_retryable(response)


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("error_type", [ConnectionRefusedError, ConnectionResetError])
async def test_eight_routes_connect_socket_failures_are_retryable(monkeypatch, method, path, error_type):
    app = mounted_app(monkeypatch)
    connect = AsyncMock(side_effect=error_type("private database detail"))
    monkeypatch.setattr(adapter.asyncpg, "connect", connect)
    response = await request(app, method, path)
    assert connect.await_count == 1
    assert_retryable(response)


@pytest.mark.parametrize("wrapper", ["direct", "cause", "context", "runtime"])
@pytest.mark.parametrize("error_type", DRIVER_ERRORS[2:])
async def test_connection_error_type_survives_cause_and_context(error_type, wrapper):
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/fail")
    async def fail():
        try:
            raise error_type("private database detail")
        except Exception as exc:
            if wrapper == "cause":
                raise HTTPException(500, "Unavailable") from exc
            if wrapper == "context":
                raise HTTPException(500, "Unavailable")
            if wrapper == "runtime":
                raise RuntimeError("Unavailable") from exc
            raise

    assert_retryable(await request(app, "GET", "/fail"))


@pytest.mark.parametrize("error_type", [ValueError, FileNotFoundError, PermissionError])
async def test_other_adapter_errors_keep_the_returned_error_contract(monkeypatch, error_type):
    conn = FakeConn()
    conn.fetch = AsyncMock(side_effect=error_type("synthetic unrelated failure"))
    monkeypatch.setattr(adapter.asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(adapter, "_TABLE_COLUMN_TYPES_CACHE", {})
    result = await adapter.QueryBuilder(None, "calls")._execute_async()
    assert result.error == "synthetic unrelated failure"
    assert conn.closed


async def test_file_not_found_still_returns_500():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/fail")
    async def fail():
        raise FileNotFoundError("synthetic missing local file")

    response = await request(app, "GET", "/fail")
    assert response.status_code == 500
    assert "Retry-After" not in response.headers


@pytest.mark.parametrize("error_type", (*DRIVER_ERRORS, ConnectionRefusedError, ConnectionResetError))
async def test_rpc_adapter_connect_preserves_retryable_type(monkeypatch, error_type):
    failure = error_type("private database detail")
    monkeypatch.setattr(adapter.asyncpg, "connect", AsyncMock(side_effect=failure))
    with pytest.raises(error_type) as caught:
        await adapter.RpcBuilder(None, "increment_campaign_counter", {})._execute_async()
    assert caught.value is failure
