"""
Email Verification Token Generation and Validation

Generates secure tokens for email verification links.
Uses secrets module for cryptographically strong random generation.
"""

from __future__ import annotations

import logging
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Token expiration: 24 hours
VERIFICATION_TOKEN_EXPIRES_HOURS = 24


def generate_verification_token() -> str:
    """
    Generate a secure verification token.

    Uses secrets.token_urlsafe(32) to generate 256 bits of entropy
    suitable for embedding in URLs.

    Returns:
        A URL-safe random token string
    """
    return secrets.token_urlsafe(32)


def get_verification_token_expiry() -> datetime:
    """
    Get the expiration time for a verification token.

    Returns:
        A datetime object representing 24 hours from now (UTC)
    """
    return datetime.now(timezone.utc) + timedelta(hours=VERIFICATION_TOKEN_EXPIRES_HOURS)


def hash_verification_token(token: str) -> str:
    """
    Hash a verification token for secure storage.

    Uses SHA-256 to hash the token before storing in the database.
    This prevents exposure of the raw token if the database is compromised.

    Args:
        token: The raw verification token

    Returns:
        SHA-256 hex digest of the token
    """
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token_expiry(expires_at: datetime) -> bool:
    """
    Check if a verification token has expired.

    Args:
        expires_at: The expiration datetime from the database

    Returns:
        True if token is still valid, False if expired
    """
    if expires_at is None:
        return False

    # Ensure both are timezone-aware for comparison
    now = datetime.now(timezone.utc)
    expires_utc = expires_at.astimezone(timezone.utc) if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)

    return now < expires_utc
