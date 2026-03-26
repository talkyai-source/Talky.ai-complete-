"""
Day 1 – Password hashing (Argon2id) + bcrypt backward compatibility.

Tests cover:
  ✓ Argon2id hash generation and format
  ✓ Successful verification round-trip
  ✓ Wrong password rejection
  ✓ bcrypt backward compatibility
  ✓ Rehash detection for bcrypt → Argon2id
  ✓ Password strength validation (NIST SP 800-63B)
  ✓ Edge cases: empty password, max-length, unknown hash format
"""
from __future__ import annotations

import bcrypt
import pytest

from app.core.security.password import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordValidationError,
    hash_password,
    needs_rehash,
    rehash_if_needed,
    validate_password_strength,
    verify_password,
)


# ========================================================================
# Password Strength Validation (NIST SP 800-63B)
# ========================================================================


class TestPasswordValidation:
    """validate_password_strength() tests."""

    def test_empty_password_raises(self):
        with pytest.raises(PasswordValidationError, match="cannot be empty"):
            validate_password_strength("")

    def test_too_short_raises(self):
        with pytest.raises(PasswordValidationError, match="at least"):
            validate_password_strength("Ab1234!")  # 7 chars < 8

    def test_too_long_raises(self):
        with pytest.raises(PasswordValidationError, match="cannot exceed"):
            validate_password_strength("A" * (MAX_PASSWORD_LENGTH + 1))

    def test_exactly_min_length_passes(self):
        # Should NOT raise
        validate_password_strength("A" * MIN_PASSWORD_LENGTH)

    def test_exactly_max_length_passes(self):
        validate_password_strength("A" * MAX_PASSWORD_LENGTH)

    def test_unicode_password_accepted(self):
        # OWASP: any printable Unicode is permitted
        validate_password_strength("P@ẞwörd🔑secure")

    def test_spaces_allowed(self):
        validate_password_strength("pass word with spaces")


# ========================================================================
# Argon2id Hash Generation
# ========================================================================


class TestHashPassword:
    """hash_password() tests."""

    def test_returns_argon2id_hash(self):
        h = hash_password("StrongPass123!")
        assert h.startswith("$argon2id$")

    def test_different_calls_produce_different_hashes(self):
        """Salt randomisation: same password → different hash each time."""
        h1 = hash_password("StrongPass123!")
        h2 = hash_password("StrongPass123!")
        assert h1 != h2

    def test_rejects_empty_password(self):
        with pytest.raises(PasswordValidationError):
            hash_password("")

    def test_rejects_short_password(self):
        with pytest.raises(PasswordValidationError):
            hash_password("short")


# ========================================================================
# Password Verification
# ========================================================================


class TestVerifyPassword:
    """verify_password() tests."""

    def test_correct_password_returns_true(self):
        pw = "CorrectHorseBatteryStaple"
        h = hash_password(pw)
        assert verify_password(pw, h) is True

    def test_wrong_password_returns_false(self):
        h = hash_password("CorrectHorseBatteryStaple")
        assert verify_password("WrongPassword123", h) is False

    def test_empty_password_returns_false(self):
        h = hash_password("ValidPassword123")
        assert verify_password("", h) is False

    def test_empty_hash_returns_false(self):
        assert verify_password("ValidPassword123", "") is False

    def test_none_inputs_return_false(self):
        assert verify_password(None, None) is False  # type: ignore[arg-type]

    def test_unknown_hash_format_returns_false(self):
        assert verify_password("anything", "$unknown$hash$value") is False

    def test_malformed_argon2_hash_returns_false(self):
        assert verify_password("test", "$argon2id$INVALID") is False


# ========================================================================
# bcrypt Backward Compatibility
# ========================================================================


class TestBcryptCompat:
    """Legacy bcrypt hash verification."""

    def _make_bcrypt_hash(self, password: str) -> str:
        """Create a bcrypt hash for testing backward compat."""
        return bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(rounds=10),
        ).decode("utf-8")

    def test_bcrypt_hash_verification(self):
        pw = "BcryptTestPass123"
        bcrypt_hash = self._make_bcrypt_hash(pw)
        assert bcrypt_hash.startswith("$2b$")
        assert verify_password(pw, bcrypt_hash) is True

    def test_bcrypt_wrong_password(self):
        bcrypt_hash = self._make_bcrypt_hash("OriginalPassword")
        assert verify_password("WrongPassword", bcrypt_hash) is False


# ========================================================================
# Rehash Detection
# ========================================================================


class TestNeedsRehash:
    """needs_rehash() / rehash_if_needed() tests."""

    def test_bcrypt_hash_needs_rehash(self):
        bcrypt_hash = bcrypt.hashpw(
            b"password",
            bcrypt.gensalt(rounds=10),
        ).decode("utf-8")
        assert needs_rehash(bcrypt_hash) is True

    def test_current_argon2_hash_does_not_need_rehash(self):
        h = hash_password("StrongPassword123")
        assert needs_rehash(h) is False

    def test_empty_hash_needs_rehash(self):
        assert needs_rehash("") is True

    def test_unknown_format_needs_rehash(self):
        assert needs_rehash("$pbkdf2$something") is True

    def test_rehash_if_needed_returns_new_hash_for_bcrypt(self):
        bcrypt_hash = bcrypt.hashpw(
            b"StrongPassword123",
            bcrypt.gensalt(rounds=10),
        ).decode("utf-8")
        new_hash = rehash_if_needed("StrongPassword123", bcrypt_hash)
        assert new_hash is not None
        assert new_hash.startswith("$argon2id$")

    def test_rehash_if_needed_returns_none_for_current(self):
        h = hash_password("StrongPassword123")
        assert rehash_if_needed("StrongPassword123", h) is None
