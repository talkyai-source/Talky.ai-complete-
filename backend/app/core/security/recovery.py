"""
Single-use MFA recovery codes.

OWASP Multifactor Authentication Cheat Sheet (official):
  https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html

  "Provide the user with a number of single-use recovery codes when they
   first setup MFA."

  "Processes implemented to allow users to bypass or reset MFA may be
   exploitable by attackers." — therefore recovery codes must be:
     - Single-use (consumed on first use, never reusable)
     - High entropy (96 bits per code)
     - Stored as hashes only (raw codes shown to user once, never again)
     - Invalidated when MFA is disabled or re-configured

Design decisions:
  - 10 codes per batch (matches GitHub, Google, and AWS Cognito defaults)
  - Each raw code is secrets.token_urlsafe(12) — 12 bytes = 96-bit entropy
  - Only the SHA-256 hex digest is stored in the database
  - batch_id UUID ties all codes from one generation together (for auditing)
  - Used codes are marked used=TRUE with a timestamp; they are NOT deleted,
    so audit logs retain a complete history of code consumption
  - Calling invalidate_all() hard-deletes all codes for a user (on MFA
    disable or re-setup) to keep the table from growing indefinitely
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Number of recovery codes generated per MFA setup / regeneration.
# 10 matches GitHub, Google Workspace, and AWS Cognito defaults.
RECOVERY_CODE_COUNT: int = 10

# Raw entropy bytes per code before URL-safe base64 encoding.
# 12 bytes → 96-bit entropy per code, ~16 printable characters.
RECOVERY_CODE_BYTES: int = 12


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    """Return the lowercase hex-encoded SHA-256 digest of *value*."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalise(raw_code: str) -> str:
    """
    Strip display formatting from a raw code before hashing.

    Users may copy codes with spaces or hyphens added by the UI display
    formatter.  Normalising before comparison prevents false rejections.
    """
    return raw_code.replace(" ", "").replace("-", "").strip()


