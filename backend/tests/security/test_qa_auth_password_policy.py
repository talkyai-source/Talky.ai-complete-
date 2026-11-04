"""QA-SEC-001: new credentials meet the non-MFA policy; old logins survive."""
import bcrypt
import pytest
import inspect
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.core.security.password import (
    PasswordValidationError, hash_password, validate_password_strength, verify_password,
)


@pytest.mark.parametrize("password", ["12345678", "password", "Password1", "Abcdef12345!",
                                     "12345678901234", "passwordpassword", "12345678901234567890"])
def test_rejects_short_or_known_common_passwords(password):
    with pytest.raises(PasswordValidationError):
        validate_password_strength(password)


def test_accepts_long_unicode_passphrase_and_hashes_it():
    password = "orchid lantern café 🔑 2026"
    validate_password_strength(password)
    assert verify_password(password, hash_password(password))


def test_existing_weak_bcrypt_password_still_authenticates():
    old_hash = bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode()
    assert verify_password("password", old_hash)
    assert not verify_password("different", old_hash)


@pytest.mark.parametrize("route", ["signup", "reset", "change"])
@pytest.mark.parametrize("password", ["Password123!", "passwordpassword"])
async def test_auth_routes_reject_weak_passwords_as_client_errors_before_side_effects(route, password):
    from app.api.v1.endpoints.auth import signup, password_reset
    from app.api.v1.endpoints.auth.schemas import SignupCompleteRequest, ChangePasswordRequest
    change = importlib.import_module("app.api.v1.endpoints.auth.password")
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                       "query_string": b"", "client": ("203.0.113.9", 1234)})
    with pytest.raises(HTTPException) as exc:
        if route == "signup":
            await inspect.unwrap(signup.signup_complete)(
                request, Response(), SignupCompleteRequest(email="owner@example.com", code="123456",
                    password=password, confirm_password=password), SimpleNamespace(pool=None), SimpleNamespace())
        elif route == "reset":
            await password_reset.reset_password(request, password_reset.ResetPasswordRequest(
                email="owner@example.com", code="123456", new_password=password),
                SimpleNamespace(pool=None), SimpleNamespace())
        else:
            await change.change_password(request, ChangePasswordRequest(old_password="old-credential", new_password=password),
                Response(), SimpleNamespace(tenant_id=None), SimpleNamespace(pool=None), SimpleNamespace())
    assert exc.value.status_code == 400


async def test_existing_admin_hash_input_contract_is_preserved(monkeypatch):
    """Admin's separate password policy is outside this task's file fence."""
    users = importlib.import_module("app.api.v1.endpoints.admin.users")
    class ReachedDatabase(Exception):
        pass

    @asynccontextmanager
    async def stop_before_database(*args):
        raise ReachedDatabase
        yield

    monkeypatch.setattr(users, "acquire_with_tenant", stop_before_database)
    monkeypatch.setattr(users, "_create_digest", lambda *args: "synthetic-digest")
    operation_id = "00000000-0000-4000-8000-000000000001"
    body = users.CreateUserRequest(operation_id=operation_id, request_id=operation_id,
                                  name="Owner", email="owner@example.com", password="password",
                                  role="platform_admin")
    with pytest.raises(ReachedDatabase):
        await users.create_user(body, SimpleNamespace(id="synthetic-admin"), SimpleNamespace(pool=None), SimpleNamespace())
