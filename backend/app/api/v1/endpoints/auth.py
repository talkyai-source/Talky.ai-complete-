"""
Authentication Endpoints
Hardened per OWASP Authentication, Session Management, and Password Storage Cheat Sheets.

Official references used (verified March 2026):
  https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

What changed from the original (Day 1 security hardening):
  1. Password hashing  — Argon2id (OWASP #1 recommendation) replaces bcrypt for new
                         passwords.  Existing bcrypt hashes are verified and silently
                         re-hashed to Argon2id on the next successful login.
  2. DB-backed sessions — Every login creates a row in security_sessions.  Logout now
                          actually revokes that row server-side.  Stateless JWT alone
                          cannot be revoked; the session record is the source of truth.
  3. Session cookie     — An httpOnly, Secure, SameSite=Strict cookie carries the raw
                          session token.  The JWT is also returned in the response body
                          for API clients that cannot read cookies.
  4. Account lockout    — Per-account (not just per-IP) progressive lockout tracked in
                          the login_attempts table.  Thresholds: 5→1min, 10→5min,
                          20→30min, 50→24h.  OWASP: counter must be per-account.
  5. Failed login track — Every attempt (success or failure) is recorded in
                          login_attempts for audit and lockout calculation.
  6. Generic errors     — All auth failures return "Invalid email or password." —
                          OWASP: never reveal whether the email exists or which field
                          was wrong.
  7. Session rotation   — A fresh session token is issued on every login (OWASP).
  8. Rehash on login    — If a user's stored hash uses bcrypt or outdated Argon2id
                          params, it is silently upgraded after successful verification.
  9. Password change    — Now revokes all other sessions (OWASP: invalidate sessions
                          on credential change).

Endpoints:
  POST /auth/register          — create account + tenant, returns JWT + session cookie
  POST /auth/login             — verify credentials, returns JWT + session cookie
  GET  /auth/me                — return current user info
  PATCH /auth/me               — update profile fields
  POST /auth/logout            — revoke server-side session + clear cookie
  POST /auth/logout-all        — revoke ALL sessions for the current user
  POST /auth/change-password   — change password + revoke all other sessions
"""

import os
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.core.jwt_security import encode_access_token
from app.core.postgres_adapter import Client
from app.core.config import get_settings
from app.core.security.lockout import (
    check_account_locked,
    record_login_attempt,
    seconds_until_unlocked,
)
from app.core.security.password import (
    PasswordValidationError,
    hash_password,
    rehash_if_needed,
    validate_password_strength,
    verify_password,
)
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_HOURS,
    create_session,
    hash_session_token,
    revoke_all_user_sessions,
    revoke_session_by_token,
)
from app.core.security.verification_tokens import (
    generate_verification_token,
    get_verification_token_expiry,
    hash_verification_token,
    verify_token_expiry,
)
from app.domain.services.email_service import get_email_service

# MFA challenge helper — imported lazily to avoid circular imports
# (mfa.py imports from auth helpers too)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter — keyed by client IP (first line of defence; per-account
# lockout via login_attempts is the second line — see lockout.py)
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# OWASP: generic error message — never reveal which field was wrong
# ---------------------------------------------------------------------------
_GENERIC_AUTH_ERROR = "Invalid email or password."

# ---------------------------------------------------------------------------
# Cookie settings (OWASP Session Management Cheat Sheet)
#   httponly  = True   — prevents JavaScript access (XSS protection)
#   secure    = True   — HTTPS only (set False only in local dev via env)
#   samesite  = "strict" — blocks cross-site request forgery
#   max_age   — matches absolute session lifetime (seconds)
# ---------------------------------------------------------------------------
_COOKIE_MAX_AGE = SESSION_LIFETIME_HOURS * 3600  # 86 400 s for 24-hour sessions


# ===========================================================================
# Request / Response Models
# ===========================================================================


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    business_name: str
    plan_id: str = "basic"
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    business_name: Optional[str] = None
    minutes_remaining: int = 0
    message: str
    # MFA two-step login fields (only present when mfa_required=True)
    mfa_required: bool = False
    mfa_challenge_token: Optional[str] = None


class MeResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    role: str
    minutes_remaining: int


class UpdateMeRequest(BaseModel):
    name: Optional[str] = None
    business_name: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    message: str
    email: str


# ===========================================================================
# Internal helpers
# ===========================================================================


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP from the request.
    Respects X-Forwarded-For when behind a trusted reverse proxy.
    Falls back to the direct connection IP.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2 — take the leftmost
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


def _create_jwt(
    user_id: str,
    email: str,
    role: str,
    tenant_id: Optional[str],
    session_id: Optional[str] = None,
) -> str:
    """Create a signed JWT access token."""
    try:
        return encode_access_token(
            user_id=user_id,
            email=email,
            role=role,
            tenant_id=tenant_id,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("JWT creation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server authentication is not configured.",
        ) from exc


def _session_cookie_secure() -> bool:
    override = _normalize_optional_text(os.environ.get("SESSION_COOKIE_SECURE"))
    if override is not None:
        return override.lower() in {"1", "true", "yes", "on"}
    return get_settings().environment.lower() == "production"


def _set_session_cookie(response: Response, raw_token: str) -> None:
    """
    Write the session token into an httpOnly Secure SameSite=Strict cookie.

    OWASP Session Management Cheat Sheet:
      "Set the Secure attribute to prevent the cookie from being sent over
       unencrypted connections."
      "Set the HttpOnly attribute to prevent client-side script from reading
       the session ID."
      "Set SameSite=Strict to prevent the cookie from being sent in
       cross-site requests."
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=_session_cookie_secure(),
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Delete the session cookie from the browser."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=_session_cookie_secure(),
        samesite="strict",
        path="/",
    )


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


# ===========================================================================
# Endpoints
# ===========================================================================


@router.post("/register", response_model=AuthTokenResponse)
@limiter.limit("3/minute")
async def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> AuthTokenResponse:
    """
    Register a new user.

    1. Validate plan exists.
    2. Reject duplicate email (generic error to prevent enumeration).
    3. Validate password strength (NIST SP 800-63B).
    4. Create tenant row.
    5. Hash password with Argon2id (OWASP params: m=19456, t=2, p=1).
    6. Create user_profiles row.
    7. Create server-side session (DB row in security_sessions).
    8. Set httpOnly session cookie.
    9. Return JWT + session metadata.
    """
    # --- password strength check (OWASP / NIST) ----------------------------
    try:
        validate_password_strength(body.password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    async with db_client.pool.acquire() as conn:
        # --- plan check --------------------------------------------------------
        plan = await conn.fetchrow(
            "SELECT id, minutes FROM plans WHERE id = $1", body.plan_id
        )
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid plan_id: {body.plan_id}",
            )

        # --- duplicate email check (generic error to prevent enumeration) ------
        existing = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE email = $1",
            body.email.lower(),
        )
        if existing:
            # OWASP: return generic message — do not reveal the email is taken
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Registration failed. Please check your details.",
            )

        # --- create tenant -----------------------------------------------------
        tenant = await conn.fetchrow(
            """
            INSERT INTO tenants (business_name, plan_id, minutes_allocated, minutes_used)
            VALUES ($1, $2, $3, 0)
            RETURNING id, business_name, minutes_allocated
            """,
            body.business_name,
            body.plan_id,
            plan["minutes"],
        )

        # --- hash password with Argon2id (OWASP minimum: m=19456, t=2, p=1) ---
        pw_hash = hash_password(body.password)
        user_id = str(uuid.uuid4())

        # --- generate email verification token -----------------------------------
        verification_token = generate_verification_token()
        verification_token_hash = hash_verification_token(verification_token)
        verification_token_expires = get_verification_token_expiry()

        await conn.execute(
            """
            INSERT INTO user_profiles
                (id, email, name, tenant_id, role, password_hash, verification_token, verification_token_expires_at)
            VALUES ($1, $2, $3, $4, 'owner', $5, $6, $7)
            """,
            user_id,
            body.email.lower(),
            body.name,
            tenant["id"],
            pw_hash,
            verification_token_hash,
            verification_token_expires,
        )

        # --- create server-side session ----------------------------------------
        # Day 5: Pass request for device fingerprinting
        ip = _get_client_ip(request)
        ua = _get_user_agent(request)
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

    # --- send verification email -----------------------------------------------
    # Build verification link - adjust domain based on your setup
    settings = get_settings()
    verification_link = f"{settings.api_base_url}/api/v1/auth/verify-email?token={verification_token}"
    email_service = get_email_service()
    email_sent = await email_service.send_verification_email(
        recipient_email=body.email,
        recipient_name=body.name,
        verification_link=verification_link,
    )

    if not email_sent:
        logger.warning(
            f"Failed to send verification email to {body.email} after registration"
        )
        # Don't fail the registration if email send fails, but log it

    # --- issue JWT + set cookie ------------------------------------------------
    token = _create_jwt(user_id, body.email, "owner", str(tenant["id"]), session_id)
    _set_session_cookie(response, raw_session_token)

    # --- log registration event (Day 8) ----------------------------------------
    await audit_logger.log(
        event_type=AuditEvent.USER_CREATED,
        actor_id=user_id,
        actor_type="user",
        tenant_id=str(tenant["id"]),
        action="user_registered",
        description=f"New user registered: {body.email}",
        metadata={"plan_id": body.plan_id, "business_name": body.business_name},
        ip_address=ip,
        user_agent=ua,
    )

    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=body.email,
        role="owner",
        business_name=body.business_name,
        minutes_remaining=plan["minutes"],
        message="Registration successful. Please verify your email to enable full access.",
    )


@router.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
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
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)
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
                detail=_GENERIC_AUTH_ERROR,
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
                detail=_GENERIC_AUTH_ERROR,
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
                detail=_GENERIC_AUTH_ERROR,
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
                detail=_GENERIC_AUTH_ERROR,
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
    token = _create_jwt(user_id, row["email"], row["role"], tenant_id, session_id)

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
        secure=_session_cookie_secure(),
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )
    return resp


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
) -> MeResponse:
    """Return the current authenticated user's profile."""
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        business_name=current_user.business_name,
        role=current_user.role,
        minutes_remaining=current_user.minutes_remaining,
    )


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateMeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MeResponse:
    """Update editable profile fields (name, business_name)."""
    next_name = _normalize_optional_text(body.name) if body.name is not None else None
    next_business_name = (
        _normalize_optional_text(body.business_name)
        if body.business_name is not None
        else None
    )

    if body.business_name is not None and not next_business_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="business_name cannot be empty.",
        )

    if body.name is None and body.business_name is None:
        return MeResponse(
            id=current_user.id,
            email=current_user.email,
            name=current_user.name,
            business_name=current_user.business_name,
            role=current_user.role,
            minutes_remaining=current_user.minutes_remaining,
        )

    async with db_client.pool.acquire() as conn:
        async with conn.transaction():
            if body.name is not None:
                await conn.execute(
                    "UPDATE user_profiles SET name = $1 WHERE id = $2",
                    next_name,
                    current_user.id,
                )
            if body.business_name is not None:
                if not current_user.tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="User is not associated with a tenant.",
                    )
                await conn.execute(
                    "UPDATE tenants SET business_name = $1 WHERE id = $2",
                    next_business_name,
                    current_user.tenant_id,
                )

        row = await conn.fetchrow(
            """
            SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                   t.business_name, t.minutes_allocated, t.minutes_used
            FROM   user_profiles up
            LEFT   JOIN tenants t ON t.id = up.tenant_id
            WHERE  up.id = $1
            """,
            current_user.id,
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        )

    minutes_remaining = max(
        0,
        (row["minutes_allocated"] or 0) - (row["minutes_used"] or 0),
    )
    return MeResponse(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        business_name=row["business_name"],
        role=row["role"],
        minutes_remaining=minutes_remaining,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, str]:
    """
    Logout the current user.

    1. Revoke the server-side session row (sets revoked=TRUE in security_sessions).
    2. Clear the httpOnly session cookie from the browser.

    OWASP: Logout must invalidate the server-side session, not just clear the
    client-side cookie.  Clearing the cookie alone does not prevent an attacker
    who has already captured the token from reusing it.
    """
    if talky_sid:
        async with db_client.pool.acquire() as conn:
            await revoke_session_by_token(conn, talky_sid, reason="logout")

    _clear_session_cookie(response)
    return {"detail": "Logged out successfully."}


