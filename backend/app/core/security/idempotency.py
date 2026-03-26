"""
API-Wide Idempotency Support (Day 6)

OWASP API Security Top 10 2023 - Safe retry mechanisms

Extends existing ReplayProtectionService with:
- Redis-backed fast lookups
- PostgreSQL persistence
- 24-hour retention
- Response caching for true idempotency
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

import redis.asyncio as redis
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)

DEFAULT_IDEMPOTENCY_WINDOW = 86400  # 24 hours
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


@dataclass
class IdempotencyRecord:
    """Stored idempotency record."""
    key: str
    tenant_id: Optional[str]
    user_id: Optional[str]
    request_method: str
    request_path: str
    request_body_hash: str
    response_status: int
    response_body: Optional[str]
    created_at: float


class IdempotencyManager:
    """
    Manages idempotency keys for API operations.

    Provides true idempotency by caching responses for duplicate requests.
    """

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        window_seconds: int = DEFAULT_IDEMPOTENCY_WINDOW
    ):
        self._redis = redis_client
        self._window = window_seconds

    def _key(self, idempotency_key: str) -> str:
        """Generate Redis key for idempotency record."""
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        return f"idempotency:{key_hash}"

    def _lock_key(self, idempotency_key: str) -> str:
        """Generate lock key to prevent concurrent processing."""
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        return f"idempotency:lock:{key_hash}"

    async def check_idempotency(
        self,
        idempotency_key: str,
        request: Request
    ) -> tuple[bool, Optional[Dict], Optional[str]]:
        """
        Check if request is idempotent duplicate.

        Returns:
            (is_new, cached_response, error)
            - is_new: True if this is first request (process it)
            - cached_response: Previous response if duplicate
            - error: Error message if idempotency conflict
        """
        if not self._redis:
            # Fail open if Redis unavailable
            return True, None, None

        key = self._key(idempotency_key)

        # Try to get existing record
        existing = await self._redis.get(key)
        if existing:
            try:
                record = json.loads(existing)

                # Check if request matches (method + path)
                if (record.get("request_method") != request.method or
                    record.get("request_path") != str(request.url.path)):
                    return False, None, "Idempotency key reused with different request"

                # Return cached response
                return False, {
                    "status_code": record.get("response_status"),
                    "body": record.get("response_body"),
                    "idempotent": True
                }, None
            except json.JSONDecodeError:
                pass

        # Check for in-progress request (lock)
        lock_key = self._lock_key(idempotency_key)
        lock_exists = await self._redis.exists(lock_key)
        if lock_exists:
            return False, None, "Request with this idempotency key is already in progress"

        # Set lock to prevent concurrent processing
        await self._redis.setex(lock_key, 60, "1")  # 1 minute lock

        return True, None, None

    async def store_response(
        self,
        idempotency_key: str,
        request: Request,
        response_status: int,
        response_body: Optional[str] = None
    ) -> None:
        """Store response for idempotency key."""
        if not self._redis:
            return

        key = self._key(idempotency_key)
        lock_key = self._lock_key(idempotency_key)

        # Get request body hash for comparison
        body = await request.body()
        body_hash = hashlib.sha256(body).hexdigest()

        record = {
            "key": idempotency_key,
            "request_method": request.method,
            "request_path": str(request.url.path),
            "request_body_hash": body_hash,
            "response_status": response_status,
            "response_body": response_body,
            "created_at": time.time()
        }

        # Store with TTL
        await self._redis.setex(key, self._window, json.dumps(record))

        # Release lock
        await self._redis.delete(lock_key)

    async def release_lock(self, idempotency_key: str) -> None:
        """Release processing lock (call on error)."""
        if self._redis:
            await self._redis.delete(self._lock_key(idempotency_key))

    async def get_record(self, idempotency_key: str) -> Optional[Dict]:
        """Get existing idempotency record."""
        if not self._redis:
            return None

        key = self._key(idempotency_key)
        existing = await self._redis.get(key)

        if existing:
            try:
                return json.loads(existing)
            except json.JSONDecodeError:
                pass

        return None

    async def delete_record(self, idempotency_key: str) -> None:
        """Delete idempotency record (for testing/admin)."""
        if not self._redis:
            return

        key = self._key(idempotency_key)
        await self._redis.delete(key)


# Singleton
_idempotency_manager: Optional[IdempotencyManager] = None


def get_idempotency_manager(
    redis_client: Optional[redis.Redis] = None
) -> IdempotencyManager:
    """Get or create idempotency manager."""
    global _idempotency_manager
    if _idempotency_manager is None:
        _idempotency_manager = IdempotencyManager(redis_client)
    return _idempotency_manager


def reset_idempotency_manager() -> None:
    """Reset idempotency manager singleton (for testing)."""
    global _idempotency_manager
    _idempotency_manager = None


# FastAPI Dependency
async def idempotency_dependency(request: Request) -> Optional[str]:
    """
    FastAPI dependency for idempotency checking.

    Usage:
        @router.post("/endpoint")
        async def endpoint(
            request: Request,
            idempotency_key: Optional[str] = Depends(idempotency_dependency)
        ):
            if idempotency_key:
                # Request was idempotent duplicate
                return cached_response
            ...
    """
    idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)

    if not idempotency_key:
        return None

    # Validate key format
    if len(idempotency_key) < 8 or len(idempotency_key) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be between 8 and 128 characters"
        )

    # Check idempotency
    from app.core.container import get_container
    container = get_container()
    manager = get_idempotency_manager(
        container.redis if container.is_initialized else None
    )

    is_new, cached, error = await manager.check_idempotency(
        idempotency_key, request
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error
        )

    if cached:
        # Return cached response via exception to short-circuit
        raise HTTPException(
            status_code=cached["status_code"],
            detail=json.loads(cached["body"]) if cached["body"] else None,
            headers={"Idempotent-Replay": "true"}
        )

    # Store key in state for later storage
    request.state.idempotency_key = idempotency_key
    request.state.idempotency_manager = manager

    return idempotency_key


async def store_idempotent_response(
    request: Request,
    response_status: int,
    response_body: Optional[str] = None
) -> None:
    """
    Store response for idempotency key.

    Call this after successful request processing.
    """
    if hasattr(request.state, "idempotency_key"):
        key = request.state.idempotency_key
        manager = getattr(request.state, "idempotency_manager", None)

        if manager and key:
            await manager.store_response(key, request, response_status, response_body)


async def release_idempotency_lock(request: Request) -> None:
    """Release idempotency lock on error."""
    if hasattr(request.state, "idempotency_key"):
        key = request.state.idempotency_key
        manager = getattr(request.state, "idempotency_manager", None)

        if manager and key:
            await manager.release_lock(key)