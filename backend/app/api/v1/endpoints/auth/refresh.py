"""POST /auth/refresh — rotate the refresh token and issue a fresh access JWT."""
from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.jwt_security import ACCESS_TOKEN_TTL_MINUTES, encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.cookies import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    set_access_cookie,
    set_refresh_cookie,
)
from app.core.security.refresh_tokens import revoke_family_by_token, rotate_refresh_token
from app.core.security.sessions import SESSION_LIFETIME_HOURS, get_session_by_id

from ._shared import get_client_ip, get_user_agent, limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


async def bind_refresh_to_session(conn, claims: dict) -> tuple[Optional[str], bool]:
    """Return ``(session_id, alive)`` for the family's login session.

    * No session on the family (issued before 0045 and unmatched by its
      backfill) → ``(None, True)``: mint without ``sid`` exactly as before.
    * Session revoked or expired → ``(sid, False)``: the login is over
      everywhere, so the refresh must fail instead of quietly out-living it.
    * Alive → slide ``last_active_at`` and extend ``expires_at`` so the login
      session lives as long as the refresh family is actively used. Before
      this the session died 24 h after login while REST kept working on the
      7-day refresh token — one more way "the session" meant different
      things on different pages.
    """
    session_id = claims.get("session_id")
    if not session_id:
        return None, True
    session = await get_session_by_id(conn, session_id, user_id=claims["user_id"])
    if session is None:
        return session_id, False
    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        UPDATE security_sessions
        SET    last_active_at = $2,
               expires_at = GREATEST(expires_at, $3)
        WHERE  id = $1
        """,
        session_id,
        now,
        now + timedelta(hours=SESSION_LIFETIME_HOURS),
    )
    return session_id, True


@router.post("/refresh", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def refresh(
    request: Request,
    response: Response,
    db_client: Client = Depends(get_db_client),
    talky_rt: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> Response:
    """
    OAuth 2.0 refresh token rotation with reuse detection.

    Validates ``talky_rt`` against the refresh_tokens table. On a clean
    rotation we mark the consumed row used, insert a successor in the
    same family, and re-issue both auth cookies. If the presented token
    was already consumed once, we revoke the entire family — a stolen
    refresh token cannot grant continued access.
    """
    if not talky_rt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token.",
        )

    ip = get_client_ip(request)
    ua = get_user_agent(request)

    # The opaque refresh cookie must be resolved before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        result = await rotate_refresh_token(
            conn,
            presented_token=talky_rt,
            ip=ip,
            user_agent=ua,
        )
        if result is None:
            clear_auth_cookies(response)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token invalid or expired.",
            )
        new_raw, claims = result

        session_id, session_alive = await bind_refresh_to_session(conn, claims)
        if not session_alive:
            # The login session was revoked (logout everywhere, admin) or has
            # expired: stop the family too, or the next tab would refresh
            # straight past the revocation.
            await revoke_family_by_token(conn, presented_token=new_raw, reason="logout")
            clear_auth_cookies(response)
            logger.info(
                "refresh.session_ended user=%s session=%s", claims["user_id"], session_id
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your login session has ended. Please sign in again.",
            )

        user_row = await conn.fetchrow(
            "SELECT email, role FROM user_profiles WHERE id = $1",
            claims["user_id"],
        )
        if user_row is None:
            clear_auth_cookies(response)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User no longer exists.",
            )

    access_jwt = encode_access_token(
        user_id=claims["user_id"],
        email=user_row["email"],
        role=user_row["role"],
        tenant_id=claims["tenant_id"],
        session_id=session_id,
        ttl=timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    )
    set_access_cookie(response, access_jwt)
    set_refresh_cookie(response, new_raw)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