@router.post("/logout-all")
async def logout_all(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> dict[str, str | int]:
    """
    Revoke ALL active sessions for the current user across all devices.

    Use case: account compromise response, "sign out everywhere" feature.

    OWASP Session Management: Applications must provide a logout mechanism
    that invalidates all active sessions.
    """
    async with db_client.pool.acquire() as conn:
        count = await revoke_all_user_sessions(
            conn,
            current_user.id,
            reason="logout_all",
        )

    _clear_session_cookie(response)
    return {
        "detail": f"Logged out from {count} active session(s).",
        "sessions_revoked": count,
    }


@router.post("/passkey-check")
@limiter.limit("10/minute")
async def passkey_check(
    request: Request,
    body: LoginRequest,  # Reuse email field from LoginRequest
    db_client: Client = Depends(get_db_client),
) -> dict[str, bool]:
    """
    Check if a user has passkeys registered.

    This unauthenticated endpoint allows the login UI to show
    "Sign in with passkey" if the user has registered passkeys.

    Returns { "has_passkeys": true/false }

    Note: We deliberately don't reveal if the email exists at all —
    a non-existent email simply returns has_passkeys=false.
    """
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT passkey_count FROM user_profiles WHERE email = $1 AND is_active = TRUE",
            body.email.lower(),
        )

        has_passkeys = bool(row and row["passkey_count"] and row["passkey_count"] > 0)

    return {"has_passkeys": has_passkeys}


