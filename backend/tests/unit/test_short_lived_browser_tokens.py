"""Real signed tokens; fake session storage, no environment files/providers."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI

from app.core.config import get_settings
from app.core.jwt_security import JWTValidationError, decode_and_validate_token, encode_access_token


SECRET = "synthetic-round3-browser-token-signing-secret-only-123456"


@pytest.fixture(autouse=True)
def token_settings(monkeypatch):
    for key, value in {"JWT_SECRET": SECRET, "JWT_ALGORITHM": "HS256",
                       "JWT_EXPIRY_HOURS": "24", "JWT_LEEWAY_SECONDS": "60",
                       "AUTH_SHORT_LIVED_TOKENS_REQUIRED": "true"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    monkeypatch.delenv("JWT_AUDIENCE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def token(**kwargs):
    return encode_access_token(user_id="user-a", email="synthetic@example.invalid",
                               role="user", tenant_id="tenant-a", session_id="session-a", **kwargs)


def signed(*, age=0, lifetime=900, **overrides):
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"sub": "user-a", "sid": "session-a", "tenant_id": "tenant-a",
              "iat": now - age, "exp": now - age + lifetime, **overrides}
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.mark.parametrize("ttl,expected", [(None, 900), (timedelta(hours=2), 900),
                                         (timedelta(minutes=5), 300)])
def test_enabled_policy_caps_all_access_tokens(ttl, expected):
    claims = decode_and_validate_token(token(ttl=ttl))
    assert claims["exp"] - claims["iat"] == expected


def test_enabled_policy_caps_actual_login_body_helper():
    from app.api.v1.endpoints.auth._shared import create_jwt
    claims = decode_and_validate_token(create_jwt("user-a", "synthetic@example.invalid", "user",
                                                 "tenant-a", "session-a"))
    assert claims["exp"] - claims["iat"] == 900


def test_default_off_preserves_existing_body_token_contract(monkeypatch):
    monkeypatch.delenv("AUTH_SHORT_LIVED_TOKENS_REQUIRED")
    get_settings.cache_clear()
    claims = decode_and_validate_token(token())
    assert claims["exp"] - claims["iat"] == 86400


def test_cutover_rejects_existing_long_lived_tokens():
    with pytest.raises(JWTValidationError):
        decode_and_validate_token(signed(lifetime=86400))


def test_enabled_policy_does_not_extend_expiry_with_clock_leeway():
    with pytest.raises(JWTValidationError):
        decode_and_validate_token(signed(age=901))


def test_enabled_policy_does_not_start_validity_before_issued_at():
    with pytest.raises(JWTValidationError):
        decode_and_validate_token(signed(age=-30))


@pytest.mark.parametrize("claims", [{"iat": float("nan")}, {"exp": float("inf")},
                                   {"iat": True}, {"exp": "9999999999"}])
def test_enabled_policy_requires_finite_numeric_dates(claims):
    with pytest.raises(JWTValidationError):
        decode_and_validate_token(signed(**claims))


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["bearer", "access_cookie"])
async def test_fresh_signed_token_cannot_reuse_a_revoked_session(monkeypatch, transport):
    from app.api.v1 import dependencies
    from app.core.security import principal

    @asynccontextmanager
    async def connection(*args):
        yield object()

    sessions = AsyncMock(return_value=None)
    load_principal = AsyncMock(side_effect=AssertionError("revoked session reached principal"))
    monkeypatch.setattr(dependencies, "get_db_client", lambda: SimpleNamespace(pool=object()))
    monkeypatch.setattr(dependencies, "acquire_with_tenant", connection)
    monkeypatch.setattr(dependencies, "get_session_by_id", sessions)
    monkeypatch.setattr(principal, "load_current_principal", load_principal)
    app = FastAPI()

    @app.get("/private")
    async def private(user=Depends(dependencies.get_current_user)):
        return {"id": user.id}

    credentials = {"headers": {"Authorization": f"Bearer {token()}"}} if transport == "bearer" else {
        "cookies": {"talky_at": token()}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.get("/private", **credentials)
    assert response.status_code == 401
    sessions.assert_awaited_once()
    assert sessions.await_args.args[1] == "session-a"
    assert sessions.await_args.kwargs == {"user_id": "user-a"}
    load_principal.assert_not_awaited()
