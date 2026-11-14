"""A tenant without explicit call limits gets the simultaneous calls its plan sells.

Release review, 14 Nov 2026: the pricing page sells 3 concurrent calls on Basic
and 10 on Business. A tenant with no tenant_call_limits row (every self-serve
sign-up: only platform-admin endpoints write that table) fell through to
TenantCallLimits() and was allowed 10 whatever it had bought.
"""
from __future__ import annotations

import pytest

from app.domain.services.call_guard import CallGuard, TenantCallLimits
from tests.unit.test_call_guard_spend_limit_decimal import _FakeConn, _FakePool, _row

TENANT = "00000000-0000-0000-0000-000000000001"


class _Conn(_FakeConn):
    """No (or one) tenant_call_limits row, and a scripted answer for the plan lookup."""

    def __init__(self, row, plan_value):
        super().__init__(row)
        self.plan_value = plan_value
        self.plan_queries = []

    async def fetchval(self, sql, *args):
        self.plan_queries.append((" ".join(sql.split()), args))
        if isinstance(self.plan_value, Exception):
            raise self.plan_value
        return self.plan_value


def _guard(row, plan_value):
    conn = _Conn(row, plan_value)
    return CallGuard(db_pool=_FakePool(conn), redis_client=None), conn


@pytest.mark.parametrize("plan_value", [3, 10, 1])
async def test_without_a_limits_row_the_plan_sets_the_simultaneous_calls(plan_value):
    guard, conn = _guard(None, plan_value)
    limits = await guard._get_tenant_limits(TENANT)

    assert limits.max_concurrent_calls == plan_value
    # Every other default is untouched.
    defaults = TenantCallLimits()
    assert (limits.calls_per_minute, limits.calls_per_day, limits.max_queue_size) == (
        defaults.calls_per_minute, defaults.calls_per_day, defaults.max_queue_size)
    # The lookup names the tenant explicitly; it never relies on row security alone.
    sql, args = conn.plan_queries[0]
    assert "FROM tenants t JOIN plans p ON p.id = t.plan_id WHERE t.id = $1" in sql
    assert args == (TENANT,)


@pytest.mark.parametrize("plan_value", [None, 0, -1, 5000, "3", True, RuntimeError("database unavailable")])
async def test_no_plan_or_an_unusable_value_keeps_the_default_and_never_blocks(plan_value):
    guard, _ = _guard(None, plan_value)
    limits = await guard._get_tenant_limits(TENANT)
    assert limits.max_concurrent_calls == TenantCallLimits().max_concurrent_calls == 10


async def test_an_explicit_limits_row_wins_over_the_plan():
    guard, conn = _guard(_row(max_concurrent_calls=25), 3)
    limits = await guard._get_tenant_limits(TENANT)
    assert limits.max_concurrent_calls == 25
    assert conn.plan_queries == [], "the plan is not consulted when an operator set the limit"
