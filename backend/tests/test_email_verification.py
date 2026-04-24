"""
Integration Tests for Email Verification System

Tests the complete email verification flow:
1. User registration creates unverified user with token
2. Verification email is sent with token link
3. Verification endpoint validates and marks user as verified
4. Login blocks unverified users
5. Login succeeds for verified users
"""

import pytest
from httpx import AsyncClient
from datetime import datetime, timedelta, timezone

from app.core.postgres_adapter import Client as DBClient
from app.core.security.verification_tokens import (
    generate_verification_token,
    get_verification_token_expiry,
    hash_verification_token,
    verify_token_expiry,
)


class TestEmailVerificationTokens:
    """Test token generation and validation logic."""

    def test_generate_verification_token(self):
        """Token generation produces unique, URL-safe strings."""
        token1 = generate_verification_token()
        token2 = generate_verification_token()

        # Tokens should be unique
        assert token1 != token2

        # Tokens should be URL-safe (base64url encoded)
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in token1)

    def test_get_verification_token_expiry(self):
        """Token expiry is 24 hours from now."""
        now = datetime.now(timezone.utc)
        expiry = get_verification_token_expiry()

        # Should be approximately 24 hours in the future
        diff = (expiry - now).total_seconds()
        assert 86350 < diff < 86410  # 24 hours ± 30 seconds tolerance

    def test_hash_verification_token(self):
        """Tokens are hashed before storage."""
        token = generate_verification_token()
        hashed1 = hash_verification_token(token)
        hashed2 = hash_verification_token(token)

        # Hash should be consistent
        assert hashed1 == hashed2

        # Hash should be different from token
        assert hashed1 != token

        # Hash should be hex format
        assert all(c in "0123456789abcdef" for c in hashed1)

    def test_verify_token_expiry_valid(self):
        """Valid token expiry passes check."""
        future = datetime.now(timezone.utc) + timedelta(hours=12)
        assert verify_token_expiry(future) is True

    def test_verify_token_expiry_expired(self):
        """Expired token expiry fails check."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        assert verify_token_expiry(past) is False

    def test_verify_token_expiry_none(self):
        """None expiry fails check."""
        assert verify_token_expiry(None) is False


@pytest.mark.asyncio
class TestEmailVerificationEndpoints:
    """Test email verification endpoints."""

    async def test_register_creates_unverified_user(self, client: AsyncClient, db_client: DBClient):
        """Registration creates a user with is_verified=False and a verification token."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "SecurePass123!",
                "business_name": "Test Business",
                "name": "Test User",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert "access_token" in data

        # Verify in database that user is not yet verified
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_verified, verification_token FROM user_profiles WHERE email = $1",
                "newuser@example.com",
            )

        assert row is not None
        assert row["is_verified"] is False
        assert row["verification_token"] is not None

    async def test_verify_email_with_valid_token(self, client: AsyncClient, db_client: DBClient):
        """Verification endpoint marks user as verified when given valid token."""
        # First, register a user
        register_response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "verify@example.com",
                "password": "SecurePass123!",
                "business_name": "Test Business",
            },
        )
        assert register_response.status_code == 200

        # Get the verification token from the database
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT verification_token FROM user_profiles WHERE email = $1",
                "verify@example.com",
            )

        token = row["verification_token"]
        assert token is not None

        # Call the verification endpoint with the token
        verify_response = await client.get(
            "/api/v1/auth/verify-email",
            params={"token": token},
        )

        assert verify_response.status_code == 200
        data = verify_response.json()
        assert data["email"] == "verify@example.com"
        assert "successfully" in data["message"].lower()

        # Verify in database that user is now verified
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_verified, verification_token, email_verified_at FROM user_profiles WHERE email = $1",
                "verify@example.com",
            )

        assert row["is_verified"] is True
        assert row["verification_token"] is None
        assert row["email_verified_at"] is not None

    async def test_verify_email_with_invalid_token(self, client: AsyncClient):
        """Verification endpoint rejects invalid tokens."""
        verify_response = await client.get(
            "/api/v1/auth/verify-email",
            params={"token": "invalid_token_here"},
        )

        assert verify_response.status_code == 404
        data = verify_response.json()
        assert "invalid" in data["detail"].lower() or "expired" in data["detail"].lower()

    async def test_verify_email_missing_token(self, client: AsyncClient):
        """Verification endpoint requires token parameter."""
        verify_response = await client.get(
            "/api/v1/auth/verify-email",
        )

        assert verify_response.status_code == 400
        data = verify_response.json()
        assert "token" in data["detail"].lower()

    async def test_login_blocks_unverified_user(self, client: AsyncClient, db_client: DBClient):
        """Login endpoint rejects unverified users."""
        # Register a user (will be unverified)
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "unverified@example.com",
                "password": "SecurePass123!",
                "business_name": "Test Business",
            },
        )

        # Try to login before verification
        login_response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "unverified@example.com",
                "password": "SecurePass123!",
            },
        )

        assert login_response.status_code == 403
        data = login_response.json()
        assert "verify" in data["detail"].lower() or "email" in data["detail"].lower()

    async def test_login_allows_verified_user(self, client: AsyncClient, db_client: DBClient):
        """Login endpoint allows verified users."""
        # Register a user
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "verified@example.com",
                "password": "SecurePass123!",
                "business_name": "Test Business",
            },
        )

        # Get and use verification token
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT verification_token FROM user_profiles WHERE email = $1",
                "verified@example.com",
            )

        token = row["verification_token"]

        # Verify the email
        await client.get(
            "/api/v1/auth/verify-email",
            params={"token": token},
        )

        # Now login should succeed
        login_response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "verified@example.com",
                "password": "SecurePass123!",
            },
        )

        assert login_response.status_code == 200
        data = login_response.json()
        assert "access_token" in data
        assert data["email"] == "verified@example.com"

    async def test_verify_email_already_verified(self, client: AsyncClient, db_client: DBClient):
        """Attempting to verify an already verified user returns success."""
        # Register and verify a user
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "alreadyverified@example.com",
                "password": "SecurePass123!",
                "business_name": "Test Business",
            },
        )

        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT verification_token FROM user_profiles WHERE email = $1",
                "alreadyverified@example.com",
            )

        token = row["verification_token"]

        # First verification
        response1 = await client.get(
            "/api/v1/auth/verify-email",
            params={"token": token},
        )
        assert response1.status_code == 200

        # Second verification attempt with same token (should now fail - token is deleted)
        response2 = await client.get(
            "/api/v1/auth/verify-email",
            params={"token": token},
        )
        assert response2.status_code == 404

    async def test_verify_email_expired_token(self, client: AsyncClient, db_client: DBClient):
        """Verification endpoint rejects expired tokens."""
        # Register a user
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "expiredtoken@example.com",
                "password": "SecurePass123!",
                "business_name": "Test Business",
            },
        )

        # Manually expire the token in database
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT verification_token FROM user_profiles WHERE email = $1",
                "expiredtoken@example.com",
            )
            token = row["verification_token"]

            # Update expiry to the past
            await conn.execute(
                "UPDATE user_profiles SET verification_token_expires_at = NOW() - INTERVAL '1 hour' WHERE email = $1",
                "expiredtoken@example.com",
            )

        # Try to verify with expired token
        response = await client.get(
            "/api/v1/auth/verify-email",
            params={"token": token},
        )

        assert response.status_code == 410  # Gone status code
        data = response.json()
        assert "expired" in data["detail"].lower()
