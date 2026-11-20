"""20 Nov 2026 production defect: the dialler re-checked every queued job every
few seconds (1.8 M log lines, ~2,500 job checks per call placed).

A campaign-wide gate (paused, in-flight cap, call gap) is now decided once per
campaign and remembered in memory; the other jobs of that campaign are only
re-scheduled. Nothing about who may be called or when changes: a job that is
actually checked goes through exactly the old gates.

All hermetic: the worker's collaborators are mocks, the queue test uses fakeredis.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import fakeredis.aioredis as fakeredis
import pytest

from app.domain.models.dialer_job import DialerJob
from app.domain.services.queue_service import DialerQueueService
from app.workers.dialer_campaign_gate_memo import CampaignGateMemo
from tests.unit.test_w1_dialer_job_lifecycle import TENANT_A, TENANT_B, _worker

CAMPAIGN_A = "44444444-4444-4444-4444-444444444444"
CAMPAIGN_B = "66666666-6666-6666-6666-666666666666"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _jobs(n: int, *, campaign: str = CAMPAIGN_A, tenant: str = TENANT_A) -> list[DialerJob]:
    return [
        DialerJob(
            job_id=str(uuid.uuid4()),
            campaign_id=campaign,
            lead_id=str(uuid.uuid4()),
            tenant_id=tenant,
            phone_number="+447700900123",
        )
        for _ in range(n)
    ]


def _w(monkeypatch, clock: Clock | None = None):
    monkeypatch.delenv("DIALER_PAUSE_REDEFER_S", raising=False)
    w = _worker()
    w._gate_memo = CampaignGateMemo(clock=clock or Clock())
    return w


def _delays(w) -> list[int]:
    return [c.kwargs["delay_seconds"] for c in w.queue_service._redefer_inflight.call_args_list]


# ---------------------------------------------------------------- paused ----
async def test_paused_campaign_is_checked_once_for_fifty_jobs(monkeypatch):
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    for job in _jobs(50):
        await w.process_job(job)

    assert w._get_campaign_status.await_count == 1
    # the existing-intent read still runs for every job (a job with an intent
    # never takes the memo path); every other check runs for the first only
    assert w._load_existing_call_intent.await_count == 50
    assert w._stale_payload_reason.await_count == 1
    assert w.queue_service._redefer_inflight.await_count == 50
    assert set(_delays(w)) == {900}  # every job parked to the same time
    w._make_call.assert_not_awaited()


async def test_pause_park_default_is_900_and_env_still_wins(monkeypatch):
    monkeypatch.delenv("DIALER_PAUSE_REDEFER_S", raising=False)
    assert DialerQueueService._paused_park_seconds() == 900
    monkeypatch.setenv("DIALER_PAUSE_REDEFER_S", "120")
    assert DialerQueueService._paused_park_seconds() == 120
    # the generic redefer default (minutes top-up etc.) is untouched
    monkeypatch.delenv("DIALER_PAUSE_REDEFER_S", raising=False)
    assert DialerQueueService._pause_redefer_seconds() == 120


async def test_memo_expires_and_the_next_job_is_checked_again(monkeypatch):
    clock = Clock()
    w = _w(monkeypatch, clock)
    w._get_campaign_status.return_value = "paused"
    a, b, c = _jobs(3)
    await w.process_job(a)
    await w.process_job(b)
    assert w._get_campaign_status.await_count == 1

    clock.now += w.PAUSED_MEMO_FRESH_S + 1
    await w.process_job(c)
    assert w._get_campaign_status.await_count == 2


async def test_state_change_seen_by_a_real_check_lets_the_job_through(monkeypatch):
    clock = Clock()
    w = _w(monkeypatch, clock)
    w._get_campaign_status.return_value = "paused"
    first, second = _jobs(2)
    await w.process_job(first)

    w._get_campaign_status.return_value = "running"
    clock.now += w.PAUSED_MEMO_FRESH_S + 1
    await w.process_job(second)

    w._make_call.assert_awaited_once()  # checked with the old gates, then placed


# ---------------------------------------------------------------- resume ----
async def test_resume_drops_the_memo_and_promotes_exactly_the_parked_jobs(monkeypatch):
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    jobs = _jobs(5)
    for job in jobs:
        await w.process_job(job)
    assert w._gate_memo.lookup(TENANT_A, CAMPAIGN_A) is not None

    # still paused: nothing is promoted
    await w._promote_resumed_campaigns()
    w.queue_service.promote_campaign_parked_jobs.assert_not_awaited()

    w._get_campaign_status.return_value = "running"
    await w._promote_resumed_campaigns()

    call = w.queue_service.promote_campaign_parked_jobs.await_args
    assert call.args[:2] == (TENANT_A, CAMPAIGN_A)
    assert call.args[2] == {str(j.job_id) for j in jobs}
    assert w._gate_memo.lookup(TENANT_A, CAMPAIGN_A) is None
    # the resumed campaign's next job is checked and placed, not parked
    (nxt,) = _jobs(1)
    await w.process_job(nxt)
    w._make_call.assert_awaited_once()


async def test_resume_poll_runs_inside_the_worker_loop_cadence():
    w = _worker()
    assert w.RESUME_POLL_S <= 10  # well inside the 30 s resume promise
    assert w.PAUSED_MEMO_FRESH_S <= 15


async def test_promotion_makes_parked_jobs_due_within_30s_and_touches_nothing_else():
    redis = fakeredis.FakeRedis(decode_responses=True)
    q = DialerQueueService(redis_client=redis)
    q._initialized = True
    now = datetime.now(timezone.utc).timestamp()

    mine = _jobs(3)
    retry = _jobs(1)[0]                       # a real retry, hours away
    other_campaign = _jobs(1, campaign=CAMPAIGN_B)[0]
    other_tenant = _jobs(1, tenant=TENANT_B)[0]  # same campaign id, other tenant
    for j in mine:
        await redis.zadd(q.SCHEDULED_ZSET, {json.dumps(j.to_redis_dict()): now + 900})
    scores = {}
    for j in (retry, other_campaign, other_tenant):
        raw = json.dumps(j.to_redis_dict())
        scores[raw] = now + 3600
        await redis.zadd(q.SCHEDULED_ZSET, {raw: now + 3600})

    promoted = await q.promote_campaign_parked_jobs(
        TENANT_A, CAMPAIGN_A, {str(j.job_id) for j in mine}
    )
    assert promoted == 3
    for raw, score in await redis.zrange(q.SCHEDULED_ZSET, 0, -1, withscores=True):
        data = json.loads(raw)
        if data["job_id"] in {str(j.job_id) for j in mine}:
            assert score - now <= 30
        else:
            assert score == pytest.approx(scores[raw])  # retry / other tenant / other campaign
    assert await q.promote_campaign_parked_jobs(TENANT_A, CAMPAIGN_A, set()) == 0


# -------------------------------------------------------------- in flight ----
async def test_in_flight_cap_checks_the_first_job_only_then_a_freed_slot_places(monkeypatch):
    clock = Clock()
    w = _w(monkeypatch, clock)
    from unittest.mock import AsyncMock, MagicMock

    w._resolve_batch_size = MagicMock(return_value=1)
    w._campaign_inflight_calls = AsyncMock(return_value=1)
    jobs = _jobs(6)
    for job in jobs[:5]:
        await w.process_job(job)

    assert w._campaign_inflight_calls.await_count == 1
    assert w._get_campaign_status.await_count == 2  # top-of-job read + the re-read at the gate (first job only)
    assert w.queue_service._redefer_inflight.await_count == 5
    delays = _delays(w)
    assert delays[0] == 1
    assert all(1 <= d <= 1 + 120 for d in delays[1:])
    w._make_call.assert_not_awaited()

    # a call ends: the slot is free and the memo window (1 s) has passed
    w._campaign_inflight_calls.return_value = 0
    clock.now += 1
    await w.process_job(jobs[5])
    assert w._campaign_inflight_calls.await_count == 2
    w._make_call.assert_awaited_once()


async def test_call_gap_is_decided_once(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    w = _w(monkeypatch)
    w._resolve_call_gap = MagicMock(return_value=60)
    w._campaign_seconds_since_last_dial = AsyncMock(return_value=10)
    for job in _jobs(20):
        await w.process_job(job)
    assert w._campaign_seconds_since_last_dial.await_count == 1
    assert w.queue_service._redefer_inflight.await_count == 20
    w._make_call.assert_not_awaited()


# ------------------------------------------------------------ isolation ----
async def test_a_block_on_one_campaign_never_touches_another_campaign(monkeypatch):
    w = _w(monkeypatch)

    async def status(campaign_id, tenant_id):
        return "paused" if campaign_id == CAMPAIGN_A else "running"

    w._get_campaign_status.side_effect = status
    for job in _jobs(5, campaign=CAMPAIGN_A):
        await w.process_job(job)
    w._make_call.assert_not_awaited()

    (b_job,) = _jobs(1, campaign=CAMPAIGN_B)
    await w.process_job(b_job)
    w._make_call.assert_awaited_once()
    assert w._gate_memo.lookup(TENANT_A, CAMPAIGN_B) is None


async def test_same_campaign_id_in_another_tenant_is_not_blocked(monkeypatch):
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    await w.process_job(_jobs(1)[0])
    assert w._gate_memo.lookup(TENANT_A, CAMPAIGN_A) is not None
    assert w._gate_memo.lookup(TENANT_B, CAMPAIGN_A) is None


async def test_a_restart_behaves_like_the_old_code_for_the_first_job(monkeypatch):
    w = _w(monkeypatch)  # fresh worker, empty memo
    w._get_campaign_status.return_value = "paused"
    await w.process_job(_jobs(1)[0])
    assert w._load_existing_call_intent.await_count == 1
    assert w._get_campaign_status.await_count == 1
    assert w._stale_payload_reason.await_count == 1
    assert _delays(w) == [900]


async def test_a_memo_hit_never_places_a_call_even_for_a_runnable_campaign(monkeypatch):
    """The memo only re-schedules. A job it parks is checked again at its time."""
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    await w.process_job(_jobs(1)[0])
    w._get_campaign_status.return_value = "running"
    await w.process_job(_jobs(1)[0])  # inside the memo window: parked, not placed
    w._make_call.assert_not_awaited()


# ------------------------------------------------------------------ logs ----
async def test_per_job_lines_are_debug_and_the_summary_is_info(monkeypatch, caplog):
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    with caplog.at_level(logging.INFO, logger="app.workers.dialer_worker"):
        for job in _jobs(50):
            await w.process_job(job)
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert not [m for m in info if "Processing job" in m]
    assert not [m for m in info if "dialer_pre_intent_redeferred" in m]
    assert not [m for m in info if "is paused" in m]

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="app.workers.dialer_worker"):
        await w.process_job(_jobs(1)[0])
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("Processing job" in m for m in debug)
    assert any("dialer_pre_intent_redeferred" in m and "campaign_paused" in m for m in debug)

    # not yet due: no summary
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.workers.dialer_worker"):
        w._log_cycle_summary_if_due()
    assert not caplog.records
    # due: exactly one INFO line with the right counts
    w._cycle_started -= 61
    with caplog.at_level(logging.INFO, logger="app.workers.dialer_worker"):
        w._log_cycle_summary_if_due()
        w._log_cycle_summary_if_due()  # same window again: silent
    lines = [r for r in caplog.records if "dialer_cycle_summary" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert lines[0].getMessage() == (
        "dialer_cycle_summary window_s=60 popped=51 placed=0 redeferred_paused=51 "
        "redeferred_call_gap=0 redeferred_capacity=0 other=0"
    )


async def test_summary_counts_a_placed_call(monkeypatch, caplog):
    w = _w(monkeypatch)
    await w.process_job(_jobs(1)[0])
    w._cycle_started -= 61
    with caplog.at_level(logging.INFO, logger="app.workers.dialer_worker"):
        w._log_cycle_summary_if_due()
    assert "popped=1 placed=1 redeferred_paused=0" in caplog.records[-1].getMessage()


async def test_queue_redefer_line_is_debug():
    redis = fakeredis.FakeRedis(decode_responses=True)
    q = DialerQueueService(redis_client=redis)
    q._initialized = True
    job = _jobs(1)[0]
    raw = json.dumps(job.to_redis_dict())
    await redis.hset(q.INFLIGHT_HASH, str(job.job_id), raw)
    await redis.lpush(q.INFLIGHT_LIST, raw)

    import app.domain.services.queue_service as qs

    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    qs.logger.addHandler(_H())
    old = qs.logger.level
    qs.logger.setLevel(logging.DEBUG)
    try:
        assert await q._redefer_inflight(str(job.job_id), "call_gap", delay_seconds=5)
    finally:
        qs.logger.setLevel(old)
        qs.logger.handlers = [h for h in qs.logger.handlers if not isinstance(h, _H)]
    line = [r for r in records if "re-deferred job" in r.getMessage()]
    assert len(line) == 1 and line[0].levelno == logging.DEBUG


# =================== review round 2 (D1 / D2 / D3) ===================
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from app.domain.services.campaign_service import CampaignService  # noqa: E402
from app.workers.dialer_campaign_gate_memo import REASON_CAPACITY, REASON_PAUSED  # noqa: E402
from app.workers.dialer_worker import DialerCallIntent  # noqa: E402
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Query, _Queue  # noqa: E402


def _intent(status: str = "initiated") -> DialerCallIntent:
    return DialerCallIntent("call-1", "tk-1", "leg-1", status, None, True)


# --- D1: a job with an existing call intent never takes the memo path -------
async def test_held_attempt_is_replayed_not_pushed_back_by_a_capacity_memo(monkeypatch):
    w = _w(monkeypatch)
    w._gate_memo.block(TENANT_A, CAMPAIGN_A, REASON_CAPACITY, 1, fresh_s=60)
    w._load_existing_call_intent.return_value = _intent("initiated")
    w._resume_existing_call_intent = AsyncMock()
    (job,) = _jobs(1)

    await w.process_job(job)

    w._resume_existing_call_intent.assert_awaited_once()
    w.queue_service._redefer_inflight.assert_not_awaited()  # the memo did not park it


async def test_paused_campaigns_unsent_intent_is_ended_and_the_lead_freed_as_before(monkeypatch):
    w = _w(monkeypatch)
    w._gate_memo.block(TENANT_A, CAMPAIGN_A, REASON_PAUSED, 900, fresh_s=60)
    w._load_existing_call_intent.return_value = _intent("initiated")
    w._get_campaign_status.return_value = "paused"
    (job,) = _jobs(1)

    await w.process_job(job)

    w._mark_call_intent_not_originated.assert_awaited_once()      # unsent intent ended
    w.queue_service.mark_skipped.assert_awaited_once()            # payload leaves, lead slot freed
    w.queue_service._redefer_inflight.assert_not_awaited()        # NOT parked 900 s with a live intent


# --- a memo hit on a row that is no longer active places nothing ------------
async def test_memo_hit_on_a_cancelled_row_places_no_call_and_moves_no_payload(monkeypatch):
    w = _w(monkeypatch)
    w._gate_memo.block(TENANT_A, CAMPAIGN_A, REASON_CAPACITY, 1, fresh_s=60)
    # the guarded UPDATE refuses a cancelled / newer-attempt row
    w._update_job_status.side_effect = RuntimeError("job row is not active")
    (job,) = _jobs(1)

    await w.process_job(job)

    w._make_call.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_not_awaited()  # DB first: no DB write, no Redis move


# --- D2: parked ids survive clear(), a reason change and a restart ----------
async def test_resume_after_a_placed_call_cleared_the_memo_still_promotes(monkeypatch):
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    jobs = _jobs(4)
    for job in jobs:
        await w.process_job(job)
    w._gate_memo.clear(TENANT_A, CAMPAIGN_A)                       # a call was placed
    w._gate_memo.block(TENANT_A, CAMPAIGN_A, REASON_CAPACITY, 1)   # and another gate fired

    w._get_campaign_status.return_value = "running"
    await w._promote_resumed_campaigns()

    call = w.queue_service.promote_campaign_parked_jobs.await_args
    assert call.args[2] == {str(j.job_id) for j in jobs}
    assert w._gate_memo.parked_campaigns() == []


async def test_poll_forgets_parked_ids_of_a_campaign_that_ended(monkeypatch):
    w = _w(monkeypatch)
    w._get_campaign_status.return_value = "paused"
    await w.process_job(_jobs(1)[0])
    w._get_campaign_status.return_value = "stopped"
    await w._promote_resumed_campaigns()
    w.queue_service.promote_campaign_parked_jobs.assert_not_awaited()
    assert w._gate_memo.parked_campaigns() == []


class _RecQuery(_Query):
    def execute(self):
        if self.table == "dialer_jobs" and self.operation == "select":
            self.db.dialer_selects.append(list(self.filters))
        return super().execute()


class _RecDB(_DB):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.dialer_selects: list = []

    def table(self, name):
        return _RecQuery(self, name)


def _parked_row(job_id: str, *, tenant="tenant-1", campaign="campaign-1", lead="lead-1",
                status="retry_scheduled", reason="campaign_paused"):
    return {"id": job_id, "tenant_id": tenant, "campaign_id": campaign, "lead_id": lead,
            "status": status, "failure_reason": reason, "priority": 5, "attempt_number": 1}


class _PromotingQueue(_Queue):
    def __init__(self, fail: bool = False):
        super().__init__([True] * 20)
        self.fail = fail
        self.promoted: list = []

    async def promote_campaign_parked_jobs(self, tenant_id, campaign_id, job_ids):
        if self.fail:
            raise RuntimeError("redis down")
        self.promoted.append((tenant_id, campaign_id, sorted(job_ids)))
        return len(job_ids)


def _paused_campaign_db() -> _RecDB:
    db = _RecDB(lead_count=2)
    db.tables["campaigns"][0]["status"] = "paused"
    db.tables["dialer_jobs"] = [
        _parked_row("job-1", lead="lead-1"),
        _parked_row("job-2", lead="lead-2"),
        _parked_row("job-other-tenant", tenant="tenant-2"),
        _parked_row("job-other-campaign", campaign="campaign-2"),
        _parked_row("job-real-retry", reason="busy"),
        _parked_row("job-done", status="completed"),
    ]
    return db


async def test_resume_through_start_campaign_promotes_the_parked_jobs_with_a_tenant_predicate():
    db, queue = _paused_campaign_db(), _PromotingQueue()

    await CampaignService(db, queue_service=queue).start_campaign("campaign-1", tenant_id="tenant-1")

    assert queue.promoted == [("tenant-1", "campaign-1", ["job-1", "job-2"])]
    parked_reads = [f for f in db.dialer_selects
                    if ("eq", "failure_reason", "campaign_paused") in f]
    assert len(parked_reads) == 1
    assert ("eq", "tenant_id", "tenant-1") in parked_reads[0]          # explicit tenant predicate
    assert ("eq", "campaign_id", "campaign-1") in parked_reads[0]
    assert ("eq", "status", "retry_scheduled") in parked_reads[0]


async def test_a_failed_promotion_does_not_fail_the_resume(caplog):
    db, queue = _paused_campaign_db(), _PromotingQueue(fail=True)
    with caplog.at_level(logging.WARNING):
        result = await CampaignService(db, queue_service=queue).start_campaign(
            "campaign-1", tenant_id="tenant-1")
    assert result.success is True
    assert db.tables["campaigns"][0]["status"] == "running"
    assert any("not promoted" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("before", ["draft", "stopped", "running"])
async def test_only_a_resume_from_paused_looks_for_parked_jobs(before):
    db, queue = _paused_campaign_db(), _PromotingQueue()
    db.tables["campaigns"][0]["status"] = before
    db.tables["dialer_jobs"] = []
    try:
        await CampaignService(db, queue_service=queue).start_campaign("campaign-1", tenant_id="tenant-1")
    except Exception:
        pass
    assert not [f for f in db.dialer_selects if ("eq", "failure_reason", "campaign_paused") in f]


async def test_resume_after_a_worker_restart_dials_within_30s_through_the_hook():
    """The worker that parked the jobs is gone (empty memo): the resume itself
    moves the parked payloads to 'due now'; a retry hours away is untouched."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    q = DialerQueueService(redis_client=redis)
    q._initialized = True
    q.get_queue_stats = AsyncMock(return_value={})
    q.close = AsyncMock()
    now = datetime.now(timezone.utc).timestamp()
    parked = [("job-1", "lead-1"), ("job-2", "lead-2")]
    for job_id, lead in parked:
        payload = {"job_id": job_id, "campaign_id": "campaign-1", "lead_id": lead,
                   "tenant_id": "tenant-1", "phone_number": "+447700900123"}
        await redis.zadd(q.SCHEDULED_ZSET, {json.dumps(payload): now + 900})
    retry = json.dumps({"job_id": "job-real-retry", "campaign_id": "campaign-1", "lead_id": "lead-9",
                        "tenant_id": "tenant-1", "phone_number": "+447700900123"})
    await redis.zadd(q.SCHEDULED_ZSET, {retry: now + 3600})
    db = _paused_campaign_db()

    await CampaignService(db, queue_service=q).start_campaign("campaign-1", tenant_id="tenant-1")

    scores = {json.loads(r)["job_id"]: sc
              for r, sc in await redis.zrange(q.SCHEDULED_ZSET, 0, -1, withscores=True)}
    assert scores["job-1"] - now <= 30 and scores["job-2"] - now <= 30
    assert scores["job-real-retry"] == pytest.approx(now + 3600)


