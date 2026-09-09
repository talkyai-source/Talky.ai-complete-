"""The session middleware must be able to SEE sessions.

Pinned root cause (2026-09-08): SessionSecurityMiddleware validated the
``talky_sid`` cookie on a raw ``pool.acquire()`` connection. ``validate_session``
joins ``user_profiles`` (forced RLS since 0038), and with no tenant GUC and no
bypass the join returned nothing — so every live session was "invalid", the
cookie was deleted on the first request after login, and idle timeout / device
binding / suspicious-session handling silently never ran for REST.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.core import session_security_middleware as mw


def _request(path="/api/v1/calls/live", cookie="talky_sid=rawtoken"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"cookie", cookie.encode()), (b"user-agent", b"ua")],
        "query_string": b"",
        "client": ("1.2.3.4", 1234),
        "scheme": "https",
        "server": ("api.test", 443),
    }
    return Request(scope)


class _Conn:
    pass


@pytest.mark.asyncio
async def test_validation_runs_on_a_bypass_connection_and_installs_session_state(monkeypatch):
    calls: list[dict] = []
    conn = _Conn()

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kwargs):
        calls.append({"pool": pool, "tenant_id": tenant_id})
        yield conn

    seen = {}

    async def fake_validate(c, raw, **kwargs):
        seen["conn"] = c
        seen["raw"] = raw
        return {"id": "sess-1", "user_id": "user-1", "is_suspicious": False, "requires_verification": False}

    pool = object()
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(mw, "get_db_pool_from_container", lambda: pool)
    monkeypatch.setattr(mw, "validate_session", fake_validate)
    monkeypatch.setattr(mw, "generate_device_fingerprint", lambda r: "v2:fp")

    request = _request()
    state = {}

    async def call_next(req):
        state["session_id"] = getattr(req.state, "session_id", None)
        state["session_user_id"] = getattr(req.state, "session_user_id", None)
        return Response("ok")

    middleware = mw.SessionSecurityMiddleware(app=None)
    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    # tenant_id=None is the bypass form — the lookup is bounded by the token
    # hash, never by a tenant the request has not established yet.
    assert calls == [{"pool": pool, "tenant_id": None}]
    assert seen == {"conn": conn, "raw": "rawtoken"}
    assert state == {"session_id": "sess-1", "session_user_id": "user-1"}


@pytest.mark.asyncio
async def test_a_rejected_cookie_is_logged_and_cleared(monkeypatch, caplog):
    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kwargs):
        yield _Conn()

    async def fake_validate(c, raw, **kwargs):
        return None

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(mw, "get_db_pool_from_container", lambda: object())
    monkeypatch.setattr(mw, "validate_session", fake_validate)
    monkeypatch.setattr(mw, "generate_device_fingerprint", lambda r: "v2:fp")

    async def call_next(req):
        return Response("ok")

    middleware = mw.SessionSecurityMiddleware(app=None)
    with caplog.at_level("INFO", logger="app.core.session_security_middleware"):
        response = await middleware.dispatch(_request(), call_next)

    assert response.status_code == 200
    assert any("session cookie rejected" in r.getMessage() for r in caplog.records)
    set_cookie = response.headers.get("set-cookie", "")
    assert "talky_sid=" in set_cookie and ("Max-Age=0" in set_cookie or "expires=" in set_cookie.lower())


def test_idle_timeout_is_env_overridable(monkeypatch):
    from app.core.security.sessions import _shared

    monkeypatch.delenv("SESSION_IDLE_TIMEOUT_MINUTES", raising=False)
    assert _shared._idle_timeout_minutes() == 30
    monkeypatch.setenv("SESSION_IDLE_TIMEOUT_MINUTES", "120")
    assert _shared._idle_timeout_minutes() == 120
    monkeypatch.setenv("SESSION_IDLE_TIMEOUT_MINUTES", "nonsense")
    assert _shared._idle_timeout_minutes() == 30


@pytest.mark.asyncio
async def test_the_request_runs_after_the_lookup_connection_is_released(monkeypatch):
    """2026-09-10 prod: 8 identical 500s/day. The idle-timeout revoke is an
    uncommitted UPDATE inside acquire_with_tenant's transaction; call_next ran
    inside that block, the endpoint's get_current_user tried to revoke the same
    row, waited on the lock and died on the asyncpg statement timeout. The
    cookie-clearing header was lost with the 500, so the browser looped."""
    events: list[str] = []

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kwargs):
        events.append("acquire")
        yield _Conn()
        events.append("release")

    async def fake_validate(c, raw, **kwargs):
        events.append("validate")
        return None  # idle timeout: revoked inside the (now closed) transaction

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(mw, "get_db_pool_from_container", lambda: object())
    monkeypatch.setattr(mw, "validate_session", fake_validate)
    monkeypatch.setattr(mw, "generate_device_fingerprint", lambda r: "v2:fp")

    async def call_next(req):
        events.append("call_next")
        return Response("ok")

    response = await mw.SessionSecurityMiddleware(app=None).dispatch(_request(), call_next)

    assert response.status_code == 200
    assert events == ["acquire", "validate", "release", "call_next"], events
    assert "talky_sid=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_a_lookup_failure_runs_the_request_exactly_once(monkeypatch, caplog):
    """Before: an exception raised while call_next ran inside the try block was
    swallowed as a 'middleware error' and call_next was invoked AGAIN."""
    count = {"call_next": 0}

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kwargs):
        yield _Conn()

    async def fake_validate(c, raw, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(mw, "get_db_pool_from_container", lambda: object())
    monkeypatch.setattr(mw, "validate_session", fake_validate)
    monkeypatch.setattr(mw, "generate_device_fingerprint", lambda r: "v2:fp")

    async def call_next(req):
        count["call_next"] += 1
        return Response("ok")

    with caplog.at_level("ERROR", logger="app.core.session_security_middleware"):
        response = await mw.SessionSecurityMiddleware(app=None).dispatch(_request(), call_next)

    assert response.status_code == 200
    assert count == {"call_next": 1}
    assert any("TimeoutError" in r.getMessage() for r in caplog.records), "the error is named, not blank"
