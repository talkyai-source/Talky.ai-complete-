"""GET /auth/mfa/status + POST /auth/mfa/disable — read state, turn it off.

Disabling requires the current account password (OWASP: reauthentication
before changing a security-affecting setting). Disabling deletes all
recovery codes so a stolen session can't quietly downgrade the account.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.password import verify_password
from app.core.security.recovery import count_remaining_codes, invalidate_all_codes
from app.core.security.refresh_tokens import revoke_all_user_refresh_tokens
from app.core.security.sessions import revoke_all_user_sessions

from .schemas import MFADisableRequest, MFAStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mfa"])


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MFAStatusResponse:
    """Return the MFA status for the current authenticated user."""
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, verified_at FROM user_mfa WHERE user_id = $1",
            current_user.id,
        )
        remaining = await count_remaining_codes(conn, current_user.id)

    enabled = bool(row["enabled"]) if row else False
    verified_at = row["verified_at"] if row else None

    return MFAStatusResponse(
        enabled=enabled,
        verified_at=verified_at,
        recovery_codes_remaining=remaining if enabled else 0,
    )


@router.post("/disable")
async def disable_mfa(
    body: MFADisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> dict[str, str]:
    """
    Disable MFA for the current user.

    OWASP MFA Cheat Sheet:
      "Require reauthentication with an existing enrolled factor before
       allowing changes."

    Requires the current account password to prevent an attacker with a
    stolen session from silently downgrading the account's security.

    On success:
      - Sets user_mfa.enabled = FALSE
      - Deletes all recovery codes
      - Updates user_profiles.mfa_enabled = FALSE
    """
    async with db_client.pool.acquire() as conn:
        # Load current password hash for reauthentication check
        pw_row = await conn.fetchrow(
            "SELECT password_hash FROM user_profiles WHERE id = $1",
            current_user.id,
        )

        if not pw_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        if not verify_password(body.password, pw_row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect.",
            )

        # Check MFA is actually enabled
        mfa_row = await conn.fetchrow(
            "SELECT id, enabled FROM user_mfa WHERE user_id = $1",
            current_user.id,
        )

        if not mfa_row or not mfa_row["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not currently enabled.",
            )

        # Disable MFA and clean up
        await conn.execute(
            """
            UPDATE user_mfa
               SET enabled      = FALSE,
                   verified_at  = NULL,
                   updated_at   = NOW(),
                   last_used_at = NULL
             WHERE user_id      = $1
            """,
            current_user.id,
        )

        await conn.execute(
            "UPDATE user_profiles SET mfa_enabled = FALSE WHERE id = $1",
            current_user.id,
        )

        await invalidate_all_codes(conn, current_user.id)

        # P3.2 — Disabling MFA is a security-affecting action. Force every
        # OTHER device/session to re-authenticate so a thief who stole one
        # session (then disabled MFA from it) can't keep using the long-
        # tail sessions elsewhere. The CURRENT session keeps working
        # until its JWT (15-min TTL) naturally expires.
        sessions_revoked = await revoke_all_user_sessions(
            conn,
            current_user.id,
            reason="mfa_disabled",
        )
        refresh_revoked = await revoke_all_user_refresh_tokens(
            conn,
            current_user.id,
            reason="mfa_disabled",
        )

    logger.info(
        "MFA disabled user=%s sessions_revoked=%s refresh_tokens_revoked=%s",
        current_user.id, sessions_revoked, refresh_revoked,
    )

    return {
        "detail": "MFA disabled successfully. All recovery codes have been deleted."
    }
