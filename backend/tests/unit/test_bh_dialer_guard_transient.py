"""outbound-telephony-dialer-6: a TRANSIENT CallGuard denial must re-defer the
job, not terminally BLOCK it.

Before the fix `_evaluate_call_guard` returned ``"block"`` for every failed
check except rate-limit/queue, and `process_job` answered ``"block"`` with
``_update_job_status(BLOCKED)`` and no re-enqueue. A platform outbound pause,
an out-of-hours moment or an abuse-velocity window therefore consumed the
whole queued backlog of every running campaign; un-pausing resumed nothing.

Permanent denials (DNC, blocked country, invalid number, tenant gone) stay
terminal.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services import call_guard as cg
from app.workers.dialer_worker import DialerCallIntent, DialerWorker


def _job() -> DialerJob:
    return DialerJob(
        job_id="job-6",
        campaign_id="campaign-6",
        lead_id="lead-6",
        tenant_id="tenant-6",
        phone_number="+15551234567",
    )


def _guard_result(check: cg.GuardCheck, reason: str) -> cg.GuardResult:
    return cg.GuardResult(
        decision=cg.GuardDecision.BLOCK,
        tenant_id="tenant-6",
        phone_number="+15551234567",
        check_results=[cg.CheckResult(check=check, passed=False, reason=reason)],
        failed_checks=[check],
        total_latency_ms=1,
    )


def _patch_guard(monkeypatch, result: cg.GuardResult) -> None:
    class _FakeGuard:
        def __init__(self, *a, **k):
            pass

        async def evaluate(self, **kwargs):
            return result

    monkeypatch.setattr(cg, "CallGuard", _FakeGuard)


TRANSIENT = [
    (cg.GuardCheck.PLATFORM_CALLS_ENABLED, "platform_outbound_calls_paused"),
    (cg.GuardCheck.BUSINESS_HOURS, "outside_business_hours"),
    (cg.GuardCheck.VELOCITY_CHECK, "recent_abuse_events: 2"),
    (cg.GuardCheck.SUBSCRIPTION_VALID, "subscription_past_due"),
    (cg.GuardCheck.TENANT_ACTIVE, "tenant_suspended"),
    (cg.GuardCheck.CONCURRENCY_LIMIT, "concurrent_limit_reached"),
    # A fail-closed check whose query raised is an infrastructure blip.
    (cg.GuardCheck.PLATFORM_CALLS_ENABLED, "check_error_blocked: pool timeout"),
    (cg.GuardCheck.DNC_CHECK, "dnc_check_unavailable: pool timeout"),
]

PERMANENT = [
    (cg.GuardCheck.DNC_CHECK, "number_on_dnc_list: internal"),
    (cg.GuardCheck.DNC_CHECK, "lead_marked_do_not_call"),
    (cg.GuardCheck.GEOGRAPHIC_ALLOWED, "blocked_country: KP"),
    (cg.GuardCheck.GEOGRAPHIC_ALLOWED, "country_not_allowed: FR"),
    (cg.GuardCheck.NUMBER_VALID, "invalid_e164_format"),
    (cg.GuardCheck.TENANT_ACTIVE, "tenant_not_found"),
    (cg.GuardCheck.TENANT_ACTIVE, "tenant_cancelled"),
    (cg.GuardCheck.SUBSCRIPTION_VALID, "subscription_canceled"),
    (cg.GuardCheck.SUBSCRIPTION_VALID, "tenant_not_found"),
    (cg.GuardCheck.SPEND_LIMIT, "spend_cap_exceeded: $10.00/$10.00"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("check,reason", TRANSIENT)
async def test_transient_guard_denial_is_a_deferral_not_a_block(monkeypatch, check, reason):
    _patch_guard(monkeypatch, _guard_result(check, reason))
    w = DialerWorker()
    decision = await w._evaluate_call_guard(_job(), CallingRules.default())
    assert decision == "defer"


@pytest.mark.asyncio
@pytest.mark.parametrize("check,reason", PERMANENT)
async def test_permanent_guard_denial_stays_a_block(monkeypatch, check, reason):
    _patch_guard(monkeypatch, _guard_result(check, reason))
    w = DialerWorker()
    decision = await w._evaluate_call_guard(_job(), CallingRules.default())
    assert decision == "block"


@pytest.mark.asyncio
async def test_mixed_denial_with_a_permanent_reason_stays_a_block(monkeypatch):
    result = cg.GuardResult(
        decision=cg.GuardDecision.BLOCK,
        tenant_id="tenant-6",
        phone_number="+15551234567",
        check_results=[
            cg.CheckResult(cg.GuardCheck.BUSINESS_HOURS, False, reason="outside_business_hours"),
            cg.CheckResult(cg.GuardCheck.DNC_CHECK, False, reason="number_on_dnc_list: x"),
        ],
        failed_checks=[cg.GuardCheck.BUSINESS_HOURS, cg.GuardCheck.DNC_CHECK],
        total_latency_ms=1,
    )
    _patch_guard(monkeypatch, result)
    w = DialerWorker()
    assert await w._evaluate_call_guard(_job(), CallingRules.default()) == "block"


def _base_worker() -> DialerWorker:
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None
    w._db_pool = None
    w._get_campaign_status = AsyncMock(return_value="running")
    w._stale_payload_reason = AsyncMock(return_value=None)
    w._load_existing_call_intent = AsyncMock(return_value=None)
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    w._get_tenant_rules = AsyncMock(return_value=CallingRules.default())
    w._get_campaign_calling_config = AsyncMock(return_value={})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)
    w._get_lead_timezone = AsyncMock(return_value=None)
    w.rules_engine.can_make_call = AsyncMock(return_value=(True, ""))
    w._resolve_batch_size = MagicMock(return_value=0)
    w._resolve_call_gap = MagicMock(return_value=0)
    w._update_job_status = AsyncMock()
    w._make_call = AsyncMock(return_value="provider-call-1")
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    w._create_call_intent = AsyncMock(return_value=intent)
    w._update_lead_status = AsyncMock()
    w._publish_reason = AsyncMock()
    return w


@pytest.mark.asyncio
async def test_deferred_guard_decision_reschedules_with_a_long_delay_and_releases_the_slot(
    monkeypatch,
):
    w = _base_worker()
    w._evaluate_call_guard = AsyncMock(return_value="defer")
    w._guard_defer_reason = "platform_outbound_calls_paused"
    w._guard_defer_delay = 600

    released = []

    async def fake_claim(redis, tenant_id):
        return 0

    async def fake_release(redis, tenant_id):
        released.append(tenant_id)

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", fake_claim
    )
    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.release_tenant_dial_slot", fake_release
    )

    job = _job()
    await w.process_job(job)

    assert released == [job.tenant_id]  # the claimed pacing slot is given back
    w._make_call.assert_not_awaited()
    w.queue_service.schedule_retry.assert_not_awaited()  # attempt not consumed
    w.queue_service.mark_failed.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] >= 60  # not a 1 s loop (each eval INCRs the limiter)
    statuses = [c.args[1] for c in w._update_job_status.call_args_list]
    assert JobStatus.BLOCKED not in statuses
    assert JobStatus.RETRY_SCHEDULED in statuses
    _, status_kwargs = w._update_job_status.call_args
    assert status_kwargs.get("reason") == "platform_outbound_calls_paused"


@pytest.mark.asyncio
async def test_permanent_guard_block_is_still_terminal(monkeypatch):
    w = _base_worker()
    w._evaluate_call_guard = AsyncMock(return_value="block")

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_not_awaited()
    statuses = [c.args[1] for c in w._update_job_status.call_args_list]
    assert statuses == [JobStatus.BLOCKED]
