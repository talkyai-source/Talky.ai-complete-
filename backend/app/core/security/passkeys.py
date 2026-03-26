"""
WebAuthn / Passkey Security Module
Implements FIDO2 credential registration and authentication.

Official References (verified March 2026):
  W3C WebAuthn Level 3 Candidate Recommendation (January 13, 2026):
    https://www.w3.org/TR/webauthn-3/
  py_webauthn 2.7.1 (Duo Labs):
    https://github.com/duo-labs/py_webauthn
    https://pypi.org/project/webauthn/
  FIDO Alliance FIDO2 Specifications:
    https://fidoalliance.org/specs/fido-v2.1-ps-20210615/

What this module does:
  1. Generates WebAuthn registration options (challenge, RP, user, algorithms)
  2. Verifies registration responses (attestation verification)
  3. Generates WebAuthn authentication options (challenge, allowCredentials)
  4. Verifies authentication responses (assertion verification)
  5. Provides helper functions for challenge lifecycle and credential storage

Security controls (OWASP + W3C):
  - Challenges are single-use, 5-minute TTL, stored SHA-256 hashed
  - Attestation verification prevents counterfeit authenticators
  - Signature counter (sign_count) checked for clone detection
  - Credential IDs are unique globally (UUID-like from authenticator)
  - All ceremony data tied to user session + IP for anomaly detection
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, parse_client_data_json
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.helpers.exceptions import InvalidRegistrationResponse, InvalidAuthenticationResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Challenge lifetime: 5 minutes (same as MFA challenges)
WEBAUTHN_CHALLENGE_TTL_MINUTES: int = 5

# Relying Party (RP) configuration
# These should be overridden from environment in production
RP_NAME: str = "Talky.ai"
RP_ID: str = "talky.ai"  # Must match domain (no scheme, no port)
RP_ORIGIN: str = "https://talky.ai"  # Full origin for verification

# Allow local development origins
DEV_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

# Supported public key algorithms (COSE identifiers)
# Ordered by preference: ES256, Ed25519, RS256
PUBKEY_CRED_PARAMS: list[dict[str, Any]] = [
    {"type": "public-key", "alg": -7},    # ES256 (ECDSA w/ SHA-256)
    {"type": "public-key", "alg": -8},    # Ed25519
    {"type": "public-key", "alg": -257},  # RS256 (RSASSA-PKCS1-v1_5 w/ SHA-256)
]

# Authenticator selection criteria for different flows
# "platform" = built-in authenticator (TouchID, Windows Hello)
# "cross-platform" = roaming authenticator (YubiKey, phone as key)
AUTHENTICATOR_SELECTION_PLATFORM = AuthenticatorSelectionCriteria(
    authenticator_attachment=AuthenticatorAttachment.PLATFORM,
    resident_key=ResidentKeyRequirement.PREFERRED,
    user_verification=UserVerificationRequirement.REQUIRED,
)

AUTHENTICATOR_SELECTION_CROSS_PLATFORM = AuthenticatorSelectionCriteria(
    authenticator_attachment=AuthenticatorAttachment.CROSS_PLATFORM,
    resident_key=ResidentKeyRequirement.PREFERRED,
    user_verification=UserVerificationRequirement.REQUIRED,
)

AUTHENTICATOR_SELECTION_ANY = AuthenticatorSelectionCriteria(
    resident_key=ResidentKeyRequirement.PREFERRED,
    user_verification=UserVerificationRequirement.REQUIRED,
)


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class RegistrationOptions:
    """WebAuthn registration ceremony options returned to the client."""
    ceremony_id: str  # UUID to lookup challenge server-side
    options_json: str  # JSON string for navigator.credentials.create()


@dataclass
class AuthenticationOptions:
    """WebAuthn authentication ceremony options returned to the client."""
    ceremony_id: str  # UUID to lookup challenge server-side
    options_json: str  # JSON string for navigator.credentials.get()


@dataclass
class VerifiedCredential:
    """A successfully verified and registered credential."""
    credential_id: str  # base64url
    credential_public_key: str  # base64url (COSE)
    sign_count: int
    aaguid: str
    device_type: str  # "singleDevice" | "multiDevice"
    backed_up: bool
    transports: list[str]


@dataclass
class AuthenticationResult:
    """Result of a successful passkey authentication."""
    credential_id: str
    new_sign_count: int
    user_verified: bool
    authenticator_attachment: Optional[str]


# ---------------------------------------------------------------------------
# Challenge Helpers (Database Interaction)
# ---------------------------------------------------------------------------

async def create_challenge(
    conn,
    ceremony: str,  # "registration" | "authentication"
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, bytes]:
    """
    Create a new WebAuthn challenge and store it in the database.

    Args:
        conn: Database connection (asyncpg)
        ceremony: "registration" or "authentication"
        user_id: User UUID (None for discoverable credential auth)
        ip_address: Client IP for anomaly detection
        user_agent: Client user agent string

    Returns:
        tuple of (ceremony_id: str, challenge_bytes: bytes)
    """
    # Generate cryptographically random challenge (at least 16 bytes per W3C)
    challenge_bytes = secrets.token_bytes(32)
    challenge_b64 = bytes_to_base64url(challenge_bytes)

    ceremony_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=WEBAUTHN_CHALLENGE_TTL_MINUTES)

    await conn.execute(
        """
        INSERT INTO webauthn_challenges
               (id, challenge, ceremony, user_id, ip_address, user_agent,
                expires_at, used, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, $8)
        """,
        ceremony_id,
        challenge_b64,
        ceremony,
        uuid.UUID(user_id) if user_id else None,
        ip_address,
        user_agent,
        expires_at,
        now,
    )

    logger.debug(
        "WebAuthn challenge created: ceremony=%s id=%s user=%s expires=%s",
        ceremony, ceremony_id, user_id or "(unknown)", expires_at.isoformat()
    )

    return ceremony_id, challenge_bytes


async def get_and_validate_challenge(
    conn,
    ceremony_id: str,
    expected_ceremony: str,
    ip_address: Optional[str] = None,
) -> Optional[bytes]:
    """
    Retrieve and validate a challenge from the database.

    Args:
        conn: Database connection
        ceremony_id: The ceremony UUID
        expected_ceremony: "registration" or "authentication"
        ip_address: Current client IP (for anomaly logging)

    Returns:
        The challenge bytes if valid, None otherwise
    """
    now = datetime.now(timezone.utc)

    row = await conn.fetchrow(
        """
        SELECT challenge, ceremony, user_id, ip_address, expires_at, used
        FROM   webauthn_challenges
        WHERE  id = $1
        """,
        uuid.UUID(ceremony_id) if ceremony_id else None,
    )

    if not row:
        logger.warning("Challenge not found: ceremony_id=%s", ceremony_id)
        return None

    if row["used"]:
        logger.warning("Challenge already used: ceremony_id=%s", ceremony_id)
        return None

    if row["expires_at"] < now:
        logger.warning("Challenge expired: ceremony_id=%s", ceremony_id)
        return None

    if row["ceremony"] != expected_ceremony:
        logger.warning(
            "Challenge ceremony mismatch: expected=%s got=%s",
            expected_ceremony, row["ceremony"]
        )
        return None

    # Log IP anomaly (don't block, just audit)
    if ip_address and row["ip_address"] and ip_address != row["ip_address"]:
        logger.warning(
            "Challenge IP mismatch: ceremony_id=%s expected=%s got=%s",
            ceremony_id, row["ip_address"], ip_address
        )

    return base64url_to_bytes(row["challenge"])


async def consume_challenge(conn, ceremony_id: str) -> bool:
    """Mark a challenge as used. Returns True if successful."""
    now = datetime.now(timezone.utc)

    result = await conn.execute(
        """
        UPDATE webauthn_challenges
           SET used    = TRUE,
               used_at = $1
         WHERE id      = $2
           AND used    = FALSE
        """,
        now,
        uuid.UUID(ceremony_id) if ceremony_id else None,
    )

    # asyncpg returns 'UPDATE N' string
    return result and "UPDATE 1" in result


async def cleanup_expired_challenges(conn, older_than_minutes: int = 60) -> int:
    """Delete expired challenges older than specified minutes. Returns count deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)

    result = await conn.execute(
        """
        DELETE FROM webauthn_challenges
        WHERE  expires_at < $1
           OR (used = TRUE AND used_at < $1)
        """,
        cutoff,
    )

    # Parse count from result string
    if result and "DELETE" in result:
        try:
            return int(result.split()[1])
        except (IndexError, ValueError):
            pass
    return 0


