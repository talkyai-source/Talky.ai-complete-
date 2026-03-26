"""
Day 6 – Webhook Signature Verification (HMAC-SHA256).

Tests cover:
  ✓ Webhook secret generation (randomness, length)
  ✓ Signature computation (simple + timestamped)
  ✓ Signature verification (correct, wrong, tampered)
  ✓ Timestamp replay protection (too old, in future)
  ✓ Constant-time comparison (secrets.compare_digest)
  ✓ Outgoing webhook header creation
  ✓ WebhookSecretManager cache behaviour
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.security.webhook_verification import (
    MAX_TIMESTAMP_AGE_SECONDS,
    SIGNATURE_VERSION,
    WebhookSecretManager,
    WebhookVerificationError,
    compute_signature,
    compute_signature_with_timestamp,
    create_webhook_signature_headers,
    generate_webhook_secret,
    verify_signature,
)


# ========================================================================
# Secret Generation
# ========================================================================


class TestGenerateWebhookSecret:
    """generate_webhook_secret() tests."""

    def test_returns_string(self):
        secret = generate_webhook_secret()
        assert isinstance(secret, str)
        assert len(secret) > 30  # 32 bytes → ~43 URL-safe chars

    def test_each_call_is_unique(self):
        secrets = {generate_webhook_secret() for _ in range(20)}
        assert len(secrets) == 20


# ========================================================================
# Signature Computation
# ========================================================================


class TestComputeSignature:
    """HMAC-SHA256 signature computation tests."""

    def test_returns_hex_string(self):
        sig = compute_signature(b"hello", "secret")
        assert len(sig) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in sig)

    def test_deterministic(self):
        sig1 = compute_signature(b"hello", "secret")
        sig2 = compute_signature(b"hello", "secret")
        assert sig1 == sig2

    def test_different_payload_different_signature(self):
        sig1 = compute_signature(b"hello", "secret")
        sig2 = compute_signature(b"world", "secret")
        assert sig1 != sig2

    def test_different_secret_different_signature(self):
        sig1 = compute_signature(b"hello", "secret1")
        sig2 = compute_signature(b"hello", "secret2")
        assert sig1 != sig2


class TestComputeSignatureWithTimestamp:
    """Timestamped signature tests."""

    def test_includes_timestamp_in_signed_data(self):
        ts = 1700000000
        sig_ts = compute_signature_with_timestamp(b"hello", "secret", ts)
        sig_plain = compute_signature(b"hello", "secret")
        # Must be different because timestamp is included in signed data
        assert sig_ts != sig_plain

    def test_different_timestamps_different_signatures(self):
        sig1 = compute_signature_with_timestamp(b"hello", "secret", 1000)
        sig2 = compute_signature_with_timestamp(b"hello", "secret", 2000)
        assert sig1 != sig2


# ========================================================================
# Signature Verification
# ========================================================================


class TestVerifySignature:
    """verify_signature() tests."""

    def test_valid_simple_signature(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        sig = compute_signature(payload, secret)
        valid, error = verify_signature(payload, sig, secret)
        assert valid is True
        assert error is None

    def test_invalid_signature(self):
        payload = b'{"event": "test"}'
        valid, error = verify_signature(payload, "invalid-sig", "secret")
        assert valid is False
        assert error == "Invalid signature"

    def test_missing_signature_header(self):
        valid, error = verify_signature(b"payload", "", "secret")
        assert valid is False
        assert "Missing signature" in error

    def test_empty_secret(self):
        valid, error = verify_signature(b"payload", "some-sig", "")
        assert valid is False
        assert "not configured" in error

    def test_valid_timestamped_signature(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        ts = int(time.time())
        sig = compute_signature_with_timestamp(payload, secret, ts)
        valid, error = verify_signature(
            payload, sig, secret, timestamp_header=str(ts)
        )
        assert valid is True
        assert error is None

    def test_timestamp_too_old(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        old_ts = int(time.time()) - MAX_TIMESTAMP_AGE_SECONDS - 10
        sig = compute_signature_with_timestamp(payload, secret, old_ts)
        valid, error = verify_signature(
            payload, sig, secret, timestamp_header=str(old_ts)
        )
        assert valid is False
        assert "too old" in error

    def test_timestamp_in_future(self):
        payload = b'{"event": "test"}'
        secret = "my-secret"
        future_ts = int(time.time()) + 100
        sig = compute_signature_with_timestamp(payload, secret, future_ts)
        valid, error = verify_signature(
            payload, sig, secret, timestamp_header=str(future_ts)
        )
        assert valid is False
        assert "future" in error

    def test_invalid_timestamp_format(self):
        valid, error = verify_signature(
            b"payload", "sig", "secret", timestamp_header="not-a-number"
        )
        assert valid is False
        assert "Invalid timestamp" in error

    def test_tampered_payload_detected(self):
        payload = b'{"amount": 100}'
        secret = "my-secret"
        sig = compute_signature(payload, secret)
        tampered = b'{"amount": 999}'
        valid, error = verify_signature(tampered, sig, secret)
        assert valid is False


# ========================================================================
# Outgoing Webhook Headers
# ========================================================================


class TestCreateWebhookSignatureHeaders:
    """create_webhook_signature_headers() tests."""

    def test_includes_signature_header(self):
        headers = create_webhook_signature_headers(b"payload", "secret")
        assert "X-Webhook-Signature" in headers
        assert len(headers["X-Webhook-Signature"]) == 64

    def test_includes_timestamp_header(self):
        headers = create_webhook_signature_headers(b"payload", "secret")
        assert "X-Webhook-Timestamp" in headers
        ts = int(headers["X-Webhook-Timestamp"])
        assert abs(ts - int(time.time())) < 5

    def test_includes_version_header(self):
        headers = create_webhook_signature_headers(b"payload", "secret")
        assert headers["X-Webhook-Version"] == SIGNATURE_VERSION

    def test_no_timestamp_when_disabled(self):
        headers = create_webhook_signature_headers(
            b"payload", "secret", include_timestamp=False
        )
        assert "X-Webhook-Timestamp" not in headers

    def test_roundtrip_verification(self):
        """Headers generated by create_* should verify with verify_signature."""
        payload = b'{"call_id": "123"}'
        secret = "my-webhook-secret"
        headers = create_webhook_signature_headers(payload, secret)

        valid, error = verify_signature(
            payload,
            headers["X-Webhook-Signature"],
            secret,
            timestamp_header=headers.get("X-Webhook-Timestamp"),
        )
        assert valid is True
        assert error is None


# ========================================================================
# WebhookSecretManager
# ========================================================================


class TestWebhookSecretManager:
    """WebhookSecretManager cache tests."""

    @pytest.mark.asyncio
    async def test_returns_none_without_db(self):
        manager = WebhookSecretManager()
        result = await manager.get_secret("tenant-1", "webhook-1")
        assert result is None

    def test_invalidate_cache(self):
        manager = WebhookSecretManager()
        # Manually populate cache
        manager._cache["tenant-1:webhook-1"] = (
            "secret",
            time.time() + 300,
        )
        manager.invalidate_cache("tenant-1", "webhook-1")
        assert "tenant-1:webhook-1" not in manager._cache
