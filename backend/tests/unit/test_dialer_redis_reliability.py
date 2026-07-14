"""Reliability regression tests for the dialer Redis layer.

Covers three root-cause fixes:

  BUG 1 — job silently LOST if the worker dies between the queue pop and the
          processing mark. Dequeue is now an atomic LMOVE into a durable
          inflight list; the reaper reclaims a crash-orphan instead of losing it.
  BUG 2 — paused / quota-blocked campaigns getting DRAINED. An empty active-
          tenant list now dequeues NOTHING, and a pause/out-of-minutes skip
          re-defers the lead instead of destroying it.
  BUG 3 — concurrency UNDERCOUNT after a Redis restart. refresh_lease now
          recreates a missing lease key so reconcile doesn't drop a live call.

All hermetic — fakeredis, no real Redis / DB.
"""
from __future__ import annotations

import json

import fakeredis.aioredis as fakeredis
import pytest

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.queue_service import DialerQueueService
from app.domain.services import global_concurrency as gc


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _job(job_id="j1", tenant="t1", campaign="c1", lead="l1", priority=5) -> DialerJob:
    return DialerJob(
        job_id=job_id,
        campaign_id=campaign,
        lead_id=lead,
        tenant_id=tenant,
        phone_number="+15551230000",
        priority=priority,
    )


async def _svc() -> DialerQueueService:
    r = fakeredis.FakeRedis(decode_responses=True)
    svc = DialerQueueService(redis_client=r)
    await svc.initialize()
    return svc


def _tenant_key(svc: DialerQueueService, tenant: str) -> str:
    return svc.TENANT_QUEUE_PREFIX.format(tenant_id=tenant)


# ──────────────────────────────────────────────────────────────────────────
# BUG 1 — crash-safe dequeue + reclaim
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crash_between_pop_and_mark_is_recoverable_not_lost():
    """Simulate a worker death in the pop→mark gap: the mark raises AFTER the
    atomic move. The payload must survive in the inflight list and be reclaimed
    (re-enqueued) by the reaper — never lost."""
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())

    # Force the "crash": _mark_processing blows up AFTER lmove has moved the
    # payload into the inflight list.
    async def boom(*_a, **_k):
        raise RuntimeError("worker died after pop, before mark")

    svc._mark_processing = boom  # type: ignore[assignment]

    result = await svc.dequeue_job(tenant_ids=["t1"])
    assert result is None  # the dequeue itself failed...

    # ...but the job is NOT lost: it lives in the durable inflight list, and it
    # is UNtracked (no processing-ZSET marker) — exactly the crash-orphan shape.
    assert await r.llen(svc.INFLIGHT_LIST) == 1
    assert await r.zcard(svc.PROCESSING_ZSET) == 0
    assert await r.llen(_tenant_key(svc, "t1")) == 0

    # The reaper reclaims it back onto the tenant queue.
    reclaimed = await svc.reap_stale_processing()
    assert reclaimed == 1
    assert await r.llen(svc.INFLIGHT_LIST) == 0
    assert await r.llen(_tenant_key(svc, "t1")) == 1
    payload = json.loads(await r.lindex(_tenant_key(svc, "t1"), 0))
    assert payload["job_id"] == "j1"


@pytest.mark.asyncio
async def test_reaper_does_not_re_enqueue_a_tracked_inflight_job():
    """A normally-dequeued job (mark landed → tracked in the ZSET) must NOT be
    re-enqueued by the reclaim pass — that would double-dial live calls."""
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=["t1"])
    assert job is not None
    assert await r.zcard(svc.PROCESSING_ZSET) == 1

    # Reclaim pass leaves the tracked job alone (age-out threshold not reached).
    assert await svc._reclaim_untracked_inflight() == 0
    assert await r.llen(_tenant_key(svc, "t1")) == 0  # not re-enqueued
    assert await r.llen(svc.INFLIGHT_LIST) == 1        # still in flight


@pytest.mark.asyncio
async def test_terminal_mark_clears_inflight_copy():
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=["t1"])
    await svc.mark_completed(job.job_id)
    assert await r.llen(svc.INFLIGHT_LIST) == 0
    assert await r.hlen(svc.INFLIGHT_HASH) == 0
    assert await r.zcard(svc.PROCESSING_ZSET) == 0


@pytest.mark.asyncio
async def test_two_workers_cannot_both_claim_same_job():
    """Single-ownership under the atomic move: two dequeues on a one-job queue
    → exactly one gets it, the other gets None."""
    svc = await _svc()
    await svc.enqueue_job(_job())
    a = await svc.dequeue_job(tenant_ids=["t1"])
    b = await svc.dequeue_job(tenant_ids=["t1"])
    assert (a is not None) ^ (b is not None)
    got = [x for x in (a, b) if x is not None]
    assert len(got) == 1 and got[0].job_id == "j1"


# ──────────────────────────────────────────────────────────────────────────
# BUG 1 (scheduled path) — promotion has no lose-it window
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduled_promotion_restores_on_enqueue_failure():
    """If enqueue fails after the scheduled entry was claimed (removed), the
    entry must be restored — never lost."""
    svc = await _svc()
    r = svc._redis
    job = _job()
    # Put a due entry directly in the scheduled set.
    await r.zadd(svc.SCHEDULED_ZSET, {json.dumps(job.to_redis_dict()): 1.0})

    async def enqueue_fail(_j):
        return False

    svc.enqueue_job = enqueue_fail  # type: ignore[assignment]
    moved = await svc.process_scheduled_jobs()
    assert moved == 0
    # Restored to the scheduled set (not lost).
    assert await r.zcard(svc.SCHEDULED_ZSET) == 1


