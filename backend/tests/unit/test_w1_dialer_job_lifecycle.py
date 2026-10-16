"""Packet W1 -- dialer job lifecycle (campaign state, stale payloads, tenant
concurrency, refused line, settled-before-processing race).

Each test pins one confirmed production defect:

* S1 / pause double-dial: a non-runnable campaign re-deferred a DELETED
  campaign's payload every 120 s forever, and wrote the DB job SKIPPED for a
  PAUSED campaign while its payload survived, so a resume minted a second job.
* stale payloads: the worker never read ``dialer_jobs.status`` / ``leads``
  before dialling, so removed/cancelled work kept dialling.
* tenant concurrency: one tenant's live calls (platform-wide SCARD) blocked
  every other tenant.
* refused line: a ``tenant_pbx_required`` hold expiring burned the attempt and
  eventually failed the lead.
* settled-before-processing: a pre-answer failure that settled first was
  overwritten by the late ``calling`` / PROCESSING writes.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.dialer.block_reasons import BlockCode
from app.domain.services.queue_service import DialerQueueService
from app.workers.dialer_worker import (
    ComplianceLookupUnavailable,
    DialerCallIntent,
    DialerWorker,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def _job(*, tenant: str = TENANT_A, attempt: int = 1) -> DialerJob:
    j = DialerJob(
        job_id="33333333-3333-3333-3333-333333333333",
        campaign_id="44444444-4444-4444-4444-444444444444",
        lead_id="55555555-5555-5555-5555-555555555555",
        tenant_id=tenant,
        phone_number="+447700900123",
    )
    j.attempt_number = attempt
    return j


def _worker() -> DialerWorker:
    """Every collaborator mocked; the success path reaches the bridge call."""
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None
    w._db_pool = None
    w._get_campaign_status = AsyncMock(return_value="running")
    w._load_existing_call_intent = AsyncMock(return_value=None)
    w._stale_payload_reason = AsyncMock(return_value=None)
    w._tenant_live_call_count = AsyncMock(return_value=None)
    w._attempt_call_settled = AsyncMock(return_value=False)
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    w._get_tenant_rules = AsyncMock(return_value=CallingRules.default())
    w._get_campaign_calling_config = AsyncMock(return_value={})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)
    w._get_lead_timezone = AsyncMock(return_value=None)
    w.rules_engine.can_make_call = AsyncMock(return_value=(True, ""))
    w._resolve_batch_size = MagicMock(return_value=0)
    w._resolve_call_gap = MagicMock(return_value=0)
    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._update_job_status = AsyncMock()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    w._create_call_intent = AsyncMock(return_value=intent)
    w._mark_call_intent_not_originated = AsyncMock(return_value=True)
    w._release_intent_line_not_ready = AsyncMock(return_value=True)
    w._park_uncertain_origination = AsyncMock()
    w._update_lead_status = AsyncMock()
    w._publish_reason = AsyncMock()
    w._record_job_failure_classification = AsyncMock()
    w._emit_unknown_bridge_code_alert = AsyncMock()
    w._persist_job_attempt_number = AsyncMock()
    w._attempt_settled_failed_unanswered = AsyncMock(return_value=False)
    w._bind_call_intent = AsyncMock()
    w._mark_campaign_dialed = AsyncMock()
    w._emit_progress_event_throttled = AsyncMock()
    w._make_call = AsyncMock(return_value="provider-call-1")
    return w


def _conn_pool(w: DialerWorker, *, fetchrow=None, fetchval=None, error=None):
    """Replace ``_acquire_db`` with a connection returning canned rows."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def _acquire():
        if error is not None:
            raise error
        yield conn

    w._acquire_db = _acquire
    return conn


def _published_codes(w: DialerWorker) -> list[BlockCode]:
    return [c.args[1].code for c in w._publish_reason.call_args_list]


# ═══ 1. campaign state: paused keeps the job, everything else ends it ════


