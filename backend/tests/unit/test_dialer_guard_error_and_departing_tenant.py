"""Guard outcomes that must not be misclassified by the dialer.

Bug hunt 2026-10-10 (outbound-telephony-dialer-6, review follow-ups):
  * A CallGuard evaluation that RAISES (a DB blip) returned a terminal "block",
    permanently failing every lead the worker touched during the blip - the
    same failure class finding 6 fixed for transient denials. It must stay
    fail-closed (never dial) but re-defer instead of terminating the lead.
  * tenant_pending_deletion (emitted by the suspended-tenant guard) means the
    tenant is leaving: its leads must stop, not re-defer forever like a
    temporary suspension.
"""
from __future__ import annotations

import types
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.services.call_guard import GuardCheck
from app.workers import dialer_worker
from app.workers.dialer_worker import DialerWorker, _classify_guard_denial


def _denial(reason):
    return SimpleNamespace(check_results=[
        SimpleNamespace(check=GuardCheck.TENANT_ACTIVE, passed=False, reason=reason)])


def test_a_departing_tenant_is_terminal_but_a_suspended_one_defers():
    assert _classify_guard_denial(_denial("tenant_pending_deletion")) is None
    assert _classify_guard_denial(_denial("tenant_suspended")) is not None


@pytest.mark.asyncio
async def test_a_guard_that_raises_defers_instead_of_blocking(monkeypatch):
    class _BrokenGuard:
        def __init__(self, **_):
            pass

        async def evaluate(self, **_):
            raise ConnectionError("connection reset by peer")

    monkeypatch.setattr("app.domain.services.call_guard.CallGuard", _BrokenGuard)
    worker = DialerWorker.__new__(DialerWorker)
    worker._db_pool, worker._redis = object(), None
    job = SimpleNamespace(job_id="j1", tenant_id=uuid4(), phone_number="+14155550100",
                          campaign_id=uuid4(), lead_id=uuid4())

    decision = await DialerWorker._evaluate_call_guard(worker, job, rules=None)

    assert decision == "defer"
    assert worker._guard_defer_reason == "guard_check_unavailable"
    assert worker._guard_defer_delay == dialer_worker._GUARD_INFRA_DEFER_SECONDS
