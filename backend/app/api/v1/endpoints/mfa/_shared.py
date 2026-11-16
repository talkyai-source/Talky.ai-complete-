"""Shared constants + private helpers for /auth/mfa endpoints."""
from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Request, Response

# MFA challenge token lifetime (5 minutes — short window for step-2 login)
MFA_CHALLENGE_TTL_MINUTES: int = 5

# Generic wrong-code answer — never reveal which factor was wrong (OWASP).
# The stable code lets the website branch without reading the sentence.
GENERIC_MFA_ERROR = {
    "code": "two_factor_failed",
    "message": "Two-factor authentication failed. Check the code and try again.",
}

# The caller's own challenge token is missing, expired, used or burned. This
# is about the token in the request, not about any account, so it can be told
# apart from a wrong code: the customer must sign in again.
MFA_SESSION_EXPIRED_ERROR = {
    "code": "two_factor_session_expired",
    "message": "Your two-factor authentication session has expired. Sign in again.",
}


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
