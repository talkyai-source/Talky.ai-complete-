"""Unit tests for the Origin-based CSRF defence (cookie auth).

The middleware is pure: it inspects method, path, Authorization header,
and Origin header. Tests run dispatch() directly with a stub call_next —
no TestClient (avoids the starlette/httpx version mismatch in this env).
"""
from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.core.security.csrf import CSRFMiddleware

ALLOWED = "https://app.talkleeai.com"


def _build_request(*, method: str, path: str, headers: dict[str, str] | None = None) -> Request:
    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "server": ("api.talkleeai.com", 443),
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 0),
    }
    return Request(scope)


async def _call_next_ok(_request: Request) -> Response:
    return Response("ok", status_code=200)


@pytest.fixture
def middleware() -> CSRFMiddleware:
    return CSRFMiddleware(app=None, allowed_origins=[ALLOWED])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_safe_methods_pass_without_origin(middleware):
    req = _build_request(method="GET", path="/api/v1/campaigns")
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unsafe_without_origin_rejected(middleware):
    req = _build_request(method="POST", path="/api/v1/campaigns")
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unsafe_with_disallowed_origin_rejected(middleware):
    req = _build_request(
        method="POST",
        path="/api/v1/campaigns",
        headers={"origin": "https://evil.com"},
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unsafe_with_allowed_origin_passes(middleware):
    req = _build_request(
        method="POST",
        path="/api/v1/campaigns",
        headers={"origin": ALLOWED},
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_bearer_auth_bypasses_csrf(middleware):
    req = _build_request(
        method="POST",
        path="/api/v1/campaigns",
        headers={"authorization": "Bearer eyJ.fake.jwt"},
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/v1/auth/login",
    "/api/v1/auth/signup/complete",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/passkey/authenticate/start",
])
async def test_auth_paths_exempt(middleware, path):
    req = _build_request(method="POST", path=path)
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


# ── Internal service-token exemption (dialer worker → originate) ──────

_TOKEN = "s3cret-internal-token-value"


@pytest.fixture
def middleware_with_token(monkeypatch) -> CSRFMiddleware:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    return CSRFMiddleware(app=None, allowed_origins=[ALLOWED])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_valid_internal_token_bypasses_csrf(middleware_with_token):
    req = _build_request(
        method="POST",
        path="/api/v1/sip/telephony/call",
        headers={"x-internal-service-token": _TOKEN},  # no Origin at all
    )
    resp = await middleware_with_token.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wrong_internal_token_is_rejected(middleware_with_token):
    req = _build_request(
        method="POST",
        path="/api/v1/sip/telephony/call",
        headers={"x-internal-service-token": "wrong"},
    )
    resp = await middleware_with_token.dispatch(req, _call_next_ok)
    assert resp.status_code == 403  # falls through to origin check, no origin


@pytest.mark.asyncio
async def test_internal_token_header_ignored_when_env_unset(monkeypatch):
    """Fail-safe: with no INTERNAL_SERVICE_TOKEN configured, presenting any
    token must NOT bypass CSRF."""
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    mw = CSRFMiddleware(app=None, allowed_origins=[ALLOWED])  # type: ignore[arg-type]
    req = _build_request(
        method="POST",
        path="/api/v1/sip/telephony/call",
        headers={"x-internal-service-token": _TOKEN},
    )
    resp = await mw.dispatch(req, _call_next_ok)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_empty_internal_token_header_does_not_bypass(middleware_with_token):
    req = _build_request(
        method="POST",
        path="/api/v1/sip/telephony/call",
        headers={"x-internal-service-token": ""},
    )
    resp = await middleware_with_token.dispatch(req, _call_next_ok)
    assert resp.status_code == 403
