"""T1.2 — cluster-wide concurrency cap.

Tests the Redis-backed lease scheme against a small in-process fake
that mimics the redis.asyncio commands the module actually uses. No
real Redis is required — keeps the suite hermetic and CI-friendly.

Behaviours covered:
  - First acquire increments, subsequent same-id acquire is idempotent.
  - Cap enforcement: the N+1th call_id is refused.
  - Release decrements.
  - Orphan reconcile drops call_ids with missing lease keys.
  - No Redis → graceful fallback (bridge keeps dialing via per-pod cap).
  - Cap resolution honours the two env vars in priority order.
"""
from __future__ import annotations

import os
import time
from typing import Any

import pytest

from app.domain.services.global_concurrency import (
    acquire_lease,
    current_count,
    reconcile_orphans,
    refresh_lease,
    release_lease,
    resolve_global_cap,
)


# ──────────────────────────────────────────────────────────────────────────
# Fake redis.asyncio client.
# Only the surface we actually use: SADD, SREM, SCARD, SMEMBERS, SET,
# EXPIRE, EXISTS, DELETE, pipeline().
# TTLs are simulated with a best-effort "expire_at" dict and a lazy
# cleanup on access.
# ──────────────────────────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.strings: dict[str, str] = {}
        self.expires: dict[str, float] = {}
        self.clock: float = time.time()

    def _expired(self, key: str) -> bool:
        exp = self.expires.get(key)
        if exp is not None and exp <= self.clock:
            self.strings.pop(key, None)
            self.expires.pop(key, None)
            return True
        return False

    async def sadd(self, key: str, *values: str) -> int:
        self.sets.setdefault(key, set())
        added = 0
        for v in values:
            if v not in self.sets[key]:
                self.sets[key].add(v)
                added += 1
        return added

    async def srem(self, key: str, *values: str) -> int:
        if key not in self.sets:
            return 0
        removed = 0
        for v in values:
            if v in self.sets[key]:
                self.sets[key].discard(v)
                removed += 1
        return removed

    async def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def set(self, key: str, value: str, *, ex: int | None = None, xx: bool = False) -> bool:
        self._expired(key)
        if xx and key not in self.strings:
            return False
        self.strings[key] = value
        if ex is not None:
            self.expires[key] = self.clock + ex
        return True

    async def expire(self, key: str, seconds: int) -> int:
        if key not in self.strings or self._expired(key):
            return 0
        self.expires[key] = self.clock + seconds
        return 1

    async def exists(self, key: str) -> int:
        self._expired(key)
        return 1 if key in self.strings else 0

    async def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            if key in self.strings:
                self.strings.pop(key, None)
                self.expires.pop(key, None)
                n += 1
        return n

    # Pipeline is simple — queue operations then execute sequentially.
    def pipeline(self, transaction: bool = True) -> "_FakePipeline":
        return _FakePipeline(self)

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward. Test helper."""
        self.clock += seconds


class _FakePipeline:
    def __init__(self, redis: _FakeRedis):
        self._redis = redis
        self._ops: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __getattr__(self, name: str):
        # Queue call → execute in .execute().
        def _queue(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return _queue

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args, kwargs in self._ops:
            fn = getattr(self._redis, name)
            results.append(await fn(*args, **kwargs))
        self._ops = []
        return results


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_acquire_succeeds():
    r = _FakeRedis()
    result = await acquire_lease(r, call_id="c1", pod_id="pod-a", cap=10)
    assert result.acquired is True
    assert result.reason == "acquired"
    assert await current_count(r) == 1


@pytest.mark.asyncio
async def test_idempotent_reacquire_does_not_double_count():
    """Calling acquire for the same call_id twice must not inflate the
    counter. Set semantics — membership is unique."""
    r = _FakeRedis()
    await acquire_lease(r, call_id="c1", pod_id="pod-a", cap=10)
    again = await acquire_lease(r, call_id="c1", pod_id="pod-a", cap=10)
    assert again.acquired is True
    assert await current_count(r) == 1


@pytest.mark.asyncio
async def test_cap_refuses_additional_calls():
    r = _FakeRedis()
    # Fill to cap.
    for i in range(3):
        ok = await acquire_lease(r, call_id=f"c{i}", pod_id="pod-a", cap=3)
        assert ok.acquired is True
    # N+1 refused.
    refused = await acquire_lease(r, call_id="c_extra", pod_id="pod-a", cap=3)
    assert refused.acquired is False
    assert refused.reason == "cap_reached"
    assert refused.current == 3
    # Cluster count should not have grown.
    assert await current_count(r) == 3


@pytest.mark.asyncio
async def test_release_returns_slot():
    r = _FakeRedis()
    await acquire_lease(r, call_id="c1", pod_id="pod-a", cap=1)
    assert await current_count(r) == 1
    await release_lease(r, call_id="c1")
    assert await current_count(r) == 0
    # Now another call can take the slot.
    again = await acquire_lease(r, call_id="c2", pod_id="pod-a", cap=1)
    assert again.acquired is True


@pytest.mark.asyncio
async def test_no_redis_falls_through_for_acquire():
    """A degraded Redis must not block origination — the per-pod cap
    is the backstop."""
    result = await acquire_lease(None, call_id="c1", pod_id="pod-a", cap=10)
    assert result.acquired is True
    assert result.reason == "redis_unavailable_fallback"


@pytest.mark.asyncio
async def test_release_is_idempotent():
    r = _FakeRedis()
    await acquire_lease(r, call_id="c1", pod_id="pod-a", cap=10)
    await release_lease(r, call_id="c1")
    # Second release is a no-op, not an error.
    await release_lease(r, call_id="c1")
    assert await current_count(r) == 0


@pytest.mark.asyncio
async def test_reconcile_drops_orphans_whose_lease_expired():
    """If a pod crashes, its lease TTLs expire but the call_id lingers
    in the active set. Reconcile drops those ghosts."""
    r = _FakeRedis()
    await acquire_lease(r, call_id="alive", pod_id="pod-a", cap=10)
    await acquire_lease(r, call_id="ghost", pod_id="pod-crashed", cap=10)
    assert await current_count(r) == 2
    # Simulate ghost's lease TTL expiry — lease key vanishes, set entry stays.
    r.strings.pop("telephony:lease:ghost", None)
    r.expires.pop("telephony:lease:ghost", None)
    removed = await reconcile_orphans(r)
    assert removed == 1
    assert await current_count(r) == 1


@pytest.mark.asyncio
async def test_refresh_keeps_lease_alive():
    r = _FakeRedis()
    await acquire_lease(r, call_id="c1", pod_id="pod-a", cap=10)
    # Advance time past original TTL, refresh before the sweep, still alive.
    r.advance(300)
    await refresh_lease(r, call_id="c1")
    r.advance(300)
    # Still within the refreshed TTL ceiling (600s).
    assert await r.exists("telephony:lease:c1") == 1


@pytest.mark.asyncio
async def test_current_count_returns_none_on_no_redis():
    assert await current_count(None) is None


# ──────────────────────────────────────────────────────────────────────────
# Cap-resolution env precedence
# ──────────────────────────────────────────────────────────────────────────

def test_resolve_cap_prefers_global_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_TELEPHONY_SESSIONS_GLOBAL", "200")
    monkeypatch.setenv("MAX_TELEPHONY_SESSIONS", "50")
    assert resolve_global_cap() == 200


def test_resolve_cap_falls_back_to_per_pod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MAX_TELEPHONY_SESSIONS_GLOBAL", raising=False)
    monkeypatch.setenv("MAX_TELEPHONY_SESSIONS", "75")
    assert resolve_global_cap() == 75


def test_resolve_cap_ignores_garbage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_TELEPHONY_SESSIONS_GLOBAL", "not-a-number")
    monkeypatch.delenv("MAX_TELEPHONY_SESSIONS", raising=False)
    assert resolve_global_cap() == 50  # module default


def test_resolve_cap_ignores_non_positive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_TELEPHONY_SESSIONS_GLOBAL", "0")
    monkeypatch.setenv("MAX_TELEPHONY_SESSIONS", "25")
    # First value is invalid (<=0) → falls through to the next env.
    assert resolve_global_cap() == 25
