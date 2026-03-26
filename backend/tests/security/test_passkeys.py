"""
Day 3 – WebAuthn / Passkeys (pure-logic tests, no authenticator).

Tests cover:
  ✓ Data class construction (RegistrationOptions, AuthenticationOptions, etc.)
  ✓ Configuration constants (RP_ID, RP_NAME, TTL, algorithms)
  ✓ Authenticator selection criteria
  ✓ Challenge lifecycle helpers (tested with mocked DB)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.security.passkeys import (
    AUTHENTICATOR_SELECTION_ANY,
    AUTHENTICATOR_SELECTION_CROSS_PLATFORM,
    AUTHENTICATOR_SELECTION_PLATFORM,
    AuthenticationOptions,
    AuthenticationResult,
    PUBKEY_CRED_PARAMS,
    RP_ID,
    RP_NAME,
    RP_ORIGIN,
    RegistrationOptions,
    VerifiedCredential,
    WEBAUTHN_CHALLENGE_TTL_MINUTES,
    consume_challenge,
    create_challenge,
    get_and_validate_challenge,
)


# ========================================================================
# Configuration Constants
# ========================================================================


class TestPasskeyConfig:
    """Verify WebAuthn configuration constants."""

    def test_rp_id_is_domain_only(self):
        """RP ID must be a domain — no scheme, no port."""
        assert "://" not in RP_ID
        assert ":" not in RP_ID or RP_ID.count(":") == 0

    def test_rp_origin_has_scheme(self):
        assert RP_ORIGIN.startswith("https://") or RP_ORIGIN.startswith("http://")

    def test_rp_name_is_set(self):
        assert len(RP_NAME) > 0

    def test_challenge_ttl_is_5_minutes(self):
        assert WEBAUTHN_CHALLENGE_TTL_MINUTES == 5

    def test_supported_algorithms(self):
        """Must support ES256 (-7), Ed25519 (-8), RS256 (-257)."""
        alg_ids = [p["alg"] for p in PUBKEY_CRED_PARAMS]
        assert -7 in alg_ids  # ES256
        assert -8 in alg_ids  # Ed25519
        assert -257 in alg_ids  # RS256

    def test_authenticator_selection_criteria(self):
        """Verify authenticator selection presets exist."""
        from webauthn.helpers.structs import UserVerificationRequirement
        assert AUTHENTICATOR_SELECTION_ANY is not None
        assert AUTHENTICATOR_SELECTION_PLATFORM is not None
        assert AUTHENTICATOR_SELECTION_CROSS_PLATFORM is not None
        # All should require user verification
        assert AUTHENTICATOR_SELECTION_ANY.user_verification == UserVerificationRequirement.REQUIRED
        assert AUTHENTICATOR_SELECTION_PLATFORM.user_verification == UserVerificationRequirement.REQUIRED


# ========================================================================
# Data Classes
# ========================================================================


class TestDataClasses:
    """Verify data class construction."""

    def test_registration_options(self):
        opts = RegistrationOptions(ceremony_id="abc-123", options_json='{"test": 1}')
        assert opts.ceremony_id == "abc-123"
        assert opts.options_json == '{"test": 1}'

    def test_authentication_options(self):
        opts = AuthenticationOptions(ceremony_id="def-456", options_json='{}')
        assert opts.ceremony_id == "def-456"

    def test_verified_credential(self):
        cred = VerifiedCredential(
            credential_id="cred-id",
            credential_public_key="pub-key",
            sign_count=0,
            aaguid="aaguid-val",
            device_type="multiDevice",
            backed_up=True,
            transports=["internal"],
        )
        assert cred.credential_id == "cred-id"
        assert cred.backed_up is True

    def test_authentication_result(self):
        result = AuthenticationResult(
            credential_id="cred-id",
            new_sign_count=5,
            user_verified=True,
            authenticator_attachment="platform",
        )
        assert result.new_sign_count == 5


# ========================================================================
# Challenge Lifecycle (mocked DB)
# ========================================================================


class TestCreateChallenge:
    """create_challenge() tests."""

    @pytest.mark.asyncio
    async def test_creates_challenge_row(self, mock_conn):
        test_user_id = str(uuid.uuid4())
        ceremony_id, challenge_bytes = await create_challenge(
            mock_conn,
            ceremony="registration",
            user_id=test_user_id,
            ip_address="192.168.1.1",
        )
        assert isinstance(ceremony_id, str)
        assert len(challenge_bytes) == 32  # 32 bytes of randomness
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_unique_ceremony_ids(self, mock_conn):
        """Each call should produce a unique ceremony_id."""
        id1, _ = await create_challenge(mock_conn, ceremony="registration")
        id2, _ = await create_challenge(mock_conn, ceremony="authentication")
        assert id1 != id2


class TestGetAndValidateChallenge:
    """get_and_validate_challenge() tests."""

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, mock_conn):
        mock_conn.fetchrow.return_value = None
        result = await get_and_validate_challenge(
            mock_conn, str(uuid.uuid4()), "registration"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_used(self, mock_conn):
        mock_conn.fetchrow.return_value = {
            "challenge": "test",
            "ceremony": "registration",
            "user_id": None,
            "ip_address": None,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "used": True,
        }
        result = await get_and_validate_challenge(
            mock_conn, str(uuid.uuid4()), "registration"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_expired(self, mock_conn):
        mock_conn.fetchrow.return_value = {
            "challenge": "test",
            "ceremony": "registration",
            "user_id": None,
            "ip_address": None,
            "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            "used": False,
        }
        result = await get_and_validate_challenge(
            mock_conn, str(uuid.uuid4()), "registration"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_ceremony_mismatch(self, mock_conn):
        mock_conn.fetchrow.return_value = {
            "challenge": "test",
            "ceremony": "authentication",  # Mismatch
            "user_id": None,
            "ip_address": None,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "used": False,
        }
        result = await get_and_validate_challenge(
            mock_conn, str(uuid.uuid4()), "registration"
        )
        assert result is None


class TestConsumeChallenge:
    """consume_challenge() tests."""

    @pytest.mark.asyncio
    async def test_single_use_returns_true(self, mock_conn):
        mock_conn.execute.return_value = "UPDATE 1"
        result = await consume_challenge(mock_conn, str(uuid.uuid4()))
        assert result is True

    @pytest.mark.asyncio
    async def test_already_used_returns_false(self, mock_conn):
        mock_conn.execute.return_value = "UPDATE 0"
        result = await consume_challenge(mock_conn, str(uuid.uuid4()))
        assert result is False
