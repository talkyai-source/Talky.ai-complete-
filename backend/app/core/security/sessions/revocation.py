"""Session revocation paths — by token, by id, all-for-user, plus periodic purge.

OWASP: server-side revocation is the only reliable way to invalidate an
in-the-wild token. The cookie clear by the client is best-effort only.
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from ._shared import (
    _hash_token,
    _now_utc,
    _parse_command_tag,
    _parse_command_tag_count,
)

logger = logging.getLogger("app.core.security.sessions")


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
