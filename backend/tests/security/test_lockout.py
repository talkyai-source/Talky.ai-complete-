"""
Day 1 – Account lockout (progressive, per-account).

Tests cover:
  ✓ Lockout threshold computation (pure function)
  ✓ Progressive lockout durations
  ✓ Sub-threshold returns zero lockout
  ✓ Boundary values at each threshold
  ✓ record_login_attempt SQL call
  ✓ get_consecutive_failures SQL call
  ✓ check_account_locked logic
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.security.lockout import (
    LOCKOUT_THRESHOLDS,
    OBSERVATION_WINDOW_MINUTES,
    _compute_lockout_seconds,
    check_account_locked,
    get_consecutive_failures,
    record_login_attempt,
    seconds_until_unlocked,
)


# ========================================================================
# _compute_lockout_seconds (pure function)
# ========================================================================


class TestComputeLockoutSeconds:
    """Verify progressive lockout durations against OWASP thresholds."""

    def test_zero_failures_returns_zero(self):
        assert _compute_lockout_seconds(0) == 0

    def test_below_first_threshold_returns_zero(self):
        assert _compute_lockout_seconds(4) == 0

    def test_at_first_threshold_returns_60(self):
        assert _compute_lockout_seconds(5) == 60

    def test_between_first_and_second_threshold(self):
        assert _compute_lockout_seconds(8) == 60

    def test_at_second_threshold_returns_300(self):
        assert _compute_lockout_seconds(10) == 300

    def test_at_third_threshold_returns_1800(self):
        assert _compute_lockout_seconds(20) == 1800

    def test_at_fourth_threshold_returns_86400(self):
        assert _compute_lockout_seconds(50) == 86400

    def test_above_max_threshold(self):
        assert _compute_lockout_seconds(100) == 86400

    def test_thresholds_order_is_ascending(self):
        """Verify that LOCKOUT_THRESHOLDS are sorted by failure count."""
        for i in range(1, len(LOCKOUT_THRESHOLDS)):
            assert LOCKOUT_THRESHOLDS[i][0] > LOCKOUT_THRESHOLDS[i - 1][0]


# ========================================================================
# get_consecutive_failures (DB-dependent)
# ========================================================================


class TestGetConsecutiveFailures:
    """Test the failure-counting query."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_failures(self, mock_conn):
        mock_conn.fetchval.return_value = 0
        result = await get_consecutive_failures(mock_conn, "user@example.com")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_count_from_db(self, mock_conn):
        mock_conn.fetchval.return_value = 7
        result = await get_consecutive_failures(mock_conn, "user@example.com")
        assert result == 7

    @pytest.mark.asyncio
    async def test_email_lowercased(self, mock_conn):
        mock_conn.fetchval.return_value = 0
        await get_consecutive_failures(mock_conn, "USER@EXAMPLE.COM")
        call_args = mock_conn.fetchval.call_args
        # First positional arg after SQL is the email — must be lowercase
        assert call_args[0][1] == "user@example.com"

    @pytest.mark.asyncio
    async def test_handles_none_result(self, mock_conn):
        mock_conn.fetchval.return_value = None
        result = await get_consecutive_failures(mock_conn, "user@example.com")
        assert result == 0


# ========================================================================
# record_login_attempt (DB-dependent)
# ========================================================================


class TestRecordLoginAttempt:
    """Test that login attempts are persisted correctly."""

    @pytest.mark.asyncio
    async def test_records_successful_login(self, mock_conn):
        await record_login_attempt(
            mock_conn,
            email="user@example.com",
            user_id="user-123",
            ip_address="192.168.1.1",
            success=True,
        )
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        # email should be lowercase
        assert call_args[1] == "user@example.com"
        # success should be True
        assert call_args[4] is True

    @pytest.mark.asyncio
    async def test_records_failed_login(self, mock_conn):
        await record_login_attempt(
            mock_conn,
            email="user@example.com",
            user_id=None,
            ip_address="192.168.1.1",
            success=False,
            failure_reason="wrong_password",
        )
        call_args = mock_conn.execute.call_args[0]
        assert call_args[4] is False
        assert call_args[5] == "wrong_password"


# ========================================================================
# check_account_locked (DB-dependent)
# ========================================================================


class TestCheckAccountLocked:
    """Test account lockout checking."""

    @pytest.mark.asyncio
    async def test_not_locked_below_threshold(self, mock_conn):
        mock_conn.fetchval.return_value = 3  # Below 5
        result = await check_account_locked(mock_conn, "user@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_locked_returns_datetime(self, mock_conn):
        # Simulate 10 failures with last failure just now
        now = datetime.now(timezone.utc)
        mock_conn.fetchval.side_effect = [10, now]
        result = await check_account_locked(mock_conn, "user@example.com")
        assert result is not None
        assert isinstance(result, datetime)
        # Lockout should be 300s (5 min) after last failure
        assert result > now


# ========================================================================
# seconds_until_unlocked (DB-dependent)
# ========================================================================


class TestSecondsUntilUnlocked:
    """Test the Retry-After helper."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_not_locked(self, mock_conn):
        mock_conn.fetchval.return_value = 2  # Below threshold
        result = await seconds_until_unlocked(mock_conn, "user@example.com")
        assert result == 0
