"""GET /auth/verify-email — consume an email-verification token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.verification_tokens import (
    hash_verification_token,
    verify_token_expiry,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger

from .schemas import VerifyEmailResponse

router = APIRouter(tags=["auth"])


@router.get("/verify-email", response_model=VerifyEmailResponse)
async def verify_email(
    token: str,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> VerifyEmailResponse:
    """
    Verify user's email address.

    Accepts a verification token from email link and marks the user as verified.
    Token must be valid and not expired.

    Args:
        token: The verification token from the email link

    Returns:
        Confirmation message with the verified email address
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is required.",
        )

    # Hash the token for database lookup
    token_hash = hash_verification_token(token)

    async with db_client.pool.acquire() as conn:
        # --- lookup user by token ----------------------------------------------
        row = await conn.fetchrow(
            """
            SELECT id, email, verification_token, verification_token_expires_at, is_verified
            FROM user_profiles
            WHERE verification_token = $1
            """,
            token_hash,
        )

        # --- token not found or user not found ---------------------------------
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid or expired verification token.",
            )

        # --- already verified --------------------------------------------------
        if row["is_verified"]:
            return VerifyEmailResponse(
                message="Email is already verified.",
                email=row["email"],
            )

        # --- token expired check -----------------------------------------------
        if not verify_token_expiry(row["verification_token_expires_at"]):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Verification token has expired. Please request a new one.",
            )

        # --- mark email as verified --------------------------------------------
        user_id = row["id"]
        await conn.execute(
            """
            UPDATE user_profiles
            SET is_verified = TRUE,
                verification_token = NULL,
                verification_token_expires_at = NULL,
                email_verified_at = NOW()
            WHERE id = $1
            """,
            user_id,
        )

        # --- log email verification event (Day 8) --------------------------------
        await audit_logger.log(
            event_type=AuditEvent.USER_UPDATED,
            actor_id=user_id,
            actor_type="user",
            action="email_verified",
            description=f"User verified their email: {row['email']}",
        )

    return VerifyEmailResponse(
        message="Email verified successfully! You can now log in.",
        email=row["email"],
    )
