"""Attempt accounting in the dialer worker.

outbound-telephony-dialer-7  voice-pipeline-unavailable (503) logged "without
    consuming attempt budget" but called ``schedule_retry`` (which owns the
    increment), minted a new failed ``calls`` row per minute and had no bound.
outbound-telephony-dialer-2  pre-answer retries advanced ``attempt_number`` in
    Redis only, so ``dialer_jobs.attempt_number`` lagged and every
    attempt-guarded settlement missed from attempt 2 on.
outbound-telephony-dialer-4  a bridge-settled failed origination (call failed,
    job failed, lead pending) was parked, then dropped as
    ``duplicate_terminal_attempt`` on replay: no retry, lead stranded.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob, JobStatus
from app.workers.dialer_worker import DialerCallIntent, DialerWorker


def _job(attempt: int = 1) -> DialerJob:
    j = DialerJob(
        job_id="job-724",
        campaign_id="campaign-724",
        lead_id="lead-724",
        tenant_id="tenant-724",
        phone_number="+15551234567",
    )
    j.attempt_number = attempt
    return j


def _base_worker(order: list | None = None) -> DialerWorker:
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
    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._update_job_status = AsyncMock()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    w._create_call_intent = AsyncMock(return_value=intent)
    w._mark_call_intent_not_originated = AsyncMock(return_value=True)
    w._park_uncertain_origination = AsyncMock()
    w._update_lead_status = AsyncMock()
    w._publish_reason = AsyncMock()
    w._record_job_failure_classification = AsyncMock()
    w._emit_unknown_bridge_code_alert = AsyncMock()
    w._persist_job_attempt_number = AsyncMock()
    w._attempt_settled_failed_unanswered = AsyncMock(return_value=False)
    if order is not None:
        async def _persisted(job):
            order.append(("persist", job.attempt_number))

        async def _scheduled(job, delay_seconds=0):
            order.append(("schedule", job.attempt_number))
            job.attempt_number += 1  # the real queue service owns the bump
            return True

        w._persist_job_attempt_number = AsyncMock(side_effect=_persisted)
        w.queue_service.schedule_retry = AsyncMock(side_effect=_scheduled)
    return w


# ═══ dialer-7: pipeline unavailable holds the SAME attempt ═══════════


@pytest.mark.asyncio
async def test_pipeline_unavailable_holds_the_same_attempt(monkeypatch):
    w = _base_worker()
    w._make_call = AsyncMock(return_value=DialerWorker._PIPELINE_UNAVAILABLE)
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

    assert job.attempt_number == 1
    w.queue_service.schedule_retry.assert_not_awaited()
    # The committed intent must stay reusable: terminalising it would mint a
    # failed calls row per outage minute and block the same-attempt replay.
    w._mark_call_intent_not_originated.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] == 60
    _, status_kwargs = w._update_job_status.call_args
    assert status_kwargs.get("reason") == "voice_pipeline_unavailable"
    assert released == [job.tenant_id]


@pytest.mark.asyncio
async def test_replayed_attempt_pipeline_unavailable_also_holds_the_same_attempt():
    w = _base_worker()
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._make_call = AsyncMock(return_value=DialerWorker._PIPELINE_UNAVAILABLE)
    job = _job()
    await w.process_job(job)

    assert job.attempt_number == 1
    w.queue_service.schedule_retry.assert_not_awaited()
    w._mark_call_intent_not_originated.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_hold_is_bounded_then_goes_through_the_classifier(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    monkeypatch.setenv("DIALER_PIPELINE_UNAVAILABLE_MAX_DEFERS", "2")
    order: list = []
    w = _base_worker(order)
    w._redis = fakeredis.FakeAsyncRedis()

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 503
        # A VOICE-PIPELINE 503 (TTS/STT warmup). A phone-system 503 such as
        # pod_draining is now a telephony outage that waits on the same attempt
        # without consuming it (see test_w1_dialer_job_lifecycle.py).
        w._last_bridge_body = json.dumps({"error": {"code": "voice_pipeline_unavailable", "message": "m"}})
        return DialerWorker._PIPELINE_UNAVAILABLE

    w._make_call = fake_make_call

    async def fake_claim(redis, tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", fake_claim
    )
    job = _job()
    for _ in range(2):
        await w.process_job(job)
    assert w.queue_service.schedule_retry.await_count == 0
    assert w.queue_service._redefer_inflight.await_count == 2

    await w.process_job(job)  # third 503 on the same attempt: bound exceeded
    assert w.queue_service._redefer_inflight.await_count == 2
    assert w.queue_service.schedule_retry.await_count == 1  # classifier retry (transient)
    assert job.attempt_number == 2


# ═══ dialer-2: Postgres attempt_number follows the Redis bump ════════


def _bridge_failure(w, status=500, body=""):
    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = status
        w._last_bridge_body = body
        return None

    w._make_call = fake_make_call


@pytest.mark.asyncio
async def test_retry_persists_the_bumped_attempt_before_scheduling():
    order: list = []
    w = _base_worker(order)
    _bridge_failure(w, status=502)  # 5xx -> transient -> retry is due
    job = _job(attempt=1)
    await w.process_job(job)

    assert order == [("persist", 2), ("schedule", 1)], order
    assert job.attempt_number == 2  # Redis payload and Postgres both at 2


@pytest.mark.asyncio
async def test_failed_persist_never_schedules_a_lagging_payload():
    order: list = []
    w = _base_worker(order)
    w._persist_job_attempt_number = AsyncMock(side_effect=RuntimeError("db down"))

    async def fake_make_call(j, rules, *, call_intent=None):
        w._last_bridge_http_status = 502
        w._last_bridge_body = ""
        return None

    w._make_call = fake_make_call
    job = _job(attempt=1)
    await w.process_job(job)

    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()  # same-attempt fallback
    assert job.attempt_number == 1


@pytest.mark.asyncio
async def test_redis_schedule_failure_reverts_the_persisted_attempt():
    w = _base_worker()
    executed = []

    class _Conn:
        async def execute(self, sql, *params):
            executed.append((sql, params))
            return "UPDATE 1"

    @asynccontextmanager
    async def _acquire():
        yield _Conn()

    w._acquire_db = _acquire
    w.queue_service.schedule_retry = AsyncMock(return_value=False)

    async def fake_make_call(j, rules, *, call_intent=None):
        w._last_bridge_http_status = 502
        w._last_bridge_body = ""
        return None

    w._make_call = fake_make_call
    job = _job(attempt=1)
    await w.process_job(job)

    assert job.attempt_number == 1
    assert len(executed) == 1
    sql, params = executed[0]
    assert "attempt_number = $6" in sql and "tenant_id = $3::uuid" in sql
    assert params[0] == 1 and params[-1] == 2
    w.queue_service._redefer_inflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_helper_is_tenant_scoped_monotonic_and_strict():
    w = DialerWorker()
    seen = {}

    class _Conn:
        def __init__(self, result):
            self._result = result

        async def execute(self, sql, *params):
            seen["sql"], seen["params"] = sql, params
            return self._result

    def _pool(result):
        @asynccontextmanager
        async def _acquire():
            yield _Conn(result)

        return _acquire

    job = _job(attempt=3)
    w._acquire_db = _pool("UPDATE 1")
    await w._persist_job_attempt_number(job)
    assert "tenant_id = $3::uuid" in seen["sql"]
    assert "attempt_number, 0) <= $1" in seen["sql"]  # never moves backwards

    w._acquire_db = _pool("UPDATE 0")
    with pytest.raises(RuntimeError):
        await w._persist_job_attempt_number(job)


# ═══ dialer-4: a bridge-settled failure is retried, not parked/dropped ═


@pytest.mark.asyncio
async def test_bridge_settled_failed_origination_is_retried_not_parked():
    order: list = []
    w = _base_worker(order)
    w._mark_call_intent_not_originated = AsyncMock(return_value=False)  # provider id stamped
    w._attempt_settled_failed_unanswered = AsyncMock(return_value=True)

    async def fake_make_call(j, rules, *, call_intent=None):
        w._last_bridge_http_status = 500
        w._last_bridge_body = ""
        return None

    w._make_call = fake_make_call
    job = _job(attempt=1)
    await w.process_job(job)

    w._park_uncertain_origination.assert_not_awaited()
    assert order == [("persist", 2), ("schedule", 1)], order
    statuses = [c.args[1] for c in w._update_job_status.call_args_list]
    assert JobStatus.RETRY_SCHEDULED in statuses
    w._update_lead_status.assert_any_await(job, "pending")


@pytest.mark.asyncio
async def test_unsettled_failure_with_a_stamped_provider_stays_parked():
    w = _base_worker()
    w._mark_call_intent_not_originated = AsyncMock(return_value=False)
    w._attempt_settled_failed_unanswered = AsyncMock(return_value=False)

    async def fake_make_call(j, rules, *, call_intent=None):
        w._last_bridge_http_status = 500
        w._last_bridge_body = ""
        return None

    w._make_call = fake_make_call
    await w.process_job(_job())

    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service.schedule_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_replay_of_a_settled_failed_attempt_retries():
    order: list = []
    w = _base_worker(order)
    failed = DialerCallIntent("call-1", "tk-1", "leg-1", "failed", "prov-1", False)
    w._load_existing_call_intent = AsyncMock(return_value=failed)
    w._mark_call_intent_not_originated = AsyncMock(return_value=False)
    w._attempt_settled_failed_unanswered = AsyncMock(return_value=True)
    w._make_call = AsyncMock()
    job = _job(attempt=1)
    await w.process_job(job)

    w.queue_service.mark_completed.assert_not_awaited()
    w._park_uncertain_origination.assert_not_awaited()
    w._make_call.assert_not_awaited()  # never re-originates the settled attempt
    assert order == [("persist", 2), ("schedule", 1)], order


@pytest.mark.asyncio
async def test_terminal_replay_of_a_finalized_attempt_is_still_dropped():
    w = _base_worker()
    ended = DialerCallIntent("call-1", "tk-1", "leg-1", "ended", "prov-1", False)
    w._load_existing_call_intent = AsyncMock(return_value=ended)
    w._attempt_settled_failed_unanswered = AsyncMock(return_value=False)
    w._make_call = AsyncMock()
    await w.process_job(_job())

    w.queue_service.mark_completed.assert_awaited_once()
    assert (
        w.queue_service.mark_completed.call_args.kwargs["outcome"]
        == "duplicate_terminal_attempt"
    )
    w.queue_service.schedule_retry.assert_not_awaited()


def _settled_row(**over):
    row = {
        "call_status": "failed",
        "answered_at": None,
        "job_status": "failed",
        "last_outcome": None,
        "job_attempt": 1,
    }
    row.update(over)
    return row


def _worker_returning(row):
    w = DialerWorker()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)

    @asynccontextmanager
    async def _acquire():
        yield conn

    w._acquire_db = _acquire
    return w, conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "over,expected",
    [
        ({}, True),
        ({"job_status": "retry_scheduled"}, True),
        ({"job_status": "processing"}, True),
        # answered -> a person was reached: never redial
        ({"answered_at": "2026-10-10T10:00:00Z"}, False),
        # the finalizer ran (it owns the retry decision, incl. the cap)
        ({"last_outcome": "no_answer"}, False),
        # a later attempt already exists
        ({"job_attempt": 2}, False),
        ({"call_status": "ended"}, False),
        ({"call_status": "completed"}, False),
        ({"call_status": "no_answer"}, False),
        ({"call_status": "dialing"}, False),
        ({"job_status": "completed"}, False),
        ({"job_status": "cancelled"}, False),
        ({"job_status": "non_retryable"}, False),
    ],
)
async def test_settled_failed_unanswered_truth_table(over, expected):
    w, conn = _worker_returning(_settled_row(**over))
    assert await w._attempt_settled_failed_unanswered(_job(attempt=1)) is expected
    sql = conn.fetchrow.call_args.args[0]
    assert "c.tenant_id = $1::uuid" in sql  # tenant predicate


@pytest.mark.asyncio
async def test_settled_check_is_false_when_there_is_no_row_or_the_db_errors():
    w, _ = _worker_returning(None)
    assert await w._attempt_settled_failed_unanswered(_job()) is False

    w = DialerWorker()
    w._db_pool = None
    assert await w._attempt_settled_failed_unanswered(_job()) is False


# ═══ reviewer additions ═══════════════════════════════════════════════
# A same-attempt hold leaves a committed, never-claimed intent behind. Two
# things follow from that and were not covered:
#   * reap_stuck_calls flips any pre-answer row older than 600 s to
#     'termination_pending'; a provider-less row is never settled by the
#     telephony owner, so a hold that outlives the reaper wedges the job
#     (the bridge answers 409 origination_cleanup_pending on every replay).
#   * the replay path skips the new-call gates, so a hold that straddles the
#     end of the calling window would otherwise dial outside it.


def _held_intent() -> DialerCallIntent:
    return DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, False)


def _transient_reject(w, status=429, code="call_throttled", hold=60):
    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = status
        w._last_bridge_body = json.dumps({"error": {"code": code, "message": "m"}})
        w._last_bridge_defer = (code, hold)
        return DialerWorker._BRIDGE_TRANSIENT_REJECT

    w._make_call = fake_make_call


@pytest.mark.asyncio
async def test_pipeline_hold_past_the_reaper_ceiling_is_written_off_not_held():
    """Past the ceiling the attempt goes through the classifier (attempt N+1)
    instead of being held on a row the reaper is about to strand."""
    order: list = []
    w = _base_worker(order)
    w._held_intent_has_headroom = AsyncMock(return_value=False)

    async def fake_make_call(job, rules, *, call_intent=None):
        w._last_bridge_http_status = 503
        # A VOICE-PIPELINE 503 (TTS/STT warmup). A phone-system 503 such as
        # pod_draining is now a telephony outage that waits on the same attempt
        # without consuming it (see test_w1_dialer_job_lifecycle.py).
        w._last_bridge_body = json.dumps({"error": {"code": "voice_pipeline_unavailable", "message": "m"}})
        return DialerWorker._PIPELINE_UNAVAILABLE

    w._make_call = fake_make_call
    job = _job()
    await w.process_job(job)

    w.queue_service._redefer_inflight.assert_not_awaited()  # not held again
    w._mark_call_intent_not_originated.assert_awaited_once()  # provider-null CAS writes it off
    assert order == [("persist", 2), ("schedule", 1)], order
    assert job.attempt_number == 2


@pytest.mark.asyncio
async def test_throttle_hold_past_the_reaper_ceiling_is_written_off_not_held():
    order: list = []
    w = _base_worker(order)
    w._held_intent_has_headroom = AsyncMock(return_value=False)
    _transient_reject(w)
    job = _job()
    await w.process_job(job)

    w.queue_service._redefer_inflight.assert_not_awaited()
    w._mark_call_intent_not_originated.assert_awaited_once()
    assert order == [("persist", 2), ("schedule", 1)], order
    _, kw = w._record_job_failure_classification.call_args
    assert kw["reason"] == "call_throttled" and kw["category"] == "transient_network"


@pytest.mark.asyncio
async def test_replayed_hold_past_the_reaper_ceiling_is_written_off_not_held():
    """A THROTTLE hold that outlives the reaper still goes through the
    classifier (attempt N+1). The refused-line (422 tenant_pbx_required) hold
    used to share this route and burned every lead of the campaign; its
    contract is pinned separately below and in test_w1_dialer_job_lifecycle.
    """
    order: list = []
    w = _base_worker(order)
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._held_intent_has_headroom = AsyncMock(return_value=False)
    _transient_reject(w, status=429, code="call_throttled", hold=60)
    await w.process_job(_job())

    w.queue_service._redefer_inflight.assert_not_awaited()
    assert order == [("persist", 2), ("schedule", 1)], order
    _, kw = w._record_job_failure_classification.call_args
    assert kw["reason"] == "call_throttled"


@pytest.mark.asyncio
async def test_replayed_refused_line_past_the_reaper_ceiling_keeps_the_attempt():
    """tenant_pbx_required (no ready line / unverified caller ID) is not a
    failure of the lead: the expired hold releases the never-sent intent and
    re-defers the SAME attempt, never persisting or scheduling attempt N+1."""
    order: list = []
    w = _base_worker(order)
    w._release_intent_line_not_ready = AsyncMock(return_value=True)
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._held_intent_has_headroom = AsyncMock(return_value=False)
    _transient_reject(w, status=422, code="tenant_pbx_required", hold=120)
    job = _job()
    await w.process_job(job)

    assert order == [], order  # no attempt persisted, none scheduled
    assert job.attempt_number == 1
    w._release_intent_line_not_ready.assert_awaited_once()
    w._record_job_failure_classification.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    assert w.queue_service._redefer_inflight.call_args.kwargs["delay_seconds"] == 300


def _age_worker(age=None, error=None):
    w = DialerWorker()
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=error) if error else AsyncMock(return_value=age)

    @asynccontextmanager
    async def _acquire():
        yield conn

    w._acquire_db = _acquire
    return w, conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "age,delay,expected",
    [
        (5, 60, True),      # a fresh hold fits
        (240, 60, True),    # exactly at the ceiling still fits
        (241, 60, False),   # one second over: the next replay would be too old
        (100, 900, False),  # a long retry-after never fits
        (None, 60, True),   # unknown age (NULL created_at): keep the hold
    ],
)
async def test_headroom_compares_intent_age_plus_delay_with_the_ceiling(
    monkeypatch, age, delay, expected
):
    monkeypatch.setenv("DIALER_HELD_INTENT_MAX_AGE_S", "300")
    w, conn = _age_worker(age=age)
    assert await w._held_intent_has_headroom(_job(), _held_intent(), delay) is expected
    sql = conn.fetchval.call_args.args[0]
    assert "tenant_id = $2::uuid" in sql and "created_at" in sql


def test_ceiling_defaults_to_half_the_stuck_call_reaper_horizon(monkeypatch):
    """reap_stuck_calls writes a provider-less pre-answer row off after
    CALL_STUCK_TIMEOUT_S; the hold must end well before that, leaving room for
    the queue latency between the due time and the replay."""
    from app.domain.services.dialer.stuck_job_reaper import CALL_STUCK_TIMEOUT_S

    monkeypatch.delenv("DIALER_HELD_INTENT_MAX_AGE_S", raising=False)
    ceiling = DialerWorker._held_intent_max_age_seconds()
    assert ceiling == max(60, CALL_STUCK_TIMEOUT_S // 2)
    assert ceiling < CALL_STUCK_TIMEOUT_S


@pytest.mark.asyncio
async def test_headroom_keeps_the_hold_when_the_age_cannot_be_read():
    w, _ = _age_worker(error=RuntimeError("db blip"))
    assert await w._held_intent_has_headroom(_job(), _held_intent(), 60) is True


@pytest.mark.asyncio
async def test_headroom_ceiling_is_env_tunable(monkeypatch):
    monkeypatch.setenv("DIALER_HELD_INTENT_MAX_AGE_S", "90")
    w, _ = _age_worker(age=40)
    assert await w._held_intent_has_headroom(_job(), _held_intent(), 60) is False
    assert await w._held_intent_has_headroom(_job(), _held_intent(), 50) is True


# ── replay of a never-claimed attempt must re-check the calling window ──────


def _window(w, open_: bool):
    """Stub the rules-engine oracle the replay gate asks (window only), honouring
    ``enforce_window`` the way the real engine does."""

    async def can_make_call(**kw):
        if open_ or not kw.get("enforce_window", True):
            return True, "all_rules_passed"
        return False, "outside_time_window_09:00_19:00"

    w.rules_engine.can_make_call = AsyncMock(side_effect=can_make_call)


def _dialing_worker():
    w = _base_worker()
    w._bind_call_intent = AsyncMock()
    w._mark_campaign_dialed = AsyncMock()
    w._emit_progress_event_throttled = AsyncMock()
    return w


@pytest.mark.asyncio
async def test_held_attempt_is_not_originated_after_the_calling_window_closed():
    order: list = []
    w = _base_worker(order)
    _window(w, open_=False)
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._make_call = AsyncMock(return_value="prov-1")
    job = _job()
    await w.process_job(job)

    w._make_call.assert_not_awaited()  # the hold must not turn into a dial outside the window
    w._mark_call_intent_not_originated.assert_awaited_once()
    assert order == [("persist", 2), ("schedule", 1)], order
    _, kw = w._record_job_failure_classification.call_args
    assert kw["reason"] == "outside_calling_window"
    # Window only: this attempt's own row must not count against the cooldown /
    # daily cap, so those inputs are withheld from the oracle.
    oracle_kw = w.rules_engine.can_make_call.call_args.kwargs
    assert oracle_kw["lead_last_called"] is None
    assert oracle_kw["lead_attempts_today"] is None
    assert oracle_kw["enforce_window"] is True


@pytest.mark.asyncio
async def test_window_straddle_is_retried_even_on_a_late_attempt():
    """A closed window is not the lead's failure: it must never become a
    terminal 'max attempts for category exhausted' on attempt 4+."""
    order: list = []
    w = _base_worker(order)
    _window(w, open_=False)
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._make_call = AsyncMock()
    await w.process_job(_job(attempt=5))

    w.queue_service.mark_failed.assert_not_awaited()
    assert order == [("persist", 6), ("schedule", 5)], order


@pytest.mark.asyncio
async def test_held_attempt_is_originated_while_the_window_is_open():
    w = _dialing_worker()
    _window(w, open_=True)
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._make_call = AsyncMock(return_value="prov-1")
    await w.process_job(_job())
    w._make_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_claimed_attempt_is_reconciled_without_the_window_gate():
    """The bridge already stamped a provider id: the call may be live, so the
    replay only reconciles it (the bridge never originates it twice)."""
    w = _dialing_worker()
    _window(w, open_=False)
    claimed = DialerCallIntent("call-1", "tk-1", "leg-1", "dialing", "talky-out-1", False)
    w._load_existing_call_intent = AsyncMock(return_value=claimed)
    w._make_call = AsyncMock(return_value="talky-out-1")
    await w.process_job(_job())
    w._make_call.assert_awaited_once()
    w.rules_engine.can_make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_call_anytime_setting_skips_the_replay_window_gate():
    w = _dialing_worker()
    _window(w, open_=False)
    w._get_campaign_calling_config = AsyncMock(return_value={"ignore_schedule": True})
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._make_call = AsyncMock(return_value="prov-1")
    await w.process_job(_job())
    w._make_call.assert_awaited_once()
    assert w.rules_engine.can_make_call.call_args.kwargs["enforce_window"] is False


@pytest.mark.asyncio
async def test_unreadable_lead_timezone_fails_closed_on_the_replay_gate():
    """A failed lead-timezone read must not let the window be judged on a
    fallback zone: the replay stays parked on the SAME attempt."""
    import app.workers.dialer_worker as dw

    w = _dialing_worker()
    _window(w, open_=True)
    w._get_lead_timezone = AsyncMock(side_effect=dw.ComplianceLookupUnavailable("blip"))
    w._load_existing_call_intent = AsyncMock(return_value=_held_intent())
    w._park_uncertain_origination = AsyncMock()
    w._make_call = AsyncMock(return_value="prov-1")
    await w.process_job(_job())
    w._make_call.assert_not_awaited()
    w._park_uncertain_origination.assert_awaited_once()
    w.queue_service.schedule_retry.assert_not_awaited()


# ── dialer-4 safety: never revive a job whose lead already has a new active job ─


@pytest.mark.asyncio
async def test_settled_check_refuses_when_another_active_job_owns_the_lead():
    """Reviving the old (bridge-failed) job while a campaign restart already
    queued a fresh one would dial the lead twice (and trips
    uq_dialer_jobs_one_active_per_lead after the Redis retry is scheduled)."""
    from app.domain.services.dialer.job_states import ACTIVE_STATUSES

    w, conn = _worker_returning(_settled_row())
    assert await w._attempt_settled_failed_unanswered(_job(attempt=1)) is True
    args = conn.fetchrow.call_args.args
    assert "NOT EXISTS" in args[0] and "other_job.id <> j.id" in args[0]
    assert "other_job.tenant_id = c.tenant_id" in args[0]  # tenant predicate
    assert list(ACTIVE_STATUSES) in args  # bound as a parameter