async def test_promotion_is_batched_and_never_reads_the_whole_scheduled_set():
    redis = fakeredis.FakeRedis(decode_responses=True)
    q = DialerQueueService(redis_client=redis)
    q._initialized = True
    now = datetime.now(timezone.utc).timestamp()
    mine = _jobs(600)
    others = _jobs(300, campaign=CAMPAIGN_B, tenant=TENANT_B)
    members = {json.dumps(j.to_redis_dict()): now + 900 for j in mine + others}
    await redis.zadd(q.SCHEDULED_ZSET, members)

    calls = {"zadd": 0}
    real_zadd = redis.zadd

    async def counting_zadd(*a, **k):
        calls["zadd"] += 1
        return await real_zadd(*a, **k)

    async def no_full_read(*a, **k):
        raise AssertionError("the whole scheduled set must not be read")

    redis.zadd = counting_zadd
    redis.zrange = no_full_read

    promoted = await q.promote_campaign_parked_jobs(
        TENANT_A, CAMPAIGN_A, {str(j.job_id) for j in mine})

    assert promoted == 600
    assert calls["zadd"] == 2  # 500 + 100: batched, not one round trip per job
    redis.zrange = type(redis).zrange.__get__(redis)
    other_scores = [sc for r, sc in await redis.zrange(q.SCHEDULED_ZSET, 0, -1, withscores=True)
                    if json.loads(r)["tenant_id"] == TENANT_B]
    assert len(other_scores) == 300 and all(sc - now > 800 for sc in other_scores)


