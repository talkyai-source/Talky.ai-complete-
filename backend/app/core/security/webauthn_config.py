"""
WebAuthn Relying Party configuration and shared helpers.

Official references (verified March 2026):
  W3C WebAuthn Level 3 Candidate Recommendation (January 13, 2026):
    https://www.w3.org/TR/webauthn-3/
  py_webauthn 2.7.1 (Duo Labs / PyPI):
    https://github.com/duo-labs/py_webauthn
    https://pypi.org/project/webauthn/
  FIDO2 / CTAP2 specification (FIDO Alliance, 2023):
    https://fidoalliance.org/specs/fido-v2.1-ps-20210615/

Environment variables (all optional — safe defaults for local development):

  WEBAUTHN_RP_ID
    The Relying Party identifier.  Must be the effective domain of the
    application, without scheme, port or path.
    Default: "localhost"
    Production example: "talky.ai" or "app.talky.ai"

  WEBAUTHN_RP_NAME
    Human-readable application name shown in authenticator prompts.
    Default: "Talky.ai"

  WEBAUTHN_ORIGIN
    Comma-separated list of acceptable origins.  Must match the exact
    scheme+host+port seen by the browser.
    Default: "http://localhost:3000"
    Production example: "https://app.talky.ai"

  WEBAUTHN_REQUIRE_UV
    Whether to require user verification (biometric/PIN) during
    authentication.  "true" | "false".  Default: "true".
    Set to "false" only if hardware keys without PIN are required.

Important (W3C WebAuthn §7.1, §7.2):
  The RP_ID must be equal to, or a registrable suffix of, the origin's
  effective domain.  Mismatches are silently rejected by the browser.

  For cross-subdomain support (e.g. app.talky.ai + admin.talky.ai) set
  WEBAUTHN_RP_ID=talky.ai so both subdomains share the same key store.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RP configuration
# ---------------------------------------------------------------------------


def get_rp_id() -> str:
    """
    Return the WebAuthn Relying Party ID.

    This is the effective domain of the application (no scheme, no port,
    no path).  It is embedded in every credential during registration and
    must match exactly on every subsequent authentication.

    Default: "localhost" (safe for local development without HTTPS).
    """
    return os.getenv("WEBAUTHN_RP_ID", "localhost").strip()


def get_rp_name() -> str:
    """
    Return the human-readable Relying Party name.

    Shown to the user in the authenticator prompt
    (e.g. "Sign in to Talky.ai").

    Default: "Talky.ai"
    """
    return os.getenv("WEBAUTHN_RP_NAME", "Talky.ai").strip()


def get_expected_origins() -> list[str]:
    """
    Return the list of acceptable WebAuthn origins.

    The origin is the full scheme + host + port of the page that calls
    ``navigator.credentials.create()`` / ``navigator.credentials.get()``.
    py_webauthn accepts either a single string or a list; we always pass
    a list so that multiple origins (e.g. HTTP for dev + HTTPS for staging)
    can be configured via a single env var.

    Configured via WEBAUTHN_ORIGIN (comma-separated).
    Default: ["http://localhost:3000"]

    Production example:
        WEBAUTHN_ORIGIN=https://app.talky.ai,https://admin.talky.ai
    """
    raw = os.getenv("WEBAUTHN_ORIGIN", "http://localhost:3000")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        origins = ["http://localhost:3000"]
    return origins


def require_user_verification() -> bool:
    """
    Return True if the server requires user verification (biometric/PIN).

    W3C WebAuthn §6.1 — User verification ensures the authenticator confirms
    the human user's presence via biometric or device PIN, providing
    phishing-resistant authentication equivalent to a second factor.

    Controlled by WEBAUTHN_REQUIRE_UV env var ("true" / "false").
    Default: True.

    Set to False only if supporting hardware keys that lack UV capability
    (e.g. bare security keys without PIN).  Passkeys always perform UV.
    """
    raw = os.getenv("WEBAUTHN_REQUIRE_UV", "true").strip().lower()
    return raw not in ("false", "0", "no", "off")


# ---------------------------------------------------------------------------
# User handle encoding (W3C WebAuthn §6.4.3)
# ---------------------------------------------------------------------------


def user_id_to_webauthn_bytes(user_id: str) -> bytes:
    """
    Encode a user UUID string to the 16-byte ``user.id`` (userHandle) used
    in WebAuthn registration options.

    W3C WebAuthn §6.4.3:
      "The user handle SHOULD NOT contain personally identifying information
       about the user, such as a username or e-mail address."

    Using the raw UUID bytes (16 bytes) satisfies this requirement — a UUID
    is an opaque identifier, not PII.  It is deterministic: the same user_id
    always produces the same byte sequence, so we never need to store this
    separately.

    Parameters
    ----------
    user_id:
        The UUID string from user_profiles.id (e.g.
        "550e8400-e29b-41d4-a716-446655440000").

    Returns
    -------
    bytes
        16-byte big-endian UUID bytes.
    """
    return uuid.UUID(user_id).bytes


def webauthn_bytes_to_user_id(handle_bytes: bytes) -> str:
    """
    Decode a WebAuthn userHandle (16 raw bytes) back to a UUID string.

    Called during the authentication ceremony: the authenticator echoes back
    the userHandle we stored during registration, allowing us to identify the
    user without requiring a username or email in the authentication request
    (discoverable credentials / passkeys).

    Parameters
    ----------
    handle_bytes:
        The raw userHandle bytes returned by the authenticator.

    Returns
    -------
    str
        UUID string (e.g. "550e8400-e29b-41d4-a716-446655440000").

    Raises
    ------
    ValueError
        If the bytes cannot be decoded as a UUID (wrong length or format).
    """
    if len(handle_bytes) != 16:
        raise ValueError(
            f"WebAuthn userHandle must be 16 bytes (UUID), got {len(handle_bytes)}"
        )
    return str(uuid.UUID(bytes=handle_bytes))


# ---------------------------------------------------------------------------
# Base64url helpers
# ---------------------------------------------------------------------------


def bytes_to_base64url(b: bytes) -> str:
    """
    Encode bytes to a URL-safe base64 string without padding.

    This is the canonical encoding for WebAuthn binary values in JSON
    (W3C WebAuthn §6.5.1 — BufferSource fields are transmitted as base64url).

    Used to store credential_id and credential_public_key in the database
    as TEXT columns, which is safer to query than BYTEA.

    Parameters
    ----------
    b:
        Raw bytes to encode.

    Returns
    -------
    str
        URL-safe base64 string without '=' padding.
    """
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def base64url_to_bytes_safe(value: str) -> bytes:
    """
    Decode a URL-safe base64 string (with or without padding) to bytes.

    Wrapper around ``webauthn.base64url_to_bytes`` that also handles
    manually-padded strings for robustness.

    Parameters
    ----------
    value:
        Base64url string, with or without '=' padding.

    Returns
    -------
    bytes
        Decoded raw bytes.

    Raises
    ------
    ValueError
        If the input is not valid base64url.
    """
    # Add padding if necessary
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ValueError(f"Invalid base64url value: {exc}") from exc


# ---------------------------------------------------------------------------
# Challenge TTL
# ---------------------------------------------------------------------------

# WebAuthn ceremony timeout: 5 minutes.
# The browser typically enforces its own timeout (often 5 minutes), so we
# use the same value server-side to expire stored challenges.
WEBAUTHN_CHALLENGE_TTL_MINUTES: int = 5

# Maximum number of passkeys a single user may register.
# Protects against storage exhaustion; adjust per business requirements.
MAX_PASSKEYS_PER_USER: int = 10
