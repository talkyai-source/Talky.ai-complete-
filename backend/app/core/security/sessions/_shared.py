"""Shared constants + private helpers for session management.

Constants and helpers in this file are imported by every other
sessions/* module. Anything tied to a specific operation (token mint,
revocation, query, verification) lives in that operation's module.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger("app.core.security.sessions")

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
# Internal helpers — time + token hashing
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
# asyncpg command-tag parsers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Internal mutation helpers shared by validate / revocation / lifecycle
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
