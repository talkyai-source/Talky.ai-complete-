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

# Per-challenge brute-force cap (C2 fix). Five wrong submissions burns
# the challenge — the user has to re-authenticate at /auth/login to
# obtain a fresh one. Migration 0007_mfa_challenge_attempts added the
# attempts column + CHECK (0 <= attempts <= 100) clamp.
MFA_VERIFY_MAX_ATTEMPTS: int = 5


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

    Checks: exists, not used, not expired, attempts under MFA_VERIFY_MAX_ATTEMPTS.
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
        SELECT id, user_id, ip_address, expires_at, used, attempts
        FROM   mfa_challenges
        WHERE  challenge_hash = $1
          AND  used           = FALSE
          AND  expires_at     > $2
        """,
        token_hash,
        now,
    )
    if row is None:
        return None
    if (row["attempts"] or 0) >= MFA_VERIFY_MAX_ATTEMPTS:
        # Already burned through the per-challenge attempt budget. Reject
        # without leaking that "this challenge exists but is throttled" —
        # the caller maps None to the same generic 401 as any other miss.
        logger.warning(
            "mfa_challenge_attempts_exceeded challenge_id=%s attempts=%s",
            row["id"], row["attempts"],
        )
        return None
    return dict(row)


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


async def record_failed_mfa_attempt(conn, challenge_id: str) -> int:
    """Increment the per-challenge failure counter; return the new total.

    Used by /auth/mfa/verify on every wrong code / wrong recovery-code so
    we can enforce MFA_VERIFY_MAX_ATTEMPTS without needing a separate
    Redis bucket. The DB CHECK constraint caps the counter at 100 to
    keep a determined attacker from running it into integer overflow
    territory during testing.
    """
    row = await conn.fetchrow(
        """
        UPDATE mfa_challenges
           SET attempts = LEAST(attempts + 1, 100)
         WHERE id = $1
        RETURNING attempts
        """,
        challenge_id,
    )
    return int(row["attempts"]) if row else 0


async def invalidate_mfa_challenge(conn, challenge_id: str) -> None:
    """Mark a challenge invalid so subsequent verify calls always 401.

    Distinct from `consume` only in intent — both flip used=TRUE. We use
    a separate function so the audit log reads `mfa_challenge_burned` vs
    `mfa_challenge_consumed`, which makes incident response easier.
    """
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
    logger.warning("mfa_challenge_burned challenge_id=%s", challenge_id)
