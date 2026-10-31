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
import secrets
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Dict, Any

import redis.asyncio as redis
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)

DEFAULT_IDEMPOTENCY_WINDOW = 86400  # 24 hours
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

# Lua: delete the in-progress lock only while it still holds the value WE set
# (owner token + body hash). A handler that outlives the lock TTL must not
# delete the lock a later request took after ours expired. Atomic server-side,
# so no other request can take the lock between the compare and the delete.
_RELEASE_LOCK_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


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
        body_hash = hashlib.sha256(await request.body()).hexdigest()

        # Try to get existing record
        existing = await self._redis.get(key)
        if existing:
            try:
                record = json.loads(existing)

                # Check if request matches (method + path + body)
                if (record.get("request_method") != request.method or
                    record.get("request_path") != str(request.url.path) or
                    record.get("request_body_hash", body_hash) != body_hash):
                    return False, None, "Idempotency key reused with different request"

                # Return cached response
                return False, {
                    "status_code": record.get("response_status"),
                    "body": record.get("response_body"),
                    "idempotent": True
                }, None
            except json.JSONDecodeError:
                pass

        # Take the in-progress lock atomically (SET NX EX): two concurrent
        # first requests must not both proceed. The lock value is
        # "<owner token>:<body hash>": the token lets this request release only
        # ITS lock (it may expire and be re-taken by a later request), the hash
        # tells a concurrent request with a different body apart.
        lock_key = self._lock_key(idempotency_key)
        lock_value = f"{secrets.token_hex(16)}:{body_hash}"
        if not await self._redis.set(lock_key, lock_value, nx=True, ex=60):  # 1 minute lock
            held = await self._redis.get(lock_key)
            if isinstance(held, bytes):
                held = held.decode()
            # A lock left by a pre-token process is the bare body hash, which
            # rpartition also yields as its last element.
            if held is not None and held.rpartition(":")[2] != body_hash:
                return False, None, "Idempotency key reused with different request"
            return False, None, "Request with this idempotency key is already in progress"

        request.state.idempotency_lock_value = lock_value
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

        # Release the lock - only if it is still ours
        await self._release_if_owner(
            lock_key, getattr(request.state, "idempotency_lock_value", None)
        )

    async def _release_if_owner(self, lock_key: str, lock_value: Optional[str]) -> None:
        """Atomically delete the lock only while it still holds our value."""
        if not lock_value:
            return  # this request never took a lock: nothing of ours to release
        await self._redis.eval(_RELEASE_LOCK_LUA, 1, lock_key, lock_value)

    async def release_lock(
        self, idempotency_key: str, lock_value: Optional[str] = None
    ) -> None:
        """Release processing lock (call on error), only if it is still ours.

        ``lock_value`` is what this request set when it took the lock
        (``request.state.idempotency_lock_value``). A lock that expired and was
        taken by a later request holds a different value and is left alone.
        """
        if self._redis:
            await self._release_if_owner(self._lock_key(idempotency_key), lock_value)

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


async def _idempotency_scope(request: Request) -> tuple[Optional[str], Optional[str]]:
    """(tenant_id, user_id) the Idempotency-Key is namespaced under.

    Keys are client-chosen, so without a scope another tenant reusing a key on
    the same method+path would replay this tenant's cached response. Prefer the
    identity already verified for this request; resolve it here only when the
    route declared this dependency before authentication.
    """
    user_id = getattr(request.state, "authenticated_user_id", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user_id is None and (request.headers.get("Authorization") or request.cookies):
        from app.api.v1.dependencies import get_current_user
        from app.core.security.sessions import SESSION_COOKIE_NAME

        try:
            user = await get_current_user(
                request,
                authorization=request.headers.get("Authorization"),
                session_cookie=request.cookies.get(SESSION_COOKIE_NAME),
                access_cookie=request.cookies.get("talky_at"),
            )
            user_id, tenant_id = user.id, user.tenant_id
        except HTTPException:
            pass  # the route's own auth dependency answers 401/403
    if user_id is None and tenant_id is None:
        # Unauthenticated (signature-verified webhook): scope by the claimed tenant.
        tenant_id = request.headers.get("X-Tenant-ID")
    return (str(tenant_id) if tenant_id else None, str(user_id) if user_id else None)


# FastAPI Dependency
async def idempotency_dependency(request: Request) -> AsyncIterator[Optional[str]]:
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

    The in-progress lock is released when the request ends without a stored
    response (any HTTPException/error after the lock was taken), so a failed
    request does not 409 the client's retry for a minute. The release is by
    owner: if the handler outlived the lock TTL and a later request took the
    lock, that request's lock is left alone.
    """
    idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)

    if not idempotency_key:
        yield None
        return

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

    tenant_id, user_id = await _idempotency_scope(request)
    scoped_key = json.dumps([tenant_id, user_id, idempotency_key])

    is_new, cached, error = await manager.check_idempotency(
        scoped_key, request
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
    request.state.idempotency_key = scoped_key
    request.state.idempotency_manager = manager
    request.state.idempotency_stored = False

    try:
        yield idempotency_key
    finally:
        if not request.state.idempotency_stored:
            await manager.release_lock(
                scoped_key, getattr(request.state, "idempotency_lock_value", None)
            )


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
            request.state.idempotency_stored = True


async def release_idempotency_lock(request: Request) -> None:
    """Release idempotency lock on error."""
    if hasattr(request.state, "idempotency_key"):
        key = request.state.idempotency_key
        manager = getattr(request.state, "idempotency_manager", None)

        if manager and key:
            await manager.release_lock(
                key, getattr(request.state, "idempotency_lock_value", None)
            )