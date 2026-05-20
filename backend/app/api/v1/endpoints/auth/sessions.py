"""Session *lifecycle* endpoints — tear down the **current** caller's session.

Mount: aggregated into the `/auth` router by `auth/__init__.py`, so the routes
are exposed as `/api/v1/auth/logout` and `/api/v1/auth/logout-all`.

Scope (intentionally narrow):
  POST /auth/logout      → revoke the current session + refresh family, clear cookies.
  POST /auth/logout-all  → revoke EVERY session/refresh-token row for the user.

This file does NOT handle cross-device session inspection, suspicious-session
verification, or selective revoke-by-id. That belongs to its sibling file:
`app/api/v1/endpoints/sessions.py`, which mounts the `/sessions/*` prefix.

The boundary in one line:
  - here  (`auth/sessions.py`)  →  "log me (or all of me) out"   →  /auth/*
  - there (`endpoints/sessions.py`) → "manage my devices"        →  /sessions/*
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.cookies import REFRESH_COOKIE_NAME, clear_auth_cookies
from app.core.security.refresh_tokens import revoke_family_by_token
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    revoke_all_user_sessions,
    revoke_session_by_token,
)

from ._shared import clear_session_cookie, limiter

router = APIRouter(tags=["auth"])


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    talky_rt: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> dict[str, str]:
    """
    Logout the current user.

    1. Revoke the legacy server-side session row (security_sessions).
    2. Revoke the refresh token family so the cookie-auth chain stops.
    3. Clear all auth cookies (legacy talky_sid + new talky_at/talky_rt).
    """
    async with db_client.pool.acquire() as conn:
        if talky_sid:
            await revoke_session_by_token(conn, talky_sid, reason="logout")
        if talky_rt:
            await revoke_family_by_token(conn, presented_token=talky_rt, reason="logout")

    clear_session_cookie(response)
    clear_auth_cookies(response)
    return {"detail": "Logged out successfully."}


@router.post("/logout-all")
@limiter.limit("10/hour")
async def logout_all(
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> dict[str, str | int]:
    """
    Revoke ALL active sessions for the current user across all devices.

    Use case: account compromise response, "sign out everywhere" feature.
    Also revokes every refresh token row owned by the user so the cookie
    auth path can't be used to continue from another browser either.

    AH-Phase-G rate limit: 10/hour per IP. Logout-all has real cost
    (one DB update per session row) and is also a griefing vector — an
    attacker who exfiltrated ONE session JWT can repeatedly invalidate
    every other session the user owns, even though they only need one
    invalidation to lock the user out. Cap it. 10/hour is well above
    any plausible legitimate use (a user clicking the button twice a
    minute by accident) and well below the abuse threshold.
    """
    async with db_client.pool.acquire() as conn:
        count = await revoke_all_user_sessions(
            conn,
            current_user.id,
            reason="logout_all",
        )
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = NOW(), revoked_reason = 'logout'
            WHERE user_id = $1 AND revoked_at IS NULL
            """,
            current_user.id,
        )

    clear_session_cookie(response)
    clear_auth_cookies(response)
    return {
        "detail": f"Logged out from {count} active session(s).",
        "sessions_revoked": count,
    }
