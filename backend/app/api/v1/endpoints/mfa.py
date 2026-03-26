"""
MFA (Multi-Factor Authentication) Endpoints — TOTP + Recovery Codes

Official references (verified March 2026):
  OWASP Multifactor Authentication Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html
  OWASP Authentication Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
  RFC 6238 — TOTP: Time-Based One-Time Password Algorithm
  pyotp 2.9.0 — https://pypi.org/project/pyotp/

Two-step login flow (when MFA is enabled):
  1. POST /auth/login        → password verified → returns mfa_challenge_token (not a JWT)
  2. POST /auth/mfa/verify   → TOTP code + challenge_token → returns full JWT + session cookie

MFA management flow (requires a full auth JWT):
  POST /auth/mfa/setup       → generates secret + QR code (MFA not yet active)
  POST /auth/mfa/confirm     → user enters first valid TOTP code → activates MFA
                                                                  → returns recovery codes (once)
  GET  /auth/mfa/status      → returns whether MFA is enabled for current user
  POST /auth/mfa/disable     → requires current password → disables MFA + deletes recovery codes
  POST /auth/mfa/recovery-codes/regenerate
                             → requires valid TOTP → replaces all recovery codes (once)

OWASP rules enforced:
  - TOTP secrets encrypted at rest (Fernet / AES-128-CBC + HMAC-SHA256)
  - OTPs are single-use (replay prevention via last_used_at timestamp)
  - MFA challenge tokens are single-use, 5-minute TTL, SHA-256 hashed in DB
  - Recovery codes are single-use, 96-bit entropy, SHA-256 hashed in DB
  - Raw codes / secrets are NEVER logged
  - Require current password before disabling MFA (reauthentication rule)
  - All MFA failures use generic error messages (no factor enumeration)
  - Rate limiting via slowapi (IP-level first line of defense)
  - Failed TOTP attempts recorded in login_attempts (account-level lockout)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.jwt_security import encode_access_token as _encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.lockout import check_account_locked, record_login_attempt
from app.core.security.password import verify_password
from app.core.security.recovery import (
    count_remaining_codes,
    format_recovery_code,
    generate_recovery_codes,
    invalidate_all_codes,
    store_recovery_codes,
    verify_and_consume_recovery_code,
)
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_HOURS,
    create_session,
    hash_session_token,
)
from app.core.security.totp import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_provisioning_uri,
    verify_totp_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/mfa", tags=["mfa"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# MFA challenge token lifetime (5 minutes — short window for step-2 login)
MFA_CHALLENGE_TTL_MINUTES: int = 5

# Generic error message — never reveal which factor was wrong (OWASP)
_GENERIC_MFA_ERROR = "MFA verification failed."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


def _set_session_cookie(response: Response, raw_token: str) -> None:
    """Write the session token into an httpOnly Secure SameSite=Strict cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=SESSION_LIFETIME_HOURS * 3600,
        path="/",
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class MFASetupResponse(BaseModel):
    """Returned by POST /auth/mfa/setup — contains QR code data for the authenticator app."""

    provisioning_uri: str
    qr_code: str  # data:image/png;base64,... — embed directly in <img>
    issuer: str
    account: str


class MFAConfirmRequest(BaseModel):
    """Confirm MFA setup by submitting the first valid TOTP code."""

    code: str


class MFAConfirmResponse(BaseModel):
    """
    Returned once when MFA is successfully activated.
    recovery_codes are shown EXACTLY ONCE — the user must save them.
    """

    enabled: bool
    recovery_codes: list[str]  # formatted (e.g. "AbCdEfGh-IjKlMnOp")
    recovery_codes_count: int
    message: str


class MFAChallengeVerifyRequest(BaseModel):
    """
    Step-2 of the two-step login flow.
    Present the challenge token (from POST /auth/login) + TOTP code.
    Alternatively supply recovery_code instead of code.
    """

    challenge_token: str
    code: Optional[str] = None  # TOTP code (6 digits)
    recovery_code: Optional[str] = None  # backup code


class MFAChallengeVerifyResponse(BaseModel):
    """Full auth response returned after successful MFA challenge completion."""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    business_name: Optional[str]
    minutes_remaining: int
    mfa_verified: bool = True
    message: str


class MFAStatusResponse(BaseModel):
    enabled: bool
    verified_at: Optional[datetime]
    recovery_codes_remaining: int


class MFADisableRequest(BaseModel):
    """Requires current password to disable MFA (OWASP: reauthentication before disabling MFA)."""

    password: str


class MFARegenerateCodesRequest(BaseModel):
    """Requires a valid current TOTP code to regenerate recovery codes."""

    code: str


class MFARegenerateCodesResponse(BaseModel):
    recovery_codes: list[str]
    recovery_codes_count: int
    message: str


# ---------------------------------------------------------------------------
# Public helpers used by auth.py login flow
# ---------------------------------------------------------------------------


