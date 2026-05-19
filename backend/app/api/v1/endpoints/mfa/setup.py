"""POST /auth/mfa/setup + POST /auth/mfa/confirm — TOTP enrolment flow."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.recovery import (
    format_recovery_code,
    generate_recovery_codes,
    invalidate_all_codes,
    store_recovery_codes,
)
from app.core.security.totp import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_provisioning_uri,
    verify_totp_code,
)

from .schemas import (
    MFAConfirmRequest,
    MFAConfirmResponse,
    MFASetupResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mfa"])


@router.post("/setup", response_model=MFASetupResponse)
async def setup_mfa(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MFASetupResponse:
    """
    Initiate MFA setup for the current user.

    Generates a new TOTP secret, encrypts it with Fernet (AES-128-CBC +
    HMAC-SHA256), and stores it in user_mfa with enabled=FALSE.

    Returns the provisioning URI and a QR code PNG (base64 data URI) for
    the user to scan with Google Authenticator, Authy, or any RFC 6238 app.

    MFA is NOT active until the user calls POST /auth/mfa/confirm with a
    valid TOTP code from their authenticator app.

    OWASP: A new setup overwrites any existing pending (unconfirmed) secret.
    """
    async with db_client.pool.acquire() as conn:
        # P3.3 — refuse to silently downgrade an account that already has
        # confirmed MFA. The previous UPSERT would reset enabled=TRUE rows
        # back to FALSE, leaving the account briefly without 2FA AND
        # losing the recovery codes the user printed out. The user must
        # explicitly call /auth/mfa/disable (password-gated) first.
        existing = await conn.fetchrow(
            "SELECT enabled FROM user_mfa WHERE user_id = $1",
            current_user.id,
        )
        if existing and existing["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "MFA is already enabled for this account. "
                    "Disable it first via POST /auth/mfa/disable, then re-run setup."
                ),
            )

        raw_secret = generate_totp_secret()
        encrypted_secret = encrypt_totp_secret(raw_secret)

        provisioning_uri = get_provisioning_uri(raw_secret, current_user.email)
        qr_data_uri = generate_qr_code_data_uri(provisioning_uri)

        # Upsert any existing (unconfirmed) MFA record with a fresh secret.
        await conn.execute(
            """
            INSERT INTO user_mfa
                   (user_id, totp_secret_enc, enabled, verified_at,
                    created_at, updated_at, last_used_at)
            VALUES ($1, $2, FALSE, NULL, NOW(), NOW(), NULL)
            ON CONFLICT (user_id)
            DO UPDATE SET
                totp_secret_enc = EXCLUDED.totp_secret_enc,
                enabled         = FALSE,
                verified_at     = NULL,
                updated_at      = NOW(),
                last_used_at    = NULL
            """,
            current_user.id,
            encrypted_secret,
        )

        # Also mark user_profiles.mfa_enabled = FALSE (pending confirmation)
        await conn.execute(
            "UPDATE user_profiles SET mfa_enabled = FALSE WHERE id = $1",
            current_user.id,
        )

        # Delete any stale recovery codes from a previous MFA setup
        await invalidate_all_codes(conn, current_user.id)

    logger.info("MFA setup initiated for user=%s", current_user.id)

    return MFASetupResponse(
        provisioning_uri=provisioning_uri,
        qr_code=qr_data_uri,
        issuer="Talky.ai",
        account=current_user.email,
    )



@router.post("/confirm", response_model=MFAConfirmResponse)
async def confirm_mfa(
    body: MFAConfirmRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MFAConfirmResponse:
    """
    Confirm MFA setup by verifying the first TOTP code from the authenticator app.

    The user must have called POST /auth/mfa/setup first.
    On success:
      1. Sets user_mfa.enabled = TRUE
      2. Sets user_mfa.verified_at = NOW()
      3. Generates RECOVERY_CODE_COUNT single-use recovery codes
      4. Returns the raw recovery codes (shown ONCE — never retrievable again)
      5. Updates user_profiles.mfa_enabled = TRUE

    OWASP: Recovery codes must be provided at setup time.
    """
    async with db_client.pool.acquire() as conn:
        # Load the pending MFA record
        row = await conn.fetchrow(
            """
            SELECT id, totp_secret_enc, enabled, last_used_at
            FROM   user_mfa
            WHERE  user_id = $1
            """,
            current_user.id,
        )

        if not row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA setup not initiated. Call POST /auth/mfa/setup first.",
            )

        if row["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is already active. Disable it first to re-setup.",
            )

        # Decrypt and verify the TOTP code
        try:
            raw_secret = decrypt_totp_secret(row["totp_secret_enc"])
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA configuration error. Please contact support.",
            )

        last_used_at = row["last_used_at"]
        code_valid = verify_totp_code(
            raw_secret,
            body.code,
            last_used_at=last_used_at,
        )

        if not code_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid TOTP code. Check your authenticator app and try again.",
            )

        now = datetime.now(timezone.utc)

        # Activate MFA
        await conn.execute(
            """
            UPDATE user_mfa
               SET enabled      = TRUE,
                   verified_at  = $1,
                   updated_at   = $1,
                   last_used_at = $1
             WHERE user_id      = $2
            """,
            now,
            current_user.id,
        )

        # Sync the denormalized flag
        await conn.execute(
            "UPDATE user_profiles SET mfa_enabled = TRUE WHERE id = $1",
            current_user.id,
        )

        # Generate and store recovery codes
        raw_codes = generate_recovery_codes()
        await store_recovery_codes(conn, current_user.id, raw_codes)

    logger.info("MFA confirmed and activated for user=%s", current_user.id)

    formatted_codes = [format_recovery_code(c) for c in raw_codes]

    return MFAConfirmResponse(
        enabled=True,
        recovery_codes=formatted_codes,
        recovery_codes_count=len(formatted_codes),
        message=(
            "MFA activated successfully. "
            "Save these recovery codes in a safe place — "
            "they will not be shown again."
        ),
    )
