"""Coverage for the 60s Redis cache added to CallGuard._check_subscription
(Worker E — production-hardening wave).

Scope, deliberately narrow:
  - _check_subscription IS cached (guard:subscription:{tenant_id}, 60s TTL,
    both ALLOW and DENY outcomes cached).
  - _check_dnc is explicitly NOT cached — a freshly-added DNC number must
    never be dialable off a stale cache (compliance). Proven here by
    asserting the DNC path never calls redis.get for its own check.
  - _check_feature_enabled is untouched (it already reads Redis-cached
    tenant/partner limits, not a direct cache of its own outcome) — not
    re-tested here, out of scope for this change.

Redis errors (get or setex) must fall through to the DB check — never to
an unconditional "allowed".
"""
from __future__ import annotations

import json

import asyncpg
import pytest

from app.domain.services.call_guard import CallGuard, GuardCheck


# ── fakes ────────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, *, row=None, raise_exc=None):
        self._row = row
        self._raise_exc = raise_exc
        self.fetchrow_calls = 0

    async def fetchrow(self, *a, **k):
        self.fetchrow_calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._row


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


class _FakeRow(dict):
    """asyncpg Record-like: supports both `row["k"]` and `row.get("k")`."""
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeRedis:
    """Minimal in-memory stand-in for the redis asyncio client, with knobs
    to simulate failures on get/setex independently."""
    def __init__(self, *, fail_get=False, fail_setex=False):
        self._store = {}
        self.fail_get = fail_get
        self.fail_setex = fail_setex
        self.get_calls = []
        self.setex_calls = []

    async def get(self, key):
        self.get_calls.append(key)
        if self.fail_get:
            raise ConnectionError("redis unavailable")
        return self._store.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        if self.fail_setex:
            raise ConnectionError("redis unavailable")
        self._store[key] = value


def _guard(conn, redis=None) -> CallGuard:
    return CallGuard(db_pool=_FakePool(conn), redis_client=redis)


# ── cache miss populates, hit skips DB ─────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_miss_hits_db_and_populates_cache():
    conn = _FakeConn(row=_FakeRow(subscription_status="active"))
    redis = _FakeRedis()
    guard = _guard(conn, redis)

    result = await guard._check_subscription(tenant_id="t1")

    assert result.passed is True
    assert conn.fetchrow_calls == 1
    assert len(redis.setex_calls) == 1
    key, ttl, value = redis.setex_calls[0]
    assert key == "guard:subscription:t1"
    assert ttl == 60
    cached = json.loads(value)
    assert cached["passed"] is True


@pytest.mark.asyncio
async def test_cache_hit_skips_db():
    conn = _FakeConn(row=_FakeRow(subscription_status="active"))
    redis = _FakeRedis()
    redis._store["guard:subscription:t1"] = json.dumps({
        "passed": True, "reason": None, "details": {"status": "active"},
    })
    guard = _guard(conn, redis)

    result = await guard._check_subscription(tenant_id="t1")

    assert result.passed is True
    assert result.check == GuardCheck.SUBSCRIPTION_VALID
    assert result.details == {"status": "active"}
    assert conn.fetchrow_calls == 0, "cache hit must not touch the DB"


@pytest.mark.asyncio
async def test_denied_subscription_is_also_cached():
    conn = _FakeConn(row=_FakeRow(subscription_status="suspended"))
    redis = _FakeRedis()
    guard = _guard(conn, redis)

    result = await guard._check_subscription(tenant_id="t2")

    assert result.passed is False
    assert result.reason == "subscription_suspended"
    key, ttl, value = redis.setex_calls[0]
    assert key == "guard:subscription:t2"
    cached = json.loads(value)
    assert cached["passed"] is False
    assert cached["reason"] == "subscription_suspended"

    # A subsequent call must reuse the cached DENY without hitting the DB.
    redis._store[key] = value
    result2 = await guard._check_subscription(tenant_id="t2")
    assert result2.passed is False
    assert conn.fetchrow_calls == 1, "second call should be served from cache"


# ── Redis errors fall through to DB, never to unconditional allow ──────────

@pytest.mark.asyncio
async def test_redis_get_error_falls_through_to_db():
    conn = _FakeConn(row=_FakeRow(subscription_status="suspended"))
    redis = _FakeRedis(fail_get=True)
    guard = _guard(conn, redis)

    result = await guard._check_subscription(tenant_id="t3")

    assert conn.fetchrow_calls == 1, "get() failure must fall through to DB, not skip it"
    assert result.passed is False
    assert result.reason == "subscription_suspended"


@pytest.mark.asyncio
async def test_redis_setex_error_does_not_break_the_result():
    conn = _FakeConn(row=_FakeRow(subscription_status="active"))
    redis = _FakeRedis(fail_setex=True)
    guard = _guard(conn, redis)

    result = await guard._check_subscription(tenant_id="t4")

    assert result.passed is True
    assert conn.fetchrow_calls == 1


@pytest.mark.asyncio
async def test_no_redis_client_still_works_uncached():
    conn = _FakeConn(row=_FakeRow(subscription_status="active"))
    guard = _guard(conn, redis=None)

    result = await guard._check_subscription(tenant_id="t5")

    assert result.passed is True
    assert conn.fetchrow_calls == 1


# ── DNC path is provably untouched by this caching change ──────────────────

@pytest.mark.asyncio
async def test_dnc_check_never_reads_or_writes_the_subscription_cache():
    conn = _FakeConn(row=None)
    redis = _FakeRedis()
    guard = _guard(conn, redis)

    result = await guard._check_dnc(tenant_id="t6", phone_number="+15551234567")

    assert result.passed is True
    assert redis.get_calls == [], "DNC check must never read from Redis at all"
    assert redis.setex_calls == [], "DNC check must never write to Redis at all"


@pytest.mark.asyncio
async def test_dnc_check_still_fails_closed_on_db_error_uncached():
    """Regression guard: this caching change must not have altered DNC's
    existing fail-closed-on-DB-error behavior."""
    conn = _FakeConn(raise_exc=asyncpg.PostgresError("db down"))
    redis = _FakeRedis()
    guard = _guard(conn, redis)

    result = await guard._check_dnc(tenant_id="t7", phone_number="+15551234567")

    assert result.passed is False
    assert redis.get_calls == []
