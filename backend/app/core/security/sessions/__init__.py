"""
Server-side session management — DB-backed.

OWASP Session Management Cheat Sheet (official):
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

NIST SP 800-63B (Session Security):
  https://pages.nist.gov/800-63-3/sp800-63b.html

Key rules applied here:
  1. Session IDs are cryptographically random — secrets.token_urlsafe(32) = 256-bit entropy.
     OWASP minimum is 128-bit; we use 256-bit.
  2. Only the SHA-256 hash of the token is stored in the database.
     The raw token is handed to the client only once (in a cookie or response body).
     If the sessions table is ever dumped, raw tokens cannot be recovered.
  3. httpOnly + Secure + SameSite=Strict cookie attributes must be set by the caller
     when writing the token to the browser.
  4. Session IDs are rotated on login (new token issued, old one not reused).
  5. Server-side revocation: marking revoked=TRUE immediately invalidates the session
     regardless of whether the client still holds the token.
  6. Idle timeout (default 30 min) AND absolute lifetime (default 24 h) are both enforced.
  7. logout-all-sessions is supported via revoke_all_user_sessions().

Day 5 Enhancements:
  8. Session binding to IP + device fingerprint for hijacking detection.
  9. Device metadata extraction for session management UI.
  10. Concurrent session limits with automatic oldest revocation.
  11. Suspicious activity detection (IP change, fingerprint mismatch).
  12. Selective session revocation by session ID.

Public surface mirrors the previous single-file `sessions.py` — every
constant and function that was importable before is re-exported here.
"""
from __future__ import annotations

# Constants
from ._shared import (
    MAX_SESSIONS_PER_USER,
    SESSION_BIND_TO_FINGERPRINT,
    SESSION_BIND_TO_IP,
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SESSION_IP_BINDING_TOLERANCE,
    SESSION_LIFETIME_HOURS,
    SESSION_STRICT_BINDING,
    SESSION_TOKEN_BYTES,
)

# Tokens
from .tokens import generate_session_token, hash_session_token

# Lifecycle (create + validate)
from .lifecycle import create_session, validate_session

# Revocation
from .revocation import (
    purge_expired_sessions,
    revoke_all_user_sessions,
    revoke_session_by_id,
    revoke_session_by_token,
)

# Read-only queries
from .queries import (
    get_active_sessions,
    get_active_sessions_detailed,
    get_session_by_id,
    get_session_security_status,
)

# Verification
from .verification import verify_suspicious_session


__all__ = [
    # Constants
    "MAX_SESSIONS_PER_USER",
    "SESSION_BIND_TO_FINGERPRINT",
    "SESSION_BIND_TO_IP",
    "SESSION_COOKIE_NAME",
    "SESSION_IDLE_TIMEOUT_MINUTES",
    "SESSION_IP_BINDING_TOLERANCE",
    "SESSION_LIFETIME_HOURS",
    "SESSION_STRICT_BINDING",
    "SESSION_TOKEN_BYTES",
    # Tokens
    "generate_session_token",
    "hash_session_token",
    # Lifecycle
    "create_session",
    "validate_session",
    # Revocation
    "purge_expired_sessions",
    "revoke_all_user_sessions",
    "revoke_session_by_id",
    "revoke_session_by_token",
    # Queries
    "get_active_sessions",
    "get_active_sessions_detailed",
    "get_session_by_id",
    "get_session_security_status",
    # Verification
    "verify_suspicious_session",
]
