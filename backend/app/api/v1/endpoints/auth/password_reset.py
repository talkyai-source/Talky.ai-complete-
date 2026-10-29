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
           revoke ALL existing sessions AND ALL refresh tokens (OWASP
           guidance on password reset). Returns 200 on success, 400
           otherwise (generic error so attackers can't tell whether
           email-existed-but-code-wrong vs email-didn't-exist).

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

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.password import (
    PasswordValidationError,
    hash_password,
    validate_password_strength,
)
from app.core.security.lockout import record_login_attempt
from app.core.security.refresh_tokens import revoke_all_user_refresh_tokens
from app.core.security.sessions import revoke_all_user_sessions
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.email_service import get_email_service

from ._shared import get_client_ip, get_user_agent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_RESET_CODE_TTL_SECONDS = 15 * 60   # 15 minutes
_RESET_REDIS_KEY_PREFIX = "pwreset:pending:"

# Per-email rate limit on /forgot-password. Closes the inbox-bombing
# attack where an attacker spams password-reset emails to a victim
# (also avoids burning the M365 SMTP quota on noreply@talkleeai.com).
# 3 codes / hour is generous enough for a legitimate user retrying
# after a delayed delivery; combined with the 15-minute code TTL it
# means an attacker cannot request more than 3 codes in one hourly window.
_RESET_RATE_LIMIT_KEY_PREFIX = "pwreset:rl:"
_RESET_RATE_LIMIT_WINDOW_SECONDS = 60 * 60  # 1 hour
_RESET_RATE_LIMIT_MAX = 3

# A separate INCR then EXPIRE can leave a permanent block if the process or
# connection fails between commands. Repair old counters that lost their TTL
# without extending healthy windows or discarding their recorded attempts.
_CLAIM_RESET_SEND = """
local n = redis.call('INCR', KEYS[1])
if n == 1 or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return n
"""


def _reset_rate_limit_key(email: str) -> str:
    return f"{_RESET_RATE_LIMIT_KEY_PREFIX}{email.strip().lower()}"


async def _check_reset_rate_limit(redis, email: str) -> bool:
    """
    Returns True if the caller may issue another reset code for this
    email, False if they've hit the cap. Increments+sets-TTL atomically.

    Legacy counters without expiry receive a bounded recovery window.
    Unconfirmed claims do not authorize email; the caller keeps the same
    generic response for existing and unknown addresses.
    """
    key = _reset_rate_limit_key(email)
    try:
        count = await redis.eval(
            _CLAIM_RESET_SEND, 1, key, _RESET_RATE_LIMIT_WINDOW_SECONDS
        )
        return int(count) <= _RESET_RATE_LIMIT_MAX
    except Exception as exc:
        logger.warning("forgot_password_rate_limit_redis_error err=%s", type(exc).__name__)
        return False


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


def _password_generation(stored_hash) -> str:
    return hashlib.sha256(str(stored_hash or "").encode("utf-8")).hexdigest()


async def _clear_reset_request(redis, email, original) -> None:
    """Cleanup cannot remove a newer request or change a committed outcome."""
    try:
        await redis.eval("""if redis.call('GET',KEYS[1]) == ARGV[1] then
            return redis.call('DEL',KEYS[1]) else return 0 end""",
            1, _reset_redis_key(email), original)
    except Exception:
        logger.warning("password_reset_request_cleanup_unavailable")


# A 6-digit code has 1e6 values; uncapped, a few hundred requests a second
# find it within its 15-minute life. Every verification claims an attempt
# atomically BEFORE the code is compared, so parallel guesses cannot exceed
# the cap, and past it the code is burned. The counter is keyed by the
# code's hash, so a newly issued code starts its own count.
_RESET_MAX_ATTEMPTS = 5
_CLAIM_RESET_ATTEMPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return -2 end
local n = redis.call('INCR', KEYS[2])
if n == 1 then
  local ttl = redis.call('PTTL', KEYS[1])
  if ttl <= 0 then ttl = tonumber(ARGV[3]) end
  redis.call('PEXPIRE', KEYS[2], ttl)
end
if n > tonumber(ARGV[2]) then
  redis.call('DEL', KEYS[1])
  return -1
end
return n
"""


async def _claim_reset_attempt(redis, email: str, original, pending: dict) -> bool:
    """True when this verification may compare the code; fails closed."""
    attempts_key = (
        f"pwreset:attempts:{email.strip().lower()}:"
        f"{str(pending.get('code_hash') or '')[:16]}"
    )
    try:
        claimed = await redis.eval(
            _CLAIM_RESET_ATTEMPT, 2, _reset_redis_key(email), attempts_key,
            original, _RESET_MAX_ATTEMPTS, _RESET_CODE_TTL_SECONDS * 1000,
        )
    except Exception as exc:
        logger.warning("password_reset_attempt_claim_unavailable err=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis is unavailable; cannot reset password right now.",
        ) from exc
    return int(claimed) > 0


# --- refresh-token revocation reason ---------------------------------------
# The accurate audit reason for this path is "password_reset", and that is
# what this code attempts FIRST — see _revoke_refresh_tokens_for_reset below.
#
# History: `refresh_tokens.revoked_reason` carries a CHECK constraint,
# `refresh_tokens_revoked_reason_check`, created inline with the table by
# Alembic/versions/0002_add_refresh_tokens.py:36-38 and reproduced at
# database/schema/baseline_2026-06-02.sql:1187. It originally permitted only:
#
#     ('rotated', 'reuse_detected', 'logout', 'admin', 'expired')
#
# so "password_reset" was rejected outright, and the CheckViolationError —
# raised INSIDE the reset transaction — would have rolled back the new
# password hash and locked the user out of the very flow they use to recover
# a compromised account. Hence the SAVEPOINT + permitted-value fallback:
# revocation itself is never skipped, only the audit label degrades.
#
# database/migrations/20260729_widen_refresh_tokens_revoked_reason.sql widens
# the constraint to admit "password_reset" (along with "password_change",
# "mfa_disabled" and "expired_with_subsequent_use", which were hard 500s on
# their own paths — auth/password.py:134, mfa/status.py:135 and
# refresh_tokens.py:149 respectively, none of which had this guard).
#
# The SAVEPOINT is KEPT ON PURPOSE, not left behind by accident:
#
#   * Once the migration is applied, attempt #1 simply succeeds and the
#     accurate "password_reset" label is what gets written. The fallback
#     never fires. There is nothing to "clean up" for correctness — the only
#     cost is one extra round trip on a flow that runs a handful of times a
#     day.
#   * Keeping it makes this module safe to deploy in EITHER order relative to
#     the migration. The migration in this repo is applied by hand via psql
#     (there is no auto-runner), so "migration first" is a human step with no
#     enforcement. Removing the guard would convert a missed/late migration
#     from a degraded audit label into a failed password reset — the single
#     worst endpoint in the app to make order-dependent, because it is the
#     one users reach when they are already locked out.
#   * It is also the rollback path: if the migration is ever reverted (see
#     that file's ROLLBACK section), this endpoint keeps working.
#
# The invariant that the constraint must cover every reason the code writes
# is now pinned by tests/security/test_refresh_revocation_reasons.py.
_RESET_REVOKE_REASON = "password_reset"
_RESET_REVOKE_REASON_FALLBACK = "logout"


async def _revoke_refresh_tokens_for_reset(conn, user_id: str) -> int:
    """Revoke every refresh token for *user_id*, constraint-safely.

    Must be called inside the caller's transaction so the revocation
    commits atomically with the new password hash.
    """
    try:
        # Nested conn.transaction() == SAVEPOINT: a CHECK violation rolls
        # back to here, leaving the outer transaction usable.
        async with conn.transaction():
            return await revoke_all_user_refresh_tokens(
                conn,
                user_id,
                reason=_RESET_REVOKE_REASON,
            )
    except asyncpg.exceptions.CheckViolationError:
        logger.warning(
            "reset_password: refresh_tokens.revoked_reason rejected %r — "
            "database/migrations/20260729_widen_refresh_tokens_revoked_reason"
            ".sql has NOT been applied to this database. Retrying with %r; "
            "revocation still applies, only the audit label degrades. Apply "
            "the migration — auth/password.py and mfa/status.py have no such "
            "fallback and are returning 500s right now.",
            _RESET_REVOKE_REASON,
            _RESET_REVOKE_REASON_FALLBACK,
        )

    return await revoke_all_user_refresh_tokens(
        conn,
        user_id,
        reason=_RESET_REVOKE_REASON_FALLBACK,
    )


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
            "If the account is eligible, check its email for a reset code. "
            "A code expires in 15 minutes; if none arrives, try again later."
        ),
    }

    # Per-email rate limit BEFORE the user-existence check so the
    # response shape stays identical for existing / non-existing
    # addresses (no enumeration via timing or rate-limit observability).
    redis = _get_redis_or_503()
    if not await _check_reset_rate_limit(redis, email):
        logger.info("forgot_password_rate_limited email=%s", email)
        # Same 200 message — never tell the attacker they hit a limit.
        # Genuine users typing the same email repeatedly will see no
        # additional emails arriving; that's the intended behaviour.
        return generic_ok

    # Password recovery starts with an email, before a tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        user_row = await conn.fetchrow(
            "SELECT id,password_hash FROM user_profiles WHERE LOWER(email) = $1",
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
        "password_generation": _password_generation(user_row["password_hash"]),
    }
    # A storage or mail failure answers exactly like an unknown address. A 500
    # here (and only for registered emails) would reveal which accounts exist.
    try:
        await redis.setex(_reset_redis_key(email), _RESET_CODE_TTL_SECONDS, json.dumps(payload))
    except Exception as exc:
        logger.warning("forgot_password_pending_write_failed err=%s", type(exc).__name__)
        return generic_ok

    # Send the code.  Tolerate send failures (logged) so a misconfigured
    # SMTP setup doesn't 500 the caller — they'd just never receive a code,
    # which is the same observable failure as a typo'd email anyway.
    try:
        email_service = get_email_service()
        # EmailService may not have a dedicated reset method; the generic
        # signup-code template ("here is your code, expires in N minutes")
        # works for both.
        sender = getattr(email_service, "send_password_reset_email", None) \
            or email_service.send_signup_code_email
        sent = await sender(
            recipient_email=email,
            recipient_name=email.split("@")[0],
            code=code,
            expires_in_minutes=_RESET_CODE_TTL_SECONDS // 60,
        )
    except Exception as exc:
        logger.warning("forgot_password_email_send_error err=%s", type(exc).__name__)
        sent = False
    if not sent:
        logger.warning(
            "forgot_password_email_send_failed email=%s — code stored in Redis "
            "but email submission was not confirmed. Check SMTP_HOST / SMTP_USER / "
            "SMTP_PASSWORD env vars.",
            email,
        )

    try:
        await audit_logger.log(
            event_type=AuditEvent.USER_UPDATED,
            actor_id=user_id,
            actor_type="user",
            action="password_reset_requested",
            description="Password reset requested; email submission accepted" if sent else "Password reset requested; email submission unconfirmed",
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
        await _clear_reset_request(redis, email, raw)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    if not isinstance(pending, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )
    if not await _claim_reset_attempt(redis, email, raw, pending):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code. Request a new code.",
        )
    if not secrets.compare_digest(_hash_reset_code(code), str(pending.get("code_hash") or "")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    user_id = pending.get("user_id")
    if not user_id:
        # Should never happen — defensive guard.
        await _clear_reset_request(redis, email, raw)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code.",
        )

    new_hash = hash_password(new_password)

    # The server-stored Redis recovery record identifies a user, but carries no
    # tenant claim; resolve and mutate it under the narrow pre-auth scope.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        # Re-confirm the user still exists and the email hasn't changed.
        row = await conn.fetchrow(
            "SELECT id,password_hash FROM user_profiles WHERE id = $1 AND LOWER(email) = $2 FOR UPDATE",
            user_id, email,
        )
        if (not row or not isinstance(pending.get("password_generation"), str)
                or not secrets.compare_digest(pending["password_generation"], _password_generation(row["password_hash"]))):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset code. Request a new code.",
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
            sessions_revoked = await revoke_all_user_sessions(
                conn,
                user_id,
                reason="password_reset",
                exclude_token_hash=None,
            )

            # --- ALSO revoke every refresh token for the user -----------------
            # Mirrors change_password (auth/password.py) and MFA-disable
            # (mfa/status.py), both of which revoke sessions AND refresh
            # tokens. Without this, a stolen `talky_rt` cookie issued
            # before the reset would silently keep minting fresh 15-min
            # access JWTs for up to REFRESH_TOKEN_LIFETIME_DAYS (7) —
            # straight through the very flow the victim used to secure
            # their account. `security_sessions` and `refresh_tokens` are
            # independent stores: revoking one does nothing to the other,
            # and POST /auth/refresh consults only the latter.
            #
            # Unlike change_password there is no current session/family to
            # preserve — password reset is the account-RECOVERY path, so
            # EVERY device must re-authenticate, including the caller.
            refresh_revoked = await _revoke_refresh_tokens_for_reset(conn, user_id)

            # Lockout is computed from login_attempts, not the columns zeroed
            # above; a success row is the boundary that clears it.
            await record_login_attempt(
                conn, email=email, user_id=user_id, ip_address=get_client_ip(request),
                success=True, failure_reason="password_reset",
            )

    logger.info(
        "password_reset_completed user=%s sessions_revoked=%s refresh_tokens_revoked=%s",
        user_id, sessions_revoked, refresh_revoked,
    )

    await _clear_reset_request(redis, email, raw)

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
