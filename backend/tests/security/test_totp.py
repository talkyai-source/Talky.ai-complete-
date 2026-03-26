"""
Day 2 – TOTP (Time-Based One-Time Password) + Replay Prevention.

Tests cover:
  ✓ Secret generation (base32, length, randomness)
  ✓ Fernet encryption / decryption round-trip
  ✓ Decryption failure with wrong key
  ✓ Provisioning URI format (otpauth://)
  ✓ QR code data URI generation
  ✓ TOTP code verification (correct code)
  ✓ TOTP code rejection (wrong code, malformed input)
  ✓ Replay-attack prevention (same time slot)
  ✓ Code normalisation (spaces, hyphens)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pyotp
import pytest

from app.core.security.totp import (
    TOTP_DIGITS,
    TOTP_INTERVAL,
    TOTP_VALID_WINDOW,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_provisioning_uri,
    is_replay_attack,
    verify_totp_code,
)


# ========================================================================
# Secret Generation
# ========================================================================


class TestGenerateSecret:
    """generate_totp_secret() tests."""

    def test_returns_base32_string(self):
        secret = generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) == 32  # pyotp default is 32 chars

    def test_each_call_produces_unique_secret(self):
        secrets = {generate_totp_secret() for _ in range(20)}
        assert len(secrets) == 20  # All unique


# ========================================================================
# Fernet Encryption / Decryption
# ========================================================================


class TestEncryptDecrypt:
    """Fernet-based TOTP secret encryption tests."""

    def test_encrypt_decrypt_roundtrip(self):
        raw_secret = generate_totp_secret()
        encrypted = encrypt_totp_secret(raw_secret)
        decrypted = decrypt_totp_secret(encrypted)
        assert decrypted == raw_secret

    def test_encrypted_is_different_from_raw(self):
        raw_secret = generate_totp_secret()
        encrypted = encrypt_totp_secret(raw_secret)
        assert encrypted != raw_secret

    def test_different_encryptions_produce_different_ciphertext(self):
        """Fernet uses a random IV each time — no ciphertext reuse."""
        raw_secret = generate_totp_secret()
        e1 = encrypt_totp_secret(raw_secret)
        e2 = encrypt_totp_secret(raw_secret)
        assert e1 != e2

    def test_decrypt_with_wrong_key_raises(self, monkeypatch):
        # Encrypt with the test key
        raw_secret = generate_totp_secret()
        encrypted = encrypt_totp_secret(raw_secret)

        # Switch to a different Fernet key
        from cryptography.fernet import Fernet
        wrong_key = Fernet.generate_key().decode("utf-8")
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", wrong_key)

        with pytest.raises(RuntimeError, match="decryption failed"):
            decrypt_totp_secret(encrypted)

    def test_missing_encryption_key_raises(self, monkeypatch):
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="TOTP_ENCRYPTION_KEY is not set"):
            encrypt_totp_secret("anything")


# ========================================================================
# Provisioning URI
# ========================================================================


class TestProvisioningUri:
    """get_provisioning_uri() tests."""

    def test_returns_otpauth_uri(self):
        secret = generate_totp_secret()
        uri = get_provisioning_uri(secret, "user@example.com")
        assert uri.startswith("otpauth://totp/")

    def test_contains_issuer(self):
        secret = generate_totp_secret()
        uri = get_provisioning_uri(secret, "user@example.com", issuer="MyApp")
        assert "MyApp" in uri

    def test_contains_secret_param(self):
        secret = generate_totp_secret()
        uri = get_provisioning_uri(secret, "user@example.com")
        assert f"secret={secret}" in uri


# ========================================================================
# QR Code Generation
# ========================================================================


class TestQRCode:
    """generate_qr_code_data_uri() tests."""

    def test_returns_data_uri(self):
        secret = generate_totp_secret()
        uri = get_provisioning_uri(secret, "user@example.com")
        data_uri = generate_qr_code_data_uri(uri)
        assert data_uri.startswith("data:image/png;base64,")
        # Ensure it has actual content
        assert len(data_uri) > 100


# ========================================================================
# Replay Attack Detection
# ========================================================================


class TestReplayAttack:
    """is_replay_attack() tests."""

    def test_no_previous_use_is_not_replay(self):
        assert is_replay_attack(None) is False

    def test_different_time_slot_is_not_replay(self):
        # 60 seconds ago → different 30-second slot
        old = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert is_replay_attack(old) is False

    def test_same_time_slot_is_replay(self):
        # Use *now* — same 30-second window
        now = datetime.now(timezone.utc)
        assert is_replay_attack(now) is True

    def test_naive_datetime_treated_as_utc(self):
        # Naive datetime — should be treated as UTC
        now_naive = datetime.utcnow()
        assert is_replay_attack(now_naive) is True


# ========================================================================
# TOTP Code Verification
# ========================================================================


class TestVerifyTotpCode:
    """verify_totp_code() tests."""

    def _generate_current_code(self, secret: str) -> str:
        """Generate a valid TOTP code for the current time."""
        totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
        return totp.now()

    def test_correct_code_returns_true(self):
        secret = generate_totp_secret()
        code = self._generate_current_code(secret)
        assert verify_totp_code(secret, code) is True

    def test_wrong_code_returns_false(self):
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "000000") is False

    def test_empty_code_returns_false(self):
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "") is False

    def test_empty_secret_returns_false(self):
        assert verify_totp_code("", "123456") is False

    def test_non_numeric_code_returns_false(self):
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "abcdef") is False

    def test_wrong_length_code_returns_false(self):
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "12345") is False  # 5 digits
        assert verify_totp_code(secret, "1234567") is False  # 7 digits

    def test_code_with_spaces_normalised(self):
        """Users may copy codes with spaces — they should be stripped."""
        secret = generate_totp_secret()
        code = self._generate_current_code(secret)
        spaced = f"{code[:3]} {code[3:]}"
        assert verify_totp_code(secret, spaced) is True

    def test_code_with_hyphens_normalised(self):
        secret = generate_totp_secret()
        code = self._generate_current_code(secret)
        dashed = f"{code[:3]}-{code[3:]}"
        assert verify_totp_code(secret, dashed) is True

    def test_replay_rejected(self):
        """A code in the same time slot as last_used_at should be rejected."""
        secret = generate_totp_secret()
        code = self._generate_current_code(secret)
        last_used = datetime.now(timezone.utc)
        assert verify_totp_code(secret, code, last_used_at=last_used) is False

    def test_expired_code_rejected(self):
        """A code from > 1 window ago should fail."""
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
        # Generate a code from 90 seconds ago (3 windows back)
        old_code = totp.at(datetime.now(timezone.utc) - timedelta(seconds=90))
        assert verify_totp_code(secret, old_code) is False
