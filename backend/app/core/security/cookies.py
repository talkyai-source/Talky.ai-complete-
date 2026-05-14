"""HttpOnly cookie issuance for the access + refresh token auth flow.

Two cookies, one purpose each:

  talky_at   short-lived access JWT (15 min)        Path=/
  talky_rt   long-lived opaque refresh token (7d)   Path=/api/v1/auth

Both are HttpOnly Secure SameSite=Strict in production. In non-production
environments the Secure flag is dropped so the cookies work over plain
HTTP during local development.
"""
from __future__ import annotations

from fastapi import Response

from app.core.config import get_settings

ACCESS_COOKIE_NAME = "talky_at"
REFRESH_COOKIE_NAME = "talky_rt"

ACCESS_TOKEN_MAX_AGE = 15 * 60
REFRESH_TOKEN_MAX_AGE = 7 * 24 * 60 * 60

REFRESH_COOKIE_PATH = "/api/v1/auth"


def _secure_flag() -> bool:
    return get_settings().environment.lower() == "production"


def set_access_cookie(response: Response, jwt_token: str) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=jwt_token,
        max_age=ACCESS_TOKEN_MAX_AGE,
        path="/",
        httponly=True,
        secure=_secure_flag(),
        samesite="strict",
    )


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=REFRESH_TOKEN_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_secure_flag(),
        samesite="strict",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_secure_flag(),
        samesite="strict",
    )
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_secure_flag(),
        samesite="strict",
    )
