"""POST /auth/logout + POST /auth/logout-all — server-side session revocation."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    revoke_all_user_sessions,
    revoke_session_by_token,
)

from ._shared import clear_session_cookie

router = APIRouter(tags=["auth"])


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, str]:
    """
    Logout the current user.

    1. Revoke the server-side session row (sets revoked=TRUE in security_sessions).
    2. Clear the httpOnly session cookie from the browser.

    OWASP: Logout must invalidate the server-side session, not just clear the
    client-side cookie.  Clearing the cookie alone does not prevent an attacker
    who has already captured the token from reusing it.
    """
    if talky_sid:
        async with db_client.pool.acquire() as conn:
            await revoke_session_by_token(conn, talky_sid, reason="logout")

    clear_session_cookie(response)
    return {"detail": "Logged out successfully."}


@router.post("/logout-all")
async def logout_all(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> dict[str, str | int]:
    """
    Revoke ALL active sessions for the current user across all devices.

    Use case: account compromise response, "sign out everywhere" feature.

    OWASP Session Management: Applications must provide a logout mechanism
    that invalidates all active sessions.
    """
    async with db_client.pool.acquire() as conn:
        count = await revoke_all_user_sessions(
            conn,
            current_user.id,
            reason="logout_all",
        )

    clear_session_cookie(response)
    return {
        "detail": f"Logged out from {count} active session(s).",
        "sessions_revoked": count,
    }
