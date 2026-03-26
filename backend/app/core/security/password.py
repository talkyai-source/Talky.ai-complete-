"""
Password hashing — Argon2id (OWASP primary recommendation).

OWASP Password Storage Cheat Sheet (official, fetched March 2026):
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

  Recommended Argon2id minimum configuration:
    m = 19456  (19 MiB memory)
    t = 2      (2 iterations)
    p = 1      (1 degree of parallelism)

  bcrypt is listed as legacy-only with work factor >= 10.
  Pre-hashing with bcrypt is explicitly warned against unless a pepper is used.

Backward compatibility:
  Existing bcrypt hashes ($2b$... / $2a$...) are verified correctly and
  transparently re-hashed to Argon2id on the next successful login.

NIST SP 800-63B password length rules (applied here):
  Minimum : 8 characters  (with MFA enabled; 15 without — enforced at call site)
  Maximum : 128 characters (prevents hash-DoS on long inputs)
"""

from __future__ import annotations

import logging
from typing import Optional

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OWASP-recommended Argon2id parameters (minimum acceptable, December 2024)
# Source: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
# ---------------------------------------------------------------------------
_ARGON2_MEMORY_COST = 19456  # 19 MiB  — OWASP minimum (m=19456)
_ARGON2_TIME_COST = 2  # 2 iterations — OWASP minimum (t=2)
_ARGON2_PARALLELISM = 1  # 1 thread     — OWASP minimum (p=1)
_ARGON2_HASH_LEN = 32  # 256-bit output
_ARGON2_SALT_LEN = 16  # 128-bit salt (auto-generated per hash)

# NIST SP 800-63B + OWASP length constraints
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# ---------------------------------------------------------------------------
# Module-level Argon2id hasher (reused across calls — thread-safe)
# ---------------------------------------------------------------------------
_hasher = PasswordHasher(
    memory_cost=_ARGON2_MEMORY_COST,
    time_cost=_ARGON2_TIME_COST,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_LEN,
    encoding="utf-8",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PasswordValidationError(ValueError):
    """Raised when a plaintext password fails the strength policy."""


def validate_password_strength(password: str) -> None:
    """
    Enforce NIST SP 800-63B length requirements.

    Raises PasswordValidationError if the password is too short or too long.
    The caller decides whether to surface the detail to the user.

    Note: OWASP says do NOT restrict character sets — any printable Unicode
    character (including spaces and emoji) is permitted.
    """
    if not password:
        raise PasswordValidationError("Password cannot be empty.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordValidationError(
            f"Password cannot exceed {MAX_PASSWORD_LENGTH} characters."
        )


def hash_password(password: str) -> str:
    """
    Hash a plaintext password with Argon2id using OWASP-recommended parameters.

    Returns a self-describing PHC string that encodes the algorithm, version,
    parameters, salt, and hash — no separate salt storage required.

    Example output:
        $argon2id$v=19$m=19456,t=2,p=1$<salt_b64>$<hash_b64>

    Raises PasswordValidationError if the password fails the strength check.
    """
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Verify a plaintext password against a stored hash.

    Supports both:
      • Argon2id  — hashes starting with ``$argon2id$`` (new standard)
      • bcrypt    — hashes starting with ``$2b$`` or ``$2a$`` (legacy)

    Returns True if the password matches, False otherwise.
    Never raises — all exceptions are caught and False is returned so that
    callers always get a safe boolean result regardless of hash format.

    OWASP: Comparison must be constant-time to prevent timing oracle attacks.
    argon2-cffi and bcrypt both handle this internally.
    """
    if not password or not stored_hash:
        return False

    try:
        if stored_hash.startswith("$argon2"):
            # argon2-cffi raises VerifyMismatchError on wrong password,
            # VerificationError on algorithm/parameter mismatch,
            # InvalidHashError on a malformed hash string.
            return _hasher.verify(stored_hash, password)

        if stored_hash.startswith(("$2b$", "$2a$", "$2y$")):
            # Legacy bcrypt — constant-time check via bcrypt.checkpw
            return bcrypt.checkpw(
                password.encode("utf-8"),
                stored_hash.encode("utf-8"),
            )

        # Unknown hash format — treat as mismatch
        logger.warning("verify_password: unrecognised hash prefix, returning False")
        return False

    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception as exc:  # pragma: no cover
        logger.error("verify_password: unexpected error: %s", exc)
        return False


def needs_rehash(stored_hash: str) -> bool:
    """
    Return True if the stored hash should be upgraded.

    Two cases trigger a rehash:
      1. The hash is a legacy bcrypt hash — always upgrade to Argon2id.
      2. The hash is Argon2id but was created with parameters below the
         current OWASP minimums (argon2-cffi detects this automatically).

    Intended usage: call this after a successful login verification and,
    if True is returned, immediately re-hash the plaintext password with
    ``hash_password()`` and persist the new hash.
    """
    if not stored_hash:
        return True

    if stored_hash.startswith(("$2b$", "$2a$", "$2y$")):
        # bcrypt → always upgrade to Argon2id
        return True

    if stored_hash.startswith("$argon2"):
        try:
            return _hasher.check_needs_rehash(stored_hash)
        except (InvalidHashError, Exception):
            return True  # Malformed hash — rehash for safety

    # Unknown format — rehash
    return True


def rehash_if_needed(
    password: str,
    stored_hash: str,
) -> Optional[str]:
    """
    Convenience helper: returns a new Argon2id hash string if the stored
    hash needs upgrading, or None if no upgrade is necessary.

    Typical call site (inside the login handler, after successful verification):

        new_hash = rehash_if_needed(plaintext_password, row["password_hash"])
        if new_hash:
            await conn.execute(
                "UPDATE user_profiles SET password_hash = $1 WHERE id = $2",
                new_hash, user_id,
            )
    """
    if not needs_rehash(stored_hash):
        return None
    try:
        return hash_password(password)
    except PasswordValidationError:
        # Password no longer meets strength requirements — skip rehash
        return None
