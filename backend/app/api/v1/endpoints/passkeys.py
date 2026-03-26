"""
Passkeys (WebAuthn) Endpoints — FIDO2 Credential Management & Login

Official References (verified March 2026):
  W3C WebAuthn Level 3 Candidate Recommendation (January 13, 2026):
    https://www.w3.org/TR/webauthn-3/
  OWASP Authentication Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
  py_webauthn 2.7.1 (Duo Labs):
    https://github.com/duo-labs/py_webauthn

What this module does:
  Registration (authenticated):
    POST /auth/passkeys/register/begin    → Start registration, return options
    POST /auth/passkeys/register/complete → Verify and store credential

  Authentication (unauthenticated for begin, authenticated for complete):
    POST /auth/passkeys/login/begin       → Start authentication, return options
    POST /auth/passkeys/login/complete    → Verify and create session

  Management (authenticated):
    GET  /auth/passkeys                   → List user's passkeys
    PATCH /auth/passkeys/{passkey_id}     → Update display name
    DELETE /auth/passkeys/{passkey_id}    → Remove a passkey

Security controls (OWASP + W3C):
  - Registration requires authentication (user must be logged in)
  - All ceremonies use single-use challenges with 5-minute TTL
  - IP address tracking for anomaly detection
  - Sign count verification for clone detection
  - User verification required (biometric/PIN) for all operations
  - Rate limiting via slowapi (IP-level)
  - Audit logging of all passkey operations

Two-step login flow with fallback:
  1. Client calls POST /auth/passkeys/login/begin (or POST /auth/login for password)
  2. If passkey available, complete with POST /auth/passkeys/login/complete
  3. If password login: regular flow, MFA if enabled
  4. If user has both, UI shows "Sign in with passkey" + "Sign in with password"
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.jwt_security import encode_access_token
from app.core.postgres_adapter import Client
from app.core.security.lockout import check_account_locked, record_login_attempt
from app.core.security.passkeys import (
    generate_authentication_options,
    generate_registration_options,
    get_credential_by_id,
    get_user_credentials,
    get_user_id_by_credential_id,
    store_credential,
    update_credential_display_name,
    update_credential_sign_count,
    delete_credential,
    verify_authentication,
    verify_registration,
    AuthenticationResult,
    VerifiedCredential,
)
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    hash_session_token,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth/passkeys", tags=["passkeys"])

# ---------------------------------------------------------------------------
# Cookie settings (same as auth.py)
# ---------------------------------------------------------------------------
_COOKIE_MAX_AGE = 24 * 3600  # 24 hours


def _set_session_cookie(response: Response, raw_token: str) -> None:
    """Write the session token into an httpOnly Secure SameSite=Strict cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP from the request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


# ===========================================================================
# Request / Response Models
# ===========================================================================


class RegisterBeginRequest(BaseModel):
    """Start a passkey registration ceremony."""
    authenticator_type: str = Field(default="any", pattern="^(platform|cross-platform|any)$")
    display_name: Optional[str] = None  # User-friendly label (e.g., "Work MacBook")


class RegisterBeginResponse(BaseModel):
    """Registration options to pass to navigator.credentials.create()"""
    ceremony_id: str
    options: dict[str, Any]  # Parsed JSON options


class RegisterCompleteRequest(BaseModel):
    """Complete a passkey registration with the authenticator response."""
    ceremony_id: str
    credential_response: dict[str, Any]  # JSON from navigator.credentials.create()
    display_name: Optional[str] = None


class RegisterCompleteResponse(BaseModel):
    """Successful registration response."""
    passkey_id: str
    credential_id: str
    device_type: str
    backed_up: bool
    message: str


class LoginBeginRequest(BaseModel):
    """Start a passkey authentication ceremony."""
    email: Optional[str] = None  # Optional: for non-discoverable flow


class LoginBeginResponse(BaseModel):
    """Authentication options to pass to navigator.credentials.get()"""
    ceremony_id: str
    options: dict[str, Any]
    has_passkeys: bool  # Hint for UI: does this user have passkeys?


class LoginCompleteRequest(BaseModel):
    """Complete a passkey authentication with the authenticator response."""
    ceremony_id: str
    credential_response: dict[str, Any]  # JSON from navigator.credentials.get()


