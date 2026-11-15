"""Platform-wide origination slot: all tenants share one voice process, so two
tenants must not start calls in the same second (the per-tenant gap alone does
not stop that)."""
import asyncio
import json

import fakeredis.aioredis as fakeredis
import pytest

from app.domain.models.dialer_job import DialerJob
from app.domain.services.dialer.global_pacing import (
    claim_platform_dial_slot,
    claim_tenant_dial_slot,
    platform_min_gap_s,
    release_platform_dial_slot,
)
from unittest.mock import AsyncMock

from tests.unit.test_op05_pre_attempt_holds import held_worker

PLATFORM_KEY = "dialer:last_dial:platform"


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


class _BrokenRedis:
    async def set(self, *a, **k):
        raise ConnectionError("down")

    async def ttl(self, *a, **k):
        raise ConnectionError("down")

    async def delete(self, *a, **k):
        raise ConnectionError("down")


# -- gap setting -----------------------------------------------------------
def test_default_gap_is_10(monkeypatch):
    monkeypatch.delenv("DIALER_PLATFORM_MIN_GAP_S", raising=False)
    assert platform_min_gap_s() == 10


def test_bad_value_falls_back_to_10(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "abc")
    assert platform_min_gap_s() == 10


def test_negative_value_is_never_negative(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "-5")
    assert platform_min_gap_s() == 0


# -- claim / release ---------------------------------------------------------
async def test_second_claim_in_window_waits_remaining_plus_one(monkeypatch, redis):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    assert await claim_platform_dial_slot(redis) == 0
    wait = await claim_platform_dial_slot(redis)
    assert 2 <= wait <= 11  # remaining TTL (1..10) + 1


async def test_claim_succeeds_again_after_expiry(monkeypatch, redis):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    assert await claim_platform_dial_slot(redis) == 0
    assert await claim_platform_dial_slot(redis) > 0
    await redis.pexpire(PLATFORM_KEY, 1)
    await asyncio.sleep(0.05)
    assert await claim_platform_dial_slot(redis) == 0


async def test_gap_zero_disables(monkeypatch, redis):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "0")
    assert await claim_platform_dial_slot(redis) == 0
    assert await claim_platform_dial_slot(redis) == 0
    assert await redis.exists(PLATFORM_KEY) == 0


