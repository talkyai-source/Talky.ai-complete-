"""Shared helpers for the /auth endpoint package.

Anything used by more than one auth flow lives here:
  - the IP-keyed slowapi `limiter`
  - cookie helpers (set / clear, secure-flag resolution)
  - JWT minting wrapper (translates internal failures to a clean 503)
  - small request-scoped helpers (client IP, user agent, text normalisation)

NOTE: keep this file narrow. New functionality goes in the flow-specific
file (login.py / registration.py / etc.), not here.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from datetime import timedelta

from app.core.config import get_settings
from app.core.jwt_security import ACCESS_TOKEN_TTL_MINUTES, encode_access_token
from app.core.security.cookies import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    set_access_cookie,
    set_refresh_cookie,
)
from app.core.security.refresh_tokens import issue_initial_refresh_token
from app.core.security.sessions import SESSION_COOKIE_NAME, SESSION_LIFETIME_HOURS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter — keyed by client IP (first line of defence; per-account
# lockout via login_attempts is the second line — see lockout.py)
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# OWASP: generic error message — never reveal which field was wrong
# ---------------------------------------------------------------------------
GENERIC_AUTH_ERROR = "Invalid email or password."

# ---------------------------------------------------------------------------
# Cookie settings (OWASP Session Management Cheat Sheet)
#   httponly  = True   — prevents JavaScript access (XSS protection)
#   secure    = True   — HTTPS only (set False only in local dev via env)
#   samesite  = "strict" — blocks cross-site request forgery
#   max_age   — matches absolute session lifetime (seconds)
# ---------------------------------------------------------------------------
COOKIE_MAX_AGE = SESSION_LIFETIME_HOURS * 3600  # 86 400 s for 24-hour sessions


def get_client_ip(request: Request) -> str:
    """
    Extract the real client IP from the request.
    Respects X-Forwarded-For when behind a trusted reverse proxy.
    Falls back to the direct connection IP.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2 — take the leftmost
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


def create_jwt(
    user_id: str,
    email: str,
    role: str,
    tenant_id: Optional[str],
    session_id: Optional[str] = None,
) -> str:
    """Create a signed JWT access token."""
    try:
        return encode_access_token(
            user_id=user_id,
            email=email,
            role=role,
            tenant_id=tenant_id,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("JWT creation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server authentication is not configured.",
        ) from exc


def session_cookie_secure() -> bool:
    override = normalize_optional_text(os.environ.get("SESSION_COOKIE_SECURE"))
    if override is not None:
        return override.lower() in {"1", "true", "yes", "on"}
    return get_settings().environment.lower() == "production"


def _session_samesite() -> str:
    """
    Mirrors the talky_at/talky_rt SameSite logic so the legacy session
    cookie also works on a cross-site Vercel-hosted frontend when
    AUTH_COOKIE_SAMESITE=none is set.
    """
    val = (os.getenv("AUTH_COOKIE_SAMESITE", "strict") or "strict").strip().lower()
    return val if val in ("strict", "lax", "none") else "strict"


def _session_cookie_secure_or_none() -> bool:
    # SameSite=None requires Secure (browser rejects otherwise).
    return session_cookie_secure() or _session_samesite() == "none"


def set_session_cookie(response: Response, raw_token: str) -> None:
    """
    Write the session token into an httpOnly Secure cookie.

    SameSite is configurable via AUTH_COOKIE_SAMESITE (default 'strict').
    Set 'none' when the frontend lives on a different eTLD+1 (e.g.
    talkleeai.vercel.app + api.talkleeai.com). Secure flag is forced on
    when SameSite=None, as browsers require.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=_session_cookie_secure_or_none(),
        samesite=_session_samesite(),  # type: ignore[arg-type]
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete the session cookie from the browser."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=_session_cookie_secure_or_none(),
        samesite=_session_samesite(),  # type: ignore[arg-type]
        path="/",
    )


async def issue_cookie_auth(
    response: Response,
    conn,
    *,
    user_id: str,
    email: str,
    role: str,
    tenant_id: Optional[str],
    session_id: Optional[str],
    ip: Optional[str],
    user_agent: Optional[str],
) -> None:
    """Set the new httpOnly access + refresh cookies on the response.

    Called by every successful authentication path so the new cookie
    pair is issued alongside the legacy session cookie + body JWT during
    the migration window.
    """
    access_jwt = encode_access_token(
        user_id=user_id,
        email=email,
        role=role,
        tenant_id=tenant_id,
        session_id=session_id,
        ttl=timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    )
    raw_refresh, _token_id, _family_id = await issue_initial_refresh_token(
        conn,
        user_id=user_id,
        tenant_id=tenant_id,
        ip=ip,
        user_agent=user_agent,
    )
    set_access_cookie(response, access_jwt)
    set_refresh_cookie(response, raw_refresh)


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None
