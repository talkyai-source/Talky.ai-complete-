"""MFA challenge token primitives — used by both auth.py login and the
/auth/mfa/verify endpoint.

A challenge token is the short-lived (5 min), single-use bearer that
proves "this user just passed step 1 (password)" so step 2 (TOTP /
recovery code) can issue the real session JWT.

Three operations:
  - create:   mint + persist a fresh challenge
  - resolve:  validate a presented token (without consuming it)
  - consume:  mark a challenge as used (called only after step-2 succeeds)
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from ._shared import MFA_CHALLENGE_TTL_MINUTES, _sha256

logger = logging.getLogger(__name__)


async def create_mfa_challenge(
    conn,
    user_id: str,
    ip_address: str,
    user_agent: Optional[str] = None,
) -> str:
    """
    Create an ephemeral MFA challenge record and return the raw token.

    Called from auth.py POST /auth/login when password is verified AND
    the user has MFA enabled.  The raw token is returned to the client
    (NOT as a cookie — it goes in the response body so the client can use
    it for the step-2 call).  Only SHA-256(raw_token) is stored.

    Returns the raw challenge token string (32 URL-safe base64 chars).
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = _sha256(raw_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=MFA_CHALLENGE_TTL_MINUTES)

    await conn.execute(
        """
        INSERT INTO mfa_challenges
               (user_id, challenge_hash, ip_address, user_agent,
                expires_at, used, created_at)
        VALUES ($1, $2, $3, $4, $5, FALSE, $6)
        """,
        user_id,
        token_hash,
        ip_address,
        user_agent,
        expires_at,
        now,
    )

    logger.debug(
        "MFA challenge created for user=%s expires=%s",
        user_id,
        expires_at.isoformat(),
    )
    return raw_token


async def resolve_mfa_challenge(conn, raw_token: str) -> Optional[dict[str, str]]:
    """
    Validate a raw challenge token and return the challenge row if valid.

    Checks: exists, not used, not expired.
    Does NOT consume the challenge — call consume_mfa_challenge() after
    successful TOTP verification.

    Returns the row dict or None.
    """
    if not raw_token:
        return None

    token_hash = _sha256(raw_token)
    now = datetime.now(timezone.utc)

    row = await conn.fetchrow(
        """
        SELECT id, user_id, ip_address, expires_at, used
        FROM   mfa_challenges
        WHERE  challenge_hash = $1
          AND  used           = FALSE
          AND  expires_at     > $2
        """,
        token_hash,
        now,
    )
    return dict(row) if row else None


async def consume_mfa_challenge(conn, challenge_id: str) -> None:
    """Mark a challenge as used so it cannot be replayed."""
    now = datetime.now(timezone.utc)
    await conn.execute(
        """
        UPDATE mfa_challenges
           SET used    = TRUE,
               used_at = $1
         WHERE id      = $2
           AND used    = FALSE
        """,
        now,
        challenge_id,
    )
