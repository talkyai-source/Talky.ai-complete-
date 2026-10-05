"""HTTP dependency boundaries with synthetic session/principal storage only."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.v1 import dependencies as dep
from app.core.security import principal


@pytest.fixture
def auth_boundary(monkeypatch):
    application = FastAPI()

    @application.get("/me")
    async def me(user=Depends(dep.get_current_user)):
        return {"id": user.id, "tenant_id": user.tenant_id, "role": user.role}

    @asynccontextmanager
    async def connection(pool, tenant_id):
        yield object()

    monkeypatch.setattr(dep, "acquire_with_tenant", connection)
    session = {"id": "session-a", "user_id": "user-a"}
    sessions = AsyncMock(return_value=session)
    cookies = AsyncMock(return_value=session)
    account = AsyncMock(return_value={"id": "user-a", "email": "user@example.com",
        "tenant_id": None, "role": "platform_admin", "name": "Synthetic", "business_name": None})
    client = Mock(return_value=SimpleNamespace(pool=object()))
    monkeypatch.setattr(dep, "get_db_client", client)
    monkeypatch.setattr(dep, "get_session_by_id", sessions)
    monkeypatch.setattr(dep, "_resolve_cookie_session", cookies)
    monkeypatch.setattr(principal, "load_current_principal", account)
    meter = AsyncMock(return_value=SimpleNamespace(allowance=lambda: {}))
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status", meter)
    return SimpleNamespace(app=application, client=client, sessions=sessions,
        cookies=cookies, account=account, meter=meter)


async def request(boundary, *, headers=None, cookies=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app),
                                 base_url="https://synthetic.example", cookies=cookies) as client:
        return await client.get("/me", headers=headers)


@pytest.mark.parametrize("header", [None, "InvalidFormat", "Bearer", "Basic invalid", "Bearer too many pieces", "Bearer bad.jwt"])
async def test_missing_or_invalid_credentials_reject_before_storage(auth_boundary, header):
    auth_boundary.client.side_effect = RuntimeError("synthetic container unavailable")

    response = await request(auth_boundary, headers={"Authorization": header} if header else None)

    assert response.status_code == 401
    auth_boundary.client.assert_not_called()
    auth_boundary.account.assert_not_awaited()


@pytest.mark.parametrize("transport", ["bearer", "access_cookie", "legacy_cookie"])
@pytest.mark.parametrize("failure", ["client", "session", "principal"])
async def test_valid_credentials_with_unavailable_verification_fail_closed(auth_boundary, monkeypatch, transport, failure):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a", "sid": "session-a"})
    if failure == "client":
        auth_boundary.client.side_effect = RuntimeError("synthetic container unavailable")
    elif failure == "session":
        auth_boundary.sessions.side_effect = ConnectionError("synthetic session database failure")
        auth_boundary.cookies.side_effect = ConnectionError("synthetic session database failure")
    else:
        auth_boundary.account.side_effect = TimeoutError("synthetic principal timeout")

    response = await request(auth_boundary,
        headers={"Authorization": "Bearer valid"} if transport == "bearer" else None,
        cookies={"talky_at": "valid"} if transport == "access_cookie" else
                {dep.SESSION_COOKIE_NAME: "valid"} if transport == "legacy_cookie" else None)

    assert response.status_code == 503
    assert response.json() == {"detail": "Account verification is temporarily unavailable"}
    auth_boundary.meter.assert_not_awaited()


@pytest.mark.parametrize("transport", ["bearer", "access_cookie", "legacy_cookie", "all_match"])
async def test_verified_matching_identity_paths_still_resolve(auth_boundary, monkeypatch, transport):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a", "sid": "session-a"})
    cookies = {}
    if transport in {"access_cookie", "all_match"}:
        cookies["talky_at"] = "valid"
    if transport in {"legacy_cookie", "all_match"}:
        cookies[dep.SESSION_COOKIE_NAME] = "valid"
    response = await request(auth_boundary, cookies=cookies,
        headers={"Authorization": "Bearer valid"} if transport in {"bearer", "all_match"} else None)

    assert response.status_code == 200
    assert response.json() == {"id": "user-a", "tenant_id": None, "role": "platform_admin"}
    auth_boundary.account.assert_awaited_once()
    auth_boundary.meter.assert_awaited_once()


async def test_conflicting_access_identities_do_not_reach_storage(auth_boundary, monkeypatch):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": token, "sid": "session-a"})
    response = await request(auth_boundary, headers={"Authorization": "Bearer user-a"}, cookies={"talky_at": "user-b"})

    assert response.status_code == 409
    auth_boundary.client.assert_not_called()
    auth_boundary.account.assert_not_awaited()


async def test_revoked_session_still_rejects_without_principal_fallback(auth_boundary, monkeypatch):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a", "sid": "session-a"})
    auth_boundary.sessions.return_value = None
    response = await request(auth_boundary, headers={"Authorization": "Bearer valid"})

    assert response.status_code == 401
    auth_boundary.account.assert_not_awaited()


async def test_invalid_access_cookie_rejects_before_storage(auth_boundary):
    auth_boundary.client.side_effect = RuntimeError("synthetic container unavailable")
    response = await request(auth_boundary, cookies={"talky_at": "bad.jwt"})
    assert response.status_code == 401
    auth_boundary.client.assert_not_called()


async def test_unbound_access_token_rejects_before_storage(auth_boundary, monkeypatch):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a"})
    response = await request(auth_boundary, headers={"Authorization": "Bearer valid"})
    assert response.status_code == 401
    auth_boundary.client.assert_not_called()


@pytest.mark.parametrize("cookie_session", [None, {"id": "session-b", "user_id": "user-b"}])
async def test_legacy_cookie_cannot_override_access_session(auth_boundary, monkeypatch, cookie_session):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a", "sid": "session-a"})
    auth_boundary.cookies.return_value = cookie_session
    response = await request(auth_boundary, headers={"Authorization": "Bearer valid"},
                             cookies={dep.SESSION_COOKIE_NAME: "other"})
    assert response.status_code == 401
    auth_boundary.account.assert_not_awaited()


async def test_current_membership_rejection_stays_unauthorized(auth_boundary, monkeypatch):
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a", "sid": "session-a"})
    auth_boundary.account.side_effect = principal.PrincipalUnavailable("membership_required")
    response = await request(auth_boundary, headers={"Authorization": "Bearer valid"})
    assert response.status_code == 401
    auth_boundary.meter.assert_not_awaited()
