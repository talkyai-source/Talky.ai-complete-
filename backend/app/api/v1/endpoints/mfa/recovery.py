"""POST /auth/mfa/recovery-codes/regenerate — replace all recovery codes.

Requires a valid current TOTP code (reauthentication) so a stolen session
can't silently harvest fresh backup codes. Returns the new codes ONCE.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.recovery import (
    format_recovery_code,
    generate_recovery_codes,
    invalidate_all_codes,
    store_recovery_codes,
)
from app.core.security.totp import decrypt_totp_secret, verify_totp_code

from .schemas import MFARegenerateCodesRequest, MFARegenerateCodesResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mfa"])


@router.post("/recovery-codes/regenerate", response_model=MFARegenerateCodesResponse)
async def regenerate_recovery_codes(
    body: MFARegenerateCodesRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MFARegenerateCodesResponse:
    """
    Regenerate recovery codes.

    Requires a valid current TOTP code to prevent an attacker with a
    stolen session from harvesting fresh recovery codes.

    Invalidates ALL existing recovery codes and generates a new batch of
    RECOVERY_CODE_COUNT single-use codes.  The new codes are returned ONCE
    and never retrievable again.

    Use case: user is running low on recovery codes, or suspects a code
    was compromised.
    """
    async with db_client.pool.acquire() as conn:
        mfa_row = await conn.fetchrow(
            "SELECT totp_secret_enc, enabled, last_used_at FROM user_mfa WHERE user_id = $1",
            current_user.id,
        )

        if not mfa_row or not mfa_row["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not currently enabled.",
            )

        # Require a valid current TOTP code (reauthentication)
        try:
            raw_secret = decrypt_totp_secret(mfa_row["totp_secret_enc"])
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA configuration error.",
            )

        code_valid = verify_totp_code(
            raw_secret,
            body.code,
            last_used_at=mfa_row["last_used_at"],
        )

        if not code_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code.",
            )

        # Update last_used_at after verification
        await conn.execute(
            "UPDATE user_mfa SET last_used_at = NOW() WHERE user_id = $1",
            current_user.id,
        )

        # Invalidate existing codes and generate fresh batch
        await invalidate_all_codes(conn, current_user.id)
        raw_codes = generate_recovery_codes()
        batch_id = str(uuid.uuid4())
        await store_recovery_codes(conn, current_user.id, raw_codes, batch_id=batch_id)

    logger.info(
        "Recovery codes regenerated for user=%s batch=%s", current_user.id, batch_id
    )

    formatted_codes = [format_recovery_code(c) for c in raw_codes]

    return MFARegenerateCodesResponse(
        recovery_codes=formatted_codes,
        recovery_codes_count=len(formatted_codes),
        message=(
            "Recovery codes regenerated. Save these in a safe place — "
            "they will not be shown again. All previous codes are now invalid."
        ),
    )
