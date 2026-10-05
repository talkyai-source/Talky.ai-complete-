"""POST /auth/refresh — rotate the refresh token and issue a fresh access JWT."""
from __future__ import annotations

import logging
import hashlib

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.jwt_security import ACCESS_TOKEN_TTL_MINUTES, encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.principal import PrincipalUnavailable, assert_expected_identity, load_current_principal
from app.core.security.cookies import (
    REFRESH_COOKIE_NAME,
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
      backfill) → ``(None, False)``: require a fresh login; never mint an
      access token which bypasses server-side session revocation.
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
        return None, False
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


@router.post("/refresh", status_code=status.HTTP_200_OK)
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

    rejection = None
    # Lock the presented row before identity checks and rotation. A competing
    # request sees committed consumption; security rejection writes must commit.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        owned = await conn.fetchrow(
            """SELECT user_id,tenant_id FROM refresh_tokens
               WHERE token_hash=$1 FOR UPDATE""",
            hashlib.sha256(talky_rt.encode()).hexdigest(),
        )
        if owned is None:
            rejection = "Refresh token invalid or expired. Please sign in again."
        else:
            assert_expected_identity(request, {"id":str(owned["user_id"]),
                "tenant_id":str(owned["tenant_id"]) if owned["tenant_id"] else None})
            try:
                principal = await load_current_principal(conn, owned["user_id"], tenant_id=owned["tenant_id"])
                assert_expected_identity(request, principal)
            except PrincipalUnavailable:
                await revoke_family_by_token(conn, presented_token=talky_rt, reason="admin")
                rejection = "Account access changed. Please sign in again or contact your administrator."
        if rejection is None:
            result = await rotate_refresh_token(conn, presented_token=talky_rt, ip=ip, user_agent=ua)
            if result is None:
                rejection = "Refresh token invalid or expired. Please sign in again."
            else:
                new_raw, claims = result
                session_id, session_alive = await bind_refresh_to_session(conn, claims)
                if not session_alive:
                    await revoke_family_by_token(conn, presented_token=new_raw, reason="logout")
                    rejection = "Your login session has ended. Please sign in again."
                else:
                    access_jwt = encode_access_token(
                        user_id=principal["id"],email=principal["email"],role=principal["role"],
                        tenant_id=principal["tenant_id"],session_id=session_id,
                        ttl=timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
                    )
    if rejection is not None:
        # No cookie mutation on an unsuccessful or mismatched refresh. In-flight
        # failures must not clear a different account's newer browser cookies.
        raise HTTPException(status_code=401, detail=rejection)
    result_response = JSONResponse(content={
        "access_token":access_jwt,"token_type":"bearer","user_id":principal["id"],
        "tenant_id":principal["tenant_id"],"role":principal["role"],
    })
    set_access_cookie(result_response, access_jwt)
    set_refresh_cookie(result_response, new_raw)
    return result_response
