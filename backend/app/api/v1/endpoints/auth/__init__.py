"""Authentication endpoints — split into one file per flow.

Hardened per OWASP Authentication, Session Management, and Password Storage
Cheat Sheets.

Official references used (verified March 2026):
  https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

Endpoints:
  POST /auth/signup/start      — generate signup verification code
  POST /auth/signup/complete   — verify code and create account
  POST /auth/register          — direct (single-step) account creation
  POST /auth/login             — verify credentials, returns JWT + session cookie
  GET  /auth/me                — return current user info
  PATCH /auth/me               — update profile fields
  POST /auth/logout            — revoke server-side session + clear cookie
  POST /auth/logout-all        — revoke ALL sessions for the current user
  POST /auth/passkey-check     — does this email have a registered passkey?
  POST /auth/change-password   — change password + revoke other sessions
  POST /auth/forgot-password   — email a 6-digit password-reset code
  POST /auth/reset-password    — verify the code + set new password
  GET  /auth/verify-email      — consume email-verification token

This package's public surface is intentionally identical to the previous
single-file `auth.py` module — `router`, `limiter`, and the endpoint
functions are all re-exported below so existing imports keep working.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    login as _login_mod,
    passkey as _passkey_mod,
    password as _password_mod,
    password_reset as _password_reset_mod,
    profile as _profile_mod,
    registration as _registration_mod,
    sessions as _sessions_mod,
    signup as _signup_mod,
    verify_email as _verify_email_mod,
)
from ._shared import limiter
from .login import login
from .registration import register
from .verify_email import verify_email

# Aggregate router. Sub-routers carry no prefix; the prefix lives here so
# routes resolve as /auth/<path>.
router = APIRouter(prefix="/auth", tags=["auth"])

# Order is cosmetic (affects /docs grouping), not functional.
router.include_router(_signup_mod.router)
router.include_router(_registration_mod.router)
router.include_router(_login_mod.router)
router.include_router(_profile_mod.router)
router.include_router(_sessions_mod.router)
router.include_router(_passkey_mod.router)
router.include_router(_password_mod.router)
router.include_router(_password_reset_mod.router)
router.include_router(_verify_email_mod.router)


__all__ = [
    "limiter",
    "login",
    "register",
    "router",
    "verify_email",
]