class LoginCompleteResponse(BaseModel):
    """Successful passkey login response (same format as regular auth)."""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    business_name: Optional[str]
    minutes_remaining: int
    message: str


class PasskeyItem(BaseModel):
    """A passkey in the user's list."""
    id: str
    credential_id: str
    display_name: str
    device_type: str
    backed_up: bool
    transports: list[str]
    created_at: str
    last_used_at: Optional[str]


class ListPasskeysResponse(BaseModel):
    """List of user's passkeys."""
    passkeys: list[PasskeyItem]
    count: int


class UpdatePasskeyRequest(BaseModel):
    """Update a passkey's display name."""
    display_name: str = Field(..., min_length=1, max_length=100)


class UpdatePasskeyResponse(BaseModel):
    """Successful update response."""
    passkey_id: str
    display_name: str
    message: str


# ===========================================================================
# Registration Endpoints (authenticated)
# ===========================================================================


@router.post("/register/begin", response_model=RegisterBeginResponse)
async def register_begin(
    request: Request,
    body: RegisterBeginRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> RegisterBeginResponse:
    """
    Begin passkey registration for the authenticated user.

    Returns WebAuthn options that the client should pass to
    navigator.credentials.create() to register a new passkey.

    The user can choose authenticator type:
      - "platform": Built-in authenticator (TouchID, Windows Hello)
      - "cross-platform": Roaming authenticator (YubiKey, phone as key)
      - "any": Either type
    """
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)

    async with db_client.pool.acquire() as conn:
        options = await generate_registration_options(
            conn=conn,
            user_id=current_user.id,
            user_email=current_user.email,
            user_name=current_user.name,
            authenticator_type=body.authenticator_type,
            ip_address=ip,
            user_agent=ua,
        )

    # Parse the JSON string to a dict for the response
    import json
    options_dict = json.loads(options.options_json)

    logger.info(
        "Passkey registration started: user=%s ceremony=%s type=%s",
        current_user.id, options.ceremony_id, body.authenticator_type
    )

    return RegisterBeginResponse(
        ceremony_id=options.ceremony_id,
        options=options_dict,
    )


@router.post("/register/complete", response_model=RegisterCompleteResponse)
async def register_complete(
    request: Request,
    body: RegisterCompleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> RegisterCompleteResponse:
    """
    Complete passkey registration by verifying the authenticator response.

    The client sends the credential response from navigator.credentials.create().
    We verify the attestation, store the credential, and return the passkey ID.
    """
    ip = _get_client_ip(request)

    async with db_client.pool.acquire() as conn:
        # Verify the registration response
        try:
            verified = await verify_registration(
                conn=conn,
                ceremony_id=body.ceremony_id,
                credential_response=body.credential_response,
                ip_address=ip,
            )
        except ValueError as e:
            logger.warning(
                "Passkey registration verification failed: user=%s error=%s",
                current_user.id, e
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Registration verification failed. The passkey could not be registered.",
            ) from e

        # Store the verified credential
        display_name = body.display_name or verified.device_type
        passkey_id = await store_credential(
            conn=conn,
            user_id=current_user.id,
            credential=verified,
            display_name=display_name,
        )

    logger.info(
        "Passkey registered: passkey_id=%s user=%s credential=%s...",
        passkey_id, current_user.id, verified.credential_id[:16]
    )

    return RegisterCompleteResponse(
        passkey_id=passkey_id,
        credential_id=verified.credential_id,
        device_type=verified.device_type,
        backed_up=verified.backed_up,
        message="Passkey registered successfully.",
    )


# ===========================================================================
# Authentication Endpoints (unauthenticated begin, authenticated complete)
# ===========================================================================


@router.post("/login/begin", response_model=LoginBeginResponse)
@limiter.limit("10/minute")
async def login_begin(
    request: Request,
    body: LoginBeginRequest,
    db_client: Client = Depends(get_db_client),
) -> LoginBeginResponse:
    """
    Begin passkey authentication (login).

    This endpoint is UNAUTHENTICATED — it's the first step of the login flow.

    If email is provided, we look up their credentials and restrict
    allowCredentials to their registered passkeys.

    If email is NOT provided, we allow discoverable credentials (username-less
    login) where the authenticator presents the userHandle.
    """
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)

    credential_ids: Optional[list[str]] = None
    user_id: Optional[str] = None
    has_passkeys = False

    async with db_client.pool.acquire() as conn:
        if body.email:
            # Look up user by email
            user_row = await conn.fetchrow(
                """
                SELECT id, passkey_count FROM user_profiles WHERE email = $1
                """,
                body.email.lower(),
            )

            if user_row and user_row["passkey_count"] > 0:
                user_id = str(user_row["id"])
                has_passkeys = True

                # Get their credential IDs
                creds = await get_user_credentials(conn, user_id)
                credential_ids = [c["credential_id"] for c in creds]
        else:
            # Discoverable credential flow - no email provided
            # Allow any passkey, user identified from userHandle in response
            has_passkeys = True  # We don't know yet, assume yes for UX

        options = await generate_authentication_options(
            conn=conn,
            user_id=user_id,  # None for discoverable flow
            credential_ids=credential_ids,
            ip_address=ip,
            user_agent=ua,
            allow_discoverable=True,
        )

    # Parse the JSON
    import json
    options_dict = json.loads(options.options_json)

    logger.debug(
        "Passkey login started: ceremony=%s user=%s has_creds=%s",
        options.ceremony_id, user_id or "(discoverable)", bool(credential_ids)
    )

    return LoginBeginResponse(
        ceremony_id=options.ceremony_id,
        options=options_dict,
        has_passkeys=has_passkeys,
    )


