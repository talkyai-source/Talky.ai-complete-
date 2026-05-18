"""Pre-warmed voice session pool — cold-start avoidance for inbound calls.

**Status: foundation primitive only.** The bridge integration (carrier
inbound flow → pool checkout) is intentionally NOT wired in this commit.
Wiring it requires three pieces that each carry production risk:

1. Provider-specific keepalive for idle pool entries (Flux WebSocket
   timeout, TTS WebSocket timeout, LLM HTTP/2 pool warmth) — Flux's
   heartbeat already runs while a transcription stream is active, but a
   pool entry that hasn't streamed yet is not yet covered. That gap is
   addressed in the integration PR.
2. Multi-tenant config keying — a pool entry pre-warmed with tenant A's
   credentials cannot be handed to tenant B. The pool is therefore
   *bucketed* by config-fingerprint; this module ships the contract,
   the bridge will pick the right bucket at acquire time.
3. Background refill scheduling — after a checkout the pool size drops;
   a refill task must repopulate without blocking the call path. The
   refill primitive lives here, but the *trigger* (when to fire it) is
   bridge-shaped.

Until the integration PR lands, this module is exercised by unit tests
only and has zero callers in production code. That is deliberate.

What this module DOES ship:
* A concurrency-safe `VoiceSessionPool` with `acquire` / `release` /
  `prewarm` / `shutdown` semantics.
* Bucket-keyed slots so multi-tenant integration drops in cleanly.
* Eviction on shutdown so no provider connection leaks past the
  app's lifecycle.
* Full test coverage for the failure modes that bit similar pools in
  other voice-AI codebases — exhaustion, double-release, shutdown
  during in-flight acquire, evict-while-acquired.

What it deliberately does NOT do:
* Idle-session keepalive ticks. That's provider-specific and lives in
  the integration PR.
* Automatic refill on checkout. The bridge will trigger refill at the
  right call-lifecycle moment; here we expose `prewarm` and let the
  caller schedule it.
* Time-based eviction (sessions older than X seconds). Add when the
  integration PR has measured the actual idle-drop rate per provider.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Bucketing — sessions are pooled per (tenant_id, persona_type, voice_id)
# tuple so a checkout always returns a session that matches the call's
# requirements. Adding more discriminators is a one-line tuple change;
# the rest of the pool code is dimension-agnostic.
# ---------------------------------------------------------------------
PoolBucketKey = Tuple[str, str, str]


def make_bucket_key(
    tenant_id: Optional[str],
    persona_type: Optional[str],
    voice_id: Optional[str],
) -> PoolBucketKey:
    """Build a bucket key from a config tuple. Empty string substitutes
    for None so dictionary keys stay hashable and human-readable in
    log lines."""
    return (tenant_id or "", persona_type or "", voice_id or "")


# A factory builds one pre-warmed session for a given bucket key. The
# pool stores opaque session objects — the factory is the only place
# that knows how to construct or discard one.
SessionFactory = Callable[[PoolBucketKey], Awaitable[Any]]
SessionDisposer = Callable[[Any], Awaitable[None]]


class VoiceSessionPool:
    """Bucketed pool of pre-warmed voice sessions.

    Thread/async safety: every public method acquires the internal
    lock; the lock is held only across O(1) bookkeeping operations,
    never across the awaitables in factory or disposer (those run
    outside the lock so a slow provider can't stall the whole pool).
    """

    def __init__(
        self,
        *,
        max_per_bucket: int,
        factory: SessionFactory,
        disposer: SessionDisposer,
    ) -> None:
        if max_per_bucket < 0:
            raise ValueError("max_per_bucket must be >= 0")
        self._max_per_bucket = max_per_bucket
        self._factory = factory
        self._disposer = disposer
        self._lock = asyncio.Lock()
        self._buckets: Dict[PoolBucketKey, Deque[Any]] = defaultdict(deque)
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Drain every bucket and dispose of each session.

        Idempotent: calling twice is safe. After shutdown, ``acquire``
        returns ``None`` and ``prewarm`` is a no-op so the pool can't
        be revived without explicit re-construction."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions_to_dispose: list[Any] = []
            for bucket in self._buckets.values():
                sessions_to_dispose.extend(bucket)
                bucket.clear()
            self._buckets.clear()

        # Dispose outside the lock so a slow disposer doesn't block
        # other shutdown paths from completing.
        for session in sessions_to_dispose:
            try:
                await self._disposer(session)
            except Exception as exc:  # noqa: BLE001 — disposer must not crash shutdown
                logger.warning(
                    "voice_session_pool_disposer_failed err=%s — leaking 1 session",
                    exc,
                )

    # ------------------------------------------------------------------
    # Pre-warming — caller schedules; pool fills.
    # ------------------------------------------------------------------

    async def prewarm(self, key: PoolBucketKey, count: int) -> int:
        """Add up to ``count`` pre-warmed sessions to the bucket.

        The bucket is never grown beyond ``max_per_bucket``; if it's
        already at capacity, no new sessions are created and ``0`` is
        returned. If the factory raises while building any session,
        the partial fill is preserved and the exception propagates so
        the caller can decide whether to retry.

        Returns the number of sessions actually added.
        """
        if self._closed or count <= 0:
            return 0

        # Compute capacity inside the lock; build outside so the lock
        # isn't held across an arbitrary provider handshake.
        async with self._lock:
            if self._closed:
                return 0
            current = len(self._buckets[key])
            room = max(0, self._max_per_bucket - current)
            to_build = min(count, room)
        if to_build == 0:
            return 0

        built: list[Any] = []
        kept_count = 0
        leftover: list[Any] = []
        try:
            for _ in range(to_build):
                session = await self._factory(key)
                built.append(session)
        finally:
            # Whatever we managed to build is sorted between "fits in
            # the bucket" and "doesn't" — partial warmup is better than
            # discarding successful work, and a concurrent prewarm
            # filling the bucket in the meantime must not cause us to
            # silently overshoot the cap.
            if built:
                async with self._lock:
                    if self._closed:
                        leftover = built
                    else:
                        bucket = self._buckets[key]
                        room_now = max(0, self._max_per_bucket - len(bucket))
                        keep = built[:room_now]
                        leftover = built[room_now:]
                        bucket.extend(keep)
                        kept_count = len(keep)
                # Dispose any sessions that didn't fit (closed pool, or
                # bucket filled while we were building) outside the lock.
                for session in leftover:
                    try:
                        await self._disposer(session)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "voice_session_pool_disposer_failed err=%s "
                            "— leaking 1 leftover session",
                            exc,
                        )
        return kept_count

    # ------------------------------------------------------------------
    # Acquire / release — the call-path API.
    # ------------------------------------------------------------------

    async def acquire(self, key: PoolBucketKey) -> Optional[Any]:
        """Check out a pre-warmed session for ``key``, or ``None`` if
        none is available. Acquiring from a closed pool also returns
        ``None`` so the caller can fall back to slow-path warmup."""
        async with self._lock:
            if self._closed:
                return None
            bucket = self._buckets.get(key)
            if not bucket:
                return None
            return bucket.popleft()

    async def release(self, key: PoolBucketKey, session: Any) -> None:
        """Return a session to the pool. If the bucket is full, or the
        pool is closed, the session is disposed instead.

        ``release`` is a deliberate caller decision — most call paths
        will NOT release at end of call (the session has been used and
        carries call state) and will instead let the disposer reclaim
        resources. Only explicitly-aborted pool checkouts (e.g. acquire
        succeeded but the call was cancelled before answer) belong here.
        """
        should_dispose = False
        async with self._lock:
            if self._closed:
                should_dispose = True
            else:
                bucket = self._buckets[key]
                if len(bucket) >= self._max_per_bucket:
                    should_dispose = True
                else:
                    bucket.append(session)
        if should_dispose:
            try:
                await self._disposer(session)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "voice_session_pool_disposer_failed err=%s — leaking 1 session",
                    exc,
                )

    # ------------------------------------------------------------------
    # Introspection — for telemetry and tests.
    # ------------------------------------------------------------------

    async def size(self, key: PoolBucketKey) -> int:
        async with self._lock:
            return len(self._buckets.get(key, ()))

    async def total_size(self) -> int:
        async with self._lock:
            return sum(len(b) for b in self._buckets.values())

    @property
    def is_closed(self) -> bool:
        return self._closed
