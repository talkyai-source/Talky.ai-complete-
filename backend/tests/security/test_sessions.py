"""
Day 1 – DB-backed Session Management.

Tests cover:
  ✓ Session token generation (length, entropy, randomness)
  ✓ Token hashing (SHA-256)
  ✓ Session constants (cookie name, lifetimes)
  ✓ Day 5 configuration (binding, concurrent limits)
"""
from __future__ import annotations

import pytest

from app.core.security.sessions import (
    MAX_SESSIONS_PER_USER,
    SESSION_BIND_TO_FINGERPRINT,
    SESSION_BIND_TO_IP,
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SESSION_LIFETIME_HOURS,
    SESSION_TOKEN_BYTES,
    generate_session_token,
    hash_session_token,
)


# ========================================================================
# Session Token Generation
# ========================================================================


class TestGenerateSessionToken:
    """generate_session_token() tests."""

    def test_returns_string(self):
        token = generate_session_token()
        assert isinstance(token, str)

    def test_minimum_length(self):
        """32 bytes → ~43 URL-safe base64 chars."""
        token = generate_session_token()
        assert len(token) >= 40

    def test_each_call_is_unique(self):
        tokens = {generate_session_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_url_safe_characters(self):
        """Token should only contain URL-safe characters."""
        token = generate_session_token()
        safe_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in safe_chars for c in token)


# ========================================================================
# Token Hashing
# ========================================================================


class TestHashSessionToken:
    """hash_session_token() tests."""

    def test_returns_hex_sha256(self):
        h = hash_session_token("test-token")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        h1 = hash_session_token("my-token")
        h2 = hash_session_token("my-token")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        h1 = hash_session_token("token-a")
        h2 = hash_session_token("token-b")
        assert h1 != h2


# ========================================================================
# Session Constants
# ========================================================================


class TestSessionConstants:
    """Verify OWASP-compliant session configuration."""

    def test_token_bytes_at_least_32(self):
        """OWASP minimum: 128-bit (16 bytes). We use 256-bit (32 bytes)."""
        assert SESSION_TOKEN_BYTES >= 32

    def test_lifetime_is_24_hours(self):
        assert SESSION_LIFETIME_HOURS == 24

    def test_idle_timeout_is_30_minutes(self):
        assert SESSION_IDLE_TIMEOUT_MINUTES == 30

    def test_cookie_name_set(self):
        assert SESSION_COOKIE_NAME == "talky_sid"


# ========================================================================
# Day 5 Configuration
# ========================================================================


class TestDay5Config:
    """Day 5 session security configuration."""

    def test_ip_binding_enabled(self):
        assert SESSION_BIND_TO_IP is True

    def test_fingerprint_binding_enabled(self):
        assert SESSION_BIND_TO_FINGERPRINT is True

    def test_concurrent_session_limit(self):
        assert MAX_SESSIONS_PER_USER == 10
