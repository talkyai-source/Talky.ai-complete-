"""Public auth routes must cap password length (no unbounded hashing)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.auth.password_reset import ResetPasswordRequest
from app.api.v1.endpoints.auth.schemas import LoginRequest, SignupCompleteRequest
from app.core.security.password import MAX_PASSWORD_LENGTH

LOGIN_MAX = 1024
EMAIL = "someone@example.com"


def _pw(n: int) -> str:
    return ("pw" * n)[:n]


def test_login_accepts_1024_and_refuses_1025():
    LoginRequest(email=EMAIL, password=_pw(LOGIN_MAX))
    with pytest.raises(ValidationError):
        LoginRequest(email=EMAIL, password=_pw(LOGIN_MAX + 1))


def _signup(n: int) -> SignupCompleteRequest:
    return SignupCompleteRequest(
        email=EMAIL, code="123456", password=_pw(n), confirm_password=_pw(n)
    )


def test_signup_complete_password_cap():
    _signup(MAX_PASSWORD_LENGTH)
    with pytest.raises(ValidationError):
        _signup(MAX_PASSWORD_LENGTH + 1)


def _reset(n: int) -> ResetPasswordRequest:
    return ResetPasswordRequest(
        email=EMAIL, code="123456", new_password=_pw(n), confirm_password=_pw(n)
    )


def test_reset_password_cap():
    _reset(MAX_PASSWORD_LENGTH)
    with pytest.raises(ValidationError):
        _reset(MAX_PASSWORD_LENGTH + 1)
