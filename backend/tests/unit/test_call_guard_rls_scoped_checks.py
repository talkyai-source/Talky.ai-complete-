"""CallGuard's concurrency and rate-limit checks read under the tenant's RLS scope.

Bug hunt 2026-10-10 (db-schema-core-6): both checks used a bare pool
connection. The concurrency lease/event/policy tables are FORCE ROW LEVEL
SECURITY, so once the app role loses BYPASSRLS (in progress: a0706b6f) the
lease count reads 0 and the concurrency cap can never trip, and the rate
limiter's event insert fails its WITH CHECK, which the guard treats as a pass.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from app.domain.services.call_guard import CallGuard, TenantCallLimits
from app.domain.services.telephony_rate_limiter import RateLimitAction

TENANT = "00000000-0000-4000-8000-000000000042"


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _RlsConn:
    """Rows are visible only after the tenant GUC is set, as under FORCE RLS."""

    def __init__(self):
        self.tenant = None

    def transaction(self):
        return _Tx()

    async def execute(self, sql, *_):
        match = re.match(r"SET LOCAL app.current_tenant_id = '(.+)'", sql)
        if match:
            self.tenant = match.group(1)
        return "SET"


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        return False


class _Pool:
    def acquire(self):
        return _Acquire(_RlsConn())


class _ConcurrencyLimiter:
    async def get_status(self, conn, *, tenant_id):
        return {"active_calls": 3 if conn.tenant == tenant_id else 0}


class _RateLimiter:
    async def evaluate(self, *, conn, tenant_id, policy_scope, metric_key):
        if conn.tenant != tenant_id:
            raise RuntimeError("new row violates row-level security policy")
        return SimpleNamespace(action=RateLimitAction.BLOCK, reason="calls_per_minute",
                               counter_value=61, threshold_value=60)


def _guard():
    guard = CallGuard(db_pool=_Pool(), redis_client=None)
    guard._concurrency_limiter = _ConcurrencyLimiter()
    guard._rate_limiter = _RateLimiter()
    return guard


async def test_the_concurrency_cap_counts_the_tenants_leases():
    result = await _guard()._check_concurrency(
        tenant_id=TENANT, tenant_limits=TenantCallLimits(max_concurrent_calls=3))
    assert result.passed is False and result.details["active_calls"] == 3


async def test_a_rate_limit_block_is_enforced():
    result = await _guard()._check_rate_limit(tenant_id=TENANT, tenant_limits=TenantCallLimits())
    assert result.passed is False and result.reason == "calls_per_minute"
