"""Regression tests for the spend-limit Decimal bug.

Root cause (pre-fix):
  asyncpg returns NUMERIC columns (`monthly_spend_cap`, `monthly_spend_used`)
  as `decimal.Decimal`. `CallGuard._check_spend_limit` did
  `current_spend + estimated_cost` where `estimated_cost` is a plain
  `float` -- `Decimal + float` raises `TypeError`.

  That TypeError used to be swallowed by `evaluate()`'s generic per-check
  handler and treated as `passed=True` -- but a separate (correct, already
  landed) fix made SPEND_LIMIT fail CLOSED on any check-function exception.
  The combination meant: ANY tenant with a spend cap configured (and any
  nonzero usage) would raise on every single evaluation and get BLOCKED
  on every call, regardless of whether they were actually over cap.

  Separately, `decimal.Decimal` is not JSON-serializable, so the
  `_get_tenant_limits` Redis cache write (`json.dumps(...)`) silently
  raised and was swallowed by its own `except Exception: pass`, so tenant
  limits were re-fetched from Postgres on every single evaluation instead
  of being cached for 60s.

Fix: coerce NUMERIC columns to `float` once at the asyncpg boundary in
`_get_tenant_limits` (both `TenantCallLimits` and `PartnerLimits`), plus a
defensive `float()` coercion at the exact arithmetic site in
`_check_spend_limit`.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.domain.services.call_guard import (
    CallGuard,
    GuardCheck,
    TenantCallLimits,
)


def _guard() -> CallGuard:
    return CallGuard(db_pool=object(), redis_client=None)


# ── _check_spend_limit: correctness with Decimal-typed inputs ──────────────

@pytest.mark.asyncio
async def test_spend_under_cap_allows_even_with_decimal_inputs():
    """Simulates a freshly-fetched-from-DB TenantCallLimits where a caller
    (or a stale code path) still hands in raw Decimal values -- the check
    must not raise and must ALLOW when under cap."""
    guard = _guard()
    limits = TenantCallLimits(
        monthly_spend_cap=Decimal("100.00"),
        monthly_spend_used=Decimal("10.00"),
    )

    result = await guard._check_spend_limit(
        tenant_id="t1", tenant_limits=limits, estimated_duration_seconds=60,
    )

    assert result.check == GuardCheck.SPEND_LIMIT
    assert result.passed is True
    assert result.details["current_spend"] == 10.00
    assert result.details["spend_cap"] == 100.00


@pytest.mark.asyncio
async def test_spend_over_cap_blocks_even_with_decimal_inputs():
    guard = _guard()
    limits = TenantCallLimits(
        monthly_spend_cap=Decimal("50.00"),
        monthly_spend_used=Decimal("49.99"),
    )

    result = await guard._check_spend_limit(
        tenant_id="t1", tenant_limits=limits, estimated_duration_seconds=600,
    )

    assert result.passed is False
    assert "spend_cap_exceeded" in result.reason


@pytest.mark.asyncio
async def test_spend_check_never_raises_typeerror_with_mixed_decimal_float():
    """The exact failure mode: Decimal (from DB) mixed with the float
    estimated_cost computed inline. Must not raise."""
    guard = _guard()
    limits = TenantCallLimits(
        monthly_spend_cap=Decimal("25.00"),
        monthly_spend_used=Decimal("0.00"),
    )
    # Should simply return a CheckResult, not raise TypeError.
    result = await guard._check_spend_limit(
        tenant_id="t1", tenant_limits=limits, estimated_duration_seconds=30,
    )
    assert result.passed is True


@pytest.mark.asyncio
async def test_spend_check_plain_float_inputs_still_work():
    """Non-regression: the already-fixed _get_tenant_limits path hands in
    plain floats -- confirm that keeps working identically."""
    guard = _guard()
    limits = TenantCallLimits(monthly_spend_cap=100.0, monthly_spend_used=99.99)

    result = await guard._check_spend_limit(
        tenant_id="t1", tenant_limits=limits, estimated_duration_seconds=120,
    )

    assert result.passed is False  # 99.99 + 0.10 > 100.0


# ── _get_tenant_limits: DB-boundary coercion + cache write succeeds ────────

class _FakeRow(dict):
    def get(self, k, default=None):
        return super().get(k, default)


class _FakeConn:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, *a, **k):
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


class _FakeRedis:
    """Minimal stand-in that raises exactly like a real redis client would
    if handed a non-JSON-serializable value, so a silent `except: pass`
    swallow shows up as `self.setex_succeeded is False` instead of masking
    the bug."""

    def __init__(self):
        self.setex_succeeded = False
        self.stored = None

    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        # Mirrors real redis-py: value must already be a str/bytes; if the
        # caller's json.dumps() raised, we'd never even get here. Simulate
        # that by requiring `value` be JSON round-trippable.
        json.loads(value)  # would raise if value wasn't valid JSON already
        self.stored = value
        self.setex_succeeded = True


def _row(**overrides):
    base = dict(
        calls_per_minute=60,
        calls_per_hour=1000,
        calls_per_day=10000,
        max_concurrent_calls=10,
        max_queue_size=50,
        monthly_minutes_allocated=0,
        monthly_minutes_used=0,
        monthly_spend_cap=Decimal("500.00"),
        monthly_spend_used=Decimal("123.45"),
        max_call_duration_seconds=3600,
        min_call_interval_seconds=300,
        allowed_country_codes=[],
        blocked_country_codes=[],
        blocked_prefixes=[],
        features_enabled={},
        features_disabled={},
        respect_business_hours=False,
        business_hours_timezone="UTC",
        is_active=True,
        business_hours_start=None,
        business_hours_end=None,
    )
    base.update(overrides)
    return _FakeRow(base)


@pytest.mark.asyncio
async def test_get_tenant_limits_coerces_decimal_to_float():
    conn = _FakeConn(_row())
    redis = _FakeRedis()
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=redis)

    limits = await guard._get_tenant_limits("t1")

    assert isinstance(limits.monthly_spend_cap, float)
    assert isinstance(limits.monthly_spend_used, float)
    assert limits.monthly_spend_cap == 500.00
    assert limits.monthly_spend_used == 123.45


@pytest.mark.asyncio
async def test_get_tenant_limits_cache_write_succeeds_with_nonzero_decimal_spend():
    """This is the regression check for the silent cache-corruption bug:
    before the fix, json.dumps(Decimal(...)) raised inside the try/except
    and `redis.setex` was NEVER called, so `setex_succeeded` stayed False
    and limits were re-fetched from Postgres on every evaluation."""
    conn = _FakeConn(_row(monthly_spend_used=Decimal("123.45")))
    redis = _FakeRedis()
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=redis)

    await guard._get_tenant_limits("t1")

    assert redis.setex_succeeded is True, (
        "the tenant-limits cache write must succeed even when the DB row "
        "carries nonzero Decimal spend values"
    )
    cached = json.loads(redis.stored)
    assert cached["monthly_spend_cap"] == 500.00
    assert cached["monthly_spend_used"] == 123.45


@pytest.mark.asyncio
async def test_get_tenant_limits_handles_null_spend_cap():
    conn = _FakeConn(_row(monthly_spend_cap=None, monthly_spend_used=None))
    redis = _FakeRedis()
    guard = CallGuard(db_pool=_FakePool(conn), redis_client=redis)

    limits = await guard._get_tenant_limits("t1")

    assert limits.monthly_spend_cap is None
    assert limits.monthly_spend_used == 0.0
    assert redis.setex_succeeded is True


# ── genuine spend-lookup error must still fail closed ───────────────────────

@pytest.mark.asyncio
async def test_evaluate_blocks_when_spend_check_raises_unexpectedly():
    """A genuine error (not the Decimal bug -- an actual exception from a
    broken spend check) must still fail CLOSED, per
    _FAIL_CLOSED_ON_ERROR_CHECKS. This proves the fix did not accidentally
    make SPEND_LIMIT fail-open."""
    from app.domain.services.call_guard import CheckResult, GuardDecision

    guard = CallGuard(db_pool=object(), redis_client=None)

    async def _pass(check, **kwargs):
        return CheckResult(check=check, passed=True)

    guard._check_tenant_active = lambda **kw: _pass(GuardCheck.TENANT_ACTIVE, **kw)
    guard._check_partner_active = lambda **kw: _pass(GuardCheck.PARTNER_ACTIVE, **kw)
    guard._check_subscription = lambda **kw: _pass(GuardCheck.SUBSCRIPTION_VALID, **kw)
    guard._check_dnc = lambda **kw: _pass(GuardCheck.DNC_CHECK, **kw)
    guard._check_rate_limit = lambda **kw: _pass(GuardCheck.RATE_LIMIT, **kw)
    guard._check_concurrency = lambda **kw: _pass(GuardCheck.CONCURRENCY_LIMIT, **kw)
    guard._check_minutes_quota = lambda **kw: _pass(GuardCheck.MINUTES_QUOTA, **kw)
    guard._get_tenant_limits = lambda tenant_id: _none()
    guard._get_partner_limits = lambda tenant_id: _none()
    guard._get_partner_id = lambda tenant_id: _none()
    guard._log_decision = lambda *a, **k: _none()

    async def _boom(**kwargs):
        raise RuntimeError("genuine spend-lookup backend failure")

    guard._check_spend_limit = _boom

    result = await guard.evaluate(tenant_id="t1", phone_number="+15551234567")

    assert result.decision == GuardDecision.BLOCK
    assert GuardCheck.SPEND_LIMIT in result.failed_checks


async def _none():
    return None