def _parse_command_tag_count(tag: str) -> int:
    """Extract the integer row count from an asyncpg command tag string."""
    try:
        return int(tag.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def generate_recovery_codes() -> list[str]:
    """
    Generate a fresh batch of single-use recovery codes.

    Uses Python's ``secrets`` module (OS CSPRNG) for each code.
    Each code is a URL-safe base64 string with 96 bits of entropy.

    Returns
    -------
    list[str]
        A list of RECOVERY_CODE_COUNT raw code strings.
        These are the ONLY time the raw codes are available.
        Hash them immediately with store_recovery_codes() and discard
        the plaintexts — they must never be stored in the database.
    """
    return [
        secrets.token_urlsafe(RECOVERY_CODE_BYTES) for _ in range(RECOVERY_CODE_COUNT)
    ]


def format_recovery_code(raw_code: str) -> str:
    """
    Format a raw recovery code for human-readable display.

    Splits the code in half with a hyphen separator for easier transcription.
    Example: "AbCdEfGhIjKlMnOp" → "AbCdEfGh-IjKlMnOp"

    Parameters
    ----------
    raw_code:
        The raw code string returned by generate_recovery_codes().

    Returns
    -------
    str
        The formatted code for display to the user.
    """
    mid = len(raw_code) // 2
    if mid > 0:
        return f"{raw_code[:mid]}-{raw_code[mid:]}"
    return raw_code


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


async def store_recovery_codes(
    conn: asyncpg.Connection,
    user_id: str,
    raw_codes: list[str],
    batch_id: Optional[str] = None,
) -> str:
    """
    Hash and insert a batch of recovery codes for *user_id*.

    The raw codes are hashed with SHA-256 before storage.  The hash values
    are stored; the raw codes are not.

    The caller is responsible for calling invalidate_all_codes() before this
    function when regenerating codes so that no stale codes remain active.

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    user_id:
        UUID string of the owning user.
    raw_codes:
        List of raw code strings from generate_recovery_codes().
        Must not be empty.
    batch_id:
        Optional UUID string to tag this batch.  A new UUID is generated
        if not provided.

    Returns
    -------
    str
        The batch_id used (newly generated or the one passed in).
    """
    if not raw_codes:
        raise ValueError("raw_codes must not be empty")

    effective_batch_id = batch_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    rows = [
        (user_id, _sha256(code), False, None, effective_batch_id, now)
        for code in raw_codes
    ]

    await conn.executemany(
        """
        INSERT INTO recovery_codes
               (user_id, code_hash, used, used_at, batch_id, created_at)
        VALUES ($1,       $2,        $3,   $4,      $5,       $6)
        """,
        rows,
    )

    logger.info(
        "Stored %d recovery codes for user=%s batch=%s",
        len(rows),
        user_id,
        effective_batch_id,
    )
    return effective_batch_id


async def invalidate_all_codes(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """
    Hard-delete ALL recovery codes for *user_id*.

    Called when:
      - MFA is disabled (OWASP: revoke all codes on MFA tear-down)
      - A new MFA setup is started (fresh codes replace old ones)
      - Recovery codes are explicitly regenerated by the user

    Returns
    -------
    int
        Number of rows deleted.
    """
    result = await conn.execute(
        "DELETE FROM recovery_codes WHERE user_id = $1",
        user_id,
    )
    count = _parse_command_tag_count(result)
    logger.info(
        "Deleted %d recovery code(s) for user=%s",
        count,
        user_id,
    )
    return count


async def count_remaining_codes(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """
    Return the number of unused recovery codes remaining for *user_id*.

    Useful for surfacing a low-codes warning in the UI
    (e.g., "You have 2 recovery codes remaining — consider regenerating.").
    """
    count = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM   recovery_codes
        WHERE  user_id = $1
          AND  used    = FALSE
        """,
        user_id,
    )
    return int(count or 0)


async def verify_and_consume_recovery_code(
    conn: asyncpg.Connection,
    user_id: str,
    raw_code: str,
) -> bool:
    """
    Verify a recovery code and mark it consumed if valid.

    OWASP: Recovery codes must be single-use.  Once consumed, the code
    cannot be used again.

    Steps
    -----
    1. Normalise the raw code (strip spaces / hyphens).
    2. Hash with SHA-256.
    3. Look up a matching, unused row for this user.
    4. If found, mark it used=TRUE and record used_at=NOW().
    5. Return True on success, False on any mismatch.

    This function never raises — all exceptions are caught and False is
    returned to the caller so auth failures degrade gracefully.

    Parameters
    ----------
    conn:
        Active asyncpg connection.
    user_id:
        UUID string of the authenticating user.
    raw_code:
        The code string entered by the user (may include hyphens / spaces).

    Returns
    -------
    bool
        True  → code was valid and has now been consumed.
        False → code was not found, already used, or belongs to another user.
    """
    if not raw_code:
        return False

    normalised = _normalise(raw_code)
    if not normalised:
        return False

    code_hash = _sha256(normalised)
    now = datetime.now(timezone.utc)

    try:
        # Fetch the matching unused code row (if any)
        row = await conn.fetchrow(
            """
            SELECT id
            FROM   recovery_codes
            WHERE  user_id   = $1
              AND  code_hash = $2
              AND  used      = FALSE
            """,
            user_id,
            code_hash,
        )

        if not row:
            logger.info(
                "Recovery code lookup: no matching unused code for user=%s",
                user_id,
            )
            return False

        # Mark the code as consumed
        result = await conn.execute(
            """
            UPDATE recovery_codes
               SET used    = TRUE,
                   used_at = $1
             WHERE id      = $2
               AND used    = FALSE
            """,
            now,
            row["id"],
        )

        # Check that exactly one row was updated (guards against race conditions)
        updated = _parse_command_tag_count(result)
        if updated != 1:
            logger.warning(
                "Recovery code consume race condition for user=%s id=%s "
                "(rows updated: %d)",
                user_id,
                row["id"],
                updated,
            )
            return False

        logger.info(
            "Recovery code consumed successfully for user=%s code_id=%s",
            user_id,
            row["id"],
        )
        return True

    except Exception as exc:
        logger.error(
            "Unexpected error in verify_and_consume_recovery_code for user=%s: %s",
            user_id,
            type(exc).__name__,
        )
        return False
