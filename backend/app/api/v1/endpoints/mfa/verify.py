"""POST /auth/mfa/verify — step-2 of two-step login.

Accepts the challenge token issued by /auth/login plus either a fresh
TOTP code or a single-use recovery code. On success, mints the real
session + JWT; on any failure, returns a generic error message and
records the attempt.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.jwt_security import encode_access_token as _encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.lockout import check_account_locked, record_login_attempt
from app.core.security.principal import load_current_principal, PrincipalUnavailable, assert_expected_identity
from app.core.security.recovery import (
    mark_recovery_code_used,
    verify_recovery_code_returning_id,
)
from app.core.security.sessions import create_session, hash_session_token
from app.core.security.totp import decrypt_totp_secret, verify_totp_step

from app.api.v1.endpoints.auth._shared import issue_cookie_auth

from ._shared import GENERIC_MFA_ERROR, _get_client_ip, _get_user_agent, _set_session_cookie
from .challenge import (
    MFA_VERIFY_MAX_ATTEMPTS,
    consume_mfa_challenge,
    invalidate_mfa_challenge,
    record_failed_mfa_attempt,
    resolve_mfa_challenge,
)
from .schemas import MFAChallengeVerifyRequest, MFAChallengeVerifyResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mfa"])


class _RejectedFactor(Exception):
    """Commit the recorded failed attempt, then return the generic denial."""


@asynccontextmanager
async def _verification_transaction(pool):
    rejected = False
    async with acquire_with_tenant(pool, None) as conn:
        try:
            yield conn
        except _RejectedFactor:
            rejected = True
    if rejected:
        raise HTTPException(status_code=401, detail=GENERIC_MFA_ERROR)


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

    # The signed challenge must be resolved before its tenant is known.
    async with _verification_transaction(db_client.pool) as conn:
        # --- Resolve and validate the challenge token -------------------------
        challenge = await resolve_mfa_challenge(conn, body.challenge_token)

        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            )

        user_id: str = str(challenge["user_id"])

        # --- Load user and MFA record -----------------------------------------
        try:
            user_row = await load_current_principal(conn, user_id)
            assert_expected_identity(request, user_row)
        except PrincipalUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_MFA_ERROR,
            ) from exc

        mfa_row = await conn.fetchrow(
            "SELECT totp_secret_enc, enabled, last_used_at FROM user_mfa WHERE user_id = $1 FOR UPDATE",
            user_id,
        )

        if not mfa_row or not mfa_row["enabled"]:
            raise _RejectedFactor()

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
            raise _RejectedFactor()

        # --- Verify the second factor ------------------------------------------
        # Two paths share the success branch — TOTP doesn't have a separate
        # consume step but recovery codes do. We verify (without consuming)
        # first, then run the consume + session creation inside a single
        # transaction below so a mid-flow failure can't strand a burned
        # recovery code while leaving the user without a session.
        mfa_ok = False
        matched_step = None
        recovery_code_id: Optional[str] = None
        challenge_id = str(challenge["id"])

        if body.code:
            try:
                raw_secret = decrypt_totp_secret(mfa_row["totp_secret_enc"])
            except RuntimeError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="MFA configuration error.",
                )

            matched_step = verify_totp_step(
                raw_secret,
                body.code,
                last_used_at=mfa_row["last_used_at"],
            )

            mfa_ok = matched_step is not None

        elif body.recovery_code:
            recovery_code_id = await verify_recovery_code_returning_id(
                conn, user_id, body.recovery_code
            )
            mfa_ok = recovery_code_id is not None

        if not mfa_ok:
            # Per-challenge brute-force counter. After MFA_VERIFY_MAX_ATTEMPTS
            # wrong submissions we burn the challenge — the attacker has to
            # go back to /auth/login to mint a fresh one, which is itself
            # rate-limited per-IP and per-email.
            new_attempts = await record_failed_mfa_attempt(conn, challenge_id)
            if new_attempts >= MFA_VERIFY_MAX_ATTEMPTS:
                await invalidate_mfa_challenge(conn, challenge_id)
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="mfa_failed",
            )
            raise _RejectedFactor()

        # --- SUCCESS: consume + create session — all in ONE transaction ---
        async with conn.transaction():
            if recovery_code_id is not None:
                # Consume the recovery code we just verified. If another
                # caller raced us and consumed it first, mark_recovery_code_used
                # returns False → treat as auth failure inside the same tx so
                # we don't strand a half-issued session.
                consumed = await mark_recovery_code_used(conn, recovery_code_id)
                if not consumed:
                    logger.warning(
                        "recovery_code_consume_race user=%s code_id=%s",
                        user_id, recovery_code_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=GENERIC_MFA_ERROR,
                    )
            else:
                # TOTP path: replay-prevention timestamp updated inside the
                # same tx as session creation so the two land together.
                await conn.execute(
                    "UPDATE user_mfa SET last_used_at = $2 WHERE user_id = $1",
                    user_id, matched_step,
                )

            if not await consume_mfa_challenge(conn, challenge_id):
                raise HTTPException(status_code=401, detail=GENERIC_MFA_ERROR)

            raw_session_token, session_id = await create_session(
                conn,
                user_id=user_id,
                ip_address=ip,
                user_agent=ua,
                request=request,
                return_session_id=True,
            )

            session_hash = hash_session_token(raw_session_token)
            await conn.execute(
                """
                UPDATE security_sessions
                   SET mfa_verified = TRUE
                 WHERE session_token_hash = $1
                """,
                session_hash,
            )

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

            tenant_id = str(user_row["tenant_id"]) if user_row["tenant_id"] else None
            token = _encode_access_token(
                user_id=user_id, email=user_row["email"], role=user_row["role"],
                tenant_id=tenant_id, session_id=session_id,
            )

            await issue_cookie_auth(response, conn, user_id=user_id,
                email=user_row["email"], role=user_row["role"], tenant_id=user_row["tenant_id"],
                session_id=session_id, ip=ip, user_agent=ua)

    # --- Build response -------------------------------------------------------
    from app.services.scripts.tenant_minutes import compute_tenant_minutes_status
    meter = await compute_tenant_minutes_status(db_client.pool, tenant_id)

    _set_session_cookie(response, raw_session_token)

    logger.info("MFA challenge verified — full session issued for user=%s", user_id)

    return MFAChallengeVerifyResponse(
        access_token=token,
        user_id=user_id,
        email=user_row["email"],
        role=user_row["role"],
        tenant_id=tenant_id,
        business_name=user_row["business_name"],
        **meter.allowance(),
        mfa_verified=True,
        message="Login successful.",
    )
