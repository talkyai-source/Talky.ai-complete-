"""POST /auth/refresh — rotate the refresh token and issue a fresh access JWT."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import get_db_client
from app.core.jwt_security import ACCESS_TOKEN_TTL_MINUTES, encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.cookies import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    set_access_cookie,
    set_refresh_cookie,
)
from app.core.security.refresh_tokens import rotate_refresh_token

from ._shared import get_client_ip, get_user_agent, limiter

router = APIRouter(tags=["auth"])


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

    async with db_client.pool.acquire() as conn:
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
        ttl=timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    )
    set_access_cookie(response, access_jwt)
    set_refresh_cookie(response, new_raw)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
