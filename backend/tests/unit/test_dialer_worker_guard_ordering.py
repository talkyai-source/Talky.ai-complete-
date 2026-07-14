"""Tests for FIX 2 (2026-07-13): the CallGuard rate-limit evaluation must
only run on the ORIGINATE path — after the batch-dispatch, call-gap, and
tenant-pacing deferral gates have all passed — not before them.

Root cause this guards against: `_evaluate_call_guard` INCRs a fixed-window
rate counter in `telephony_rate_limiter` as a side effect. When it ran
BEFORE the batch/call_gap/tenant_gap gates, a job that any of those gates
went on to defer had ALREADY been counted, with no decrement — the counter
tracked re-evaluation churn instead of actual dials, and could climb to
throttle a big campaign that was never really over its dial rate.

These tests drive `DialerWorker.process_job` with every collaborator
mocked except the gate-ordering logic itself, and assert
`_evaluate_call_guard` is NOT awaited when an earlier gate defers the job,
and IS awaited (exactly once) when every gate passes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker


def _job() -> DialerJob:
    return DialerJob(
        job_id="job-123",
        campaign_id="campaign-123",
        lead_id="lead-123",
        tenant_id="tenant-123",
        phone_number="+15551234567",
    )


def _base_worker() -> DialerWorker:
    """A DialerWorker with every process_job collaborator stubbed to the
    "everything is fine, keep going" answer, so only the gate under test
    actually defers the job."""
    worker = DialerWorker()
    worker.queue_service = AsyncMock()
    worker._redis = None  # tenant-pacing / release_tenant_dial_slot fail-open on None

    worker._get_campaign_status = AsyncMock(return_value="running")
    worker._tenant_minutes_exhausted = AsyncMock(return_value=False)
    worker._get_tenant_rules = AsyncMock(return_value=CallingRules.default())
    worker._get_campaign_calling_config = AsyncMock(return_value={})
    worker._get_lead_last_called = AsyncMock(return_value=None)
    worker._get_lead_attempts_today = AsyncMock(return_value=0)

    worker.rules_engine.can_make_call = AsyncMock(return_value=(True, ""))

    # Gates default to "pass" — individual tests override one to defer.
    worker._resolve_batch_size = MagicMock(return_value=0)  # 0 = gate disabled
    worker._campaign_inflight_calls = AsyncMock(return_value=0)
    worker._resolve_call_gap = MagicMock(return_value=0)  # 0 = gate disabled
    worker._campaign_seconds_since_last_dial = AsyncMock(return_value=None)

    worker._evaluate_call_guard = AsyncMock(return_value="allow")
    worker._update_job_status = AsyncMock()
    # A truthy provider_call_id so the "all gates passed" test walks the
    # real happy path (not the `raise Exception("No call_id...")` branch,
    # which would release the tenant slot for an unrelated reason and
    # defeat the assertion that ONLY a guard block/throttle/queue releases
    # it).
    worker._make_call = AsyncMock(return_value="provider-call-1")
    worker._create_call_record = AsyncMock(return_value=("call-1", "tk-1", "leg-1"))
    worker._update_lead_status = AsyncMock()
    worker._mark_campaign_dialed = AsyncMock()
    worker._emit_progress_event_throttled = AsyncMock()

    return worker


@pytest.mark.asyncio
async def test_guard_not_evaluated_when_batch_gate_defers():
    worker = _base_worker()
    worker._resolve_batch_size = MagicMock(return_value=1)
    worker._campaign_inflight_calls = AsyncMock(return_value=1)  # at capacity

    await worker.process_job(_job())

    worker._evaluate_call_guard.assert_not_awaited()
    worker.queue_service.schedule_retry.assert_awaited_once()
    worker._update_job_status.assert_awaited_once()
    _, kwargs = worker._update_job_status.call_args
    assert kwargs.get("reason") == "batch_capacity"


@pytest.mark.asyncio
async def test_guard_not_evaluated_when_call_gap_defers():
    worker = _base_worker()
    worker._resolve_call_gap = MagicMock(return_value=300)
    worker._campaign_seconds_since_last_dial = AsyncMock(return_value=5)  # too recent

    await worker.process_job(_job())

    worker._evaluate_call_guard.assert_not_awaited()
    worker.queue_service.schedule_retry.assert_awaited_once()
    _, kwargs = worker._update_job_status.call_args
    assert kwargs.get("reason") == "call_gap"


@pytest.mark.asyncio
async def test_guard_not_evaluated_when_tenant_gap_defers(monkeypatch):
    worker = _base_worker()

    async def fake_claim(redis, tenant_id):
        return 42  # someone else holds the tenant slot — defer

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        fake_claim,
    )

    job = _job()
    await worker.process_job(job)

    worker._evaluate_call_guard.assert_not_awaited()
    worker.queue_service.schedule_retry.assert_awaited_once_with(
        job, delay_seconds=42,
    )
    _, kwargs = worker._update_job_status.call_args
    assert kwargs.get("reason") == "tenant_gap"


@pytest.mark.asyncio
async def test_guard_evaluated_exactly_once_when_all_gates_pass(monkeypatch):
    worker = _base_worker()

    async def fake_claim(redis, tenant_id):
        return 0  # claimed — proceed

    released = []

    async def fake_release(redis, tenant_id):
        released.append(tenant_id)

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        fake_claim,
    )
    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.release_tenant_dial_slot",
        fake_release,
    )

    await worker.process_job(_job())

    worker._evaluate_call_guard.assert_awaited_once()
    # Guard allowed the call through — the slot claimed above must NOT be
    # released (that would re-open the tenant window early).
    assert released == []


@pytest.mark.asyncio
async def test_guard_block_releases_already_claimed_tenant_slot(monkeypatch):
    """The tenant pacing slot is claimed BEFORE the guard now runs (guard
    moved after the tenant_gap gate — see dialer_worker.process_job). A
    guard block/throttle/queue decision must give that slot back or the
    tenant's next legitimate job is paced out for nothing."""
    worker = _base_worker()
    worker._evaluate_call_guard = AsyncMock(return_value="block")

    async def fake_claim(redis, tenant_id):
        return 0  # claimed

    released = []

    async def fake_release(redis, tenant_id):
        released.append(tenant_id)

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        fake_claim,
    )
    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.release_tenant_dial_slot",
        fake_release,
    )

    job = _job()
    await worker.process_job(job)

    worker._evaluate_call_guard.assert_awaited_once()
    assert released == [job.tenant_id]
    worker._make_call.assert_not_called()
