"""A suspended tenant cannot place calls.

Bug hunt 2026-10-10 (tenancy-rbac-admin-3): suspend_tenant writes
tenants.status='suspended' and never touches subscription_status. The guard
read only subscription_status, so a tenant suspended for abuse kept dialling.
The Redis "suspension:propagate" messages that were meant to reach the guard
have no consumer.
"""
from __future__ import annotations

import pytest

from app.domain.services.call_guard import CallGuard, GuardCheck
from tests.unit.test_subscription_status_vocabulary import TENANT_ID, _FakeConn, _FakePool, _FakeRow
from tests.unit.test_call_guard_formal_authority import authority_row


def _guard(status):
    row = _FakeRow(authority_row(id=TENANT_ID, status=status))
    return CallGuard(db_pool=_FakePool(_FakeConn(row)), redis_client=None)


@pytest.mark.parametrize("status", ["suspended", "pending_deletion"])
async def test_a_suspended_or_departing_tenant_is_blocked(status):
    result = await _guard(status)._check_tenant_active(tenant_id=TENANT_ID)
    assert result.check == GuardCheck.TENANT_ACTIVE
    assert result.passed is False and result.reason == f"tenant_{status}"


@pytest.mark.parametrize("status", ["active", None])
async def test_an_active_tenant_still_dials(status):
    assert (await _guard(status)._check_tenant_active(tenant_id=TENANT_ID)).passed is True
