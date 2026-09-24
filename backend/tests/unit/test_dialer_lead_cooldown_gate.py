"""Reproduces the 2026-09-23 'lead-cooldown-always-cleared' production defect.

See scratchpad issues_all.txt id=lead-cooldown-always-cleared: the
unconditional `elif "lead_cooldown" in reason:` branch in
`DialerWorker.process_job` cleared `leads.last_called_at` and redialled
even when a real, already-answered/still-live call for the same lead
existed inside the cooldown window — +16478471491 was answered at
18:05:45 and redialled again at 18:09:12, 3.6 minutes later, well inside
its 2h cooldown (jobs 4cc194a0 -> 620d9ac5, calls 8b3176ca -> 6e0e221b).
It also bumped `job.attempt_number` only in memory, never persisting it
to `dialer_jobs`, which desynced the column that
`_record_ambiguous_attempt_state`'s guarded UPDATE matches on
(`dialer_jobs.attempt_number = job.attempt_number`), leaving
`dialer_jobs.call_id` NULL for an answered call (state_recorded=False).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker


TENANT_ID = "11111111-1111-4111-8111-111111111111"
CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
LEAD_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"


def _job() -> DialerJob:
    return DialerJob(
        job_id=JOB_ID,
        campaign_id=CAMPAIGN_ID,
        lead_id=LEAD_ID,
        tenant_id=TENANT_ID,
        phone_number="+16478471491",
        attempt_number=1,
    )


def _cooldown_worker() -> DialerWorker:
    """A worker whose only active gate is the 2h lead_cooldown block.

    Mirrors the `_ready_worker()` harness in
    test_dialer_origination_durability.py: every gate ahead of the
    scheduling-rules check is stubbed at its own DB/service boundary so
    `process_job`'s branching logic — the actual unit under test — runs
    for real.
    """
    worker = DialerWorker()
    worker.queue_service = AsyncMock()
    worker._redis = None
    worker._db_pool = None
    worker._load_existing_call_intent = AsyncMock(return_value=None)
    worker._get_campaign_status = AsyncMock(return_value="running")
    worker._tenant_minutes_exhausted = AsyncMock(return_value=False)
    worker._get_tenant_rules = AsyncMock(
        return_value=CallingRules(min_hours_between_calls=2)
    )
    worker._get_campaign_calling_config = AsyncMock(return_value={})
    worker._get_lead_last_called = AsyncMock(return_value=None)
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker._get_lead_attempts_today = AsyncMock(return_value=0)
    worker.rules_engine.can_make_call = AsyncMock(
        return_value=(False, "lead_cooldown_0.1h_of_2h")
    )
    worker._clear_lead_last_called = AsyncMock()
    worker._publish_reason = AsyncMock()
    worker._update_job_status = AsyncMock()
    # The DB check the fix adds: does a call for this lead in the cooldown
    # window exist that was answered or is still live? Mocked at the DB
    # boundary exactly like every other `_get_*`/`_clear_*` helper above —
    # process_job's branching decision is what these tests verify.
    worker._lead_has_live_or_answered_call = AsyncMock(return_value=False)
    worker._persist_job_attempt_number = AsyncMock()
    # Round-2 follow-up (review non-blocking #1 on e8e93870/620d9ac5): the
    # genuine-cooldown branch now times its retry off the lead's actual
    # remaining cooldown instead of a fixed 300s. Mocked at the same DB
    # boundary as the other `_get_*`/`_lead_*` helpers above; None (unknown)
    # is the default so tests that don't care about the exact delay still
    # exercise the real fallback-to-full-window path.
    worker._lead_cooldown_remaining_seconds = AsyncMock(return_value=None)
    return worker


@pytest.mark.asyncio
async def test_genuine_cooldown_is_respected_not_cleared():
    """A real, answered call for this lead exists inside the cooldown
    window (the 2026-09-23 case) -> the cooldown must be respected, never
    cleared, and the job must defer like any other blocked reason.
    """
    worker = _cooldown_worker()
    worker._lead_has_live_or_answered_call = AsyncMock(return_value=True)

    await worker.process_job(_job())

    worker._clear_lead_last_called.assert_not_called()
    worker.queue_service.enqueue_job.assert_not_called()
    worker.queue_service.schedule_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_genuinely_stale_cooldown_clears_and_persists_attempt_number():
    """No answered/live call backs the cooldown timestamp -> the original
    'set at origination, not at answer' workaround still applies, but the
    bumped attempt_number must reach `dialer_jobs`, not just the in-memory
    job copy (the desync that left dialer_jobs.call_id NULL on 09-23).
    """
    worker = _cooldown_worker()
    worker._lead_has_live_or_answered_call = AsyncMock(return_value=False)

    job = _job()
    await worker.process_job(job)

    worker._clear_lead_last_called.assert_awaited_once()
    worker.queue_service.enqueue_job.assert_awaited_once()
    assert job.attempt_number == 2
    worker._persist_job_attempt_number.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_genuine_cooldown_retries_once_when_it_actually_clears():
    """Round-2 follow-up (review non-blocking #1): the old fixed 300s retry
    on a genuine cooldown re-woke this branch every 5 minutes for the whole
    window -- about 24 cycles on a 2h cooldown -- bumping
    `job.attempt_number` via `queue_service.schedule_retry` each cycle
    (Redis only, never persisted here). `call_service._effective_attempt_
    number` takes max(db, live), so `decide_disposition` then saw attempt
    ~25 and treated the first post-cooldown no-answer as terminal. Timing
    the retry off the real remaining cooldown makes this one retry, not 24.
    """
    worker = _cooldown_worker()
    worker._lead_has_live_or_answered_call = AsyncMock(return_value=True)
    # 100 of 120 minutes already elapsed on the call backing the cooldown.
    worker._lead_cooldown_remaining_seconds = AsyncMock(return_value=1200.0)

    await worker.process_job(_job())

    worker.queue_service.schedule_retry.assert_awaited_once()
    assert worker.queue_service.schedule_retry.call_args.kwargs["delay_seconds"] == 1200


@pytest.mark.asyncio
async def test_genuine_cooldown_retry_delay_has_a_floor_and_a_fallback():
    """A near-expired cooldown still waits the sane floor, not an immediate
    re-poll; an unknown remaining time (the qualifying call fell outside
    the window between the two reads) falls back to the full configured
    window rather than the old fixed 300s.
    """
    worker = _cooldown_worker()
    worker._lead_has_live_or_answered_call = AsyncMock(return_value=True)
    worker._lead_cooldown_remaining_seconds = AsyncMock(return_value=5.0)

    await worker.process_job(_job())

    assert worker.queue_service.schedule_retry.call_args.kwargs["delay_seconds"] == 300

    worker2 = _cooldown_worker()
    worker2._lead_has_live_or_answered_call = AsyncMock(return_value=True)
    worker2._lead_cooldown_remaining_seconds = AsyncMock(return_value=None)

    await worker2.process_job(_job())

    # CallingRules(min_hours_between_calls=2) -> fallback is the full 2h window.
    assert worker2.queue_service.schedule_retry.call_args.kwargs["delay_seconds"] == 7200
