"""
Device Fingerprinting for Session Security (Day 5)

OWASP Session Management Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

NIST SP 800-63B (Session Security):
  https://pages.nist.gov/800-63-3/sp800-63b.html

Purpose:
  Device fingerprinting helps detect session hijacking by identifying when a
  session is used from a different device or browser than originally created.
  This provides defense-in-depth beyond session tokens alone.

Privacy Note:
  All fingerprinting is server-side using standard HTTP headers. No additional
  client-side tracking (canvas fingerprinting, etc.) is used.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)

# Regex patterns for User-Agent parsing
_MOBILE_PATTERNS = [
    r"Mobile",
    r"Android.*Mobile",
    r"iPhone",
    r"iPod",
    r"Windows Phone",
    r"BlackBerry",
]

_TABLET_PATTERNS = [
    r"iPad",
    r"Android(?!.*Mobile)",
    r"Tablet",
    r"Kindle",
    r"Silk",
]

_BROWSER_PATTERNS = [
    (r"Edg|Edge", "edge"),
    (r"Chrome(?!.*Edg|.*OPR|.*Brave)", "chrome"),
    (r"Safari(?!.*Chrome)", "safari"),
    (r"Firefox", "firefox"),
    (r"OPR|Opera", "opera"),
    (r"Brave", "brave"),
    (r"MSIE|Trident", "ie"),
]

_OS_PATTERNS = [
    (r"Windows NT 10\.0", "windows"),
    (r"Windows NT 6\.[23]", "windows"),
    (r"Windows", "windows"),
    (r"Mac OS X|macOS", "macos"),
    (r"iPhone|iPad|iPod", "ios"),
    (r"Android", "android"),
    (r"Linux", "linux"),
]


def generate_device_fingerprint(
    request: Request,
    user_agent: Optional[str] = None,
) -> str:
    """
    Generate a device fingerprint from request headers.

    Uses multiple signals to create a stable but unique identifier for the
    device/browser combination. The fingerprint is NOT guaranteed to be unique
    per device (VPNs, header randomization can cause collisions), but changes
    in fingerprint within a session are a strong signal of session hijacking.

    Signals used (in order of stability):
      1. User-Agent string (primary signal)
      2. Accept headers (content negotiation preferences)
      3. Accept-Language (locale preference)
      4. Accept-Encoding (compression support)
      5. Client Hints (Sec-Ch-Ua*, modern browsers)

    Args:
        request: FastAPI Request object
        user_agent: Optional User-Agent string (if already extracted)

    Returns:
        64-character hex string (SHA-256 truncated) representing device fingerprint

    Security Note:
        The fingerprint is a hash - original values cannot be recovered.
        This preserves privacy while allowing comparison.
    """
    signals: list[str] = []

    # Primary signal: User-Agent
    ua = user_agent or request.headers.get("User-Agent", "")
    signals.append(ua)

    # Secondary signals - header-based content negotiation
    # These are stable per browser installation
    signals.append(request.headers.get("Accept", ""))
    signals.append(request.headers.get("Accept-Language", ""))
    signals.append(request.headers.get("Accept-Encoding", ""))

    # Client hints (Chromium-based browsers)
    # Provides structured browser/OS info
    signals.append(request.headers.get("Sec-Ch-Ua", ""))
    signals.append(request.headers.get("Sec-Ch-Ua-Mobile", ""))
    signals.append(request.headers.get("Sec-Ch-Ua-Platform", ""))
    signals.append(request.headers.get("Sec-Ch-Ua-Platform-Version", ""))

    # Network/client hints
    signals.append(request.headers.get("DNT", ""))  # Do Not Track
    signals.append(request.headers.get("Sec-Fetch-Dest", ""))
    signals.append(request.headers.get("Sec-Fetch-Mode", ""))

    # Combine with delimiter and hash
    # Using | as delimiter since it's unlikely to appear in headers
    fingerprint_data = "|".join(signals)

    # SHA-256 hash, truncated to 64 chars (256 bits in hex)
    # Full hash provides collision resistance
    return hashlib.sha256(fingerprint_data.encode("utf-8")).hexdigest()


def parse_user_agent(user_agent: Optional[str]) -> dict:
    """
    Parse User-Agent string to extract device metadata.

    Uses regex patterns to identify device type, browser, and operating system.
    This is a lightweight parser; for production use consider the 'ua-parser'
    library which uses up-to-date regex patterns from uap-core.

    Args:
        user_agent: Raw User-Agent header string

    Returns:
        Dictionary containing:
        - device_type: 'mobile' | 'tablet' | 'desktop' | 'unknown'
        - browser: 'chrome' | 'firefox' | 'safari' | 'edge' | 'opera' | 'brave' | 'ie' | 'other'
        - os: 'windows' | 'macos' | 'linux' | 'ios' | 'android' | 'other'
        - device_name: Human-readable name like "Chrome on Windows"

    Example:
        >>> parse_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        {
            'device_type': 'desktop',
            'browser': 'chrome',
            'os': 'windows',
            'device_name': 'Chrome on Windows'
        }
    """
    if not user_agent:
        return {
            "device_type": "unknown",
            "browser": "other",
            "os": "other",
            "device_name": "Unknown Device",
        }

    # Detect device type
    device_type = "desktop"  # Default assumption
    for pattern in _TABLET_PATTERNS:
        if re.search(pattern, user_agent, re.IGNORECASE):
            device_type = "tablet"
            break
    else:
        for pattern in _MOBILE_PATTERNS:
            if re.search(pattern, user_agent, re.IGNORECASE):
                device_type = "mobile"
                break

    # Detect browser
    browser = "other"
    for pattern, name in _BROWSER_PATTERNS:
        if re.search(pattern, user_agent, re.IGNORECASE):
            browser = name
            break

    # Detect OS
    os_name = "other"
    for pattern, name in _OS_PATTERNS:
        if re.search(pattern, user_agent, re.IGNORECASE):
            os_name = name
            break

    # Generate friendly device name
    device_name = _generate_device_name(browser, os_name, device_type)

    return {
        "device_type": device_type,
        "browser": browser,
        "os": os_name,
        "device_name": device_name,
    }


def _generate_device_name(browser: str, os_name: str, device_type: str) -> str:
    """
    Generate a human-readable device name from parsed components.

    Examples:
        - "Chrome on Windows"
        - "Safari on iPhone"
        - "Firefox on macOS"
        - "Unknown Device"
    """
    if browser == "other" and os_name == "other":
        return "Unknown Device"

    browser_display = browser.capitalize()
    if browser == "ie":
        browser_display = "Internet Explorer"

    os_display = os_name.capitalize()
    if os_name == "macos":
        os_display = "macOS"
    elif os_name == "ios":
        os_display = "iOS"

    # Special handling for mobile devices
    if device_type == "mobile":
        if os_name == "ios":
            return f"{browser_display} on iPhone"
        elif os_name == "android":
            return f"{browser_display} on Android"

    return f"{browser_display} on {os_display}"


def generate_ip_subnet(ip_address: str, subnet_bits: int = 24) -> Optional[str]:
    """
    Generate a subnet identifier from an IP address.

    Used for IP binding tolerance - mobile users may change IPs within the
    same /24 subnet (carrier-grade NAT, WiFi handoffs).

    Args:
        ip_address: IPv4 or IPv6 address string
        subnet_bits: Number of bits for subnet (default /24 for IPv4)

    Returns:
        Subnet string (e.g., "192.168.1.0/24") or None if invalid IP
    """
    if not ip_address or ip_address == "unknown":
        return None

    try:
        # Handle IPv4
        if "." in ip_address and ":" not in ip_address:
            octets = ip_address.split(".")
            if len(octets) != 4:
                return None

            # Calculate subnet mask
            mask_octets = subnet_bits // 8
            subnet_octets = octets[:mask_octets]
            subnet_octets.extend(["0"] * (4 - mask_octets))

            return ".".join(subnet_octets) + f"/{subnet_bits}"

        # IPv6 handling would go here (simplified)
        return None

    except (ValueError, IndexError):
        return None


def compare_fingerprints(
    stored_fingerprint: Optional[str],
    current_fingerprint: str,
) -> dict:
    """
    Compare two fingerprints and return similarity analysis.

    Args:
        stored_fingerprint: Original fingerprint from session creation
        current_fingerprint: Current fingerprint from request

    Returns:
        Dictionary with:
        - match: True if fingerprints are identical
        - confidence: 'high' | 'medium' | 'low' - how certain we are of mismatch
        - recommendation: 'allow' | 'verify' | 'revoke' - suggested action
    """
    if not stored_fingerprint:
        # No stored fingerprint - cannot compare
        return {
            "match": True,
            "confidence": "low",
            "recommendation": "allow",
        }

    if stored_fingerprint == current_fingerprint:
        return {
            "match": True,
            "confidence": "high",
            "recommendation": "allow",
        }

    # Fingerprints don't match
    # This could be:
    # - Browser upgrade (legitimate)
    # - Different browser profile (legitimate)
    # - Session hijacking (attack)
    # - VPN on/off (legitimate)

    return {
        "match": False,
        "confidence": "medium",
        "recommendation": "verify",  # Require verification rather than immediate revoke
    }


def is_ip_change_significant(
    original_ip: Optional[str],
    current_ip: str,
    strict_mode: bool = False,
) -> dict:
    """
    Determine if an IP address change is significant enough to flag.

    Args:
        original_ip: IP address at session creation
        current_ip: Current IP address
        strict_mode: If True, any change is significant; if False, /24 subnet allowed

    Returns:
        Dictionary with:
        - significant: True if change should be flagged
        - reason: Explanation of the decision
    """
    if not original_ip or original_ip == "unknown":
        return {
            "significant": False,
            "reason": "no_original_ip",
        }

    if original_ip == current_ip:
        return {
            "significant": False,
            "reason": "same_ip",
        }

    if strict_mode:
        return {
            "significant": True,
            "reason": "strict_mode_ip_change",
        }

    # Check /24 subnet match
    original_subnet = generate_ip_subnet(original_ip, 24)
    current_subnet = generate_ip_subnet(current_ip, 24)

    if original_subnet and current_subnet and original_subnet == current_subnet:
        return {
            "significant": False,
            "reason": "same_subnet",
        }

    return {
        "significant": True,
        "reason": "different_subnet",
    }


# =============================================================================
# Re-export for convenience
# =============================================================================

__all__ = [
    "generate_device_fingerprint",
    "parse_user_agent",
    "generate_ip_subnet",
    "compare_fingerprints",
    "is_ip_change_significant",
]
