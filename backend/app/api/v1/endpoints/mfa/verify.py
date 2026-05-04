"""POST /auth/mfa/verify — step-2 of two-step login.

Accepts the challenge token issued by /auth/login plus either a fresh
TOTP code or a single-use recovery code. On success, mints the real
session + JWT; on any failure, returns a generic error message and
records the attempt.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import get_db_client
from app.core.jwt_security import encode_access_token as _encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.lockout import check_account_locked, record_login_attempt
from app.core.security.recovery import verify_and_consume_recovery_code
from app.core.security.sessions import create_session, hash_session_token
from app.core.security.totp import decrypt_totp_secret, verify_totp_code

from ._shared import GENERIC_MFA_ERROR, _get_client_ip, _get_user_agent, _set_session_cookie
from .challenge import consume_mfa_challenge, resolve_mfa_challenge
from .schemas import MFAChallengeVerifyRequest, MFAChallengeVerifyResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mfa"])


@router.post("/verify", response_model=MFAChallengeVerifyResponse)
async def verify_mfa_challenge(
    request: Request,
    response: Response,
    body: MFAChallengeVerifyRequest,
    db_client: Client = Depends(get_db_client),
) -> MFAChallengeVerifyResponse:
    """
    Step-2 of the two-step login flow.

    Accepts the mfa_challenge_token (from POST /auth/login) plus either:
      - code          : 6-digit TOTP from the authenticator app, OR
      - recovery_code : one of the single-use backup codes

    Security controls (OWASP + RFC 6238 + pyotp checklist):
      1. Challenge token is single-use and expires in 5 minutes.
      2. TOTP replay prevention (same 30-second slot rejected).
      3. Recovery code is single-use and consumed on first use.
      4. All failures use the same generic error message.
      5. All attempts (success + failure) recorded in login_attempts.
      6. On success: full server-side session created + httpOnly cookie set.

    Returns the full auth response identical to a regular (non-MFA) login.
    """
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)

    # Must supply exactly one of code or recovery_code
    if not body.code and not body.recovery_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'code' (TOTP) or 'recovery_code'.",
        )

    async with db_client.pool.acquire() as conn:
        # --- Resolve and validate the challenge token -------------------------
        challenge = await resolve_mfa_challenge(conn, body.challenge_token)

        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            )

        user_id: str = str(challenge["user_id"])

        # --- Load user and MFA record -----------------------------------------
        user_row = await conn.fetchrow(
            """
            SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                   up.is_active,
                   t.business_name, t.minutes_allocated, t.minutes_used
            FROM   user_profiles up
            LEFT   JOIN tenants t ON t.id = up.tenant_id
            WHERE  up.id = $1
            """,
            user_id,
        )

        if not user_row or not user_row["is_active"]:
            await record_login_attempt(
                conn,
                email=user_row["email"] if user_row else "unknown",
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_inactive",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            )

        mfa_row = await conn.fetchrow(
            "SELECT totp_secret_enc, enabled, last_used_at FROM user_mfa WHERE user_id = $1",
            user_id,
        )

        if not mfa_row or not mfa_row["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            )

        # --- Per-account lockout check ----------------------------------------
        normalised_email = user_row["email"].lower()
        locked_until = await check_account_locked(conn, normalised_email)
        if locked_until is not None:
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_locked",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            )

        # --- Verify the second factor ------------------------------------------
        mfa_ok = False

        if body.code:
            # TOTP path
            try:
                raw_secret = decrypt_totp_secret(mfa_row["totp_secret_enc"])
            except RuntimeError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="MFA configuration error.",
                )

            mfa_ok = verify_totp_code(
                raw_secret,
                body.code,
                last_used_at=mfa_row["last_used_at"],
            )

            if mfa_ok:
                # Update last_used_at for replay prevention
                await conn.execute(
                    "UPDATE user_mfa SET last_used_at = NOW() WHERE user_id = $1",
                    user_id,
                )

        elif body.recovery_code:
            # Recovery code path
            mfa_ok = await verify_and_consume_recovery_code(
                conn, user_id, body.recovery_code
            )

        if not mfa_ok:
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="mfa_failed",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            )

        # --- SUCCESS: consume challenge + create full session -----------------
        await consume_mfa_challenge(conn, str(challenge["id"]))

        raw_session_token, session_id = await create_session(
            conn,
            user_id=user_id,
            ip_address=ip,
            user_agent=ua,
            request=request,
            return_session_id=True,
        )

        # Mark session as MFA verified
        session_hash = hash_session_token(raw_session_token)
        await conn.execute(
            """
            UPDATE security_sessions
               SET mfa_verified = TRUE
             WHERE session_token_hash = $1
            """,
            session_hash,
        )

        # Record successful login
        await record_login_attempt(
            conn,
            email=normalised_email,
            user_id=user_id,
            ip_address=ip,
            success=True,
        )

        await conn.execute(
            "UPDATE user_profiles SET last_login_at = NOW() WHERE id = $1",
            user_id,
        )

    # --- Build response -------------------------------------------------------
    tenant_id = str(user_row["tenant_id"]) if user_row["tenant_id"] else None
    minutes_remaining = max(
        0,
        (user_row["minutes_allocated"] or 0) - (user_row["minutes_used"] or 0),
    )

    token = _encode_access_token(
        user_id=user_id,
        email=user_row["email"],
        role=user_row["role"],
        tenant_id=tenant_id,
        session_id=session_id,
    )

    _set_session_cookie(response, raw_session_token)

    logger.info("MFA challenge verified — full session issued for user=%s", user_id)

    return MFAChallengeVerifyResponse(
        access_token=token,
        user_id=user_id,
        email=user_row["email"],
        role=user_row["role"],
        business_name=user_row["business_name"],
        minutes_remaining=minutes_remaining,
        mfa_verified=True,
        message="Login successful.",
    )
