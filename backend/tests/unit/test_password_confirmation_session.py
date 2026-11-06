"""Wrong reauthentication secrets must not revoke an authenticated session."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.v1 import dependencies as dep
from app.core.error_handlers import register_error_handlers
from app.core.security import principal


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["change", "disable"])
async def test_wrong_current_password_is_validation_and_session_still_works(monkeypatch, operation):
    change = importlib.import_module("app.api.v1.endpoints.auth.password")
    mfa = importlib.import_module("app.api.v1.endpoints.mfa.status")
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={"password_hash": "synthetic-hash"}),
                           execute=AsyncMock(side_effect=AssertionError("wrong secret must not write")))

    @asynccontextmanager
    async def connection(*args):
        yield conn

    client = SimpleNamespace(pool=object())
    sessions = AsyncMock(return_value={"id": "session-a", "user_id": "user-a"})
    row = {"id": "user-a", "email": "synthetic@example.invalid", "tenant_id": None,
           "role": "user", "name": "Synthetic", "business_name": None}
    monkeypatch.setattr(dep, "get_db_client", lambda: client)
    monkeypatch.setattr(dep, "acquire_with_tenant", connection)
    monkeypatch.setattr(dep, "get_session_by_id", sessions)
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: {"sub": "user-a", "sid": "session-a"})
    monkeypatch.setattr(principal, "load_current_principal", AsyncMock(return_value=row))
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status",
                        AsyncMock(return_value=SimpleNamespace(allowance=lambda: {})))
    for module in (change, mfa):
        monkeypatch.setattr(module, "acquire_with_tenant", connection)
        monkeypatch.setattr(module, "verify_password", lambda *args: False)
        monkeypatch.setattr(module, "revoke_all_user_sessions", AsyncMock(side_effect=AssertionError("revoke")))
        monkeypatch.setattr(module, "revoke_all_user_refresh_tokens", AsyncMock(side_effect=AssertionError("revoke")))
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(change.router, prefix="/auth")
    app.include_router(mfa.router, prefix="/auth/mfa")
    # Router Depends objects retain the original dependency callable.
    app.dependency_overrides[change.get_db_client] = lambda: client
    app.dependency_overrides[change.get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())

    @app.get("/private")
    async def private(user=Depends(dep.get_current_user)):
        return {"id": user.id}

    path, body = ("/auth/change-password", {"old_password": "wrong-current",
                    "new_password": "orchid lantern cobalt 2026!"}) if operation == "change" else (
                        "/auth/mfa/disable", {"password": "wrong-current"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic",
                                headers={"Authorization": "Bearer synthetic-session-token"}) as http:
        assert (await http.get("/private")).status_code == 200
        response = await http.post(path, json=body)
        after = await http.get("/private")
    assert after.status_code == 200
    assert after.json() == {"id": "user-a"}
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "current_password_invalid"
    assert response.json()["error"]["message"] == "Current password is incorrect."
    assert "set-cookie" not in response.headers
    conn.execute.assert_not_awaited()
    assert sessions.await_count == 3
