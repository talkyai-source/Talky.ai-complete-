"""Shared constants + private helpers for /auth/mfa endpoints."""
from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Request, Response

# MFA challenge token lifetime (5 minutes — short window for step-2 login)
MFA_CHALLENGE_TTL_MINUTES: int = 5

# Generic error message — never reveal which factor was wrong (OWASP)
GENERIC_MFA_ERROR = "MFA verification failed."


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_client_ip(request: Request) -> str:
    # Not the leftmost X-Forwarded-For entry: nginx appends to it, so that
    # entry is caller-supplied (live-risk auth-2).
    from app.core.api_security_middleware import resolve_client_ip

    return resolve_client_ip(request)


def _get_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


def _set_session_cookie(response: Response, raw_token: str) -> None:
    """Use the same configured legacy-cookie policy as ordinary sign-in."""
    from app.api.v1.endpoints.auth._shared import set_session_cookie

    set_session_cookie(response, raw_token)
