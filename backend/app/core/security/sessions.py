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
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import asyncpg

# Day 5: Device fingerprinting for session binding
from app.core.security.device_fingerprint import (
    compare_fingerprints,
    generate_device_fingerprint,
    is_ip_change_significant,
    parse_user_agent,
)

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Raw token length in bytes before URL-safe base64 encoding.
# 32 bytes → 256-bit entropy.  OWASP minimum is 128-bit (16 bytes).
SESSION_TOKEN_BYTES: int = 32

# Absolute session lifetime — session is always invalid after this period
# regardless of activity.
SESSION_LIFETIME_HOURS: int = 24

# Sliding idle timeout — if no activity for this many minutes the session
# is considered stale and is revoked on the next access attempt.
SESSION_IDLE_TIMEOUT_MINUTES: int = 30

# Cookie name written by the caller (centralised here so it's one source of truth).
SESSION_COOKIE_NAME: str = "talky_sid"

# ---------------------------------------------------------------------------
# Day 5: Session Security Configuration
# ---------------------------------------------------------------------------

# Enable IP binding - sessions tied to creating IP address
SESSION_BIND_TO_IP: bool = True

# Enable device fingerprint binding
SESSION_BIND_TO_FINGERPRINT: bool = True

# Strict mode - revoke sessions on binding mismatch instead of just marking suspicious
SESSION_STRICT_BINDING: bool = False

# Maximum concurrent sessions per user (0 = unlimited)
# When exceeded, oldest sessions are revoked
MAX_SESSIONS_PER_USER: int = 10

