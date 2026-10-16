"""Packet W6 items 1-2: the account gates and the tenant limits fail CLOSED.

Production finding (2026-10-16 outbound audit): ``CallGuard._check_tenant_active``,
``_check_partner_active`` and ``_check_subscription_uncached`` caught every
``asyncpg.PostgresError`` and returned ``passed=True`` ("schema_check_skipped"),
and ``_check_subscription`` then cached that 60 s. ``_get_tenant_limits`` turned
any ``PostgresError`` into permissive ``TenantCallLimits()`` defaults (no blocked
countries, no spend cap, no business hours). A database blip therefore let a
suspended / cancelled / over-cap tenant dial.

Contract after the fix:
  * Only ``UndefinedTableError`` / ``UndefinedColumnError`` mean "this schema
    does not have the column yet" -> ``schema_check_skipped`` / defaults.
  * Any other error makes the check FAIL with a ``check_error_blocked`` reason,
    the vocabulary ``dialer_worker._classify_guard_denial`` recognises as a
    transient infrastructure denial (deferred 60 s, never terminally blocked).
  * A failed or degraded subscription result is never written to the cache.
  * Tenant / partner configuration that cannot be loaded makes ``evaluate()``
    return a BLOCK the dialer defers.
"""
from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from app.domain.services.call_guard import (
    CallGuard,
    CheckResult,
    GuardCheck,
    GuardDecision,
    TenantCallLimits,
)
from app.workers.dialer_worker import _classify_guard_denial

TENANT = "00000000-0000-0000-0000-000000000201"
PARTNER = "00000000-0000-0000-0000-000000000202"


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return None


class _Conn:
    """Raises ``exc`` from every query (or only from queries whose SQL contains
    ``exc_when``) until ``exc`` is cleared."""

    def __init__(self, *, exc=None, row=None, exc_when=None):
        self.exc = exc
        self.row = row
        self.exc_when = exc_when
        self.queries = 0

    def transaction(self):
        return _Ctx(self)

    async def execute(self, *_a, **_k):
        return "OK"

    async def fetchrow(self, sql, *_a, **_k):
        self.queries += 1
        if self.exc is not None and (self.exc_when is None or self.exc_when in sql):
            raise self.exc
        return self.row


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, *_a, **_k):
        return _Ctx(self.conn)


class _DictRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


def _guard(conn, redis=None) -> CallGuard:
    return CallGuard(db_pool=_Pool(conn), redis_client=redis)


# Errors that are NOT "this schema is missing": every one must fail closed.
NON_SCHEMA_ERRORS = [
    pytest.param(asyncpg.PostgresError("generic server error"), id="postgres-error"),
    pytest.param(asyncpg.InternalServerError("recovery mode"), id="internal-server-error"),
    pytest.param(asyncpg.ConnectionDoesNotExistError("connection was closed"), id="connection-lost"),
    pytest.param(asyncpg.InterfaceError("pool is closing"), id="interface-error"),
    pytest.param(asyncio.TimeoutError(), id="pool-acquire-timeout"),
]
SCHEMA_ERRORS = [
    pytest.param(asyncpg.UndefinedTableError("relation does not exist"), id="undefined-table"),
    pytest.param(asyncpg.UndefinedColumnError("column does not exist"), id="undefined-column"),
]

ACCOUNT_CHECKS = [
    pytest.param("_check_tenant_active", GuardCheck.TENANT_ACTIVE, {}, id="tenant_active"),
    pytest.param("_check_partner_active", GuardCheck.PARTNER_ACTIVE, {"partner_id": PARTNER}, id="partner_active"),
    pytest.param("_check_subscription", GuardCheck.SUBSCRIPTION_VALID, {}, id="subscription_valid"),
]


# ── item 1: the three account gates ────────────────────────────────────────


@pytest.mark.parametrize("method,check,extra", ACCOUNT_CHECKS)
@pytest.mark.parametrize("exc", NON_SCHEMA_ERRORS)
async def test_account_gate_fails_closed_on_a_non_schema_error(method, check, extra, exc):
    guard = _guard(_Conn(exc=exc))

    result = await getattr(guard, method)(tenant_id=TENANT, **extra)

    assert result.check == check
    assert result.passed is False, "a database error must not wave the call through"
    assert (result.reason or "").startswith("check_error_blocked")


@pytest.mark.parametrize("method,check,extra", ACCOUNT_CHECKS)
@pytest.mark.parametrize("exc", SCHEMA_ERRORS)
async def test_account_gate_skips_only_when_the_schema_is_missing(method, check, extra, exc):
    guard = _guard(_Conn(exc=exc))

    result = await getattr(guard, method)(tenant_id=TENANT, **extra)

    assert result.check == check
    assert result.passed is True
    assert result.reason == "schema_check_skipped"


@pytest.mark.parametrize("method,check,extra", ACCOUNT_CHECKS)
async def test_account_gate_failure_is_a_transient_denial_for_the_dialer(method, check, extra):
    guard = _guard(_Conn(exc=asyncpg.PostgresError("db blip")))

    result = await getattr(guard, method)(tenant_id=TENANT, **extra)

    from app.domain.services.call_guard import GuardResult

    denial = GuardResult(
        decision=GuardDecision.BLOCK,
        tenant_id=TENANT,
        phone_number="+15551234567",
        check_results=[result],
        failed_checks=[check],
        total_latency_ms=1,
    )
    classified = _classify_guard_denial(denial)
    assert classified is not None, "an infrastructure error must be deferred, not terminally blocked"
    reason, delay = classified
    assert reason == "guard_check_unavailable"
    assert delay == 60


