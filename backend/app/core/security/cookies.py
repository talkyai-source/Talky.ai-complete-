"""HttpOnly cookie issuance for the access + refresh token auth flow.

Two cookies, one purpose each:

  talky_at   short-lived access JWT (15 min)        Path=/api/v1
  talky_rt   long-lived opaque refresh token (7d)   Path=/api/v1/auth

Both are HttpOnly Secure. SameSite is configurable via AUTH_COOKIE_SAMESITE
env var because the right value depends on where the frontend is hosted:

  - Same eTLD+1 as API (talkleeai.com + api.talkleeai.com) → "strict" (safest,
    blocks every cross-site fetch — full CSRF immunity for the cookie).
  - Different site (talkleeai.vercel.app + api.talkleeai.com) → "none"
    (browser-mandated for cross-site cookies; requires Secure=True which we
    already set in production). The CSRF middleware (Origin check) and the
    short access-token TTL still provide CSRF protection.

In non-production environments the Secure flag is dropped so the cookies
work over plain HTTP during local development.
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import Response

from app.core.config import get_settings

ACCESS_COOKIE_NAME = "talky_at"
REFRESH_COOKIE_NAME = "talky_rt"

ACCESS_TOKEN_MAX_AGE = 15 * 60
REFRESH_TOKEN_MAX_AGE = 7 * 24 * 60 * 60

# Cookie Paths are scoped to /api/v1 so the cookies are only sent on
# real API requests, never on incidental traffic to other origins behind
# the same host (e.g. /health, /metrics, static asset proxies). The
# HttpOnly + Secure + SameSite flags make /api/v1 a defense-in-depth
# tightening, not the primary defense — but narrower scopes reduce the
# surface for future bugs (a misconfigured /static route can't exfil the
# cookie via Set-Cookie reflection because the cookie isn't sent there
# in the first place).
ACCESS_COOKIE_PATH = "/api/v1"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _secure_flag() -> bool:
    return get_settings().environment.lower() == "production"


def _samesite() -> Literal["strict", "lax", "none"]:
    """
    SameSite policy for auth cookies. Default 'strict' for same-origin
    deployments; set AUTH_COOKIE_SAMESITE=none in env when the frontend
    is on a different eTLD+1 from the API (e.g. Vercel preview / staging).
    """
    val = (os.getenv("AUTH_COOKIE_SAMESITE", "strict") or "strict").strip().lower()
    if val in ("strict", "lax", "none"):
        return val  # type: ignore[return-value]
    return "strict"


def _cookie_secure() -> bool:
    """
    Secure flag — always True when SameSite=None (browser rejects otherwise)
    or when running in production. False only in dev with Strict/Lax.
    """
    return _secure_flag() or _samesite() == "none"


def set_access_cookie(response: Response, jwt_token: str) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=jwt_token,
        max_age=ACCESS_TOKEN_MAX_AGE,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_samesite(),
    )


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=REFRESH_TOKEN_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_samesite(),
    )


def clear_auth_cookies(response: Response) -> None:
    # IMPORTANT: delete_cookie ignores cookies whose path doesn't match,
    # so we must also issue a clear at the legacy "/" path for any user
    # whose cookie was set before the path was tightened in P4.2.
    # Without this their stale Path=/ cookie keeps being sent and they
    # appear "still logged in" even after logout. The double-delete is
    # cheap (two Set-Cookie headers, both Max-Age=0).
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_samesite(),
    )
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_secure(),
        samesite=_samesite(),
    )
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_samesite(),
    )