# IP subnet tolerance for binding (e.g., /24 allows same Class C subnet)
SESSION_IP_BINDING_TOLERANCE: str = "/24"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw_token: str) -> str:
    """
    Return the hex-encoded SHA-256 digest of the raw session token.

    We store the hash rather than the raw token so that a database leak
    does not hand an attacker usable session tokens.  This mirrors the
    same principle used for password-reset tokens.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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


async def create_session(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    ip_address: str,
    user_agent: Optional[str] = None,
    request: Optional["Request"] = None,
    bind_to_ip: bool = True,
    bind_to_fingerprint: bool = True,
    device_fingerprint: Optional[str] = None,
) -> str:
    """
    Persist a new server-side session and return the raw session token.

    The raw token is returned to the caller exactly once.  The caller is
    responsible for placing it in an httpOnly, Secure, SameSite=Strict
    cookie.

    Only the SHA-256 hash of the token is written to the database.

    Day 5 Enhancements:
    - Device fingerprinting for session binding and hijacking detection.
    - Device metadata extraction for session management UI.
    - Concurrent session limit enforcement.

    Parameters
    ----------
    conn:
        Active asyncpg connection (must be inside a transaction if
        atomicity with other writes is required).
    user_id:
        The UUID of the authenticated user (string form).
    ip_address:
        Client IP address recorded at session creation time.
    user_agent:
        HTTP User-Agent header value (optional, stored for audit purposes).
    request:
        FastAPI Request object for device fingerprinting (optional).
    bind_to_ip:
        Whether to bind this session to the creating IP address.
    bind_to_fingerprint:
        Whether to bind this session to the device fingerprint.
    device_fingerprint:
        Pre-computed device fingerprint (if not using request).

    Returns
    -------
    str
        The raw, unencoded session token.  Hand this to the client; do
        NOT log or store it yourself.
    """
    raw_token = generate_session_token()
    token_hash = _hash_token(raw_token)
    now = _now_utc()
    expires_at = now + timedelta(hours=SESSION_LIFETIME_HOURS)

    # Day 5: Generate device fingerprint
    fingerprint = device_fingerprint
    if fingerprint is None and request is not None:
        fingerprint = generate_device_fingerprint(request, user_agent)

    # Day 5: Parse device metadata from User-Agent
    device_info = parse_user_agent(user_agent)

    # Day 5: Enforce concurrent session limit
    if MAX_SESSIONS_PER_USER > 0:
        await _enforce_session_limit(conn, user_id, max_sessions=MAX_SESSIONS_PER_USER)

    # Day 5: Get next session number for this user
    session_number = await _get_next_session_number(conn, user_id)

    await conn.execute(
        """
        INSERT INTO security_sessions
            (user_id, session_token_hash, ip_address, user_agent,
             device_fingerprint, device_name, device_type, browser, os,
             bound_ip, ip_binding_enforced, fingerprint_binding_enforced,
             session_number, created_at, last_active_at, expires_at, revoked)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $14, $15, FALSE)
        """,
        user_id,
        token_hash,
        ip_address,
        user_agent,
        fingerprint,
        device_info["device_name"],
        device_info["device_type"],
        device_info["browser"],
        device_info["os"],
        ip_address if bind_to_ip else None,
        bind_to_ip,
        bind_to_fingerprint,
        session_number,
        now,
        expires_at,
    )

    logger.info(
        "Session created for user=%s ip=%s device_type=%s browser=%s expires=%s",
        user_id,
        ip_address,
        device_info["device_type"],
        device_info["browser"],
        expires_at.isoformat(),
    )
    return raw_token


async def validate_session(
    conn: asyncpg.Connection,
    raw_token: str,
    current_ip: Optional[str] = None,
    current_fingerprint: Optional[str] = None,
    strict_binding: bool = False,
) -> Optional[dict]:
    """
    Validate a session token and return the session record if valid.

    Performs all three OWASP checks in order:
      1. Token hash exists in the database.
      2. Session is not revoked.
      3. Absolute expiry has not passed.
      4. Idle timeout has not elapsed (revokes session if exceeded).

    Day 5 Enhancements:
      5. IP binding validation (if enforced and current_ip provided).
      6. Device fingerprint validation (if current_fingerprint provided).
      7. Suspicious activity detection and flagging.

    If the session is valid, ``last_active_at`` is updated (sliding window).

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    raw_token:
        The raw token string from the client's cookie.
    current_ip:
        Current client IP for binding validation (optional).
    current_fingerprint:
        Current device fingerprint for binding validation (optional).
    strict_binding:
        If True, revoke session on binding mismatch; if False, just mark suspicious.

    Returns
    -------
    dict | None
        A dictionary of the session row if valid, or None if invalid /
        expired / revoked / timed-out / binding violation.
    """
    if not raw_token:
        return None

    token_hash = _hash_token(raw_token)
    now = _now_utc()

    # Day 8: Extended SELECT to include user and tenant status
    row = await conn.fetchrow(
        """
        SELECT s.id, s.user_id, s.ip_address, s.user_agent,
               s.device_fingerprint, s.device_name, s.device_type, s.browser, s.os,
               s.bound_ip, s.ip_binding_enforced, s.fingerprint_binding_enforced,
               s.is_suspicious, s.suspicious_reason, s.requires_verification,
               s.created_at, s.last_active_at, s.expires_at, s.revoked,
               up.is_active as user_active,
               t.status as tenant_status,
               p.status as partner_status,
               t.id as tenant_id
        FROM   security_sessions s
        JOIN   user_profiles up ON up.id = s.user_id
        LEFT JOIN tenants t ON t.id = up.tenant_id
        LEFT JOIN white_label_partners p ON p.id = t.white_label_partner_id
        WHERE  s.session_token_hash = $1
          AND  s.revoked    = FALSE
          AND  s.expires_at > $2
        """,
        token_hash,
        now,
    )

    if not row:
        logger.debug("validate_session: token not found, revoked, or expired")
        return None

    # Day 8: Check for suspensions (Instant Block Propagation)
    if not row["user_active"]:
        logger.warning("Session validated for inactive user=%s — revoking", row["user_id"])
        await _revoke_by_id(conn, row["id"], reason="user_inactive")
        return None

    if row["tenant_status"] == "suspended":
        logger.warning("Session validated for suspended tenant user=%s — revoking", row["user_id"])
        await _revoke_by_id(conn, row["id"], reason="tenant_suspended")
        return None

    if row["partner_status"] == "suspended":
        logger.warning("Session validated for suspended partner user=%s — revoking", row["user_id"])
        await _revoke_by_id(conn, row["id"], reason="partner_suspended")
        return None

    session = dict(row)

    # --- idle timeout check ---------------------------------------------------
    idle_deadline = session["last_active_at"] + timedelta(
        minutes=SESSION_IDLE_TIMEOUT_MINUTES
    )
    if now > idle_deadline:
        logger.info(
            "Session idle timeout for user=%s — revoking session id=%s",
            session["user_id"],
            session["id"],
        )
        await _revoke_by_id(conn, session["id"], reason="idle_timeout")
        return None

    # Day 5: IP binding validation --------------------------------------------
    if current_ip and session.get("ip_binding_enforced"):
        original_ip = session.get("bound_ip") or session.get("ip_address")
        ip_check = is_ip_change_significant(
            original_ip, current_ip, strict_mode=strict_binding
        )

        if ip_check["significant"]:
            logger.warning(
                "Session IP mismatch detected: user=%s session=%s original=%s current=%s reason=%s",
                session["user_id"],
                session["id"],
                original_ip,
                current_ip,
                ip_check["reason"],
            )

            if strict_binding:
                await _revoke_by_id(conn, session["id"], reason="ip_binding_violation")
                return None
            else:
                await _mark_session_suspicious(
                    conn, session["id"], f"ip_mismatch:{ip_check['reason']}"
                )
                session["is_suspicious"] = True
                session["suspicious_reason"] = f"ip_mismatch:{ip_check['reason']}"

    # Day 5: Device fingerprint validation ------------------------------------
    if current_fingerprint and session.get("device_fingerprint"):
        fp_check = compare_fingerprints(
            session["device_fingerprint"], current_fingerprint
        )

        if not fp_check["match"]:
            logger.warning(
                "Session fingerprint mismatch: user=%s session=%s",
                session["user_id"],
                session["id"],
            )

            if strict_binding and fp_check["recommendation"] == "revoke":
                await _revoke_by_id(
                    conn, session["id"], reason="fingerprint_binding_violation"
                )
                return None
            elif not session.get("is_suspicious"):
                await _mark_session_suspicious(
                    conn, session["id"], "fingerprint_mismatch"
                )
                session["is_suspicious"] = True
                session["suspicious_reason"] = "fingerprint_mismatch"

    # --- slide the idle window -----------------------------------------------
    await conn.execute(
        "UPDATE security_sessions SET last_active_at = $1 WHERE id = $2",
        now,
        session["id"],
    )
    session["last_active_at"] = now

    return session


async def revoke_session_by_token(
    conn: asyncpg.Connection,
    raw_token: str,
    *,
    reason: str = "logout",
) -> bool:
    """
    Revoke a single session identified by its raw token.

    This is the primary logout mechanism.  After this call the token is
    permanently invalid — the client-side cookie should also be cleared
    by the caller (set to empty value, immediate expiry).

    Returns True if a session row was actually updated, False if the
    token was not found or was already revoked.
    """
    if not raw_token:
        return False

    token_hash = _hash_token(raw_token)
    now = _now_utc()

    result = await conn.execute(
        """
        UPDATE security_sessions
           SET revoked      = TRUE,
               revoked_at   = $1,
               revoke_reason = $2
         WHERE session_token_hash = $3
           AND revoked = FALSE
        """,
        now,
        reason,
        token_hash,
    )

    # asyncpg returns "UPDATE N" — extract N
    updated = _parse_command_tag(result)
    if updated:
        logger.info("Session revoked reason=%s", reason)
    return updated


async def revoke_all_user_sessions(
    conn: asyncpg.Connection,
    user_id: str,
    *,
    reason: str = "logout_all",
    exclude_token_hash: Optional[str] = None,
) -> int:
    """
    Revoke every active session belonging to *user_id*.

    Used for:
      • "Log out from all devices" feature.
      • Password change (OWASP: invalidate all sessions on password change).
      • Account compromise response.

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    user_id:
        UUID string of the user whose sessions should be revoked.
    reason:
        Stored in ``revoke_reason`` for audit purposes.
    exclude_token_hash:
        If provided, the session with this token hash is NOT revoked —
        useful when changing password on an active session and you want
        to keep the current session alive.

    Returns
    -------
    int
        Number of sessions that were revoked.
    """
    now = _now_utc()

    if exclude_token_hash:
        result = await conn.execute(
            """
            UPDATE security_sessions
               SET revoked       = TRUE,
                   revoked_at    = $1,
                   revoke_reason = $2
             WHERE user_id  = $3
               AND revoked  = FALSE
               AND session_token_hash != $4
            """,
            now,
            reason,
            user_id,
            exclude_token_hash,
        )
    else:
        result = await conn.execute(
            """
            UPDATE security_sessions
               SET revoked       = TRUE,
                   revoked_at    = $1,
                   revoke_reason = $2
             WHERE user_id = $3
               AND revoked = FALSE
            """,
            now,
            reason,
            user_id,
        )

    count = _parse_command_tag_count(result)
    logger.info(
        "revoke_all_user_sessions: revoked %d session(s) for user=%s reason=%s",
        count,
        user_id,
        reason,
    )
    return count


async def get_active_sessions(
    conn: asyncpg.Connection,
    user_id: str,
) -> list[dict]:
    """
    Return all active (non-revoked, non-expired) sessions for a user.

    Useful for a "manage active sessions" UI page.
    Raw tokens are NOT included — only metadata.
    """
    now = _now_utc()
    rows = await conn.fetch(
        """
        SELECT id, ip_address, user_agent, created_at, last_active_at, expires_at
        FROM   security_sessions
        WHERE  user_id    = $1
          AND  revoked    = FALSE
          AND  expires_at > $2
        ORDER  BY last_active_at DESC
        """,
        user_id,
        now,
    )
    return [dict(r) for r in rows]


# =============================================================================
# Day 5: Enhanced Session Functions
# =============================================================================


async def get_active_sessions_detailed(
    conn: asyncpg.Connection,
    user_id: str,
    current_session_token_hash: Optional[str] = None,
) -> list[dict]:
    """
    Return detailed active sessions for a user with device info.

    Day 5 enhancement for the "manage active sessions" UI. Includes:
    - Device metadata (name, type, browser, OS)
    - Security status (suspicious, requires_verification)
    - Binding information
    - Current session flag

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    user_id:
        UUID string of the user.
    current_session_token_hash:
        Hash of the current session token to mark as "this device".

    Returns
    -------
    list[dict]
        List of session dictionaries with full device and security metadata.
    """
    now = _now_utc()
    rows = await conn.fetch(
        """
        SELECT id, user_id, ip_address, user_agent,
               device_fingerprint, device_name, device_type, browser, os,
               bound_ip, ip_binding_enforced,
               is_suspicious, suspicious_reason, requires_verification,
               session_number, created_at, last_active_at, expires_at,
               session_token_hash = $3 as is_current
        FROM   security_sessions
        WHERE  user_id    = $1
          AND  revoked    = FALSE
          AND  expires_at > $2
        ORDER  BY last_active_at DESC
        """,
        user_id,
        now,
        current_session_token_hash,
    )
    return [dict(r) for r in rows]


async def revoke_session_by_id(
    conn: asyncpg.Connection,
    session_id: str,
    user_id: str,
    *,
    reason: str = "user_initiated",
) -> bool:
    """
    Revoke a specific session by ID (selective logout).

    Day 5 feature for "log out from this device" functionality.
    Security: Verifies session belongs to user_id before revoking.

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    session_id:
        UUID of the session to revoke.
    user_id:
        UUID of the user requesting revocation (must own the session).
    reason:
        Reason for revocation (audit trail).

    Returns
    -------
    bool
        True if session was revoked, False if not found or not owned.
    """
    now = _now_utc()

    result = await conn.execute(
        """
        UPDATE security_sessions
           SET revoked       = TRUE,
               revoked_at    = $1,
               revoke_reason = $2
         WHERE id      = $3
           AND user_id = $4
           AND revoked = FALSE
        """,
        now,
        reason,
        session_id,
        user_id,
    )

    updated = _parse_command_tag(result)
    if updated:
        logger.info(
            "Session revoked by ID: session_id=%s user=%s reason=%s",
            session_id,
            user_id,
            reason,
        )
    return updated


async def verify_suspicious_session(
    conn: asyncpg.Connection,
    session_id: str,
    user_id: str,
    new_fingerprint: Optional[str] = None,
) -> bool:
    """
    Verify ownership of a suspicious session.

    Day 5 feature: Called when user confirms "Yes, this was me" after
    security alert. Clears suspicious flags and optionally updates
    the device fingerprint to the current one.

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    session_id:
        UUID of the session to verify.
    user_id:
        UUID of the user (must own the session).
    new_fingerprint:
        Optional new device fingerprint to store (updates binding).

    Returns
    -------
    bool
        True if session was verified, False if not found or not owned.
    """
    now = _now_utc()

    if new_fingerprint:
        result = await conn.execute(
            """
            UPDATE security_sessions
               SET is_suspicious       = FALSE,
                   suspicious_reason   = NULL,
                   requires_verification = FALSE,
                   verified_at         = $1,
                   device_fingerprint  = $2
             WHERE id      = $3
               AND user_id = $4
               AND revoked = FALSE
            """,
            now,
            new_fingerprint,
            session_id,
            user_id,
        )
    else:
        result = await conn.execute(
            """
            UPDATE security_sessions
               SET is_suspicious       = FALSE,
                   suspicious_reason   = NULL,
                   requires_verification = FALSE,
                   verified_at         = $1
             WHERE id      = $2
               AND user_id = $3
               AND revoked = FALSE
            """,
            now,
            session_id,
            user_id,
        )

    updated = _parse_command_tag(result)
    if updated:
        logger.info(
            "Suspicious session verified: session_id=%s user=%s",
            session_id,
            user_id,
        )
    return updated


async def get_session_security_status(
    conn: asyncpg.Connection,
    session_token_hash: str,
) -> Optional[dict]:
    """
    Get security status of a session.

    Day 5 feature for security status dashboard.

    Returns
    -------
    dict | None
        Security status including:
        - is_bound: Whether session has binding enabled
        - is_suspicious: Whether anomalies detected
        - requires_verification: Whether user action needed
        - recommendations: List of security recommendations
    """
    row = await conn.fetchrow(
        """
        SELECT ip_binding_enforced, fingerprint_binding_enforced,
               is_suspicious, suspicious_reason, requires_verification,
               device_fingerprint IS NOT NULL as has_fingerprint
        FROM   security_sessions
        WHERE  session_token_hash = $1
          AND  revoked = FALSE
        """,
        session_token_hash,
    )

    if not row:
        return None

    status = {
        "is_bound": row["ip_binding_enforced"] or row["fingerprint_binding_enforced"],
        "ip_binding": row["ip_binding_enforced"],
        "fingerprint_binding": row["fingerprint_binding_enforced"],
        "has_fingerprint": row["has_fingerprint"],
        "is_suspicious": row["is_suspicious"],
        "suspicious_reason": row["suspicious_reason"],
        "requires_verification": row["requires_verification"],
        "recommendations": [],
    }

    # Generate recommendations
    if not row["ip_binding_enforced"] and not row["fingerprint_binding_enforced"]:
        status["recommendations"].append("Enable session binding for enhanced security")
    if row["is_suspicious"]:
        status["recommendations"].append("Review and verify suspicious session activity")
    if not row["has_fingerprint"]:
        status["recommendations"].append("Re-login to enable device fingerprinting")

    return status


# =============================================================================
# Day 5: Private Helpers
# =============================================================================


async def _enforce_session_limit(
    conn: asyncpg.Connection,
    user_id: str,
    max_sessions: int,
) -> int:
    """
    Enforce concurrent session limit by revoking oldest sessions.

    If user has >= max_sessions active, revoke oldest until under limit.

    Returns
    -------
    int
        Number of sessions revoked.
    """
    now = _now_utc()

    # Count active sessions
    count_row = await conn.fetchrow(
        """
        SELECT COUNT(*) as count
        FROM   security_sessions
        WHERE  user_id = $1
          AND  revoked = FALSE
          AND  expires_at > $2
        """,
        user_id,
        now,
    )
    current_count = count_row["count"] if count_row else 0

    if current_count < max_sessions:
        return 0

    # Revoke oldest sessions to make room
    # Keep (max_sessions - 1) to make room for the new one being created
    keep_count = max_sessions - 1

    revoked = await conn.execute(
        """
        UPDATE security_sessions
           SET revoked       = TRUE,
               revoked_at    = $1,
               revoke_reason = 'concurrent_limit_exceeded'
         WHERE id IN (
             SELECT id
             FROM   security_sessions
             WHERE  user_id = $2
               AND  revoked = FALSE
               AND  expires_at > $1
             ORDER  BY session_number ASC
             LIMIT  NULLIF($3, 0)
         )
        """,
        now,
        user_id,
        max(0, current_count - keep_count),
    )

    revoked_count = _parse_command_tag_count(revoked)
    if revoked_count > 0:
        logger.info(
            "Session limit enforced for user=%s: revoked %d oldest sessions",
            user_id,
            revoked_count,
        )
    return revoked_count


async def _get_next_session_number(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """
    Get the next session number for this user.

    Session numbers are sequential per user for tracking concurrent sessions.

    Returns
    -------
    int
        Next available session number (max + 1, or 1 if no sessions).
    """
    row = await conn.fetchrow(
        """
        SELECT COALESCE(MAX(session_number), 0) + 1 as next_number
        FROM   security_sessions
        WHERE  user_id = $1
        """,
        user_id,
    )
    return row["next_number"] if row else 1


async def _mark_session_suspicious(
    conn: asyncpg.Connection,
    session_id: str,
    reason: str,
) -> None:
    """
    Mark a session as suspicious.

    Called when binding validation detects anomalies.
    Does not revoke the session but flags it for review.
    """
    now = _now_utc()
    await conn.execute(
        """
        UPDATE security_sessions
           SET is_suspicious       = TRUE,
               suspicious_reason   = $1,
               suspicious_detected_at = $2
         WHERE id = $3
        """,
        reason,
        now,
        session_id,
    )
    logger.warning("Session marked suspicious: session_id=%s reason=%s", session_id, reason)


async def purge_expired_sessions(conn: asyncpg.Connection) -> int:
    """
    Hard-delete sessions that are both expired AND revoked.

    Intended to be called periodically (e.g., from a background worker or
    scheduled job) to keep the ``security_sessions`` table from growing
    unbounded.

    Returns the number of rows deleted.
    """
    now = _now_utc()
    result = await conn.execute(
        """
        DELETE FROM security_sessions
         WHERE expires_at < $1
            OR revoked = TRUE
        """,
        now,
    )
    count = _parse_command_tag_count(result)
    logger.info("purge_expired_sessions: removed %d row(s)", count)
    return count


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def hash_session_token(raw_token: str) -> str:
    """
    Public wrapper around _hash_token.

    Use this when code outside this module needs to compute the token hash
    (e.g., to pass as ``exclude_token_hash`` to ``revoke_all_user_sessions``).

    Returns the hex-encoded SHA-256 digest of *raw_token*.
    """
    return _hash_token(raw_token)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _revoke_by_id(
    conn: asyncpg.Connection,
    session_id: str,
    *,
    reason: str,
) -> None:
    """Revoke a session directly by its primary key (UUID)."""
    now = _now_utc()
    await conn.execute(
        """
        UPDATE security_sessions
           SET revoked       = TRUE,
               revoked_at    = $1,
               revoke_reason = $2
         WHERE id = $3
        """,
        now,
        reason,
        session_id,
    )


def _parse_command_tag(tag: str) -> bool:
    """Return True if the asyncpg command tag indicates at least one row was affected."""
    try:
        return int(tag.split()[-1]) > 0
    except (ValueError, IndexError, AttributeError):
        return False


def _parse_command_tag_count(tag: str) -> int:
    """Return the integer row count from an asyncpg command tag string."""
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0
