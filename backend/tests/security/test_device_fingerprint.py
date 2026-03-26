"""
Day 5 – Device Fingerprinting + Session Binding.

Tests cover:
  ✓ Device fingerprint generation (SHA-256 hash)
  ✓ User-Agent parsing (browser, OS, device type)
  ✓ IP subnet generation (IPv4 /24)
  ✓ Fingerprint comparison (match, mismatch, no stored)
  ✓ IP change significance detection (same subnet, different subnet, strict mode)
  ✓ Edge cases: empty/null inputs
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.security.device_fingerprint import (
    compare_fingerprints,
    generate_device_fingerprint,
    generate_ip_subnet,
    is_ip_change_significant,
    parse_user_agent,
)


# ========================================================================
# generate_device_fingerprint()
# ========================================================================


class TestGenerateDeviceFingerprint:
    """Device fingerprint generation from request headers."""

    def test_returns_hex_sha256(self, mock_request):
        fp = generate_device_fingerprint(mock_request)
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in fp)

    def test_same_request_same_fingerprint(self, mock_request):
        fp1 = generate_device_fingerprint(mock_request)
        fp2 = generate_device_fingerprint(mock_request)
        assert fp1 == fp2

    def test_different_user_agent_different_fingerprint(self, mock_request):
        fp1 = generate_device_fingerprint(mock_request)
        fp2 = generate_device_fingerprint(
            mock_request, user_agent="Firefox/100.0"
        )
        assert fp1 != fp2

    def test_custom_user_agent_overrides_header(self, mock_request):
        fp_default = generate_device_fingerprint(mock_request)
        fp_custom = generate_device_fingerprint(
            mock_request, user_agent="CustomAgent/1.0"
        )
        assert fp_default != fp_custom


# ========================================================================
# parse_user_agent()
# ========================================================================


class TestParseUserAgent:
    """User-Agent string parsing tests."""

    def test_none_returns_unknown(self):
        result = parse_user_agent(None)
        assert result["device_type"] == "unknown"
        assert result["browser"] == "other"
        assert result["os"] == "other"

    def test_empty_string_returns_unknown(self):
        result = parse_user_agent("")
        assert result["device_type"] == "unknown"

    def test_chrome_on_windows(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        result = parse_user_agent(ua)
        assert result["browser"] == "chrome"
        assert result["os"] == "windows"
        assert result["device_type"] == "desktop"

    def test_safari_on_mac(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
        result = parse_user_agent(ua)
        assert result["browser"] == "safari"
        assert result["os"] == "macos"
        assert result["device_type"] == "desktop"

    def test_chrome_on_android_mobile(self):
        ua = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.43 Mobile Safari/537.36"
        result = parse_user_agent(ua)
        assert result["browser"] == "chrome"
        assert result["os"] == "android"
        assert result["device_type"] == "mobile"

    def test_safari_on_ipad(self):
        ua = "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
        result = parse_user_agent(ua)
        assert result["device_type"] == "tablet"
        # iPads report 'Mac OS X' in the UA string, which the parser may
        # classify as macos or ios depending on implementation.
        assert result["os"] in ("ios", "macos")

    def test_firefox_on_linux(self):
        ua = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0"
        result = parse_user_agent(ua)
        assert result["browser"] == "firefox"
        assert result["os"] == "linux"
        assert result["device_type"] == "desktop"

    def test_edge_detected(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.91"
        result = parse_user_agent(ua)
        assert result["browser"] == "edge"

    def test_device_name_friendly(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        result = parse_user_agent(ua)
        assert "Chrome" in result["device_name"]
        assert "Windows" in result["device_name"]


# ========================================================================
# generate_ip_subnet()
# ========================================================================


class TestGenerateIpSubnet:
    """IP subnet generation tests."""

    def test_ipv4_default_24(self):
        result = generate_ip_subnet("192.168.1.100")
        assert result == "192.168.1.0/24"

    def test_ipv4_full_address(self):
        result = generate_ip_subnet("10.0.5.42")
        assert result == "10.0.5.0/24"

    def test_empty_ip_returns_none(self):
        assert generate_ip_subnet("") is None

    def test_unknown_ip_returns_none(self):
        assert generate_ip_subnet("unknown") is None

    def test_invalid_ip_returns_none(self):
        assert generate_ip_subnet("not-an-ip") is None

    def test_partial_ip_returns_none(self):
        assert generate_ip_subnet("192.168.1") is None


# ========================================================================
# compare_fingerprints()
# ========================================================================


class TestCompareFingerprints:
    """Fingerprint comparison tests."""

    def test_matching_fingerprints(self):
        result = compare_fingerprints("abc123", "abc123")
        assert result["match"] is True
        assert result["confidence"] == "high"
        assert result["recommendation"] == "allow"

    def test_mismatched_fingerprints(self):
        result = compare_fingerprints("abc123", "xyz789")
        assert result["match"] is False
        assert result["confidence"] == "medium"
        assert result["recommendation"] == "verify"

    def test_no_stored_fingerprint(self):
        result = compare_fingerprints(None, "abc123")
        assert result["match"] is True  # Cannot compare → allow
        assert result["confidence"] == "low"
        assert result["recommendation"] == "allow"

    def test_empty_stored_fingerprint(self):
        result = compare_fingerprints("", "abc123")
        assert result["match"] is True  # Empty → treated as no stored


# ========================================================================
# is_ip_change_significant()
# ========================================================================


class TestIpChangeSignificant:
    """IP change significance detection tests."""

    def test_same_ip_not_significant(self):
        result = is_ip_change_significant("192.168.1.100", "192.168.1.100")
        assert result["significant"] is False
        assert result["reason"] == "same_ip"

    def test_same_subnet_not_significant(self):
        result = is_ip_change_significant("192.168.1.100", "192.168.1.200")
        assert result["significant"] is False
        assert result["reason"] == "same_subnet"

    def test_different_subnet_significant(self):
        result = is_ip_change_significant("192.168.1.100", "10.0.0.1")
        assert result["significant"] is True
        assert result["reason"] == "different_subnet"

    def test_strict_mode_flags_any_change(self):
        result = is_ip_change_significant(
            "192.168.1.100", "192.168.1.200", strict_mode=True
        )
        assert result["significant"] is True
        assert result["reason"] == "strict_mode_ip_change"

    def test_no_original_ip_not_significant(self):
        result = is_ip_change_significant(None, "192.168.1.100")
        assert result["significant"] is False
        assert result["reason"] == "no_original_ip"

    def test_unknown_original_ip_not_significant(self):
        result = is_ip_change_significant("unknown", "192.168.1.100")
        assert result["significant"] is False
        assert result["reason"] == "no_original_ip"
