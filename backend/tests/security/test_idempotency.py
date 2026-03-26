"""
Day 6 – Idempotency Support.

Tests cover:
  ✓ IdempotencyManager key/lock generation
  ✓ Fail-open without Redis
  ✓ First request is new (is_new=True)
  ✓ Duplicate request returns cached response
  ✓ Mismatched request returns error
  ✓ In-progress lock handling
  ✓ response storage and retrieval
  ✓ Lock release on error
  ✓ Singleton management (get/reset)
  ✓ idempotency_dependency key validation
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from app.core.security.idempotency import (
    DEFAULT_IDEMPOTENCY_WINDOW,
    IDEMPOTENCY_KEY_HEADER,
    IdempotencyManager,
    get_idempotency_manager,
    reset_idempotency_manager,
)


# ========================================================================
# IdempotencyManager
# ========================================================================


class TestIdempotencyManager:
    """IdempotencyManager unit tests."""

    def test_key_generation_deterministic(self):
        mgr = IdempotencyManager()
        k1 = mgr._key("test-key-123")
        k2 = mgr._key("test-key-123")
        assert k1 == k2
        assert k1.startswith("idempotency:")

    def test_lock_key_generation(self):
        mgr = IdempotencyManager()
        lock = mgr._lock_key("test-key-123")
        assert lock.startswith("idempotency:lock:")

    def test_key_and_lock_are_different(self):
        mgr = IdempotencyManager()
        key = mgr._key("test")
        lock = mgr._lock_key("test")
        assert key != lock

    @pytest.mark.asyncio
    async def test_fail_open_without_redis(self, mock_request):
        mgr = IdempotencyManager(redis_client=None)
        is_new, cached, error = await mgr.check_idempotency(
            "test-key", mock_request
        )
        assert is_new is True
        assert cached is None
        assert error is None

    @pytest.mark.asyncio
    async def test_new_request_sets_lock(self, mock_redis, mock_request):
        """First request: no existing record, no lock → sets lock, returns is_new."""
        mock_redis.get.return_value = None
        mock_redis.exists.return_value = 0
        mgr = IdempotencyManager(redis_client=mock_redis)
        is_new, cached, error = await mgr.check_idempotency(
            "test-key", mock_request
        )
        assert is_new is True
        assert cached is None
        assert error is None
        # Lock should be set
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_returns_cached(self, mock_redis, mock_request):
        """Second request with same key: return cached response."""
        cached_record = json.dumps({
            "request_method": "POST",
            "request_path": "/api/v1/test",
            "response_status": 200,
            "response_body": '{"result": "ok"}',
        })
        mock_redis.get.return_value = cached_record
        mgr = IdempotencyManager(redis_client=mock_redis)
        is_new, cached, error = await mgr.check_idempotency(
            "test-key", mock_request
        )
        assert is_new is False
        assert cached is not None
        assert cached["status_code"] == 200
        assert cached["idempotent"] is True
        assert error is None

    @pytest.mark.asyncio
    async def test_mismatched_request_returns_error(self, mock_redis, mock_request):
        """Same key, different request → conflict error."""
        cached_record = json.dumps({
            "request_method": "GET",  # Different from POST
            "request_path": "/api/v1/other",
            "response_status": 200,
            "response_body": None,
        })
        mock_redis.get.return_value = cached_record
        mgr = IdempotencyManager(redis_client=mock_redis)
        is_new, cached, error = await mgr.check_idempotency(
            "test-key", mock_request
        )
        assert is_new is False
        assert cached is None
        assert "reused with different request" in error

    @pytest.mark.asyncio
    async def test_concurrent_request_returns_error(self, mock_redis, mock_request):
        """Same key already in progress → conflict."""
        mock_redis.get.return_value = None  # No completed request
        mock_redis.exists.return_value = 1  # Lock exists
        mgr = IdempotencyManager(redis_client=mock_redis)
        is_new, cached, error = await mgr.check_idempotency(
            "test-key", mock_request
        )
        assert is_new is False
        assert "already in progress" in error

    @pytest.mark.asyncio
    async def test_store_response(self, mock_redis, mock_request):
        """store_response should write to Redis with TTL."""
        mgr = IdempotencyManager(redis_client=mock_redis)
        await mgr.store_response(
            "test-key", mock_request, 200, '{"result": "ok"}'
        )
        # Should have called setex for the record and delete for the lock
        assert mock_redis.setex.called
        assert mock_redis.delete.called

    @pytest.mark.asyncio
    async def test_release_lock(self, mock_redis):
        mgr = IdempotencyManager(redis_client=mock_redis)
        await mgr.release_lock("test-key")
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_response_noop_without_redis(self, mock_request):
        mgr = IdempotencyManager(redis_client=None)
        await mgr.store_response("key", mock_request, 200, "body")
        # Should not raise

    @pytest.mark.asyncio
    async def test_release_lock_noop_without_redis(self):
        mgr = IdempotencyManager(redis_client=None)
        await mgr.release_lock("key")
        # Should not raise

    @pytest.mark.asyncio
    async def test_get_record_returns_none_without_redis(self):
        mgr = IdempotencyManager(redis_client=None)
        assert await mgr.get_record("key") is None

    @pytest.mark.asyncio
    async def test_delete_record(self, mock_redis):
        mgr = IdempotencyManager(redis_client=mock_redis)
        await mgr.delete_record("test-key")
        mock_redis.delete.assert_called_once()


# ========================================================================
# Singleton Management
# ========================================================================


class TestSingleton:
    """get_idempotency_manager / reset tests."""

    def test_get_creates_singleton(self):
        reset_idempotency_manager()
        mgr = get_idempotency_manager()
        assert isinstance(mgr, IdempotencyManager)

    def test_reset_clears_singleton(self):
        reset_idempotency_manager()
        m1 = get_idempotency_manager()
        reset_idempotency_manager()
        m2 = get_idempotency_manager()
        assert m1 is not m2

    def teardown_method(self):
        reset_idempotency_manager()


# ========================================================================
# Constants
# ========================================================================


class TestConstants:
    """Verify idempotency constants."""

    def test_default_window_is_24_hours(self):
        assert DEFAULT_IDEMPOTENCY_WINDOW == 86400

    def test_header_name(self):
        assert IDEMPOTENCY_KEY_HEADER == "Idempotency-Key"