@pytest.mark.asyncio
async def test_paused_campaign_job_is_redeferred_and_stays_active():
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value="paused")
    await w.process_job(_job())

    # The payload survives (re-deferred) and the DB job is NEVER written
    # SKIPPED: a resume must find the SAME active job, not mint a second one.
    w.queue_service.mark_skipped.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 120
    statuses = [c.args[1] for c in w._update_job_status.call_args_list]
    assert JobStatus.SKIPPED not in statuses
    assert statuses == [JobStatus.RETRY_SCHEDULED]
    assert w._update_job_status.call_args.kwargs["reason"] == "campaign_paused"
    w._make_call.assert_not_awaited()
    assert BlockCode.CAMPAIGN_NOT_RUNNING in _published_codes(w)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["deleted", "stopped", "completed", "cancelled", "draft", "archived", None]
)
async def test_every_other_non_runnable_campaign_ends_the_job(status):
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value=status)
    await w.process_job(_job())

    # Terminal: the payload leaves Redis with a reason that is NOT in
    # NON_TERMINAL_SKIP_REASONS (those are re-deferred forever) ...
    w.queue_service.mark_skipped.assert_awaited_once()
    reason = w.queue_service.mark_skipped.call_args.kwargs["reason"]
    assert reason == "campaign_not_runnable"
    assert reason not in DialerQueueService.NON_TERMINAL_SKIP_REASONS
    w.queue_service._redefer_inflight.assert_not_awaited()
    # ... and the DB job is written SKIPPED so the lead can be re-queued.
    args, kwargs = w._update_job_status.call_args
    assert args[1] is JobStatus.SKIPPED
    assert kwargs["error"].startswith("campaign_not_runnable")
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_campaign_stopped_between_validation_and_originate_is_terminal_too():
    w = _worker()
    w._get_campaign_status = AsyncMock(side_effect=["running", "deleted"])
    await w.process_job(_job())

    w.queue_service.mark_skipped.assert_awaited_once()
    assert (
        w.queue_service.mark_skipped.call_args.kwargs["reason"]
        not in DialerQueueService.NON_TERMINAL_SKIP_REASONS
    )
    w._create_call_intent.assert_not_awaited()
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_campaign_recheck_before_originate_keeps_the_job():
    w = _worker()
    w._get_campaign_status = AsyncMock(side_effect=["running", "paused"])
    await w.process_job(_job())

    w.queue_service.mark_skipped.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert JobStatus.SKIPPED not in [
        c.args[1] for c in w._update_job_status.call_args_list
    ]
    w._create_call_intent.assert_not_awaited()