@router.post("/login/complete", response_model=LoginCompleteResponse)
@limiter.limit("10/minute")
async def login_complete(
    request: Request,
    response: Response,
    body: LoginCompleteRequest,
    db_client: Client = Depends(get_db_client),
) -> LoginCompleteResponse:
    """
    Complete passkey authentication and create a session.

    This endpoint verifies the authenticator response and, on success,
    creates a full session (JWT + httpOnly cookie) just like password login.

    Supports both:
      - Non-discoverable: credential ID maps to known user
      - Discoverable: userHandle in response identifies the user
    """
    ip = _get_client_ip(request)
    ua = _get_user_agent(request)

    # Extract credential ID from the response
    raw_credential_id = body.credential_response.get("id")
    if not raw_credential_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing credential ID in response.",
        )

    async with db_client.pool.acquire() as conn:
        # Look up the credential
        credential = await get_credential_by_id(conn, raw_credential_id)

        if not credential:
            logger.warning(
                "Passkey login failed: unknown credential_id=%s...", raw_credential_id[:16]
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )

        user_id = str(credential["user_id"])

        # Load user details
        user_row = await conn.fetchrow(
            """
            SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                   up.is_active, up.mfa_enabled,
                   t.business_name, t.minutes_allocated, t.minutes_used
            FROM   user_profiles up
            LEFT   JOIN tenants t ON t.id = up.tenant_id
            WHERE  up.id = $1
            """,
            credential["user_id"],
        )

        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )

        if not user_row["is_active"]:
            await record_login_attempt(
                conn,
                email=user_row["email"],
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_inactive",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )

        # Check account lockout
        locked_until = await check_account_locked(conn, user_row["email"].lower())
        if locked_until is not None:
            await record_login_attempt(
                conn,
                email=user_row["email"],
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="account_locked",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )

        # Verify the authentication response
        try:
            auth_result = await verify_authentication(
                conn=conn,
                ceremony_id=body.ceremony_id,
                credential_response=body.credential_response,
                credential_id=credential["credential_id"],
                credential_public_key=credential["credential_public_key"],
                current_sign_count=credential["sign_count"],
                ip_address=ip,
            )
        except ValueError as e:
            await record_login_attempt(
                conn,
                email=user_row["email"],
                user_id=user_id,
                ip_address=ip,
                success=False,
                failure_reason="passkey_verification_failed",
            )
            logger.warning(
                "Passkey verification failed: user=%s error=%s", user_id, e
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            ) from e

        # Update sign count
        await update_credential_sign_count(
            conn, credential["credential_id"], auth_result.new_sign_count
        )

        # Record successful login
        await record_login_attempt(
            conn,
            email=user_row["email"],
            user_id=user_id,
            ip_address=ip,
            success=True,
        )

        # Update last_login_at
        await conn.execute(
            "UPDATE user_profiles SET last_login_at = NOW() WHERE id = $1",
            credential["user_id"],
        )

        # Create session
        raw_session_token = await create_session(
            conn,
            user_id=user_id,
            ip_address=ip,
            user_agent=ua,
        )

        # Build JWT
        tenant_id = str(user_row["tenant_id"]) if user_row["tenant_id"] else None
        token = encode_access_token(
            user_id=user_id,
            email=user_row["email"],
            role=user_row["role"],
            tenant_id=tenant_id,
        )

    # Set session cookie
    _set_session_cookie(response, raw_session_token)

    minutes_remaining = max(
        0,
        (user_row["minutes_allocated"] or 0) - (user_row["minutes_used"] or 0),
    )

    logger.info(
        "Passkey login successful: user=%s credential=%s...",
        user_id, raw_credential_id[:16]
    )

    return LoginCompleteResponse(
        access_token=token,
        user_id=user_id,
        email=user_row["email"],
        role=user_row["role"],
        business_name=user_row["business_name"],
        minutes_remaining=minutes_remaining,
        message="Login successful.",
    )


# ===========================================================================
# Management Endpoints (authenticated)
# ===========================================================================


@router.get("", response_model=ListPasskeysResponse)
async def list_passkeys(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> ListPasskeysResponse:
    """
    List all passkeys registered for the current user.

    Returns metadata about each passkey including:
      - Display name (user-assigned label)
      - Device type (singleDevice / multiDevice)
      - Backup status (backed_up indicates synced passkey)
      - Last used timestamp
    """
    async with db_client.pool.acquire() as conn:
        credentials = await get_user_credentials(conn, current_user.id)

    passkeys = [
        PasskeyItem(
            id=str(c["id"]),
            credential_id=c["credential_id"],
            display_name=c["display_name"] or "Unnamed Passkey",
            device_type=c["device_type"],
            backed_up=c["backed_up"],
            transports=c["transports"] or [],
            created_at=c["created_at"].isoformat() if c["created_at"] else "",
            last_used_at=c["last_used_at"].isoformat() if c["last_used_at"] else None,
        )
        for c in credentials
    ]

    return ListPasskeysResponse(
        passkeys=passkeys,
        count=len(passkeys),
    )


@router.patch("/{passkey_id}", response_model=UpdatePasskeyResponse)
async def update_passkey(
    passkey_id: str,
    body: UpdatePasskeyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> UpdatePasskeyResponse:
    """
    Update the display name of a passkey.

    Users can rename their passkeys to easily identify them
    (e.g., "Work MacBook", "iPhone 15", "YubiKey Nano").
    """
    async with db_client.pool.acquire() as conn:
        success = await update_credential_display_name(
            conn, passkey_id, current_user.id, body.display_name
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Passkey not found.",
            )

    logger.info(
        "Passkey renamed: passkey_id=%s user=%s display_name=%s",
        passkey_id, current_user.id, body.display_name
    )

    return UpdatePasskeyResponse(
        passkey_id=passkey_id,
        display_name=body.display_name,
        message="Passkey renamed successfully.",
    )


@router.delete("/{passkey_id}")
async def delete_passkey(
    passkey_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> dict[str, str]:
    """
    Delete a passkey.

    Removes the credential from the database and decrements the user's
    passkey_count. This does NOT revoke the credential from the authenticator
    itself (that's not possible via WebAuthn).
    """
    async with db_client.pool.acquire() as conn:
        # Check if this is their last passkey
        count_result = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM user_passkeys WHERE user_id = $1",
            current_user.id,
        )
        current_count = count_result["cnt"] if count_result else 0

        success = await delete_credential(conn, passkey_id, current_user.id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Passkey not found.",
            )

    logger.info(
        "Passkey deleted: passkey_id=%s user=%s remaining=%d",
        passkey_id, current_user.id, current_count - 1
    )

    message = "Passkey deleted successfully."
    if current_count <= 1:
        message += " You have no remaining passkeys. Consider registering a new one."

    return {"detail": message}