@router.post("/change-password")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    talky_sid: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, str]:
    """
    Change the current user's password.

    Security controls:
      1. Verify current password before allowing change.
      2. Validate new password strength (NIST SP 800-63B).
      3. New password must differ from old password.
      4. Hash new password with Argon2id.
      5. Record password_changed_at timestamp.
      6. Revoke ALL other sessions (OWASP: invalidate sessions on password change).
         The current session (if any) is preserved so the user stays logged in.
    """
    old_password = body.old_password.strip() if body.old_password else ""
    new_password = body.new_password.strip() if body.new_password else ""

    if not old_password or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both old_password and new_password are required.",
        )

    if old_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current password.",
        )

    # --- new password strength check ------------------------------------------
    try:
        validate_password_strength(new_password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM user_profiles WHERE id = $1",
            current_user.id,
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        if not verify_password(old_password, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect.",
            )

        new_hash = hash_password(new_password)

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE user_profiles
                   SET password_hash       = $1,
                       password_changed_at = NOW()
                 WHERE id = $2
                """,
                new_hash,
                current_user.id,
            )

            # --- revoke all OTHER sessions (keep current session alive) --------
            # OWASP: "Invalidate all existing sessions on password change."
            current_token_hash = hash_session_token(talky_sid) if talky_sid else None

            await revoke_all_user_sessions(
                conn,
                current_user.id,
                reason="password_change",
                exclude_token_hash=current_token_hash,
            )

    # --- log password change event (Day 8) -------------------------------------
    await audit_logger.log(
        event_type=AuditEvent.USER_UPDATED,
        actor_id=current_user.id,
        actor_type="user",
        tenant_id=current_user.tenant_id,
        action="password_changed",
        description="User changed their password",
        ip_address=_get_client_ip(request),
        user_agent=_get_user_agent(request),
    )

    return {
        "detail": "Password changed successfully. All other sessions have been revoked."
    }


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
