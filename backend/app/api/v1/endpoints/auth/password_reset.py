"""Password-reset flow — two-step, email-code based.

Step 1: POST /auth/forgot-password   {email}
        -> If a user with that email exists, generate a 6-digit code,
           store its hash in Redis (key `pwreset:pending:<email>`) with
           TTL 15 min, and send the code via SMTP. Always returns 200
           regardless of whether the email is registered, so an attacker
           can't enumerate users.

Step 2: POST /auth/reset-password    {email, code, new_password}
        -> Verify the code from Redis (hashed compare), validate the new
           password's strength, update user_profiles.password_hash, and
           revoke ALL existing sessions (OWASP guidance on password
           reset). Returns 200 on success, 400 otherwise (generic error
           so attackers can't tell whether email-existed-but-code-wrong
           vs email-didn't-exist).

The whole flow deliberately mirrors signup_start / signup_complete so
the frontend pattern (email -> code email -> code+new-password) stays
consistent and the SMTP / Redis plumbing is reused.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.password import (
    PasswordValidationError,
    hash_password,
    validate_password_strength,
)
from app.core.security.sessions import revoke_all_user_sessions
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.email_service import get_email_service

from ._shared import get_client_ip, get_user_agent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_RESET_CODE_TTL_SECONDS = 15 * 60   # 15 minutes
_RESET_REDIS_KEY_PREFIX = "pwreset:pending:"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    new_password: str
    confirm_password: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers (mirrors signup.py)
# ---------------------------------------------------------------------------

def _reset_redis_key(email: str) -> str:
    return f"{_RESET_REDIS_KEY_PREFIX}{email.strip().lower()}"


def _hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _get_redis_or_503():
    from app.core.container import get_container
    container = get_container()
    if not container.is_initialized or not container.redis_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis is unavailable; cannot reset password right now.",
        )
    return container.redis


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/forgot-password")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> dict[str, str]:
    """
    Send a password-reset code via email. Always returns 200 to prevent
    user-enumeration. The frontend should follow the success response
    with a 'code entry + new password' screen regardless.
    """
    email = body.email.strip().lower()
    generic_ok = {
        "message": (
            "If an account exists for that email, a 6-digit reset code "
            "has been sent. The code expires in 15 minutes."
        ),
    }

    async with db_client.pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE LOWER(email) = $1",
            email,
        )

    # Always behave the same on the wire — return 200 even if the email
    # isn't in our system. Just don't bother generating a code in that case.
    if not user_row:
        logger.info("forgot_password ignored (no user) email=%s", email)
        return generic_ok

    user_id = str(user_row["id"])

    # Generate a 6-digit code and stash its hash in Redis.
    code = f"{secrets.randbelow(1_000_000):06d}"
    payload = {
        "user_id": user_id,
        "email": email,
        "code_hash": _hash_reset_code(code),
    }
    redis = _get_redis_or_503()
    await redis.setex(_reset_redis_key(email), _RESET_CODE_TTL_SECONDS, json.dumps(payload))

    # Send the code.  Tolerate send failures (logged) so a misconfigured
    # SMTP setup doesn't 500 the caller — they'd just never receive a code,
    # which is the same observable failure as a typo'd email anyway.
    email_service = get_email_service()
    try:
        sent = await email_service.send_password_reset_email(
            recipient_email=email,
            recipient_name=email.split("@")[0],
            code=code,
            expires_in_minutes=_RESET_CODE_TTL_SECONDS // 60,
        )
    except AttributeError:
        # EmailService may not have a dedicated reset method yet; fall back
        # to the generic signup-code template — the body wording is similar
        # ("here is your code, expires in N minutes") and works for both.
        sent = await email_service.send_signup_code_email(
            recipient_email=email,
            recipient_name=email.split("@")[0],
            code=code,
            expires_in_minutes=_RESET_CODE_TTL_SECONDS // 60,
        )
    if not sent:
        logger.warning(
            "forgot_password_email_send_failed email=%s — code stored in Redis "
            "but no email was delivered. Check SMTP_HOST / SMTP_USER / "
            "SMTP_PASSWORD env vars.",
            email,
        )

    try:
        await audit_logger.log(
            event_type=AuditEvent.USER_UPDATED,
            actor_id=user_id,
            actor_type="user",
            action="password_reset_requested",
            description=f"Password reset code emailed to {email}",
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    except Exception:
        # Audit logging is best-effort; never fail the request on it.
        logger.exception("audit log failed for password_reset_requested")

    return generic_ok


@router.post("/reset-password")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> dict[str, str]:
    """
    Verify the 6-digit code from the email and set a new password.
    """
    email = body.email.strip().lower()
    code = body.code.strip()
    new_password = body.new_password
    confirm = body.confirm_password

    if confirm is not None and confirm != new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match.",
        )

    try:
        validate_password_strength(new_password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    redis = _get_redis_or_503()
    raw = await redis.get(_reset_redis_key(email))
    if not raw:
        # Could be: never requested, expired, or already consumed.  Same
        # error message in all cases to avoid leaking timing info.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    try:
        pending = json.loads(raw)
    except Exception:
        # Corrupted Redis entry — treat as invalid.
        await redis.delete(_reset_redis_key(email))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    if _hash_reset_code(code) != pending.get("code_hash"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    user_id = pending.get("user_id")
    if not user_id:
        # Should never happen — defensive guard.
        await redis.delete(_reset_redis_key(email))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    new_hash = hash_password(new_password)

    async with db_client.pool.acquire() as conn:
        # Re-confirm the user still exists and the email hasn't changed.
        row = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE id = $1 AND LOWER(email) = $2",
            user_id, email,
        )
        if not row:
            await redis.delete(_reset_redis_key(email))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset code.",
            )

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE user_profiles
                   SET password_hash       = $1,
                       password_changed_at = NOW(),
                       failed_login_count  = 0,
                       account_locked_until = NULL
                 WHERE id = $2
                """,
                new_hash,
                user_id,
            )
            # OWASP: invalidate all sessions on password reset.
            await revoke_all_user_sessions(
                conn,
                user_id,
                reason="password_reset",
                exclude_token_hash=None,
            )

    await redis.delete(_reset_redis_key(email))

    try:
        await audit_logger.log(
            event_type=AuditEvent.USER_UPDATED,
            actor_id=user_id,
            actor_type="user",
            action="password_reset_completed",
            description=f"Password reset completed for {email}",
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
    except Exception:
        logger.exception("audit log failed for password_reset_completed")

    return {
        "message": "Password has been reset. Please log in with your new password.",
    }