# --- D3: pace after a call ends ---------------------------------------------
async def test_next_call_is_placed_within_the_capacity_window_after_a_call_ends(monkeypatch):
    clock = Clock()
    w = _w(monkeypatch, clock)
    w._resolve_batch_size = MagicMock(return_value=1)
    w._campaign_inflight_calls = AsyncMock(return_value=1)
    head, follower, nxt = _jobs(3)
    await w.process_job(head)                 # checked: at capacity -> memo (1 s) + parked 1 s
    clock.now += 0.5
    await w.process_job(follower)             # inside the window: parked, not checked
    assert w._campaign_inflight_calls.await_count == 1

    w._campaign_inflight_calls.return_value = 0   # the call ended
    clock.now += 0.5                              # the 1 s window has lapsed
    await w.process_job(nxt)
    w._make_call.assert_awaited_once()            # first job after the lapse is placed

    # worst case from "call ended" to "placed": re-check 1 s + move tick 1 s + loop poll 1 s
    assert w.CAPACITY_RECHECK_S + w.SCHEDULED_MOVE_INTERVAL_S + w.POLL_INTERVAL <= 3


async def test_the_loop_moves_due_jobs_every_second_not_every_ten(monkeypatch):
    w = _w(monkeypatch)
    w.running = True
    w.POLL_INTERVAL = 0
    w.queue_service.process_scheduled_jobs = AsyncMock(return_value=0)
    w._reap_stuck_jobs_tick = AsyncMock()
    w._get_active_tenant_ids = AsyncMock(return_value=[TENANT_A])

    async def stop(**_k):
        w.running = False
        return None

    w.queue_service.dequeue_job = AsyncMock(side_effect=stop)
    w._last_scheduled_check = datetime.now(timezone.utc).replace(microsecond=0) \
        - __import__("datetime").timedelta(seconds=2)
    w._last_reap_check = datetime.now(timezone.utc)

    await w._run_loop(0)

    w.queue_service.process_scheduled_jobs.assert_awaited_once()
