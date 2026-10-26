"""Actual MFA HTTP responses with synthetic factor/session/database ports.

Cookie issuance, header serialization and the success/rejection branches are
real. These cases do not claim browser cookie acceptance or MFA cryptography.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from importlib import import_module
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.auth import _shared as auth_shared
from app.core.security import cookies
from app.core.security.sessions import SESSION_LIFETIME_HOURS


mfa = import_module("app.api.v1.endpoints.mfa.verify")
USER_ID = "528491ab-bffb-4491-8128-270b3c78a437"
SESSION_ID = "53121621-64a8-408d-8d3f-0a3580851609"


@pytest.fixture
async def mfa_http(monkeypatch):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("MFA cookie cases must not open network sockets")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)

    @asynccontextmanager
    async def transaction():
        yield

    conn = SimpleNamespace(
        transaction=transaction,
        execute=AsyncMock(),
        fetchrow=AsyncMock(return_value={"totp_secret_enc": "synthetic", "enabled": True, "last_used_at": None}),
    )

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield conn

    monkeypatch.setattr(mfa, "acquire_with_tenant", acquire)
    monkeypatch.setattr(mfa, "lock_user_credential_issuance", AsyncMock())
    monkeypatch.setattr(mfa, "resolve_mfa_challenge", AsyncMock(return_value={"id": "challenge", "user_id": USER_ID}))
    monkeypatch.setattr(mfa, "load_current_principal", AsyncMock(return_value={
        "id": USER_ID, "email": "cookie-fixture@example.invalid", "role": "platform_admin",
        "tenant_id": None, "business_name": None,
    }))
    monkeypatch.setattr(mfa, "check_account_locked", AsyncMock(return_value=None))
    monkeypatch.setattr(mfa, "decrypt_totp_secret", lambda value: "synthetic-factor")
    totp = lambda *args, **kwargs: datetime(2026, 10, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(mfa, "verify_totp_step", totp)
    recovery = AsyncMock(return_value="recovery-row")
    monkeypatch.setattr(mfa, "verify_recovery_code_returning_id", recovery)
    consume = AsyncMock(return_value=True)
    monkeypatch.setattr(mfa, "mark_recovery_code_used", consume)
    monkeypatch.setattr(mfa, "consume_mfa_challenge", AsyncMock(return_value=True))
    monkeypatch.setattr(mfa, "record_failed_mfa_attempt", AsyncMock(return_value=1))
    monkeypatch.setattr(mfa, "record_login_attempt", AsyncMock())
    session = AsyncMock(return_value=("synthetic-session-token", SESSION_ID))
    monkeypatch.setattr(mfa, "create_session", session)
    monkeypatch.setattr(mfa, "_encode_access_token", lambda **kwargs: "synthetic-body-access")
    monkeypatch.setattr(auth_shared, "encode_access_token", lambda **kwargs: "synthetic-cookie-access")
    refresh = AsyncMock(return_value=("synthetic-refresh", UUID(SESSION_ID), UUID(SESSION_ID)))
    monkeypatch.setattr(auth_shared, "issue_initial_refresh_token", refresh)
    meter = AsyncMock(return_value=SimpleNamespace(allowance=lambda: {}))
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status", meter)
    app = FastAPI()
    app.include_router(mfa.router, prefix="/api/v1/auth/mfa")
    app.dependency_overrides[mfa.get_db_client] = lambda: SimpleNamespace(pool=object())
    try:
        yield SimpleNamespace(app=app, conn=conn, session=session, refresh=refresh, recovery=recovery, consume=consume)
    finally:
        # Restore before Windows asyncio's next event loop creates its self-pipe.
        monkeypatch.undo()


def cookie_metadata(response):
    observed = {}
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        for name, morsel in parsed.items():
            assert name not in observed, "A login must not emit competing cookie variants"
            observed[name] = {
                "secure": bool(morsel["secure"]), "httponly": bool(morsel["httponly"]),
                "samesite": morsel["samesite"], "path": morsel["path"],
                "domain": morsel["domain"], "max_age": int(morsel["max-age"]),
            }
    return observed


async def verify(application, factor):
    payload = {"challenge_token": "synthetic-challenge", factor: "123456" if factor == "code" else "synthetic-recovery"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="https://api.example.invalid") as client:
        return await client.post("/api/v1/auth/mfa/verify", json=payload)


@pytest.mark.parametrize("factor", ["code", "recovery_code"], ids=["totp", "recovery"])
@pytest.mark.parametrize("environment,samesite,override,expected_site,expected_secure", [
    ("test", "strict", None, "strict", False),
    ("production", "strict", None, "strict", True),
    ("test", "none", "false", "none", True),
    ("production", "none", None, "none", True),
    ("test", "lax", None, "lax", False),
    ("test", "invalid-policy", None, "strict", False),
], ids=["local-strict", "production-strict", "none-forces-secure", "production-none", "local-lax", "invalid-fallback"])
async def test_mfa_success_emits_configured_cookie_policy(
    mfa_http, monkeypatch, factor, environment, samesite, override, expected_site, expected_secure,
):
    settings = SimpleNamespace(environment=environment)
    monkeypatch.setattr(auth_shared, "get_settings", lambda: settings)
    monkeypatch.setattr(cookies, "get_settings", lambda: settings)
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", samesite)
    monkeypatch.setenv("AUTH_COOKIE_DOMAIN", "example.invalid")
    if override is None:
        monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    else:
        monkeypatch.setenv("SESSION_COOKIE_SECURE", override)

    response = await verify(mfa_http.app, factor)

    assert response.status_code == 200
    assert response.json()["mfa_verified"] is True
    common = {"secure": expected_secure, "httponly": True, "samesite": expected_site}
    assert cookie_metadata(response) == {
        "talky_sid": {**common, "path": "/", "domain": "", "max_age": SESSION_LIFETIME_HOURS * 3600},
        "talky_at": {**common, "path": "/api/v1", "domain": "example.invalid", "max_age": 900},
        "talky_rt": {**common, "path": "/api/v1/auth", "domain": "example.invalid", "max_age": 604800},
    }
    mfa_http.session.assert_awaited_once()
    mfa_http.refresh.assert_awaited_once()
    if factor == "recovery_code":
        mfa_http.consume.assert_awaited_once_with(mfa_http.conn, "recovery-row")
    else:
        mfa_http.consume.assert_not_awaited()


@pytest.mark.parametrize("factor", ["code", "recovery_code"], ids=["totp", "recovery"])
async def test_rejected_factor_does_not_issue_any_auth_cookie(mfa_http, monkeypatch, factor):
    monkeypatch.setattr(mfa, "verify_totp_step", lambda *args, **kwargs: None)
    mfa_http.recovery.return_value = None

    response = await verify(mfa_http.app, factor)

    assert response.status_code == 401
    assert response.headers.get_list("set-cookie") == []
    mfa_http.session.assert_not_awaited()
    mfa_http.refresh.assert_not_awaited()
