"""Two-step signup flow — email-first, code-then-password.

Step 1: POST /auth/signup/start    {name, business_name, email}
        -> generates a 6-digit code, sends it to the email, stores a
           short-lived pending record in Redis. NO database row is
           created yet.
Step 2: POST /auth/signup/complete {email, code, password, confirm_password}
        -> validates the code, creates tenants + user_profiles
           (plan_id="free" hardcoded), issues JWT + session cookie.

plan_id is never accepted from the frontend — every new account lands
on the `free` tier and upgrades happen later from the dashboard.
"""

import hashlib
import json
import logging
import secrets
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.postgres_adapter import Client
from app.core.security.password import (
    PasswordValidationError,
    hash_password,
    validate_password_strength,
)
from app.core.security.sessions import create_session
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.email_service import get_email_service

from ._shared import (
    create_jwt,
    get_client_ip,
    get_user_agent,
    limiter,
    set_session_cookie,
)
from .schemas import (
    AuthTokenResponse,
    SignupCompleteRequest,
    SignupStartRequest,
    SignupStartResponse,
    SignupVerifyCodeRequest,
    SignupVerifyCodeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_SIGNUP_CODE_TTL_SECONDS = 15 * 60   # 15 minutes
_SIGNUP_REDIS_KEY_PREFIX = "signup:pending:"


def _hash_signup_code(code: str) -> str:
    """SHA-256 hash for the 6-digit code (so we never store the raw code)."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _signup_redis_key(email: str) -> str:
    return f"{_SIGNUP_REDIS_KEY_PREFIX}{email.strip().lower()}"


def _get_redis_or_503():
    """Lazy redis lookup; 503 if Redis isn't initialized.

    Avoids importing the container at module-load time and keeps the
    failure mode obvious to clients."""
    from app.core.container import get_container
    container = get_container()
    if not container.is_initialized or container.redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signup service temporarily unavailable.",
        )
    return container.redis


@router.post("/signup/start", response_model=SignupStartResponse)
@limiter.limit("3/minute")
async def signup_start(
    request: Request,
    body: SignupStartRequest = Body(...),
    db_client: Client = Depends(get_db_client),
) -> SignupStartResponse:
    """Step 1 of signup. Generate a 6-digit code, email it, store
    pending {name, business_name, email, code_hash} in Redis with a
    15-minute TTL. No DB row is created yet."""

    email = body.email.strip().lower()

    # Reject if a real account with this email already exists.
    async with db_client.pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE email = $1", email
        )
    if existing:
        # Generic message — don't confirm/deny enumeration. Match
        # /register's behaviour at line 322.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed. Please check your details.",
        )

    # 6-digit numeric code, zero-padded.
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = _hash_signup_code(code)

    pending_payload = {
        "name": body.name,
        "business_name": body.business_name,
        "email": email,
        "code_hash": code_hash,
    }

    redis = _get_redis_or_503()
    await redis.setex(
        _signup_redis_key(email),
        _SIGNUP_CODE_TTL_SECONDS,
        json.dumps(pending_payload),
    )

    # Send email. We tolerate send failures (logged) so a misconfigured
    # SMTP setup doesn't block development. In production, the SMTP env
    # vars must be set or no codes will ever reach users — see README.
    email_service = get_email_service()
    sent = await email_service.send_signup_code_email(
        recipient_email=email,
        recipient_name=body.name,
        code=code,
        expires_in_minutes=_SIGNUP_CODE_TTL_SECONDS // 60,
    )
    if not sent:
        logger.warning(
            "signup_start_email_send_failed email=%s — "
            "code stored in Redis but no email was delivered. "
            "Check SMTP_HOST / SMTP_USER / SMTP_PASSWORD env vars.",
            email,
        )

    return SignupStartResponse(
        message="Verification code sent to your email.",
        expires_in_minutes=_SIGNUP_CODE_TTL_SECONDS // 60,
        email=email,
    )


@router.post("/signup/verify-code", response_model=SignupVerifyCodeResponse)
@limiter.limit("10/minute")
async def signup_verify_code(
    request: Request,
    body: SignupVerifyCodeRequest = Body(...),
) -> SignupVerifyCodeResponse:
    """Check-only verification of the 6-digit code stored in Redis.

    Lets the frontend gate the password screen behind a correct code
    without consuming the pending record — /signup/complete still needs
    to find it. Returns 400 on invalid/expired."""

    email = body.email.strip().lower()
    redis = _get_redis_or_503()
    raw = await redis.get(_signup_redis_key(email))
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code expired or not found. "
                   "Please request a new code.",
        )

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        pending = json.loads(raw)
    except json.JSONDecodeError:
        await redis.delete(_signup_redis_key(email))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signup record corrupted. Please request a new code.",
        )

    if not secrets.compare_digest(
        _hash_signup_code(body.code.strip()),
        pending["code_hash"],
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code.",
        )

    return SignupVerifyCodeResponse(
        message="Code verified.",
        email=email,
    )


@router.post("/signup/complete", response_model=AuthTokenResponse)
@limiter.limit("5/minute")
async def signup_complete(
    request: Request,
    response: Response,
    body: SignupCompleteRequest = Body(...),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> AuthTokenResponse:
    """Step 2 of signup. Validate the code stored in Redis, then
    create tenant + user_profiles (plan_id always "free") and issue
    a JWT + session cookie just like /register does."""

    email = body.email.strip().lower()

    # Confirm passwords match.
    if body.password != body.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match.",
        )

    # Password strength (NIST SP 800-63B).
    try:
        validate_password_strength(body.password)
    except PasswordValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Pull pending signup from Redis.
    redis = _get_redis_or_503()
    raw = await redis.get(_signup_redis_key(email))
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code expired or not found. "
                   "Please request a new code.",
        )

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        pending = json.loads(raw)
    except json.JSONDecodeError:
        # Defensive: corrupted payload — drop it and ask user to restart.
        await redis.delete(_signup_redis_key(email))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signup record corrupted. Please request a new code.",
        )

    # Constant-time-ish comparison via secrets.compare_digest.
    if not secrets.compare_digest(
        _hash_signup_code(body.code.strip()),
        pending["code_hash"],
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code.",
        )

    # All checks passed — create the account. plan_id is hardcoded.
    forced_plan_id = "free"

    async with db_client.pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT id, minutes FROM plans WHERE id = $1", forced_plan_id
        )
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Default plan unavailable; contact support.",
            )

        # Race: someone may have registered with the same email between
        # /signup/start and /signup/complete. Re-check.
        existing = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE email = $1", email
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Registration failed. Please check your details.",
            )

        # Wrap tenant + user_profile + session in a single transaction so a
        # failed user INSERT (e.g. constraint violation) rolls back the
        # tenant we just created. Without this, a 500 mid-flow leaves an
        # orphan tenant row behind and the user has to use a new email.
        async with conn.transaction():
            tenant = await conn.fetchrow(
                """
                INSERT INTO tenants (business_name, plan_id, minutes_allocated, minutes_used)
                VALUES ($1, $2, $3, 0)
                RETURNING id, business_name, minutes_allocated
                """,
                pending["business_name"],
                forced_plan_id,
                plan["minutes"],
            )

            pw_hash = hash_password(body.password)
            user_id = str(uuid.uuid4())

            # Email is already verified at this point because the code matched.
            # No verification_token row required. user_profiles.is_verified is
            # set TRUE and email_verified_at is timestamped — the CHECK
            # constraint chk_email_verification_consistency requires both
            # together when is_verified is TRUE.
            # role 'tenant_admin' is the canonical name post-day4 RBAC migration
            # (day4_rbac_tenant_isolation.sql renamed the legacy 'owner' role
            # and added chk_user_profiles_role_valid restricting role to
            # {platform_admin, partner_admin, tenant_admin, user, readonly}).
            await conn.execute(
                """
                INSERT INTO user_profiles
                    (id, email, name, tenant_id, role, password_hash,
                     is_verified, email_verified_at)
                VALUES ($1, $2, $3, $4, 'tenant_admin', $5, TRUE, NOW())
                """,
                user_id,
                email,
                pending["name"],
                tenant["id"],
                pw_hash,
            )

            ip = get_client_ip(request)
            ua = get_user_agent(request)
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

    # Pending record served its purpose — drop it from Redis.
    await redis.delete(_signup_redis_key(email))

    token = create_jwt(user_id, email, "tenant_admin", str(tenant["id"]), session_id)
    set_session_cookie(response, raw_session_token)

    await audit_logger.log(
        event_type=AuditEvent.USER_CREATED,
        actor_id=user_id,
        actor_type="user",
        tenant_id=str(tenant["id"]),
        action="user_registered_two_step",
        description=f"New user registered (two-step): {email}",
        metadata={
            "plan_id": forced_plan_id,
            "business_name": pending["business_name"],
            "flow": "signup/start+complete",
        },
        ip_address=ip,
        user_agent=ua,
    )

    return AuthTokenResponse(
        access_token=token,
        user_id=user_id,
        email=email,
        role="owner",
        business_name=pending["business_name"],
        minutes_remaining=plan["minutes"],
        message="Account created successfully.",
    )