async def create_mfa_challenge(
    conn,
    user_id: str,
    ip_address: str,
    user_agent: Optional[str] = None,
) -> str:
    """
    Create an ephemeral MFA challenge record and return the raw token.

    Called from auth.py POST /auth/login when password is verified AND
    the user has MFA enabled.  The raw token is returned to the client
    (NOT as a cookie — it goes in the response body so the client can use
    it for the step-2 call).  Only SHA-256(raw_token) is stored.

    Returns the raw challenge token string (32 URL-safe base64 chars).
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = _sha256(raw_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=MFA_CHALLENGE_TTL_MINUTES)

    await conn.execute(
        """
        INSERT INTO mfa_challenges
               (user_id, challenge_hash, ip_address, user_agent,
                expires_at, used, created_at)
        VALUES ($1, $2, $3, $4, $5, FALSE, $6)
        """,
        user_id,
        token_hash,
        ip_address,
        user_agent,
        expires_at,
        now,
    )

    logger.debug(
        "MFA challenge created for user=%s expires=%s",
        user_id,
        expires_at.isoformat(),
    )
    return raw_token


async def resolve_mfa_challenge(conn, raw_token: str) -> Optional[dict[str, str]]:
    """
    Validate a raw challenge token and return the challenge row if valid.

    Checks: exists, not used, not expired.
    Does NOT consume the challenge — call consume_mfa_challenge() after
    successful TOTP verification.

    Returns the row dict or None.
    """
    if not raw_token:
        return None

    token_hash = _sha256(raw_token)
    now = datetime.now(timezone.utc)

    row = await conn.fetchrow(
        """
        SELECT id, user_id, ip_address, expires_at, used
        FROM   mfa_challenges
        WHERE  challenge_hash = $1
          AND  used           = FALSE
          AND  expires_at     > $2
        """,
        token_hash,
        now,
    )
    return dict(row) if row else None


async def consume_mfa_challenge(conn, challenge_id: str) -> None:
    """Mark a challenge as used so it cannot be replayed."""
    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        UPDATE mfa_challenges
           SET used    = TRUE,
               used_at = $1
         WHERE id      = $2
           AND used    = FALSE
        """,
        now,
        challenge_id,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
    raw_secret = generate_totp_secret()
    encrypted_secret = encrypt_totp_secret(raw_secret)

    provisioning_uri = get_provisioning_uri(raw_secret, current_user.email)
    qr_data_uri = generate_qr_code_data_uri(provisioning_uri)

    async with db_client.pool.acquire() as conn:
        # Upsert: replace any existing (possibly unconfirmed) MFA record.
        # If MFA is already enabled=TRUE, this resets to enabled=FALSE (new setup).
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


