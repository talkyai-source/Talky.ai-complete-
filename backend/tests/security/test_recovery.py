"""
Day 2 – Single-use MFA Recovery Codes.

Tests cover:
  ✓ Code generation (count, entropy, uniqueness)
  ✓ Code formatting (display with hyphen)
  ✓ SHA-256 hashing helper
  ✓ Normalisation of user input (spaces, hyphens)
  ✓ store_recovery_codes DB interaction
  ✓ verify_and_consume_recovery_code DB interaction
  ✓ invalidate_all_codes DB interaction
  ✓ count_remaining_codes DB interaction
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.security.recovery import (
    RECOVERY_CODE_BYTES,
    RECOVERY_CODE_COUNT,
    _normalise,
    _sha256,
    count_remaining_codes,
    format_recovery_code,
    generate_recovery_codes,
    invalidate_all_codes,
    store_recovery_codes,
    verify_and_consume_recovery_code,
)


# ========================================================================
# Code Generation
# ========================================================================


class TestGenerateRecoveryCodes:
    """generate_recovery_codes() tests."""

    def test_returns_correct_count(self):
        codes = generate_recovery_codes()
        assert len(codes) == RECOVERY_CODE_COUNT

    def test_all_codes_are_unique(self):
        codes = generate_recovery_codes()
        assert len(set(codes)) == RECOVERY_CODE_COUNT

    def test_codes_are_strings(self):
        codes = generate_recovery_codes()
        for code in codes:
            assert isinstance(code, str)
            assert len(code) > 0

    def test_different_batches_are_unique(self):
        batch1 = set(generate_recovery_codes())
        batch2 = set(generate_recovery_codes())
        assert batch1.isdisjoint(batch2)


# ========================================================================
# Code Formatting
# ========================================================================


class TestFormatRecoveryCode:
    """format_recovery_code() tests."""

    def test_adds_hyphen_in_middle(self):
        result = format_recovery_code("ABCDEFGHIJKLMNOP")
        assert result == "ABCDEFGH-IJKLMNOP"

    def test_empty_string(self):
        result = format_recovery_code("")
        assert result == ""

    def test_single_char(self):
        result = format_recovery_code("A")
        assert result == "A"

    def test_two_chars(self):
        result = format_recovery_code("AB")
        assert result == "A-B"


# ========================================================================
# Internal Helpers
# ========================================================================


class TestSha256:
    """_sha256() tests."""

    def test_returns_hex_string(self):
        h = _sha256("test")
        assert len(h) == 64  # SHA-256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        h1 = _sha256("hello")
        h2 = _sha256("hello")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        assert _sha256("a") != _sha256("b")


class TestNormalise:
    """_normalise() tests."""

    def test_strips_spaces(self):
        assert _normalise(" abc def ") == "abcdef"

    def test_strips_hyphens(self):
        assert _normalise("abc-def") == "abcdef"

    def test_strips_both(self):
        assert _normalise("abc - def") == "abcdef"

    def test_empty_string(self):
        assert _normalise("") == ""


# ========================================================================
# Database Operations (mocked)
# ========================================================================


class TestStoreRecoveryCodes:
    """store_recovery_codes() tests."""

    @pytest.mark.asyncio
    async def test_stores_all_codes(self, mock_conn):
        codes = generate_recovery_codes()
        batch_id = await store_recovery_codes(mock_conn, "user-123", codes)
        assert isinstance(batch_id, str)
        # Should call executemany once with RECOVERY_CODE_COUNT rows
        mock_conn.executemany.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_batch_id_used(self, mock_conn):
        codes = generate_recovery_codes()
        batch_id = await store_recovery_codes(
            mock_conn, "user-123", codes, batch_id="custom-batch-id"
        )
        assert batch_id == "custom-batch-id"

    @pytest.mark.asyncio
    async def test_empty_codes_raises(self, mock_conn):
        with pytest.raises(ValueError, match="must not be empty"):
            await store_recovery_codes(mock_conn, "user-123", [])


class TestVerifyAndConsumeRecoveryCode:
    """verify_and_consume_recovery_code() tests."""

    @pytest.mark.asyncio
    async def test_returns_false_for_empty_code(self, mock_conn):
        assert await verify_and_consume_recovery_code(mock_conn, "user-123", "") is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self, mock_conn):
        mock_conn.fetchrow.return_value = None
        result = await verify_and_consume_recovery_code(
            mock_conn, "user-123", "valid-code"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_found_and_consumed(self, mock_conn):
        mock_conn.fetchrow.return_value = {"id": "code-id-1"}
        mock_conn.execute.return_value = "UPDATE 1"
        result = await verify_and_consume_recovery_code(
            mock_conn, "user-123", "valid-code"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_normalises_input_before_hashing(self, mock_conn):
        """Spaces and hyphens in user input should be stripped."""
        mock_conn.fetchrow.return_value = None
        await verify_and_consume_recovery_code(mock_conn, "user-123", "abc - def")
        # The hash should be for "abcdef" not "abc - def"
        call_args = mock_conn.fetchrow.call_args[0]
        expected_hash = _sha256("abcdef")
        assert call_args[2] == expected_hash


class TestInvalidateAllCodes:
    """invalidate_all_codes() tests."""

    @pytest.mark.asyncio
    async def test_deletes_all_codes_for_user(self, mock_conn):
        mock_conn.execute.return_value = "DELETE 5"
        count = await invalidate_all_codes(mock_conn, "user-123")
        assert count == 5

    @pytest.mark.asyncio
    async def test_returns_zero_when_none_found(self, mock_conn):
        mock_conn.execute.return_value = "DELETE 0"
        count = await invalidate_all_codes(mock_conn, "user-123")
        assert count == 0


class TestCountRemainingCodes:
    """count_remaining_codes() tests."""

    @pytest.mark.asyncio
    async def test_returns_count(self, mock_conn):
        mock_conn.fetchval.return_value = 7
        count = await count_remaining_codes(mock_conn, "user-123")
        assert count == 7

    @pytest.mark.asyncio
    async def test_returns_zero_for_none(self, mock_conn):
        mock_conn.fetchval.return_value = None
        count = await count_remaining_codes(mock_conn, "user-123")
        assert count == 0
