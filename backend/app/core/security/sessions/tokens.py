"""Session token generation + public hashing helper."""
from __future__ import annotations

import secrets

from ._shared import SESSION_TOKEN_BYTES, _hash_token


def generate_session_token() -> str:
    """
    Generate a cryptographically secure, URL-safe session token.

    Uses Python's ``secrets`` module which is backed by the OS CSPRNG.
    The returned string is approximately 43 characters long (32 bytes in
    URL-safe base64).

    OWASP: "Session IDs must be created with a CSPRNG that produces at
    least 128 bits of entropy."
    """
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(raw_token: str) -> str:
    """
    Public wrapper around _hash_token.

    Use this when code outside this module needs to compute the token hash
    (e.g., to pass as ``exclude_token_hash`` to ``revoke_all_user_sessions``).

    Returns the hex-encoded SHA-256 digest of *raw_token*.
    """
    return _hash_token(raw_token)
