"""Actual worker/queue pacing holds must not consume a dial attempt."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.test_op05_pre_attempt_holds import held_worker


CASES = [("batch_capacity", 5), ("call_gap", 17), ("tenant_gap", 23),
         ("call_guard_throttled", 60), ("call_guard_queued", 30)]


async def pacing_worker(monkeypatch, reason, *, job=None):
    worker, job, queue = await held_worker(monkeypatch, job=job)
    worker.rules_engine.can_make_call = AsyncMock(return_value=(True, "all_rules_passed"))
    claim = AsyncMock(return_value=23 if reason == "tenant_gap" else 0)
    release = AsyncMock()
    monkeypatch.setattr("app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", claim)
    monkeypatch.setattr("app.domain.services.dialer.global_pacing.release_tenant_dial_slot", release)
    if reason == "batch_capacity":
        worker._resolve_batch_size = MagicMock(return_value=2)
        worker._campaign_inflight_calls = AsyncMock(return_value=2)
    elif reason == "call_gap":
        worker._resolve_call_gap = MagicMock(return_value=30)
        worker._campaign_seconds_since_last_dial = AsyncMock(return_value=13)
    elif reason.startswith("call_guard"):
        worker._evaluate_call_guard = AsyncMock(return_value="throttle" if reason.endswith("throttled") else "queue")
    return worker, job, queue, release


@pytest.mark.parametrize("reason,delay", CASES)
async def test_pacing_hold_preserves_attempt_and_exact_durable_due(monkeypatch, reason, delay):
    worker, job, queue, release = await pacing_worker(monkeypatch, reason)
    try:
        before = datetime.now(timezone.utc).timestamp()
        await worker.process_job(job)
        entries = await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1, withscores=True)
        assert len(entries) == 1
        payload, due = entries[0]
        saved = json.loads(payload)
        assert saved["job_id"] == job.job_id
        assert saved["attempt_number"] == job.attempt_number == 1
        assert worker._update_job_status.await_args.kwargs["scheduled_at"].timestamp() == due
        assert before + delay <= due <= datetime.now(timezone.utc).timestamp() + delay
        assert await queue._redis.llen(queue.INFLIGHT_LIST) == 0
        worker._create_call_intent.assert_not_awaited()
        worker._make_call.assert_not_awaited()
        if reason.startswith("call_guard"):
            release.assert_awaited_once_with(worker._redis, job.tenant_id)
    finally:
        await queue._redis.aclose()


@pytest.mark.parametrize("reason,delay", CASES)
async def test_pacing_hold_database_failure_leaves_original_inflight(monkeypatch, reason, delay):
    worker, job, queue, _ = await pacing_worker(monkeypatch, reason)
    original = await queue._redis.hget(queue.INFLIGHT_HASH, job.job_id)
    worker._update_job_status.side_effect = ConnectionError("synthetic durable hold failure")
    try:
        await worker.process_job(job)
        assert job.attempt_number == 1
        assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 0
        assert await queue._redis.hget(queue.INFLIGHT_HASH, job.job_id) == original
        worker._create_call_intent.assert_not_awaited()
        worker._make_call.assert_not_awaited()
    finally:
        await queue._redis.aclose()