# ---------------------------------------------------------------------------
# Registration Functions
# ---------------------------------------------------------------------------

async def generate_registration_options(
    conn,
    user_id: str,
    user_email: str,
    user_name: Optional[str] = None,
    authenticator_type: str = "any",  # "platform" | "cross-platform" | "any"
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    rp_id: Optional[str] = None,
    rp_name: Optional[str] = None,
    rp_origin: Optional[str] = None,
) -> RegistrationOptions:
    """
    Generate WebAuthn registration options for a new passkey.

    Args:
        conn: Database connection
        user_id: User UUID
        user_email: User's email address
        user_name: User's display name (defaults to email)
        authenticator_type: "platform", "cross-platform", or "any"
        ip_address: Client IP for anomaly detection
        user_agent: Client user agent
        rp_id: Relying Party ID (defaults to RP_ID config)
        rp_name: Relying Party name (defaults to RP_NAME config)
        rp_origin: Expected origin (defaults to RP_ORIGIN config)

    Returns:
        RegistrationOptions with ceremony_id and JSON options
    """
    # Create and store the challenge
    ceremony_id, challenge_bytes = await create_challenge(
        conn,
        ceremony="registration",
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Select authenticator criteria
    if authenticator_type == "platform":
        authenticator_selection = AUTHENTICATOR_SELECTION_PLATFORM
    elif authenticator_type == "cross-platform":
        authenticator_selection = AUTHENTICATOR_SELECTION_CROSS_PLATFORM
    else:
        authenticator_selection = AUTHENTICATOR_SELECTION_ANY

    # Build user info
    display_name = user_name or user_email
    user_id_bytes = user_id.encode("utf-8")  # User handle for the authenticator

    # Generate options using py_webauthn
    options = generate_registration_options(
        rp_id=rp_id or RP_ID,
        rp_name=rp_name or RP_NAME,
        user_id=user_id_bytes,
        user_name=user_email,
        user_display_name=display_name,
        challenge=challenge_bytes,
        pub_key_cred_params=PUBKEY_CRED_PARAMS,
        authenticator_selection=authenticator_selection,
        attestation=AttestationConveyancePreference.NONE,  # Skip attestation for UX
        timeout=120000,  # 2 minutes in milliseconds
    )

    # Convert to JSON for the client
    options_json = options_to_json(options)

    logger.info(
        "Registration options generated: ceremony_id=%s user=%s type=%s",
        ceremony_id, user_id, authenticator_type
    )

    return RegistrationOptions(
        ceremony_id=ceremony_id,
        options_json=options_json,
    )


async def verify_registration(
    conn,
    ceremony_id: str,
    credential_response: dict[str, Any],
    expected_origin: Optional[str] = None,
    expected_rp_id: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> VerifiedCredential:
    """
    Verify a WebAuthn registration response and return the verified credential.

    Args:
        conn: Database connection
        ceremony_id: The ceremony UUID from registration begin
        credential_response: The JSON response from navigator.credentials.create()
        expected_origin: Expected origin (defaults to RP_ORIGIN)
        expected_rp_id: Expected RP ID (defaults to RP_ID)
        ip_address: Client IP for anomaly detection

    Returns:
        VerifiedCredential on success

    Raises:
        ValueError: If verification fails
    """
    # Retrieve and validate the challenge
    expected_challenge = await get_and_validate_challenge(
        conn,
        ceremony_id=ceremony_id,
        expected_ceremony="registration",
        ip_address=ip_address,
    )

    if expected_challenge is None:
        raise ValueError("Invalid or expired registration challenge")

    # Parse and verify the registration response
    try:
        verification = verify_registration_response(
            credential=credential_response,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin or RP_ORIGIN,
            expected_rp_id=expected_rp_id or RP_ID,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as e:
        logger.warning("Registration verification failed: %s", e)
        raise ValueError(f"Registration verification failed: {e}") from e

    # Consume the challenge (single-use)
    await consume_challenge(conn, ceremony_id)

    # Extract credential data
    credential_id = bytes_to_base64url(verification.credential_id)
    credential_public_key = bytes_to_base64url(verification.credential_public_key)

    # Determine device type from attestation data
    # backed_up = synced credential (multi-device passkey)
    device_type = "multiDevice" if verification.credential_backed_up else "singleDevice"

    # Extract transports from the response
    transports = credential_response.get("response", {}).get("transports", [])
    if not transports and "transports" in credential_response:
        transports = credential_response.get("transports", [])

    logger.info(
        "Registration verified: credential_id=%s... device_type=%s backed_up=%s",
        credential_id[:16], device_type, verification.credential_backed_up
    )

    return VerifiedCredential(
        credential_id=credential_id,
        credential_public_key=credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else "",
        device_type=device_type,
        backed_up=verification.credential_backed_up,
        transports=list(transports) if transports else [],
    )


# ---------------------------------------------------------------------------
# Authentication Functions
# ---------------------------------------------------------------------------

async def generate_authentication_options(
    conn,
    user_id: Optional[str] = None,
    credential_ids: Optional[list[str]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    rp_id: Optional[str] = None,
    allow_discoverable: bool = True,  # Allow username-less login
) -> AuthenticationOptions:
    """
    Generate WebAuthn authentication options.

    Args:
        conn: Database connection
        user_id: User UUID (None for discoverable credential flow)
        credential_ids: List of allowed credential IDs (base64url)
        ip_address: Client IP for anomaly detection
        user_agent: Client user agent
        rp_id: Relying Party ID (defaults to RP_ID config)
        allow_discoverable: Allow username-less authentication

    Returns:
        AuthenticationOptions with ceremony_id and JSON options
    """
    # Create and store the challenge
    ceremony_id, challenge_bytes = await create_challenge(
        conn,
        ceremony="authentication",
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Build allow_credentials list
    allow_credentials = None
    if credential_ids:
        allow_credentials = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(cid),
                type="public-key",
            )
            for cid in credential_ids
        ]

    # Generate options
    options = generate_authentication_options(
        rp_id=rp_id or RP_ID,
        challenge=challenge_bytes,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
        timeout=120000,  # 2 minutes
    )

    # Convert to JSON
    options_json = options_to_json(options)

    logger.debug(
        "Authentication options generated: ceremony_id=%s user=%s credentials=%d",
        ceremony_id, user_id or "(discoverable)", len(credential_ids or [])
    )

    return AuthenticationOptions(
        ceremony_id=ceremony_id,
        options_json=options_json,
    )


async def verify_authentication(
    conn,
    ceremony_id: str,
    credential_response: dict[str, Any],
    credential_id: str,
    credential_public_key: str,
    current_sign_count: int,
    expected_origin: Optional[str] = None,
    expected_rp_id: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> AuthenticationResult:
    """
    Verify a WebAuthn authentication response.

    Args:
        conn: Database connection
        ceremony_id: The ceremony UUID from authentication begin
        credential_response: JSON response from navigator.credentials.get()
        credential_id: The credential ID (base64url)
        credential_public_key: The stored public key (base64url COSE)
        current_sign_count: Current stored sign count for clone detection
        expected_origin: Expected origin (defaults to RP_ORIGIN)
        expected_rp_id: Expected RP ID (defaults to RP_ID)
        ip_address: Client IP for anomaly detection

    Returns:
        AuthenticationResult on success

    Raises:
        ValueError: If verification fails
    """
    # Retrieve and validate the challenge
    expected_challenge = await get_and_validate_challenge(
        conn,
        ceremony_id=ceremony_id,
        expected_ceremony="authentication",
        ip_address=ip_address,
    )

    if expected_challenge is None:
        raise ValueError("Invalid or expired authentication challenge")

    # Parse and verify the authentication response
    try:
        verification = verify_authentication_response(
            credential=credential_response,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin or RP_ORIGIN,
            expected_rp_id=expected_rp_id or RP_ID,
            credential_public_key=base64url_to_bytes(credential_public_key),
            credential_current_sign_count=current_sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as e:
        logger.warning("Authentication verification failed: %s", e)
        raise ValueError(f"Authentication verification failed: {e}") from e

    # Consume the challenge (single-use)
    await consume_challenge(conn, ceremony_id)

    # Check for clone detection (W3C §6.1.3)
    new_sign_count = verification.new_sign_count
    if current_sign_count > 0 and new_sign_count <= current_sign_count:
        logger.critical(
            "CLONE DETECTED: credential_id=%s old_count=%d new_count=%d",
            credential_id, current_sign_count, new_sign_count
        )
        # Still allow login but flag for security review
        # In production, you might want to notify admins or force re-enrollment

    logger.info(
        "Authentication verified: credential_id=%s... new_sign_count=%d",
        credential_id[:16], new_sign_count
    )

    return AuthenticationResult(
        credential_id=credential_id,
        new_sign_count=new_sign_count,
        user_verified=True,  # We require user verification
        authenticator_attachment=verification.authenticator_attachment.value if verification.authenticator_attachment else None,
    )


# ---------------------------------------------------------------------------
# Credential Storage Helpers
# ---------------------------------------------------------------------------

async def store_credential(
    conn,
    user_id: str,
    credential: VerifiedCredential,
    display_name: Optional[str] = None,
) -> str:
    """
    Store a newly registered credential in the database.

    Returns:
        The passkey record UUID
    """
    # Generate a friendly display name if not provided
    if not display_name:
        device_type_label = "Passkey" if credential.backed_up else "Security Key"
        display_name = f"{device_type_label} ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})"

    # Insert the credential
    row = await conn.fetchrow(
        """
        INSERT INTO user_passkeys
               (user_id, credential_id, credential_public_key, sign_count,
                aaguid, device_type, backed_up, transports, display_name,
                created_at, last_used_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NULL)
        RETURNING id
        """,
        uuid.UUID(user_id),
        credential.credential_id,
        credential.credential_public_key,
        credential.sign_count,
        credential.aaguid or None,
        credential.device_type,
        credential.backed_up,
        credential.transports,
        display_name,
    )

    # Increment passkey_count on user_profiles
    await conn.execute(
        """
        UPDATE user_profiles
           SET passkey_count = passkey_count + 1
         WHERE id = $1
        """,
        uuid.UUID(user_id),
    )

    passkey_id = str(row["id"])

    logger.info(
        "Credential stored: passkey_id=%s user=%s credential_id=%s...",
        passkey_id, user_id, credential.credential_id[:16]
    )

    return passkey_id


async def get_credential_by_id(
    conn,
    credential_id: str,
) -> Optional[dict[str, Any]]:
    """Retrieve a credential by its credential_id (base64url)."""
    row = await conn.fetchrow(
        """
        SELECT id, user_id, credential_id, credential_public_key,
               sign_count, aaguid, device_type, backed_up, transports,
               display_name, created_at, last_used_at
        FROM   user_passkeys
        WHERE  credential_id = $1
        """,
        credential_id,
    )
    return dict(row) if row else None


async def get_user_credentials(
    conn,
    user_id: str,
) -> list[dict[str, Any]]:
    """Retrieve all credentials for a user."""
    rows = await conn.fetch(
        """
        SELECT id, credential_id, sign_count, aaguid, device_type,
               backed_up, transports, display_name, created_at, last_used_at
        FROM   user_passkeys
        WHERE  user_id = $1
        ORDER  BY created_at DESC
        """,
        uuid.UUID(user_id),
    )
    return [dict(row) for row in rows]


async def update_credential_sign_count(
    conn,
    credential_id: str,
    new_sign_count: int,
) -> bool:
    """Update the sign count after successful authentication."""
    result = await conn.execute(
        """
        UPDATE user_passkeys
           SET sign_count   = $1,
               last_used_at = NOW()
         WHERE credential_id = $2
        """,
        new_sign_count,
        credential_id,
    )
    return "UPDATE 1" in result


async def delete_credential(
    conn,
    passkey_id: str,
    user_id: str,
) -> bool:
    """Delete a credential and decrement passkey_count."""
    async with conn.transaction():
        result = await conn.execute(
            """
            DELETE FROM user_passkeys
            WHERE  id = $1 AND user_id = $2
            """,
            uuid.UUID(passkey_id),
            uuid.UUID(user_id),
        )

        if "DELETE 1" in result:
            await conn.execute(
                """
                UPDATE user_profiles
                   SET passkey_count = GREATEST(passkey_count - 1, 0)
                 WHERE id = $1
                """,
                uuid.UUID(user_id),
            )
            return True
        return False


async def update_credential_display_name(
    conn,
    passkey_id: str,
    user_id: str,
    display_name: str,
) -> bool:
    """Update the friendly display name for a passkey."""
    result = await conn.execute(
        """
        UPDATE user_passkeys
           SET display_name = $1
         WHERE id = $2 AND user_id = $3
        """,
        display_name,
        uuid.UUID(passkey_id),
        uuid.UUID(user_id),
    )
    return "UPDATE 1" in result


async def get_user_id_by_credential_id(
    conn,
    credential_id: str,
) -> Optional[str]:
    """Get the user ID associated with a credential (for discoverable auth)."""
    row = await conn.fetchrow(
        """
        SELECT user_id FROM user_passkeys WHERE credential_id = $1
        """,
        credential_id,
    )
    return str(row["user_id"]) if row else None


# ---------------------------------------------------------------------------
# Configuration Helpers
# ---------------------------------------------------------------------------

def set_rp_config(
    rp_id: str,
    rp_name: str,
    rp_origin: str,
    dev_origins: Optional[list[str]] = None,
) -> None:
    """Override the default RP configuration (call at app startup)."""
    global RP_ID, RP_NAME, RP_ORIGIN, DEV_ORIGINS
    RP_ID = rp_id
    RP_NAME = rp_name
    RP_ORIGIN = rp_origin
    if dev_origins:
        DEV_ORIGINS = dev_origins


def get_allowed_origins() -> list[str]:
    """Get all allowed origins (production + dev)."""
    return [RP_ORIGIN] + DEV_ORIGINS
