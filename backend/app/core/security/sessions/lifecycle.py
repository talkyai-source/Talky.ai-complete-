"""Session lifecycle — create_session + validate_session.

These two functions form the read-write hot path: every successful login
creates a session, every authenticated request validates one.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

import asyncpg

from app.core.security.device_fingerprint import (
    compare_fingerprints,
    generate_device_fingerprint,
    is_ip_change_significant,
    parse_user_agent,
)

from ._shared import (
    MAX_SESSIONS_PER_USER,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SESSION_LIFETIME_HOURS,
    _enforce_session_limit,
    _get_next_session_number,
    _hash_token,
    _mark_session_suspicious,
    _now_utc,
    _revoke_by_id,
)
from .tokens import generate_session_token

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger("app.core.security.sessions")


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
    return_session_id: bool = False,
) -> str | tuple[str, str]:
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
    str | tuple[str, str]
        The raw, unencoded session token. When ``return_session_id=True``,
        returns ``(raw_token, session_id)`` so callers can bind downstream
        access tokens to the revocable server-side session.
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

    row = await conn.fetchrow(
        """
        INSERT INTO security_sessions
            (user_id, session_token_hash, ip_address, user_agent,
             device_fingerprint, device_name, device_type, browser, os,
             bound_ip, ip_binding_enforced, fingerprint_binding_enforced,
             session_number, created_at, last_active_at, expires_at, revoked)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $14, $15, FALSE)
        RETURNING id
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
    if return_session_id:
        return raw_token, str(row["id"])
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
