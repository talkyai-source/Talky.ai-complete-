"""auth-sessions-6: /login must not reset the failure counter for MFA users.

A success row written after the password step is the reset boundary used by
the lockout SQL, so every fresh password login zeroed the count that is meant
to throttle TOTP/recovery-code guessing. For mfa_enabled users the success row
now belongs to /auth/mfa/verify (on completion); non-MFA users are unchanged.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

import importlib
from app.api.v1.endpoints import mfa as mfa_pkg
from app.api.v1.endpoints.auth.schemas import LoginRequest

# the package re-exports the ``login`` function, shadowing the submodule name
login_mod = importlib.import_module("app.api.v1.endpoints.auth.login")
USER_ID = uuid4()


def _request():
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": [],
                    "client": ("127.0.0.1", 1), "scheme": "https", "server": ("a", 443)})


def _setup(monkeypatch, *, mfa_enabled):
    conn = SimpleNamespace(
        is_in_transaction=lambda: True,
        fetchrow=AsyncMock(return_value={
            "id": USER_ID, "email": "u@example.com", "name": "U", "role": "tenant_admin",
            "password_hash": "h", "tenant_id": uuid4(), "is_active": True, "is_verified": True,
            "mfa_enabled": mfa_enabled, "business_name": "Biz",
            "minutes_allocated": 0, "minutes_used": 0}),
        execute=AsyncMock())

    @asynccontextmanager
    async def acquire(pool, tenant_id, **_):
        yield conn

    attempts = AsyncMock()
    monkeypatch.setattr(login_mod, "acquire_with_tenant", acquire)
    monkeypatch.setattr(login_mod, "check_account_locked", AsyncMock(return_value=None))
    monkeypatch.setattr(login_mod, "verify_password", lambda *_: True)
    monkeypatch.setattr(login_mod, "rehash_if_needed", lambda *_: None)
    monkeypatch.setattr(login_mod, "record_login_attempt", attempts)
    monkeypatch.setattr(login_mod, "load_current_principal", AsyncMock(return_value={
        "email": "u@example.com", "role": "tenant_admin", "tenant_id": str(uuid4()),
        "business_name": "Biz"}))
    monkeypatch.setattr(mfa_pkg, "create_mfa_challenge", AsyncMock(return_value="challenge-token"))
    monkeypatch.setattr(login_mod, "create_session", AsyncMock(side_effect=RuntimeError("stop-after-success")))
    return attempts


async def _login():
    return await login_mod.login.__wrapped__(
        _request(), LoginRequest(email="u@example.com", password="pw-123456789"),
        SimpleNamespace(pool=object()), SimpleNamespace(log_security_event=AsyncMock(), log=AsyncMock()))


def _success_rows(attempts):
    return [c for c in attempts.await_args_list if c.kwargs.get("success") is True]


async def test_mfa_user_password_step_does_not_record_a_success_row(monkeypatch):
    attempts = _setup(monkeypatch, mfa_enabled=True)
    response = await _login()
    assert b"mfa_challenge_token" in response.body
    assert _success_rows(attempts) == []


async def test_non_mfa_user_still_records_success_before_session(monkeypatch):
    attempts = _setup(monkeypatch, mfa_enabled=False)
    with pytest.raises(RuntimeError, match="stop-after-success"):
        await _login()
    assert len(_success_rows(attempts)) == 1


async def test_mfa_enabled_during_password_work_is_read_again_under_the_profile_fence(monkeypatch):
    attempts = _setup(monkeypatch, mfa_enabled=False)
    monkeypatch.setattr(login_mod, "lock_user_credential_issuance", AsyncMock(return_value={
        "id": USER_ID, "password_hash": "h", "mfa_enabled": True}))
    response = await _login()
    assert b"mfa_challenge_token" in response.body
    assert _success_rows(attempts) == []
    login_mod.create_session.assert_not_awaited()
