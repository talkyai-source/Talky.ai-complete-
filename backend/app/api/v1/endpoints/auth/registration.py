"""Direct /auth/register endpoint — single-step account creation with
a verification email sent after the row is committed."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.core.config import get_settings
from app.core.postgres_adapter import Client
from app.core.security.password import (
    PasswordValidationError,
    hash_password,
    validate_password_strength,
)
from app.core.security.verification_tokens import (
    generate_verification_token,
    get_verification_token_expiry,
    hash_verification_token,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.email_service import get_email_service

from ._shared import (
    get_client_ip,
    get_user_agent,
    limiter,
)
from .schemas import RegisterRequest, RegisterResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
async def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> RegisterResponse:
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

    # All new signups land on the `free` plan unconditionally. Whatever
    # `plan_id` (if any) the frontend sent is ignored — the form was
    # observed sending the user's first-name into this slot by accident.
    # Plan upgrades are made later via the dashboard.
    forced_plan_id = "free"

    async with db_client.pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT id, minutes FROM plans WHERE id = $1", forced_plan_id
        )
        if not plan:
            # The free row should always exist — see plans table seed.
            # If it's missing, signup is genuinely broken.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Default plan unavailable; contact support.",
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
            forced_plan_id,
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

        # Seed the platform-default SIP trunk (Blaze Digitel) so the new
        # tenant can place outbound calls immediately AFTER they verify.
        from app.services.scripts.seed_platform_sip_trunk import seed_for_tenant
        await seed_for_tenant(conn, str(tenant["id"]))

        ip = get_client_ip(request)
        ua = get_user_agent(request)

    # --- send verification email -----------------------------------------------
    # No session is created until the user clicks the verification link.
    # Issuing a session here historically let an attacker register as
    # victim@example.com (without owning the inbox) and get a 15-min
    # JWT + 7-day refresh cookie immediately — full tenant_admin powers
    # without ever proving email ownership. The verification link lands
    # the user on /auth/login where they sign in normally.
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

    # --- log registration event (Day 8) ----------------------------------------
    await audit_logger.log(
        event_type=AuditEvent.USER_CREATED,
        actor_id=user_id,
        actor_type="user",
        tenant_id=str(tenant["id"]),
        action="user_registered",
        description=f"New user registered: {body.email} (pending email verification)",
        metadata={"plan_id": forced_plan_id, "business_name": body.business_name},
        ip_address=ip,
        user_agent=ua,
    )

    return RegisterResponse(
        user_id=user_id,
        email=body.email,
        business_name=body.business_name,
        verification_required=True,
        verification_email_sent=bool(email_sent),
        message=(
            "Account created. Check your inbox for a verification link — "
            "you'll be able to sign in after clicking it."
        ),
    )
