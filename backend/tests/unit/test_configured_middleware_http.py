"""Actual configured ASGI wrappers; synthetic endpoint/Redis/session ports only.

No lifespan, database, provider, or external socket is started. These cases
isolate response visibility from endpoint authorization (tested separately).
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import socket
import uuid

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.responses import StreamingResponse

from app.api.v1.endpoints.admin.tenants import TenantCreateRequest, TenantVersionRequest
from app.core import app_bootstrap, jwt_security, security_headers_middleware
from app.core.db import DatabasePoolTimeoutError
from app.core.request_id_middleware import get_request_id


ORIGIN = "https://admin.synthetic.example"
PATH = "/api/v1/admin/middleware-check"
REQUEST_ID = "61ea029e-b830-4330-ad5e-09cdfef3db49"


@pytest.fixture
async def configured_app(monkeypatch):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Configured middleware tests must not open sockets")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    settings = SimpleNamespace(
        allowed_origins=[ORIGIN], environment="production",
        effective_jwt_secret="synthetic-configured-middleware-secret-only",
        jwt_algorithm="HS256",
    )
    monkeypatch.setattr(app_bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(security_headers_middleware, "get_settings", lambda: settings)
    monkeypatch.setattr(jwt_security, "get_settings", lambda: settings)
    app = FastAPI()
    app.state.endpoint_calls = 0
    app.state.request_started = asyncio.Event()

    @app.api_route(PATH, methods=["GET", "POST", "PATCH"])
    async def sentinel(failure: str | None = None):
        app.state.endpoint_calls += 1
        if failure == "http":
            raise HTTPException(403, "Synthetic endpoint permission denial")
        if failure == "database":
            raise DatabasePoolTimeoutError("Synthetic pool timeout")
        if failure == "unhandled":
            raise RuntimeError("Synthetic private failure detail")
        if failure == "cancelled":
            app.state.request_started.set()
            await asyncio.Event().wait()
        if failure == "stream":
            async def chunks():
                yield b"confirmed-prefix"
                raise RuntimeError("Synthetic late stream failure")
            return StreamingResponse(chunks(), media_type="text/plain")
        return {"request_id": get_request_id()}

    @app.post(PATH + "/tenant-create")
    async def validate_tenant_create(body: TenantCreateRequest):
        app.state.endpoint_calls += 1
        return {"valid": True}

    @app.post(PATH + "/tenant-version")
    async def validate_tenant_version(body: TenantVersionRequest):
        app.state.endpoint_calls += 1
        return {"valid": True}

    app_bootstrap.configure_middleware(app)
    try:
        yield app
    finally:
        # Windows creates the asyncio self-pipe using loopback sockets. Keep
        # the prohibition within the already-running test loop, not loop setup.
        monkeypatch.undo()


async def request(app, method="GET", *, headers=None, query="", remote=False, path=PATH, body=None):
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False,
        client=("203.0.113.10" if remote else "127.0.0.1", 43210),
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://api.synthetic.example") as client:
        return await client.request(method, path + query, headers=headers or {},
                                    **({"json": body} if body is not None else {}))


def assert_response_wrappers(response, *, allowed_origin=True, request_id=REQUEST_ID):
    assert response.headers.get("x-request-id") == request_id
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-xss-protection") == "0"
    assert "default-src 'none'" in response.headers.get("content-security-policy", "")
    assert response.headers.get("strict-transport-security", "").startswith("max-age=31536000")
    if allowed_origin:
        assert response.headers.get("access-control-allow-origin") == ORIGIN
        assert response.headers.get("access-control-allow-credentials") == "true"
        assert "origin" in response.headers.get("vary", "").lower()
        # Preflight does not have expose-headers; actual responses must.
        if "access-control-allow-methods" not in response.headers:
            assert "x-request-id" in response.headers.get("access-control-expose-headers", "").lower()
    else:
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("extra,method,status,detail", [
    ({"Authorization": "Bearer definitely-not-a-jwt"}, "GET", 401, "Invalid authentication token"),
    ({"User-Agent": "sqlmap"}, "GET", 403, "Access denied"),
    ({"Content-Length": "10485761"}, "POST", 413, "Request body too large"),
    ({"Content-Type": "application/octet-stream"}, "POST", 415, "not supported"),
])
async def test_early_security_denials_are_readable_without_reaching_endpoint(configured_app, extra, method, status, detail):
    response = await request(configured_app, method, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID, **extra})
    assert response.status_code == status
    assert detail in response.json()["detail"]
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response)


async def test_auth_configuration_unavailable_is_readable(configured_app, monkeypatch):
    monkeypatch.setattr(jwt_security, "_require_secret", lambda: (_ for _ in ()).throw(jwt_security.JWTValidationError("Synthetic auth unavailable", 503)))
    response = await request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID, "Authorization": "Bearer invalid"})
    assert response.status_code == 503
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response)


async def test_global_rate_limit_is_readable_and_preserves_retry_after(configured_app, monkeypatch):
    from app.core import container
    from app.core.security import api_security
    limiter = SimpleNamespace(check_all_tiers=AsyncMock(return_value=(False, {"Retry-After": "17"}, "Synthetic limit reached")))
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=True, redis_enabled=True, redis=object()))
    monkeypatch.setattr(api_security, "get_api_rate_limiter", lambda redis: limiter)
    response = await request(configured_app, remote=True, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    limiter.check_all_tiers.assert_awaited_once()
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response)


async def test_session_reverification_denial_is_readable(configured_app, monkeypatch):
    from app.core import db_utils, session_security_middleware as sessions
    @asynccontextmanager
    async def synthetic_session_lookup(pool, tenant):
        yield object()
    monkeypatch.setattr(sessions, "get_db_pool_from_container", lambda: object())
    monkeypatch.setattr(db_utils, "acquire_with_tenant", synthetic_session_lookup)
    validator = AsyncMock(return_value={"requires_verification": True})
    monkeypatch.setattr(sessions, "validate_session", validator)
    response = await request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID, "Cookie": f"{sessions.SESSION_COOKIE_NAME}=synthetic-session"})
    assert response.status_code == 403
    assert response.json()["code"] == "SESSION_VERIFICATION_REQUIRED"
    assert response.headers["www-authenticate"] == "Bearer"
    validator.assert_awaited_once()
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response)


@pytest.mark.parametrize("failure,status", [("http", 403), ("database", 503), ("unhandled", 500)])
async def test_endpoint_errors_keep_correlation_and_browser_visibility(configured_app, failure, status):
    response = await request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID}, query=f"?failure={failure}")
    assert response.status_code == status
    assert response.json()["error"]["request_id"] == REQUEST_ID
    assert "Synthetic private failure detail" not in response.text
    assert_response_wrappers(response)
    if failure == "database":
        assert response.headers["retry-after"] == "5"


@pytest.mark.parametrize("origin,method,header,status", [
    (ORIGIN, "PATCH", "X-Talky-Expected-User, X-Talky-Expected-Tenant, X-CSRF-Token, Authorization", 200),
    ("https://denied.synthetic.example", "PATCH", "Authorization", 400),
    (ORIGIN, "TRACE", "Authorization", 400),
    (ORIGIN, "PATCH", "X-Forbidden-Header", 400),
])
async def test_actual_stack_preflight_policy(configured_app, origin, method, header, status):
    response = await request(configured_app, "OPTIONS", headers={"Origin": origin, "X-Request-ID": REQUEST_ID, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": header})
    assert response.status_code == status
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response, allowed_origin=origin == ORIGIN)


@pytest.mark.parametrize("origin", [None, "https://denied.synthetic.example", "null"])
async def test_cookie_write_csrf_denial_not_weakened_by_cors(configured_app, origin):
    headers = {"X-Request-ID": REQUEST_ID, "Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    response = await request(configured_app, "POST", headers=headers)
    assert response.status_code == 403
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response, allowed_origin=False)


async def test_success_uses_same_scoped_id_and_does_not_leak_context(configured_app):
    response = await request(configured_app, "POST", headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID, "Content-Type": "application/json"})
    assert response.status_code == 200
    assert response.json()["request_id"] == REQUEST_ID
    assert_response_wrappers(response)
    assert get_request_id() == "-"
    next_response = await request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": "unsafe request identifier"})
    generated = next_response.headers["x-request-id"]
    assert str(uuid.UUID(generated)) == generated
    assert generated != REQUEST_ID
    assert next_response.json()["request_id"] == generated
    assert get_request_id() == "-"


async def test_denied_origin_cannot_read_even_early_auth_errors(configured_app):
    response = await request(configured_app, headers={"Origin": "https://denied.synthetic.example", "X-Request-ID": REQUEST_ID, "Authorization": "Bearer invalid"})
    assert response.status_code == 401
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response, allowed_origin=False)


async def test_unhandled_failure_is_logged_once_and_still_raised_to_server(configured_app, caplog):
    transport = httpx.ASGITransport(app=configured_app, raise_app_exceptions=True, client=("127.0.0.1", 43210))
    async with httpx.AsyncClient(transport=transport, base_url="https://api.synthetic.example") as client:
        with pytest.raises(RuntimeError, match="Synthetic private failure detail"):
            await client.get(PATH + "?failure=unhandled", headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID})
    logs = [record for record in caplog.records if record.name == "app.core.error_handlers" and record.message == "Unhandled exception in request handler"]
    assert len(logs) == 1
    assert get_request_id() == "-"


async def test_late_stream_failure_does_not_replace_already_started_response(configured_app, caplog):
    response = await request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID}, query="?failure=stream")
    assert response.status_code == 200
    assert response.text == "confirmed-prefix"
    assert "internal_error" not in response.text
    assert_response_wrappers(response)
    logs = [record for record in caplog.records if record.name == "app.core.error_handlers" and record.message == "Unhandled exception in request handler"]
    assert len(logs) == 1
    assert get_request_id() == "-"


async def test_cancelled_request_is_not_converted_to_success_or_json_failure(configured_app, caplog):
    pending = asyncio.create_task(request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID}, query="?failure=cancelled"))
    await asyncio.wait_for(configured_app.state.request_started.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not [record for record in caplog.records if record.name == "app.core.error_handlers"]
    assert get_request_id() == "-"


async def test_failure_inside_security_guard_is_still_correlated(configured_app, monkeypatch):
    from app.core.api_security_middleware import APISecurityMiddleware
    def failed_guard(*args):
        raise RuntimeError("Synthetic middleware failure")
    monkeypatch.setattr(APISecurityMiddleware, "_is_exempt_path", failed_guard)
    response = await request(configured_app, headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID})
    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == REQUEST_ID
    assert "Synthetic middleware failure" not in response.text
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response)


@pytest.mark.parametrize("route,payload,field,kind,message", [
    ("tenant-create", {"business_name": "   "}, "business_name", "value_error", "business_name must not be blank"),
    ("tenant-version", {"expected_updated_at": "2026-10-21T10:00:00"}, "expected_updated_at", "value_error", "must include a timezone"),
    ("tenant-create", {"business_name": "Synthetic tenant", "plan_id": {"password": "synthetic-rejected-credential-marker"}},
     "plan_id", "string_type", "Input should be a valid string"),
    ("tenant-version", {}, "expected_updated_at", "missing", "Field required"),
])
async def test_validation_details_are_safe_and_readable_through_actual_stack(configured_app, route, payload, field, kind, message):
    response = await request(configured_app, "POST", path=PATH + "/" + route, body=payload,
                             headers={"Origin": ORIGIN, "X-Request-ID": REQUEST_ID})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Request payload failed validation"
    assert error["request_id"] == REQUEST_ID
    assert len(error["details"]) == 1
    detail = error["details"][0]
    assert set(detail) == {"type", "loc", "msg"}
    assert detail["type"] == kind and detail["loc"] == ["body", field]
    assert message in detail["msg"]
    assert "synthetic-rejected-credential-marker" not in response.text
    assert configured_app.state.endpoint_calls == 0
    assert_response_wrappers(response)
    assert get_request_id() == "-"