async def _real_queue_job(campaign_status: str):
    """Real DialerQueueService on fakeredis + the real worker control flow."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    queue = DialerQueueService(redis_client=redis)
    await queue.initialize()
    job = _job()
    assert await queue.enqueue_job(job)
    dequeued = await queue.dequeue_job(tenant_ids=[TENANT_A])
    assert dequeued is not None and dequeued.job_id == job.job_id
    assert await redis.llen(DialerQueueService.INFLIGHT_LIST) == 1

    w = _worker()
    w.queue_service = queue
    w._get_campaign_status = AsyncMock(return_value=campaign_status)
    await w.process_job(dequeued)
    return redis, w


@pytest.mark.asyncio
async def test_deleted_campaign_payload_leaves_redis_instead_of_cycling_forever():
    redis, w = await _real_queue_job("deleted")
    assert await redis.zcard(DialerQueueService.SCHEDULED_ZSET) == 0
    assert await redis.llen(DialerQueueService.INFLIGHT_LIST) == 0
    assert await redis.hlen(DialerQueueService.INFLIGHT_HASH) == 0
    assert JobStatus.SKIPPED in [c.args[1] for c in w._update_job_status.call_args_list]


@pytest.mark.asyncio
async def test_paused_campaign_payload_survives_in_the_scheduled_set():
    redis, w = await _real_queue_job("paused")
    assert await redis.zcard(DialerQueueService.SCHEDULED_ZSET) == 1
    assert await redis.llen(DialerQueueService.INFLIGHT_LIST) == 0
    assert JobStatus.SKIPPED not in [c.args[1] for c in w._update_job_status.call_args_list]


# ═══ 2. stale payloads must not dial ═════════════════════════════════════


def _state_row(job="queued", lead_row="lead", lead="pending", dnc=False):
    return {
        "job_status": job,
        "lead_row_id": lead_row,
        "lead_status": lead,
        "lead_do_not_call": dnc,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,expected",
    [
        (_state_row(), None),
        (_state_row(job="retry_scheduled"), None),
        (_state_row(job="processing"), None),
        (_state_row(job="cancelled"), "job_not_active"),
        (_state_row(job="skipped"), "job_not_active"),
        (_state_row(job="failed"), "job_not_active"),
        (_state_row(job="completed"), "job_not_active"),
        (_state_row(job=None), "job_not_active"),  # no such job row
        (_state_row(lead_row=None, lead=None), "lead_not_dialable"),
        (_state_row(lead="deleted"), "lead_not_dialable"),
        (_state_row(lead="dnc"), "lead_not_dialable"),
        (_state_row(dnc=True), "lead_not_dialable"),
        (_state_row(dnc=None), None),
        # an inactive job wins: nothing else matters once the DB ended it
        (_state_row(job="cancelled", lead="deleted"), "job_not_active"),
    ],
)
async def test_stale_payload_reason_truth_table(row, expected):
    w = DialerWorker()
    conn = _conn_pool(w, fetchrow=row)
    assert await w._stale_payload_reason(_job()) == expected

    # ONE tenant-scoped query reads the job and the lead together.
    assert conn.fetchrow.await_count == 1
    sql = conn.fetchrow.call_args.args[0]
    assert "j.tenant_id = $2::uuid" in sql
    assert "l.tenant_id = $2::uuid" in sql
    assert conn.fetchrow.call_args.args[1:] == (
        _job().job_id,
        TENANT_A,
        _job().lead_id,
    )


@pytest.mark.asyncio
async def test_stale_payload_read_failure_raises_so_the_job_is_deferred():
    w = DialerWorker()
    _conn_pool(w, error=RuntimeError("db down"))
    with pytest.raises(ComplianceLookupUnavailable):
        await w._stale_payload_reason(_job())


@pytest.mark.asyncio
async def test_job_cancelled_in_the_database_is_not_dialled_from_a_stale_payload():
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value="job_not_active")
    await w.process_job(_job())

    w._create_call_intent.assert_not_awaited()
    w._make_call.assert_not_awaited()
    w.queue_service.mark_skipped.assert_awaited_once()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == "job_not_active"
    # The DB already ended this job; its history must not be rewritten.
    w._update_job_status.assert_not_awaited()
    w._update_lead_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_removed_or_dnc_lead_is_not_dialled_and_not_resurrected():
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value="lead_not_dialable")
    await w.process_job(_job())

    w._create_call_intent.assert_not_awaited()
    w._make_call.assert_not_awaited()
    assert (
        w.queue_service.mark_skipped.call_args.kwargs["reason"] == "lead_not_dialable"
    )
    args, kwargs = w._update_job_status.call_args
    assert args[1] is JobStatus.SKIPPED and kwargs["error"] == "lead_not_dialable"
    # A removed contact must never be flipped back to 'pending'.
    w._update_lead_status.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_not_awaited()


@pytest.mark.asyncio
async def test_lead_not_dialable_skip_survives_a_failed_job_status_write():
    w = _worker()
    w._stale_payload_reason = AsyncMock(return_value="lead_not_dialable")
    w._update_job_status = AsyncMock(side_effect=RuntimeError("0 rows"))
    await w.process_job(_job())

    # Must not fall into the generic failure boundary (it would write the
    # removed lead back to 'pending' and schedule a retry for it).
    w._update_lead_status.assert_not_awaited()
    w.queue_service.schedule_retry.assert_not_awaited()
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_job_state_defers_and_never_dials():
    w = _worker()
    w._stale_payload_reason = AsyncMock(
        side_effect=ComplianceLookupUnavailable("blip")
    )
    await w.process_job(_job())

    w._create_call_intent.assert_not_awaited()
    w._make_call.assert_not_awaited()
    w.queue_service.mark_skipped.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 30


@pytest.mark.asyncio
async def test_a_live_job_and_dialable_lead_still_dial():
    w = _worker()
    await w.process_job(_job())
    w._stale_payload_reason.assert_awaited_once()
    w._make_call.assert_awaited_once()
    w.queue_service.mark_skipped.assert_not_awaited()


# ═══ 3. concurrency is THIS tenant's, not the platform's ═════════════════


@pytest.mark.asyncio
async def test_tenant_live_count_is_scoped_to_the_tenant_and_connected_calls():
    w = DialerWorker()
    conn = _conn_pool(w, fetchval=7)
    assert await w._tenant_live_call_count(TENANT_A) == 7

    sql = conn.fetchval.call_args.args[0]
    assert "tenant_id = $1::uuid" in sql
    assert "'answered'" in sql and "'in_call'" in sql
    assert "NOT is_test" in sql
    assert "make_interval" in sql  # recency bound
    assert conn.fetchval.call_args.args[1] == TENANT_A


@pytest.mark.asyncio
async def test_tenant_live_count_is_none_when_it_cannot_be_read():
    w = DialerWorker()
    _conn_pool(w, error=RuntimeError("db down"))
    assert await w._tenant_live_call_count(TENANT_A) is None


@pytest.mark.asyncio
async def test_the_gate_receives_the_tenants_own_count_not_the_platform_set():
    fakeredis = pytest.importorskip("fakeredis")
    from app.domain.services import global_concurrency as gc

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    # Ten live calls on the PLATFORM (another tenant's): the old gate read this.
    await redis.sadd(gc._ACTIVE_SET_KEY, *[f"call-{i}" for i in range(10)])

    w = _worker()
    w._redis = redis
    w._tenant_live_call_count = AsyncMock(return_value=2)
    await w.process_job(_job(tenant=TENANT_B))

    kwargs = w.rules_engine.can_make_call.call_args.kwargs
    assert kwargs["active_calls_override"] == 2
    w._tenant_live_call_count.assert_awaited_once_with(TENANT_B)


@pytest.mark.asyncio
async def test_unavailable_tenant_count_keeps_the_in_memory_fallback():
    w = _worker()
    w._tenant_live_call_count = AsyncMock(return_value=None)
    await w.process_job(_job())
    assert w.rules_engine.can_make_call.call_args.kwargs["active_calls_override"] is None


@pytest.mark.asyncio
async def test_one_tenants_ten_live_calls_do_not_block_another_tenant():
    """End to end through the REAL rules engine: tenant A is at its cap of 10,
    tenant B (zero live calls) must still dial."""
    fakeredis = pytest.importorskip("fakeredis")
    from app.domain.services import global_concurrency as gc

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    await redis.sadd(gc._ACTIVE_SET_KEY, *[f"call-{i}" for i in range(10)])
    live = {TENANT_A: 10, TENANT_B: 0}

    def _make(tenant: str) -> DialerWorker:
        w = _worker()
        del w.rules_engine.can_make_call  # use the real engine
        w._redis = redis
        w._get_tenant_rules = AsyncMock(
            return_value=CallingRules(
                time_window_start="00:00",
                time_window_end="23:59",
                allowed_days=[0, 1, 2, 3, 4, 5, 6],
                max_concurrent_calls=10,
                timezone="UTC",
            )
        )
        w._tenant_live_call_count = AsyncMock(return_value=live[tenant])
        return w

    blocked = _make(TENANT_A)
    await blocked.process_job(_job(tenant=TENANT_A))
    blocked._make_call.assert_not_awaited()
    assert BlockCode.MAX_CONCURRENT_CALLS in _published_codes(blocked)

    other = _make(TENANT_B)
    await other.process_job(_job(tenant=TENANT_B))
    other._make_call.assert_awaited_once()


# ═══ 5. a refused line must not burn the lead ════════════════════════════


def _line_refusal(w: DialerWorker, reason: str | None = "selected_trunk_not_ready"):
    """The bridge answered 422 tenant_pbx_required (envelope shape)."""
    details = {"reason": reason} if reason else None

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 422
        w._last_bridge_body = json.dumps(
            {
                "error": {
                    "code": "tenant_pbx_required",
                    "message": "not ready",
                    "details": details,
                }
            }
        )
        w._last_bridge_defer = ("tenant_pbx_required", 120)
        return DialerWorker._BRIDGE_TRANSIENT_REJECT

    w._make_call = fake_make_call


@pytest.mark.asyncio
async def test_line_refusal_hold_publishes_a_dedicated_line_not_ready_reason():
    w = _worker()
    w._held_intent_has_headroom = AsyncMock(return_value=True)
    _line_refusal(w, "trunk_caller_id_not_verified")
    await w.process_job(_job())

    codes = _published_codes(w)
    assert BlockCode.LINE_NOT_READY in codes
    assert BlockCode.ORIGINATE_FAILED not in codes
    message = w._publish_reason.call_args.args[1].message.lower()
    assert "caller id" in message
    # Still the SAME attempt, held 120 s.
    w.queue_service.schedule_retry.assert_not_awaited()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 120


@pytest.mark.asyncio
async def test_expired_line_refusal_does_not_consume_the_attempt_or_fail_the_lead():
    w = _worker()
    w._held_intent_has_headroom = AsyncMock(return_value=False)  # hold expired
    _line_refusal(w)
    job = _job(attempt=2)
    await w.process_job(job)

    # Not a failure of the lead and not an attempt: nothing is classified,
    # retried, failed or persisted.
    assert job.attempt_number == 2
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w._persist_job_attempt_number.assert_not_awaited()
    w._mark_call_intent_not_originated.assert_not_awaited()
    w._record_job_failure_classification.assert_not_awaited()
    assert "failed" not in [c.args[1] for c in w._update_lead_status.call_args_list]
    assert JobStatus.FAILED not in [
        c.args[1] for c in w._update_job_status.call_args_list
    ]
    # The never-sent intent is released as 'line_not_ready' ...
    w._release_intent_line_not_ready.assert_awaited_once()
    # ... and the SAME attempt is re-deferred with a longer, bounded wait.
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 300
    assert w._update_job_status.call_args.kwargs["reason"] == "line_not_ready"
    assert BlockCode.LINE_NOT_READY in _published_codes(w)


@pytest.mark.asyncio
async def test_expired_line_refusal_on_a_replay_is_handled_the_same_way():
    w = _worker()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._held_intent_has_headroom = AsyncMock(return_value=False)
    _line_refusal(w, "no_own_trunk")
    job = _job()
    await w.process_job(job)

    assert job.attempt_number == 1
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w._release_intent_line_not_ready.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 300


@pytest.mark.asyncio
async def test_line_refusal_expiry_never_tight_loops():
    for attempt in (1, 2, 4, 7):
        w = _worker()
        w._held_intent_has_headroom = AsyncMock(return_value=False)
        _line_refusal(w)
        await w.process_job(_job(attempt=attempt))
        assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] >= 300
        w.queue_service.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_unprovable_release_parks_the_attempt_instead_of_guessing():
    w = _worker()
    w._held_intent_has_headroom = AsyncMock(return_value=False)
    w._release_intent_line_not_ready = AsyncMock(return_value=False)
    _line_refusal(w)
    await w.process_job(_job())

    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_expired_holds_still_go_through_the_classifier():
    """The throttle hold is a different animal: it keeps its capped retry."""
    w = _worker()
    w._held_intent_has_headroom = AsyncMock(return_value=False)

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 429
        w._last_bridge_body = json.dumps(
            {"error": {"code": "call_throttled", "message": "m"}}
        )
        w._last_bridge_defer = ("call_throttled", 60)
        return DialerWorker._BRIDGE_TRANSIENT_REJECT

    w._make_call = fake_make_call
    await w.process_job(_job())
    w._release_intent_line_not_ready.assert_not_awaited()
    w._mark_call_intent_not_originated.assert_awaited_once()
    w.queue_service.schedule_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_helper_is_tenant_scoped_proves_absence_and_frees_the_attempt():
    w = DialerWorker()
    conn = _conn_pool(w)
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    assert await w._release_intent_line_not_ready(_job(attempt=3), intent) is True

    sql = conn.execute.call_args.args[0]
    assert "tenant_id = $2::uuid" in sql
    assert "provider_call_id IS NULL" in sql and "external_call_uuid IS NULL" in sql
    assert "status IN ('queued', 'initiated')" in sql
    assert "'line_not_ready'" in sql
    assert "status = 'cancelled'" in sql and "status = 'failed'" not in sql
    # Frees (job, attempt) for the fresh intent the re-deferred pass creates,
    # and keeps the recovery sweep (terminal_settled_at IS NULL) away from it.
    assert "dialer_attempt_number = NULL" in sql
    assert "terminal_settled_at" in sql
    assert conn.execute.call_args.args[1:] == (
        "call-1",
        TENANT_A,
        _job().job_id,
        3,
    )


@pytest.mark.asyncio
async def test_release_helper_reports_false_when_the_row_no_longer_proves_absence():
    w = DialerWorker()
    conn = _conn_pool(w)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    assert await w._release_intent_line_not_ready(_job(), intent) is False

    w2 = DialerWorker()
    _conn_pool(w2, error=RuntimeError("db down"))
    assert await w2._release_intent_line_not_ready(_job(), intent) is False


# ═══ 6. settled before PROCESSING: do not resurrect the job / lead ═══════


@pytest.mark.asyncio
async def test_call_that_settled_first_is_not_overwritten_with_calling_processing():
    w = _worker()
    w._attempt_call_settled = AsyncMock(return_value=True)
    await w.process_job(_job())

    w._make_call.assert_awaited_once()
    assert "calling" not in [c.args[1] for c in w._update_lead_status.call_args_list]
    assert JobStatus.PROCESSING not in [
        c.args[1] for c in w._update_job_status.call_args_list
    ]


@pytest.mark.asyncio
async def test_live_call_still_records_calling_and_processing():
    w = _worker()
    job = _job()
    await w.process_job(job)

    w._update_lead_status.assert_any_await(job, "calling")
    args, kwargs = w._update_job_status.call_args
    assert args[1] is JobStatus.PROCESSING and kwargs["call_id"] == "call-1"


@pytest.mark.asyncio
async def test_replayed_attempt_that_settled_first_is_not_overwritten_either():
    w = _worker()
    claimed = DialerCallIntent("call-1", "tk-1", "leg-1", "dialing", "prov-1", False)
    w._load_existing_call_intent = AsyncMock(return_value=claimed)
    w._attempt_call_settled = AsyncMock(return_value=True)
    w._make_call = AsyncMock(return_value="prov-1")
    await w.process_job(_job())

    assert "calling" not in [c.args[1] for c in w._update_lead_status.call_args_list]
    assert JobStatus.PROCESSING not in [
        c.args[1] for c in w._update_job_status.call_args_list
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,expected",
    [
        ({"status": "failed", "terminal_settled_at": None}, True),
        ({"status": "ended", "terminal_settled_at": None}, True),
        ({"status": "no_answer", "terminal_settled_at": None}, True),
        ({"status": "rejected", "terminal_settled_at": None}, True),
        ({"status": "dialing", "terminal_settled_at": "2026-10-16T10:00:00Z"}, True),
        ({"status": "dialing", "terminal_settled_at": None}, False),
        ({"status": "ringing", "terminal_settled_at": None}, False),
        (None, False),
    ],
)
async def test_attempt_call_settled_truth_table(row, expected):
    w = DialerWorker()
    conn = _conn_pool(w, fetchrow=row)
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "dialing", "prov-1", False)
    assert await w._attempt_call_settled(_job(attempt=2), intent) is expected

    sql = conn.fetchrow.call_args.args[0]
    assert "tenant_id = $2::uuid" in sql and "dialer_attempt_number = $4" in sql


@pytest.mark.asyncio
async def test_attempt_call_settled_fails_open_to_the_previous_behaviour():
    w = DialerWorker()
    _conn_pool(w, error=RuntimeError("db down"))
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "dialing", "prov-1", False)
    assert await w._attempt_call_settled(_job(), intent) is False


# ═══ lead review fixes (integration pass) ════════════════════════════════


@pytest.mark.asyncio
async def test_paused_campaign_with_a_terminalised_intent_ends_the_job_instead_of_holding():
    # Paused while a never-claimed intent exists: holding the SAME attempt
    # would replay into the settled-failed path and burn the lead while the
    # campaign is paused. The intent is terminalised and the job ends.
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value="paused")
    w._load_existing_call_intent = AsyncMock(
        return_value=DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    )
    await w.process_job(_job())

    w._mark_call_intent_not_originated.assert_awaited_once()
    w.queue_service._redefer_inflight.assert_not_awaited()
    w.queue_service.mark_skipped.assert_awaited_once()
    assert w.queue_service.mark_skipped.call_args.kwargs["reason"] == "campaign_not_runnable"
    assert JobStatus.SKIPPED in [c.args[1] for c in w._update_job_status.call_args_list]
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_hold_drops_a_removed_contact_instead_of_writing_it_back_to_pending():
    w = _worker()
    w._get_campaign_status = AsyncMock(return_value="paused")
    w._stale_payload_reason = AsyncMock(return_value="lead_not_dialable")
    w._drop_stale_payload = AsyncMock()
    await w.process_job(_job())

    w._drop_stale_payload.assert_awaited_once()
    assert w._drop_stale_payload.call_args.args[1] == "lead_not_dialable"
    w.queue_service._redefer_inflight.assert_not_awaited()
    assert "pending" not in [c.args[1] for c in w._update_lead_status.call_args_list]


@pytest.mark.asyncio
async def test_out_of_minutes_keeps_the_job_active_so_a_top_up_resumes_it():
    # A SKIPPED write here made the stale-payload gate drop the lead for good
    # on the next dequeue, so the campaign never dialled it after a top-up.
    w = _worker()
    w._tenant_minutes_exhausted = AsyncMock(return_value=True)
    w._emit_out_of_minutes_event = AsyncMock()
    await w.process_job(_job())

    w.queue_service.mark_skipped.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    statuses = [c.args[1] for c in w._update_job_status.call_args_list]
    assert statuses == [JobStatus.RETRY_SCHEDULED]
    assert w._update_job_status.call_args.kwargs["reason"] == "out_of_minutes"
    w._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_out_of_minutes_payload_survives_in_the_scheduled_set():
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    queue = DialerQueueService(redis_client=redis)
    await queue.initialize()
    job = _job()
    assert await queue.enqueue_job(job)
    dequeued = await queue.dequeue_job(tenant_ids=[TENANT_A])
    w = _worker()
    w.queue_service = queue
    w._tenant_minutes_exhausted = AsyncMock(return_value=True)
    w._emit_out_of_minutes_event = AsyncMock()
    await w.process_job(dequeued)

    assert await redis.zcard(DialerQueueService.SCHEDULED_ZSET) == 1
    assert await redis.llen(DialerQueueService.INFLIGHT_LIST) == 0
    assert JobStatus.SKIPPED not in [c.args[1] for c in w._update_job_status.call_args_list]


@pytest.mark.asyncio
async def test_daily_lead_cap_does_not_count_intents_released_while_the_line_was_refused():
    w = DialerWorker()
    conn = _conn_pool(w, fetchval=2)
    assert await w._get_lead_attempts_today("lead-1") == 2
    sql = conn.fetchval.call_args.args[0]
    assert "line_not_ready" in sql and "provider_call_id IS NULL" in sql


def test_tenant_config_load_error_is_a_short_transient_guard_hold():
    from types import SimpleNamespace

    from app.workers.dialer_worker import _classify_guard_denial

    result = SimpleNamespace(check_results=[
        SimpleNamespace(check=SimpleNamespace(value="tenant_active"), passed=False,
                        reason="configuration_load_error"),
    ])
    assert _classify_guard_denial(result) == ("guard_check_unavailable", 60)


@pytest.mark.asyncio
@pytest.mark.parametrize("over_budget,headroom", [(True, True), (False, False)])
async def test_long_telephony_outage_keeps_the_attempt_instead_of_burning_it(over_budget, headroom):
    # The phone system (not the lead) is down past the hold window: release the
    # never-sent intent and wait on the SAME attempt, like a refused line.
    w = _worker()
    w._pipeline_hold_reason = MagicMock(return_value="telephony_unavailable")
    w._pipeline_unavailable_defer_count = AsyncMock(return_value=999 if over_budget else 1)
    w._held_intent_has_headroom = AsyncMock(return_value=headroom)
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)

    await w._defer_pipeline_unavailable(_job(), CallingRules.default(), intent)

    w._release_intent_line_not_ready.assert_awaited_once()
    assert w._release_intent_line_not_ready.call_args.kwargs["reason"] == "telephony_unavailable"
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 300
    assert BlockCode.TELEPHONY_UNAVAILABLE in _published_codes(w)


@pytest.mark.asyncio
async def test_a_real_voice_pipeline_outage_past_its_budget_still_goes_to_the_classifier():
    w = _worker()
    w._pipeline_hold_reason = MagicMock(return_value="voice_pipeline_unavailable")
    w._pipeline_unavailable_defer_count = AsyncMock(return_value=999)
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)

    with pytest.raises(RuntimeError):
        await w._defer_pipeline_unavailable(_job(), CallingRules.default(), intent)
    w._release_intent_line_not_ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_helper_names_a_telephony_outage():
    w = DialerWorker()
    conn = _conn_pool(w)
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    assert await w._release_intent_line_not_ready(
        _job(), intent, reason="telephony_unavailable") is True
    sql = conn.execute.call_args.args[0]
    assert "'telephony_unavailable'" in sql and "'line_not_ready'" not in sql
