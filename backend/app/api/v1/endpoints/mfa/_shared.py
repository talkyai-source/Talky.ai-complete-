"""Shared constants + private helpers for /auth/mfa endpoints."""
from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Request, Response

from app.core.security.sessions import SESSION_COOKIE_NAME, SESSION_LIFETIME_HOURS

# MFA challenge token lifetime (5 minutes — short window for step-2 login)
MFA_CHALLENGE_TTL_MINUTES: int = 5

# Generic error message — never reveal which factor was wrong (OWASP)
GENERIC_MFA_ERROR = "MFA verification failed."


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


def _set_session_cookie(response: Response, raw_token: str) -> None:
    """Write the session token into an httpOnly Secure SameSite=Strict cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=SESSION_LIFETIME_HOURS * 3600,
        path="/",
    )
