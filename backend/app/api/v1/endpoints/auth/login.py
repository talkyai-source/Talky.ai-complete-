"""POST /auth/login — credential verification with OWASP-aligned controls."""

import logging
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.lockout import (
    check_account_locked,
    record_login_attempt,
    seconds_until_unlocked,
)
from app.core.security.password import rehash_if_needed, verify_password
from app.core.security.sessions import SESSION_COOKIE_NAME, create_session
from app.domain.services.audit_logger import AuditEvent, AuditLogger

from ._shared import (
    COOKIE_MAX_AGE,
    GENERIC_AUTH_ERROR,
    create_jwt,
    get_client_ip,
    get_user_agent,
    issue_cookie_auth,
    limiter,
    session_cookie_secure,
)
from .schemas import LoginRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


@router.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest = Body(...),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Login with email + password.

    Security controls applied (OWASP Authentication Cheat Sheet):
      1. IP-level rate limit (10/minute via slowapi).
      2. Per-account lockout check (login_attempts table).
      3. Constant-time password comparison (argon2-cffi / bcrypt both do this).
      4. Generic error message regardless of failure reason.
      5. Every attempt (success + failure) logged to login_attempts.
      6. Argon2id rehash on login if stored hash is legacy bcrypt or outdated.
      7. Server-side session created; raw token delivered in httpOnly cookie.
      8. Retry-After header on locked accounts (without revealing lock reason).
    """
    ip = get_client_ip(request)
    ua = get_user_agent(request)
    normalised_email = body.email.lower()

    async with db_client.pool.acquire() as conn:
        # --- per-account lockout check -----------------------------------------
        locked_until = await check_account_locked(conn, normalised_email)
        if locked_until is not None:
            # Day 8: Log lockout security event
            await audit_logger.log_security_event(
                event_type="account_lockout",
                severity="HIGH",
                description=f"Blocked login attempt for locked account: {normalised_email}",
                metadata={"email": normalised_email},
                ip_address=ip,
                user_agent=ua,
            )

            retry_after = await seconds_until_unlocked(conn, normalised_email)
            # Record the blocked attempt
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=None,
                ip_address=ip,
                success=False,
                failure_reason="account_locked",
            )
            # OWASP: generic error — do not confirm the account is locked
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_AUTH_ERROR,
                headers={"Retry-After": str(retry_after)},
            )

        # --- fetch user row (email lookup) -------------------------------------
        row = await conn.fetchrow(
            """
            SELECT up.id,
                   up.email,
                   up.name,
                   up.role,
                   up.password_hash,
                   up.tenant_id,
                   up.is_active,
                   up.is_verified,
                   up.mfa_enabled,
                   t.business_name,
                   t.minutes_allocated,
                   t.minutes_used
            FROM   user_profiles up
            LEFT   JOIN tenants t ON t.id = up.tenant_id
            WHERE  up.email = $1
            """,
            normalised_email,
        )

        # --- user-not-found branch ---------------------------------------------
        if not row or not row["password_hash"]:
            # Day 8: Log suspicious attempt (user enumeration protection)
            await audit_logger.log_security_event(
                event_type="failed_login_user_not_found",
                severity="LOW",
                description=f"Login attempt for non-existent user: {normalised_email}",
                metadata={"email": normalised_email},
                ip_address=ip,
                user_agent=ua,
            )

            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=None,
                ip_address=ip,
                success=False,
                failure_reason="user_not_found",  # internal only — never sent to client
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_AUTH_ERROR,
            )

        user_id = str(row["id"])

        # --- inactive / suspended account check --------------------------------
        if not row.get("is_active", True):
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_inactive",  # internal only
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_AUTH_ERROR,
            )

        # --- email verification check ------------------------------------------
        # Day 1: Block login if email not verified
        if not row.get("is_verified", False):
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="email_not_verified",  # internal only
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please verify your email before logging in.",
            )

        # --- password verification (constant-time via argon2-cffi / bcrypt) ----
        password_ok = verify_password(body.password, row["password_hash"])

        if not password_ok:
            # Day 8: Log failed login security event
            await audit_logger.log_security_event(
                event_type="failed_login_wrong_password",
                severity="MEDIUM",
                description=f"Failed login (wrong password) for user: {normalised_email}",
                user_id=uuid.UUID(user_id),
                tenant_id=row["tenant_id"],
                ip_address=ip,
                user_agent=ua,
            )

            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="wrong_password",  # internal only
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_AUTH_ERROR,
            )

        # --- SUCCESS -----------------------------------------------------------
        # Record success (resets the effective failure window)
        await record_login_attempt(
            conn,
            email=normalised_email,
            user_id=user_id,
            ip_address=ip,
            success=True,
        )

        # Update last_login_at on user profile
        await conn.execute(
            "UPDATE user_profiles SET last_login_at = NOW() WHERE id = $1",
            user_id,
        )

        # --- silent Argon2id rehash (bcrypt → Argon2id upgrade) ---------------
        new_hash = rehash_if_needed(body.password, row["password_hash"])
        if new_hash:
            await conn.execute(
                "UPDATE user_profiles SET password_hash = $1 WHERE id = $2",
                new_hash,
                user_id,
            )
            logger.info("Password hash upgraded to Argon2id for user=%s", user_id)

        # --- MFA check: if user has MFA enabled, issue a challenge token ------
        # instead of a full JWT.  The client must complete step-2 via
        # POST /auth/mfa/verify to receive the real session + JWT.
        mfa_enabled = bool(row.get("mfa_enabled", False))
        if mfa_enabled:
            from app.api.v1.endpoints.mfa import create_mfa_challenge

            mfa_challenge_token = await create_mfa_challenge(
                conn,
                user_id=user_id,
                ip_address=ip,
                user_agent=ua,
            )

            logger.info(
                "MFA required — challenge issued for user=%s (no JWT yet)", user_id
            )

            # Return early — no JWT, no session cookie.
            return JSONResponse(content={
                "access_token": "",
                "token_type": "bearer",
                "user_id": user_id,
                "email": row["email"],
                "role": row["role"],
                "business_name": row["business_name"],
                "minutes_remaining": 0,
                "mfa_required": True,
                "mfa_challenge_token": mfa_challenge_token,
                "message": "MFA verification required. Use mfa_challenge_token with POST /auth/mfa/verify.",
            })

        # --- create new server-side session (OWASP: rotate on login) ----------
        # Day 5: Pass request for device fingerprinting
        raw_session_token, session_id = await create_session(
            conn,
            user_id=user_id,
            ip_address=ip,
            user_agent=ua,
            request=request,
            bind_to_ip=True,
            bind_to_fingerprint=True,
            return_session_id=True,
        )

    # --- build response --------------------------------------------------------
    minutes_remaining = max(
        0,
        (row["minutes_allocated"] or 0) - (row["minutes_used"] or 0),
    )
    tenant_id = str(row["tenant_id"]) if row["tenant_id"] else None
    token = create_jwt(user_id, row["email"], row["role"], tenant_id, session_id)

    # --- log login event (Day 8) -----------------------------------------------
    await audit_logger.log(
        event_type=AuditEvent.LOGIN_SUCCESS,
        actor_id=user_id,
        actor_type="user",
        tenant_id=tenant_id,
        action="user_logged_in",
        description=f"User logged in: {row['email']}",
        ip_address=ip,
        user_agent=ua,
    )

    resp = JSONResponse(content={
        "access_token": token,
        "token_type": "bearer",
        "user_id": user_id,
        "email": row["email"],
        "role": row["role"],
        "business_name": row["business_name"],
        "minutes_remaining": minutes_remaining,
        "message": "Login successful.",
        "mfa_required": False,
    })
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        httponly=True,
        secure=session_cookie_secure(),
        samesite="strict",
        max_age=COOKIE_MAX_AGE,
        path="/",
    )

    async with db_client.pool.acquire() as conn:
        await issue_cookie_auth(
            resp,
            conn,
            user_id=user_id,
            email=row["email"],
            role=row["role"],
            tenant_id=tenant_id,
            session_id=session_id,
            ip=ip,
            user_agent=ua,
        )
    return resp