async def test_redis_error_fails_open(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    assert await claim_platform_dial_slot(_BrokenRedis()) == 0
    await release_platform_dial_slot(_BrokenRedis())  # must not raise


async def test_redis_none_returns_zero(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    assert await claim_platform_dial_slot(None) == 0
    await release_platform_dial_slot(None)


async def test_release_lets_next_claim_through(monkeypatch, redis):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    assert await claim_platform_dial_slot(redis) == 0
    assert await claim_platform_dial_slot(redis) > 0
    await release_platform_dial_slot(redis)
    assert await claim_platform_dial_slot(redis) == 0


# -- process_job ---------------------------------------------------------------
async def _open_worker(monkeypatch):
    worker, job, queue = await held_worker(monkeypatch)
    worker.rules_engine.can_make_call = AsyncMock(return_value=(True, "all_rules_passed"))
    worker._redis = queue._redis
    return worker, job, queue


async def _next_job(queue, tenant_id, job_id):
    j = DialerJob(
        job_id=job_id,
        campaign_id="35c79aeb-0000-0000-0000-000000000001",
        lead_id=f"lead-{job_id}",
        tenant_id=tenant_id,
        phone_number="+447700900124",
    )
    assert await queue.enqueue_job(j)
    got = await queue.dequeue_job([tenant_id])
    assert got is not None
    return got


async def test_other_tenant_inside_platform_window_is_deferred_and_tenant_slot_returned(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    monkeypatch.setenv("DIALER_TENANT_MIN_GAP_S", "90")
    worker, job_a, queue = await _open_worker(monkeypatch)
    try:
        # Tenant A dials and takes both slots.
        await worker.process_job(job_a)
        worker._make_call.assert_awaited_once()
        assert await queue._redis.exists(PLATFORM_KEY) == 1

        # Tenant B, a moment later, is held back and does not originate.
        job_b = await _next_job(queue, "tenant-b", "job-b")
        await worker.process_job(job_b)
        worker._make_call.assert_awaited_once()  # still only A's call
        assert worker._update_job_status.await_args.kwargs["reason"] == "platform_gap"
        entries = await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1)
        assert [json.loads(e)["job_id"] for e in entries] == ["job-b"]
        published = [c.args[1].code.value for c in worker._publish_reason.call_args_list]
        assert "platform_gap" in published

        # B's own 90 s tenant window was given back: B can claim it.
        assert await queue._redis.exists("dialer:last_dial:tenant:tenant-b") == 0
        assert await claim_tenant_dial_slot(queue._redis, "tenant-b") == 0
    finally:
        await queue._redis.aclose()


async def test_failed_originate_gives_both_slots_back(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    monkeypatch.setenv("DIALER_TENANT_MIN_GAP_S", "90")
    worker, job, queue = await _open_worker(monkeypatch)
    held_at_dial = []

    async def blow_up(*_args, **_kwargs):
        # Both slots are held at the moment of dialling...
        held_at_dial.append(await queue._redis.exists(PLATFORM_KEY))
        held_at_dial.append(await queue._redis.exists(f"dialer:last_dial:tenant:{job.tenant_id}"))
        raise RuntimeError("originate blew up")

    worker._make_call.side_effect = blow_up
    try:
        await worker.process_job(job)
        worker._make_call.assert_awaited()
        assert held_at_dial == [1, 1]
        # ...and both are given back when the call did not start.
        assert await queue._redis.exists(PLATFORM_KEY) == 0
        assert await queue._redis.exists(f"dialer:last_dial:tenant:{job.tenant_id}") == 0
    finally:
        await queue._redis.aclose()


async def test_call_guard_refusal_gives_both_slots_back(monkeypatch):
    monkeypatch.setenv("DIALER_PLATFORM_MIN_GAP_S", "10")
    monkeypatch.setenv("DIALER_TENANT_MIN_GAP_S", "90")
    worker, job, queue = await _open_worker(monkeypatch)
    held_at_guard = []

    async def refuse(*_args, **_kwargs):
        # The platform slot is held while the guard decides...
        held_at_guard.append(await queue._redis.exists(PLATFORM_KEY))
        return "throttle"

    worker._evaluate_call_guard.side_effect = refuse
    try:
        await worker.process_job(job)
        worker._make_call.assert_not_awaited()
        assert held_at_guard == [1]
        # ...and given back when the guard refuses.
        assert await queue._redis.exists(PLATFORM_KEY) == 0
        assert await queue._redis.exists(f"dialer:last_dial:tenant:{job.tenant_id}") == 0
    finally:
        await queue._redis.aclose()


# -- the new reason is known everywhere the tenant gap is ---------------------
def test_platform_gap_is_a_self_clearing_pacing_reason_with_its_own_title():
    from app.domain.services.dialer import block_state
    from app.domain.services.dialer.block_reasons import (
        SELF_CLEARING_CODES,
        BlockCode,
        classify,
    )

    reason = classify("platform_gap", retry_after_seconds=7)
    assert reason.code is BlockCode.PLATFORM_GAP
    assert reason.code in SELF_CLEARING_CODES
    assert reason.severity == "info"
    assert reason.retry_after_seconds == 7
    assert block_state._title_for(reason) == "Spacing out call starts"


def test_platform_gap_has_its_own_advice_not_the_fallback():
    from app.domain.services import call_issue_advice

    advice = call_issue_advice.advise("platform_gap")
    assert advice is not call_issue_advice._FALLBACK
    assert advice.title == "Spacing out call starts"
    assert advice.severity == "info"


def test_call_issues_query_leaves_out_platform_gap_like_the_other_pacing_reasons():
    # Guard on the query text: the reason is filtered in SQL, and no database
    # runs in this suite. A held job must not become a Call Issues card.
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "app/api/v1/endpoints/calls.py"
    text = source.read_text(encoding="utf-8")
    start = text.index("NOT IN ('call_gap'")
    excluded = text[start : text.index(")", start)]
    assert "'tenant_gap'" in excluded
    assert "'platform_gap'" in excluded
