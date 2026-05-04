"""Suspicious-session verification flow.

Called when a user confirms ownership after a security alert ("Yes, this
was me"). Clears the suspicious flag and optionally rebinds the device
fingerprint to the current device.
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from ._shared import _now_utc, _parse_command_tag

logger = logging.getLogger("app.core.security.sessions")


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
