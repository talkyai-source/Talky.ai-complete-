"""GET /auth/verify-email — consume an email-verification token.

POST /auth/verify-email/resend — send a fresh link to an unverified account.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.auth_abuse import budget_key, claim_budget, enforce_budget
from app.core.security.verification_tokens import (
    generate_verification_token,
    get_verification_token_expiry,
    hash_verification_token,
    verify_token_expiry,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.email_service import get_email_service

from ._shared import get_client_ip
from .schemas import VerifyEmailResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# Same shape of limits as POST /auth/forgot-password: per IP (5/min, 20/day)
# and per address (3/hour), so this cannot be used to bomb an inbox.
_RESEND_EMAIL_LIMIT = 3
_RESEND_EMAIL_WINDOW_SECONDS = 60 * 60
_RESEND_ANSWER = {"message": "If that address needs verifying, we have sent a new email."}


class ResendVerificationRequest(BaseModel):
    email: EmailStr


def _verification_redis():
    from app.core.container import get_container

    container = get_container()
    if not container.is_initialized or not container.redis_enabled:
        logger.error("verification_resend_unavailable: Redis is not available")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email is not available right now. Try again in a few minutes.",
        )
    return container.redis


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

    # The opaque verification token must be resolved before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
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


@router.post("/verify-email/resend")
async def resend_verification_email(
    request: Request,
    body: ResendVerificationRequest,
    db_client: Client = Depends(get_db_client),
) -> dict[str, str]:
    """Send a new verification link. Always answers 200 with the same body.

    Whether the address has no account, is already verified, is over its
    per-address limit, or the mail could not be sent, the caller sees the same
    answer; a link is sent only for an existing, active, unverified account.
    """
    email = body.email.strip().lower()
    address = get_client_ip(request)
    redis = _verification_redis()
    await enforce_budget(redis, "resend-verify-ip", address, limit=5, seconds=60)
    await enforce_budget(redis, "resend-verify-ip-day", address, limit=20, seconds=24 * 60 * 60)
    if not await claim_budget(redis, budget_key("resend-verify-email", email),
                              limit=_RESEND_EMAIL_LIMIT, seconds=_RESEND_EMAIL_WINDOW_SECONDS):
        logger.info("verification_resend_rate_limited")
        return dict(_RESEND_ANSWER)

    async with acquire_with_tenant(db_client.pool, None) as conn:
        user = await conn.fetchrow(
            """
            SELECT id, email, name, is_verified, is_active
            FROM user_profiles
            WHERE LOWER(email) = $1
            """,
            email,
        )
        if not user or user["is_verified"] or user["is_active"] is False:
            return dict(_RESEND_ANSWER)

        token = generate_verification_token()
        await conn.execute(
            """
            UPDATE user_profiles
            SET verification_token = $2,
                verification_token_expires_at = $3
            WHERE id = $1 AND is_verified = FALSE
            """,
            user["id"],
            hash_verification_token(token),
            get_verification_token_expiry(),
        )

    try:
        sent = await get_email_service().send_verification_email(
            recipient_email=user["email"],
            recipient_name=user["name"],
            verification_link=f"/auth/verify-email?token={token}",
        )
    except Exception as exc:
        logger.warning("verification_resend_email_error err=%s", type(exc).__name__)
        sent = False
    if not sent:
        logger.warning("verification_resend_email_unconfirmed user=%s", user["id"])
    return dict(_RESEND_ANSWER)