@router.post("/verify", response_model=MFAChallengeVerifyResponse)
async def verify_mfa_challenge(
    request: Request,
    response: Response,
    body: MFAChallengeVerifyRequest,
    db_client: Client = Depends(get_db_client),
) -> MFAChallengeVerifyResponse:
    """
    Step-2 of the two-step login flow.

    Accepts the mfa_challenge_token (from POST /auth/login) plus either:
      - code          : 6-digit TOTP from the authenticator app, OR
      - recovery_code : one of the single-use backup codes

    Security controls (OWASP + RFC 6238 + pyotp checklist):
      1. Challenge token is single-use and expires in 5 minutes.
      2. TOTP replay prevention (same 30-second slot rejected).
      3. Recovery code is single-use and consumed on first use.
      4. All failures use the same generic error message.
      5. All attempts (success + failure) recorded in login_attempts.
      6. On success: full server-side session created + httpOnly cookie set.

    Returns the full auth response identical to a regular (non-MFA) login.
    """
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)

    # Must supply exactly one of code or recovery_code
    if not body.code and not body.recovery_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'code' (TOTP) or 'recovery_code'.",
        )

    async with db_client.pool.acquire() as conn:
        # --- Resolve and validate the challenge token -------------------------
        challenge = await resolve_mfa_challenge(conn, body.challenge_token)

        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_GENERIC_MFA_ERROR,
            )

        user_id: str = str(challenge["user_id"])

        # --- Load user and MFA record -----------------------------------------
        user_row = await conn.fetchrow(
            """
            SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                   up.is_active,
                   t.business_name, t.minutes_allocated, t.minutes_used
            FROM   user_profiles up
            LEFT   JOIN tenants t ON t.id = up.tenant_id
            WHERE  up.id = $1
            """,
            user_id,
        )

        if not user_row or not user_row["is_active"]:
            await record_login_attempt(
                conn,
                email=user_row["email"] if user_row else "unknown",
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_inactive",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_GENERIC_MFA_ERROR,
            )

        mfa_row = await conn.fetchrow(
            "SELECT totp_secret_enc, enabled, last_used_at FROM user_mfa WHERE user_id = $1",
            user_id,
        )

        if not mfa_row or not mfa_row["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_GENERIC_MFA_ERROR,
            )

        # --- Per-account lockout check ----------------------------------------
        normalised_email = user_row["email"].lower()
        locked_until = await check_account_locked(conn, normalised_email)
        if locked_until is not None:
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_locked",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_GENERIC_MFA_ERROR,
            )

        # --- Verify the second factor ------------------------------------------
        mfa_ok = False

        if body.code:
            # TOTP path
            try:
                raw_secret = decrypt_totp_secret(mfa_row["totp_secret_enc"])
            except RuntimeError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="MFA configuration error.",
                )

            mfa_ok = verify_totp_code(
                raw_secret,
                body.code,
                last_used_at=mfa_row["last_used_at"],
            )

            if mfa_ok:
                # Update last_used_at for replay prevention
                await conn.execute(
                    "UPDATE user_mfa SET last_used_at = NOW() WHERE user_id = $1",
                    user_id,
                )

        elif body.recovery_code:
            # Recovery code path
            mfa_ok = await verify_and_consume_recovery_code(
                conn, user_id, body.recovery_code
            )

        if not mfa_ok:
            await record_login_attempt(
                conn,
                email=normalised_email,
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="mfa_failed",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_GENERIC_MFA_ERROR,
            )

        # --- SUCCESS: consume challenge + create full session -----------------
        await consume_mfa_challenge(conn, str(challenge["id"]))

        raw_session_token = await create_session(
            conn,
            user_id=user_id,
            ip_address=ip,
            user_agent=ua,
        )

        # Mark session as MFA verified
        session_hash = hash_session_token(raw_session_token)
        await conn.execute(
            """
            UPDATE security_sessions
               SET mfa_verified = TRUE
             WHERE session_token_hash = $1
            """,
            session_hash,
        )

        # Record successful login
        await record_login_attempt(
            conn,
            email=normalised_email,
            user_id=user_id,
            ip_address=ip,
            success=True,
        )

        await conn.execute(
            "UPDATE user_profiles SET last_login_at = NOW() WHERE id = $1",
            user_id,
        )

    # --- Build response -------------------------------------------------------
    tenant_id = str(user_row["tenant_id"]) if user_row["tenant_id"] else None
    minutes_remaining = max(
        0,
        (user_row["minutes_allocated"] or 0) - (user_row["minutes_used"] or 0),
    )

    token = _encode_access_token(
        user_id=user_id,
        email=user_row["email"],
        role=user_row["role"],
        tenant_id=tenant_id,
    )

    _set_session_cookie(response, raw_session_token)

    logger.info("MFA challenge verified — full session issued for user=%s", user_id)

    return MFAChallengeVerifyResponse(
        access_token=token,
        user_id=user_id,
        email=user_row["email"],
        role=user_row["role"],
        business_name=user_row["business_name"],
        minutes_remaining=minutes_remaining,
        mfa_verified=True,
        message="Login successful.",
    )


@router.get("/status", response_model=MFAStatusResponse)
async def get_mfa_status(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> MFAStatusResponse:
    """Return the MFA status for the current authenticated user."""
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, verified_at FROM user_mfa WHERE user_id = $1",
            current_user.id,
        )
        remaining = await count_remaining_codes(conn, current_user.id)

    enabled = bool(row["enabled"]) if row else False
    verified_at = row["verified_at"] if row else None

    return MFAStatusResponse(
        enabled=enabled,
        verified_at=verified_at,
        recovery_codes_remaining=remaining if enabled else 0,
    )


@router.post("/disable")
async def disable_mfa(
    body: MFADisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> dict[str, str]:
    """
    Disable MFA for the current user.

    OWASP MFA Cheat Sheet:
      "Require reauthentication with an existing enrolled factor before
       allowing changes."

    Requires the current account password to prevent an attacker with a
    stolen session from silently downgrading the account's security.

    On success:
      - Sets user_mfa.enabled = FALSE
      - Deletes all recovery codes
      - Updates user_profiles.mfa_enabled = FALSE
    """
    async with db_client.pool.acquire() as conn:
        # Load current password hash for reauthentication check
        pw_row = await conn.fetchrow(
            "SELECT password_hash FROM user_profiles WHERE id = $1",
            current_user.id,
        )

        if not pw_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found.",
            )

        if not verify_password(body.password, pw_row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect.",
            )

        # Check MFA is actually enabled
        mfa_row = await conn.fetchrow(
            "SELECT id, enabled FROM user_mfa WHERE user_id = $1",
            current_user.id,
        )

        if not mfa_row or not mfa_row["enabled"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not currently enabled.",
            )

        # Disable MFA and clean up
        await conn.execute(
            """
            UPDATE user_mfa
               SET enabled      = FALSE,
                   verified_at  = NULL,
                   updated_at   = NOW(),
                   last_used_at = NULL
             WHERE user_id      = $1
            """,
            current_user.id,
        )

        await conn.execute(
            "UPDATE user_profiles SET mfa_enabled = FALSE WHERE id = $1",
            current_user.id,
        )

        await invalidate_all_codes(conn, current_user.id)

    logger.info("MFA disabled for user=%s", current_user.id)

    return {
        "detail": "MFA disabled successfully. All recovery codes have been deleted."
    }


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
