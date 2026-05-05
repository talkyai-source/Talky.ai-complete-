from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.config import get_settings
from app.core.security_headers_middleware import SecurityHeadersMiddleware


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _request(path: str = "/api/v1/auth/login") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_security_headers_remove_server_headers_without_pop():
    middleware = SecurityHeadersMiddleware(FastAPI())

    async def call_next(request: Request):
        return JSONResponse(
            {"ok": True},
            headers={"Server": "test-server", "X-Powered-By": "test-framework"},
        )

    response = await middleware.dispatch(_request(), call_next)

    assert response.status_code == 200
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
