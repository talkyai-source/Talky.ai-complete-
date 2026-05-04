"""Read-only session queries — listings + per-session lookups.

These never mutate state. Used by:
  - "manage active sessions" UI
  - admin/security dashboards
  - JWT-to-session binding lookups
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from ._shared import _now_utc


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


async def get_session_by_id(
    conn: asyncpg.Connection,
    session_id: str,
    *,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Return an active session by its primary key.

    Used to bind bearer tokens to a revocable server-side session record.
    """
    now = _now_utc()
    params: list[object] = [session_id, now]
    conditions = [
        "id = $1",
        "revoked = FALSE",
        "expires_at > $2",
    ]
    if user_id:
        params.append(user_id)
        conditions.append(f"user_id = ${len(params)}")

    row = await conn.fetchrow(
        f"""
        SELECT id, user_id, created_at, last_active_at, expires_at,
               mfa_verified, revoked, requires_verification
        FROM   security_sessions
        WHERE  {' AND '.join(conditions)}
        """,
        *params,
    )
    return dict(row) if row else None