async def test_subscription_error_result_is_never_cached():
    redis = _DictRedis()
    conn = _Conn(exc=asyncpg.PostgresError("db blip"))
    guard = _guard(conn, redis)

    first = await guard._check_subscription(tenant_id=TENANT)
    assert first.passed is False
    assert redis.store == {}, "an error result must not be cached"

    # The database recovers: the very next call must read it, not a cached verdict.
    conn.exc = None
    conn.row = {"subscription_status": "active"}
    second = await guard._check_subscription(tenant_id=TENANT)
    assert second.passed is True
    assert conn.queries == 2


async def test_subscription_schema_skip_is_not_cached_either():
    redis = _DictRedis()
    guard = _guard(_Conn(exc=asyncpg.UndefinedColumnError("no subscription_status")), redis)

    result = await guard._check_subscription(tenant_id=TENANT)

    assert result.reason == "schema_check_skipped"
    assert redis.store == {}


async def test_subscription_real_verdicts_are_still_cached():
    redis = _DictRedis()
    guard = _guard(_Conn(row={"subscription_status": "active"}), redis)

    result = await guard._check_subscription(tenant_id=TENANT)

    assert result.passed is True
    cached = json.loads(redis.store[f"guard:subscription:{TENANT}"])
    assert cached["passed"] is True


async def test_tenant_active_still_reports_a_suspended_tenant():
    guard = _guard(_Conn(row={"id": TENANT, "subscription_status": "active", "status": "suspended"}))

    result = await guard._check_tenant_active(tenant_id=TENANT)

    assert result.passed is False
    assert result.reason == "tenant_suspended"


# ── item 2: tenant / partner limits ────────────────────────────────────────


@pytest.mark.parametrize("exc", NON_SCHEMA_ERRORS)
async def test_tenant_limits_do_not_default_on_a_non_schema_error(exc):
    guard = _guard(_Conn(exc=exc))

    with pytest.raises(type(exc)):
        await guard._get_tenant_limits(TENANT)


@pytest.mark.parametrize("exc", SCHEMA_ERRORS)
async def test_tenant_limits_default_only_when_the_table_or_column_is_missing(exc):
    guard = _guard(_Conn(exc=exc))

    limits = await guard._get_tenant_limits(TENANT)

    assert isinstance(limits, TenantCallLimits)
    assert limits == TenantCallLimits()


async def test_tenant_with_no_limits_row_still_gets_defaults():
    guard = _guard(_Conn(row=None))

    assert await guard._get_tenant_limits(TENANT) == TenantCallLimits()


@pytest.mark.parametrize("exc", NON_SCHEMA_ERRORS)
async def test_partner_lookup_does_not_report_no_partner_on_a_non_schema_error(exc):
    guard = _guard(_Conn(exc=exc))

    with pytest.raises(type(exc)):
        await guard._get_partner_id(TENANT)


@pytest.mark.parametrize("exc", SCHEMA_ERRORS)
async def test_partner_lookup_returns_none_only_when_the_column_is_missing(exc):
    guard = _guard(_Conn(exc=exc))

    assert await guard._get_partner_id(TENANT) is None


@pytest.mark.parametrize("exc", NON_SCHEMA_ERRORS)
async def test_partner_limits_do_not_vanish_on_a_non_schema_error(exc):
    guard = _guard(_Conn(exc=exc))

    async def _partner_id(_tenant_id):
        return PARTNER

    guard._get_partner_id = _partner_id

    with pytest.raises(type(exc)):
        await guard._get_partner_limits(TENANT)


@pytest.mark.parametrize("exc", SCHEMA_ERRORS)
async def test_partner_limits_are_none_only_when_the_table_is_missing(exc):
    guard = _guard(_Conn(exc=exc))

    async def _partner_id(_tenant_id):
        return PARTNER

    guard._get_partner_id = _partner_id

    assert await guard._get_partner_limits(TENANT) is None


# ── end to end: evaluate() and the dialer's deferral vocabulary ────────────


async def _pass(check):
    return CheckResult(check=check, passed=True)


def _stub_non_account_checks(guard: CallGuard) -> None:
    guard._check_platform_calls_enabled = lambda **kw: _pass(GuardCheck.PLATFORM_CALLS_ENABLED)

    async def _no_log(*_a, **_k):
        return None

    guard._log_decision = _no_log


async def test_evaluate_defers_instead_of_dialing_when_limits_cannot_be_loaded():
    # Only the tenant_call_limits query fails; the account gates would all pass.
    conn = _Conn(
        exc=asyncpg.PostgresError("db blip"),
        exc_when="tenant_call_limits",
        row={"id": TENANT, "subscription_status": "active", "status": "active"},
    )
    guard = _guard(conn)
    _stub_non_account_checks(guard)

    result = await guard.evaluate(tenant_id=TENANT, phone_number="+15551234567")

    assert result.decision == GuardDecision.BLOCK
    assert result.failed_checks == [GuardCheck.TENANT_ACTIVE]
    assert result.check_results[0].reason == "configuration_load_error"
    assert _classify_guard_denial(result) is not None, (
        "an unloadable configuration must be deferred by the dialer, not terminally blocked"
    )


async def test_evaluate_defers_instead_of_dialing_when_the_account_gate_errors():
    guard = _guard(_Conn(exc=asyncpg.PostgresError("db blip")))
    _stub_non_account_checks(guard)

    async def _limits(_tenant_id):
        return TenantCallLimits()

    async def _none(_tenant_id):
        return None

    guard._get_tenant_limits = _limits
    guard._get_partner_limits = _none
    guard._get_partner_id = _none

    result = await guard.evaluate(tenant_id=TENANT, phone_number="+15551234567")

    assert result.decision == GuardDecision.BLOCK
    assert GuardCheck.TENANT_ACTIVE in result.failed_checks
    assert _classify_guard_denial(result) == ("guard_check_unavailable", 60)
