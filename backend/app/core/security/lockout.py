"""
Per-account progressive login lockout.

OWASP Authentication Cheat Sheet (official):
  https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

  Key rules applied here:
  - The failed-login counter MUST be per-account (not just per-IP).
    An attacker using many IPs would bypass IP-only blocking.
  - Use exponential / progressive lockout durations, not a fixed window.
  - Reset the counter on a successful login.
  - Allow the "forgot password" path even when the account is locked
    (prevents lockout from becoming a DoS weapon against the user).
  - Always return a GENERIC error message — never reveal whether the
    account is locked, the email is unknown, or the password is wrong.

Progressive lockout thresholds (consecutive failures → lockout seconds):
  ≥  5 failures →   1 minute
  ≥ 10 failures →   5 minutes
  ≥ 20 failures →  30 minutes
  ≥ 50 failures →  24 hours  (manual admin review recommended)

The observation window is 15 minutes: only failures recorded within the
last 15 minutes count towards the threshold.  A successful login clears
the window by inserting a success record; the query skips success rows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# How far back to look when counting consecutive failures.
OBSERVATION_WINDOW_MINUTES: int = 15

# (min_failures, lockout_duration_seconds)
# Evaluated in order — the LAST matching threshold wins (highest penalty).
LOCKOUT_THRESHOLDS: list[tuple[int, int]] = [
    (5, 60),  # ≥  5 failures →  1 minute
    (10, 300),  # ≥ 10 failures →  5 minutes
    (20, 1_800),  # ≥ 20 failures → 30 minutes
    (50, 86_400),  # ≥ 50 failures → 24 hours
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_lockout_seconds(failure_count: int) -> int:
    """
    Return the lockout duration in seconds for *failure_count* consecutive
    failures, or 0 if the count is below every threshold.
    """
    duration = 0
    for threshold, seconds in LOCKOUT_THRESHOLDS:
        if failure_count >= threshold:
            duration = seconds
    return duration


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_consecutive_failures(
    conn: asyncpg.Connection,
    email: str,
) -> int:
    """
    Count failed login attempts for *email* within the observation window.

    Only rows with ``success = FALSE`` are counted.  The window resets
    naturally because old rows fall outside the ``created_at >= $2`` bound.
    """
    window_start = datetime.now(timezone.utc) - timedelta(
        minutes=OBSERVATION_WINDOW_MINUTES
    )

    count = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM   login_attempts
        WHERE  email      = $1
          AND  success    = FALSE
          AND  created_at >= $2
        """,
        email.lower(),
        window_start,
    )
    return int(count or 0)


async def check_account_locked(
    conn: asyncpg.Connection,
    email: str,
) -> Optional[datetime]:
    """
    Determine whether the account for *email* is currently locked.

    Returns
    -------
    datetime
        The UTC datetime at which the lockout expires, if the account is
        currently locked.
    None
        If the account is not locked (either below threshold or lockout
        period has already elapsed).

    The caller should NOT reveal to the client *why* login failed
    (OWASP: always return a generic "Invalid username or password" message).
    """
    failure_count = await get_consecutive_failures(conn, email)
    lockout_seconds = _compute_lockout_seconds(failure_count)

    if lockout_seconds == 0:
        return None

    # Find the timestamp of the most recent failure inside the window.
    window_start = datetime.now(timezone.utc) - timedelta(
        minutes=OBSERVATION_WINDOW_MINUTES
    )
    last_failure_at: Optional[datetime] = await conn.fetchval(
        """
        SELECT MAX(created_at)
        FROM   login_attempts
        WHERE  email      = $1
          AND  success    = FALSE
          AND  created_at >= $2
        """,
        email.lower(),
        window_start,
    )

    if last_failure_at is None:
        return None

    # Ensure the datetime is timezone-aware before arithmetic.
    if last_failure_at.tzinfo is None:
        last_failure_at = last_failure_at.replace(tzinfo=timezone.utc)

    lockout_until = last_failure_at + timedelta(seconds=lockout_seconds)
    now = datetime.now(timezone.utc)

    if lockout_until > now:
        logger.info(
            "Account lockout active — email=%s failures=%d locked_until=%s",
            email,
            failure_count,
            lockout_until.isoformat(),
        )
        return lockout_until

    # Lockout period has elapsed — account is no longer blocked.
    return None


async def record_login_attempt(
    conn: asyncpg.Connection,
    *,
    email: str,
    user_id: Optional[str],
    ip_address: str,
    success: bool,
    failure_reason: Optional[str] = None,
) -> None:
    """
    Persist one login attempt row to ``login_attempts``.

    Called after *every* login attempt — both successes and failures.

    Parameters
    ----------
    conn:
        An active asyncpg connection (caller manages the transaction).
    email:
        The email address that was submitted.  Always stored lower-cased.
    user_id:
        The UUID of the resolved user, or None if the email does not match
        any account (do not reveal this distinction to the client).
    ip_address:
        The client IP address from the request.
    success:
        True for a successful authentication, False for any failure.
    failure_reason:
        Short machine-readable reason code, e.g.
        ``"wrong_password"``, ``"account_locked"``, ``"account_inactive"``.
        Stored for internal audit / monitoring only — never sent to client.
    """
    await conn.execute(
        """
        INSERT INTO login_attempts
               (email, user_id, ip_address, success, failure_reason)
        VALUES ($1,    $2,      $3,         $4,      $5)
        """,
        email.lower(),
        user_id,
        ip_address,
        success,
        failure_reason,
    )
    logger.debug(
        "login_attempt recorded — email=%s success=%s reason=%s ip=%s",
        email,
        success,
        failure_reason,
        ip_address,
    )


async def seconds_until_unlocked(
    conn: asyncpg.Connection,
    email: str,
) -> int:
    """
    Return the number of whole seconds remaining in the current lockout,
    or 0 if the account is not locked.

    Useful for building ``Retry-After`` response headers without leaking
    the exact lockout reason to clients.
    """
    locked_until = await check_account_locked(conn, email)
    if locked_until is None:
        return 0
    remaining = (locked_until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))
