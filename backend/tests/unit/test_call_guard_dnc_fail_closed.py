"""Regression tests for BUG 2 (CRITICAL/compliance): DNC check must FAIL
CLOSED, not fail open.

Root cause (pre-fix):
  1. `CallGuard._check_dnc` caught `asyncpg.PostgresError` and fell through
     to `return CheckResult(passed=True)` — a DB error was treated as
     "number is clean", letting a DNC-listed number dial straight through
     the instant the DNC lookup errored.
  2. `CallGuard.evaluate()`'s generic per-check exception handler converted
     ANY check exception (not just DNC) into `passed=True`, so even an
     unexpected (non-PostgresError) failure inside `_check_dnc` would be
     silently waved through and never appear in `failed_checks`.

Both paths must now fail CLOSED for DNC specifically, while non-compliance
("availability") checks like rate-limit/concurrency keep failing open by
design (a transient infra blip there must not block an otherwise-compliant
call).
"""
from __future__ import annotations

import inspect

import asyncpg
import pytest

from app.domain.services.call_guard import (
    CallGuard,
    CheckResult,
    GuardCheck,
    GuardDecision,
    _FAIL_CLOSED_ON_ERROR_CHECKS,
)

# `_check_dnc` reads only `self._db_pool` — build a minimal stand-in rather
# than wiring a real asyncpg pool.
class _FakeConn:
    def __init__(self, *, row=None, raise_exc=None):
        self._row = row
        self._raise_exc = raise_exc

    async def fetchrow(self, *a, **k):
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


def _guard_with_conn(conn) -> CallGuard:
    return CallGuard(db_pool=_FakePool(conn), redis_client=None)


# ── `_check_dnc` itself: PostgresError must fail CLOSED ────────────────────

@pytest.mark.asyncio
async def test_dnc_postgres_error_fails_closed():
    """OLD CODE: `except asyncpg.PostgresError: pass` fell through to
    `passed=True` — this test fails against it (asserts `passed is False`)."""
    guard = _guard_with_conn(_FakeConn(raise_exc=asyncpg.PostgresError("db down")))

    result = await guard._check_dnc(tenant_id="t1", phone_number="+15551234567")

    assert result.check == GuardCheck.DNC_CHECK
    assert result.passed is False, (
        "a DNC lookup that errors must NOT be treated as 'number is clean' "
        "— this is exactly the fail-open compliance bug"
    )
    assert "dnc_check_unavailable" in (result.reason or "")


@pytest.mark.asyncio
async def test_dnc_listed_number_still_blocks():
    row = {"id": "dnc-1", "source": "tenant_upload", "reason": "opt_out"}
    guard = _guard_with_conn(_FakeConn(row=row))

    result = await guard._check_dnc(tenant_id="t1", phone_number="+15551234567")

    assert result.passed is False
    assert "number_on_dnc_list" in result.reason


@pytest.mark.asyncio
async def test_dnc_clean_number_passes():
    guard = _guard_with_conn(_FakeConn(row=None))

    result = await guard._check_dnc(tenant_id="t1", phone_number="+15551234567")

    assert result.passed is True


# ── generic per-check exception handler in evaluate() ───────────────────────

def test_fail_closed_set_contains_dnc_and_spend_only_the_intended_checks():
    assert GuardCheck.DNC_CHECK in _FAIL_CLOSED_ON_ERROR_CHECKS
    assert GuardCheck.SPEND_LIMIT in _FAIL_CLOSED_ON_ERROR_CHECKS
    # Availability checks must be preserved as fail-OPEN — an infra blip in
    # rate limiting or concurrency lookups must not block legitimate calls.
    assert GuardCheck.RATE_LIMIT not in _FAIL_CLOSED_ON_ERROR_CHECKS
    assert GuardCheck.CONCURRENCY_LIMIT not in _FAIL_CLOSED_ON_ERROR_CHECKS
    assert GuardCheck.TENANT_ACTIVE not in _FAIL_CLOSED_ON_ERROR_CHECKS


def test_evaluate_source_fails_closed_on_the_fail_closed_set():
    """Structural guard: evaluate()'s except-block must branch on
    `_FAIL_CLOSED_ON_ERROR_CHECKS` and add the check to `failed_checks` (so
    the decision mapping sees it) instead of unconditionally passing."""
    src = inspect.getsource(CallGuard.evaluate)
    assert "_FAIL_CLOSED_ON_ERROR_CHECKS" in src
    assert "failed_checks.append(check_type)" in src


@pytest.mark.asyncio
async def test_evaluate_blocks_when_dnc_check_raises_unexpectedly():
    """End-to-end: an unexpected (non-PostgresError) exception inside
    `_check_dnc` — the kind the OLD generic handler silently converted to
    `passed=True` for every check — must now result in GuardDecision.BLOCK
    with DNC_CHECK recorded in failed_checks. Earlier checks in priority
    order (tenant/partner/subscription active) are stubbed to pass instantly
    so the test needs no live DB; NUMBER_VALID/FEATURE_ENABLED/
    GEOGRAPHIC_ALLOWED run for real since they're pure functions."""
    guard = CallGuard(db_pool=object(), redis_client=None)

    async def _pass(check, **kwargs):
        return CheckResult(check=check, passed=True)

    guard._check_tenant_active = lambda **kw: _pass(GuardCheck.TENANT_ACTIVE, **kw)
    guard._check_partner_active = lambda **kw: _pass(GuardCheck.PARTNER_ACTIVE, **kw)
    guard._check_subscription = lambda **kw: _pass(GuardCheck.SUBSCRIPTION_VALID, **kw)
    guard._get_tenant_limits = lambda tenant_id: _none()
    guard._get_partner_limits = lambda tenant_id: _none()
    guard._get_partner_id = lambda tenant_id: _none()

    async def _boom(**kwargs):
        raise RuntimeError("unexpected dnc backend failure")

    guard._check_dnc = _boom
    guard._log_decision = lambda *a, **k: _none()

    result = await guard.evaluate(tenant_id="t1", phone_number="+15551234567")

    assert result.decision == GuardDecision.BLOCK, (
        "an unexpected error inside the DNC check must BLOCK the call, not "
        "silently ALLOW it through the generic 'treat as passed' path"
    )
    assert GuardCheck.DNC_CHECK in result.failed_checks
    dnc_results = [r for r in result.check_results if r.check == GuardCheck.DNC_CHECK]
    assert len(dnc_results) == 1
    assert dnc_results[0].passed is False
    # Checks after DNC_CHECK in priority order must never have run (fail-fast).
    assert not any(r.check == GuardCheck.RATE_LIMIT for r in result.check_results)


async def _none():
    return None
