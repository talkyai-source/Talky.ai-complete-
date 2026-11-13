"""D4-5: a formally held account is refused at sign-in with a coded reason.

A formal hold suspends every membership of the held account, so the principal
check failed early as "membership_required" and login answered the generic
"Account access is unavailable" 401. The customer could not tell a hold from
any other problem. The hold is now named, and only after the password (or
passkey) proof succeeded, exactly where the generic refusal already was.
"""
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.security import principal

# The auth package re-exports the ``login`` function under the module's name.
login_ep = importlib.import_module("app.api.v1.endpoints.auth.login")
from tests.unit.test_principal_formal_account_holds import PrincipalPort


class MembershipPort(PrincipalPort):
    def __init__(self, *, membership="suspended", **kwargs):
        super().__init__(**kwargs)
        self.membership = membership

    async def fetchrow(self, query, *args):
        row = await super().fetchrow(query, *args)
        if "FROM user_profiles up" in query:
            row = {**row, "membership_status": self.membership}
        return row


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction,reason", [
    ({"account_status": "suspended"}, "tenant_suspended"),
    ({"active_holds": [{"target_type": "tenant", "immediate": True}]}, "formal_account_hold"),
    ({"parent_status": "suspended"}, "formal_partner_inactive"),
], ids=["tenant-status", "hold-row", "parent"])
async def test_a_membership_suspended_by_a_hold_names_the_hold(restriction, reason):
    conn = MembershipPort(membership="suspended", **restriction)
    with pytest.raises(principal.PrincipalUnavailable) as refused:
        await principal.load_current_principal(conn, conn.user)
    assert refused.value.code == "account_restricted"
    assert refused.value.reason == reason
    assert conn.bypass == "off"


@pytest.mark.asyncio
async def test_a_teammate_suspended_inside_a_healthy_account_is_not_called_a_hold():
    conn = MembershipPort(membership="suspended")
    with pytest.raises(principal.PrincipalUnavailable) as refused:
        await principal.load_current_principal(conn, conn.user)
    assert refused.value.code == "membership_required"


@pytest.mark.asyncio
async def test_an_inactive_membership_never_reads_account_authority():
    conn = MembershipPort(membership="inactive", account_status="suspended")
    with pytest.raises(principal.PrincipalUnavailable) as refused:
        await principal.load_current_principal(conn, conn.user)
    assert refused.value.code == "membership_required"
    assert conn.lookups == 0


def _login_app(monkeypatch, refusal):
    conn = SimpleNamespace(
        fetchval=AsyncMock(return_value=0),
        fetchrow=AsyncMock(return_value={
            "id": "11111111-2222-4333-8444-555555555555", "email": "held@example.com",
            "name": "Held", "role": "user", "password_hash": "hash",
            "tenant_id": "aabbccdd-1234-4abc-8def-0123456789ab", "is_active": True,
            "is_verified": True, "mfa_enabled": False, "business_name": "Held",
            "minutes_allocated": 0, "minutes_used": 0}),
        execute=AsyncMock(),
        transaction=MagicMock(),
    )
    acquire_context = AsyncMock()
    acquire_context.__aenter__.return_value = conn
    acquire_context.__aexit__.return_value = None
    pool = MagicMock()
    pool.acquire.return_value = acquire_context

    async def lock(_conn, _user_id):
        return {"password_hash": "hash", "mfa_enabled": False}

    async def load(_conn, _user_id):
        raise refusal

    monkeypatch.setattr(login_ep, "verify_turnstile", AsyncMock(return_value=None))
    monkeypatch.setattr(login_ep, "verify_password", lambda _password, _hash: True)
    monkeypatch.setattr(login_ep, "lock_user_credential_issuance", lock)
    monkeypatch.setattr(login_ep, "load_current_principal", load)
    app = FastAPI()
    app.include_router(login_ep.router, prefix="/auth")
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=pool)
    app.dependency_overrides[get_audit_logger] = lambda: SimpleNamespace(log_security_event=AsyncMock())
    return app


async def _post_login(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post("/auth/login", json={"email": "held@example.com", "password": "correct horse"})


@pytest.mark.asyncio
async def test_login_names_a_formal_hold_with_a_coded_403(monkeypatch):
    app = _login_app(monkeypatch, principal.PrincipalUnavailable("account_restricted", reason="formal_account_hold"))
    response = await _post_login(app)
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "tenant_suspended"
    assert detail["reason"] == "formal_account_hold"
    assert "hold" in detail["message"].lower()


@pytest.mark.asyncio
async def test_other_principal_refusals_keep_the_generic_401(monkeypatch):
    app = _login_app(monkeypatch, principal.PrincipalUnavailable("membership_required"))
    response = await _post_login(app)
    assert response.status_code == 401
    assert response.json()["detail"] == "Account access is unavailable. Contact your account administrator."
