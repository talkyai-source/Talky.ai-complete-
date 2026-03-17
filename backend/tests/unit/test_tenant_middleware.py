from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.config import get_settings
from app.core.tenant_middleware import TenantMiddleware


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET",
        "unit-test-secret-with-minimum-length-32-bytes-0001",
    )
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _request(path: str, token: str | None) -> Request:
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode("utf-8")))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _invalid_signature_token() -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-1",
        "tenant_id": "tenant-1",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    return jwt.encode(
        payload,
        "wrong-secret-with-minimum-length-32-bytes-0001",
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_invalid_token_does_not_break_public_api_health():
    middleware = TenantMiddleware(FastAPI())
    request = _request("/api/v1/health", _invalid_signature_token())

    async def call_next(req: Request):
        return JSONResponse({"status": "healthy", "tenant_id": getattr(req.state, "tenant_id", None)})

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_invalid_token_returns_401_not_500():
    middleware = TenantMiddleware(FastAPI())
    request = _request("/api/v1/protected", _invalid_signature_token())

    async def call_next(req: Request):
        return JSONResponse({"tenant_id": getattr(req.state, "tenant_id", None)})

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 401
    assert response.body is not None