@pytest.mark.asyncio
async def test_scheduled_promotion_claims_once():
    """A due entry is promoted exactly once (ZREM claim). A second pass finds
    nothing — no double promotion / double dial."""
    svc = await _svc()
    r = svc._redis
    job = _job()
    await r.zadd(svc.SCHEDULED_ZSET, {json.dumps(job.to_redis_dict()): 1.0})
    assert await svc.process_scheduled_jobs() == 1
    assert await svc.process_scheduled_jobs() == 0
    assert await r.llen(_tenant_key(svc, "t1")) == 1  # exactly one copy queued


# ──────────────────────────────────────────────────────────────────────────
# BUG 2a — empty active-tenant list dequeues NOTHING
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_tenant_list_dequeues_nothing():
    """No active tenants (or a failed lookup → []) must process NOTHING — never
    drain paused/idle queues. Priority queue is protected too."""
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job(job_id="normal"))
    await svc.enqueue_job(_job(job_id="prio", priority=9))  # priority queue

    result = await svc.dequeue_job(tenant_ids=[])
    assert result is None
    # Everything stays put — nothing popped, nothing moved to inflight.
    assert await r.llen(svc.PRIORITY_QUEUE) == 1
    assert await r.llen(_tenant_key(svc, "t1")) == 1
    assert await r.llen(svc.INFLIGHT_LIST) == 0
    assert await r.zcard(svc.PROCESSING_ZSET) == 0


@pytest.mark.asyncio
async def test_none_tenant_list_still_scans_all():
    """None (explicitly "scan everything") keeps working — distinct from []."""
    svc = await _svc()
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=None)
    assert job is not None and job.job_id == "j1"


# ──────────────────────────────────────────────────────────────────────────
# BUG 2b — paused / quota-blocked leads survive (re-defer, not terminal skip)
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_paused_campaign_lead_survives_pause_then_resume():
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=["t1"])
    assert job is not None

    # Worker sees the campaign paused → skip with a NON-terminal reason.
    await svc.mark_skipped(job.job_id, reason="campaign_stopped")

    # Lead NOT destroyed: re-deferred into the scheduled set, inflight cleared.
    assert await r.llen(svc.INFLIGHT_LIST) == 0
    assert await r.zcard(svc.PROCESSING_ZSET) == 0
    assert await r.zcard(svc.SCHEDULED_ZSET) == 1

    # Resume: make the scheduled entry due, promote it, and confirm the SAME
    # lead comes back onto the queue and can be dialed.
    members = await r.zrange(svc.SCHEDULED_ZSET, 0, -1)
    await r.zadd(svc.SCHEDULED_ZSET, {members[0]: 1.0})  # force "due"
    assert await svc.process_scheduled_jobs() == 1
    again = await svc.dequeue_job(tenant_ids=["t1"])
    assert again is not None and again.lead_id == "l1"


@pytest.mark.asyncio
async def test_out_of_minutes_lead_survives():
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=["t1"])
    await svc.mark_skipped(job.job_id, reason="out_of_minutes")
    assert await r.zcard(svc.SCHEDULED_ZSET) == 1  # preserved, not dropped


@pytest.mark.asyncio
async def test_terminal_skip_reason_still_drops():
    """A genuinely terminal skip reason is NOT re-deferred."""
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=["t1"])
    await svc.mark_skipped(job.job_id, reason="spam_blocked")
    assert await r.zcard(svc.SCHEDULED_ZSET) == 0
    assert await r.llen(svc.INFLIGHT_LIST) == 0


@pytest.mark.asyncio
async def test_stop_purges_inflight_so_skip_does_not_cycle():
    """After a STOP purges the campaign, a straggler skip finds no inflight copy
    and must NOT re-defer (which would cycle a stopped lead forever)."""
    svc = await _svc()
    r = svc._redis
    await svc.enqueue_job(_job())
    job = await svc.dequeue_job(tenant_ids=["t1"])
    await svc.clear_campaign_jobs("c1")  # STOP path purges inflight
    await svc.mark_skipped(job.job_id, reason="campaign_stopped")
    assert await r.zcard(svc.SCHEDULED_ZSET) == 0  # not re-deferred


# ──────────────────────────────────────────────────────────────────────────
# BUG 3 — concurrency undercount after Redis restart
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_lease_recreated_and_reconcile_keeps_it():
    r = fakeredis.FakeRedis(decode_responses=True)
    await gc.acquire_lease(r, call_id="live", pod_id="pod-a", cap=10)
    assert await gc.current_count(r) == 1

    # Simulate a Redis restart / eviction losing the lease key.
    await r.delete(gc._lease_key("live"))
    assert await r.exists(gc._lease_key("live")) == 0

    # Watchdog refresh must RECREATE the key (old SET XX left it missing).
    await gc.refresh_lease(r, call_id="live")
    assert await r.exists(gc._lease_key("live")) == 1

    # Reconcile now keeps the live call — no undercount.
    removed = await gc.reconcile_orphans(r)
    assert removed == 0
    assert await gc.current_count(r) == 1


@pytest.mark.asyncio
async def test_without_refresh_a_lost_lease_is_reconciled_away():
    """Control: proves the lost-lease → reconcile-drop mechanism is real, so the
    refresh recreate in the test above is what prevents the undercount."""
    r = fakeredis.FakeRedis(decode_responses=True)
    await gc.acquire_lease(r, call_id="ghost", pod_id="pod-a", cap=10)
    await r.delete(gc._lease_key("ghost"))
    removed = await gc.reconcile_orphans(r)
    assert removed == 1
    assert await gc.current_count(r) == 0
