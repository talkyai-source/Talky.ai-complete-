"""The live per-campaign block-reason channel.

Reuses the existing real-time surface (``stream_events``, read by
``GET /api/v1/events``) plus a Redis key holding the CURRENT reason for
``GET /campaigns/{id}/stats``. The contract that matters operationally: emit
on CHANGE, never once per poll — a blocked campaign re-evaluates its jobs
continuously and would otherwise bury the Event Stream.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer import block_state
from app.domain.services.dialer.block_reasons import BlockCode, classify


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=None):
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def emitted(monkeypatch):
    """Capture every stream_events row the module would write."""
    rows: list[dict] = []

    async def _fake_emit(pool, **kwargs):
        rows.append(kwargs)

    monkeypatch.setattr(
        "app.domain.services.event_emitter.emit_event_via_pool", _fake_emit,
    )
    return rows


def _rules() -> CallingRules:
    return CallingRules(
        timezone="Europe/London", time_window_start="14:00",
        time_window_end="17:00", allowed_days=[0, 4],
    )


def _schedule_reason():
    return classify("calling_not_allowed_on_Tue", rules=_rules())


@pytest.mark.asyncio
async def test_first_block_emits_one_event_and_stores_state(emitted):
    redis = FakeRedis()
    reason = _schedule_reason()

    did_emit = await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=reason,
    )

    assert did_emit is True
    assert len(emitted) == 1
    row = emitted[0]
    assert row["related_campaign_id"] == "c1"
    assert row["tenant_id"] == "t1"
    assert row["metadata"]["block_code"] == BlockCode.SCHEDULE_DAY_NOT_ALLOWED.value
    assert row["metadata"]["next_eligible_at"]
    # The Event Stream row carries the human sentence, not a log string.
    assert "Mon & Fri" in row["description"]

    stored = await block_state.read_block_reason(redis, "c1")
    assert stored["code"] == BlockCode.SCHEDULE_DAY_NOT_ALLOWED.value
    assert stored["message"] == reason.message
    assert stored["title"]


@pytest.mark.asyncio
async def test_repeated_identical_reason_does_not_spam_the_event_stream(emitted):
    redis = FakeRedis()

    results = [
        await block_state.publish_block_reason(
            redis, object(), tenant_id="t1", campaign_id="c1",
            reason=_schedule_reason(),
        )
        for _ in range(25)
    ]

    assert results[0] is True
    assert not any(results[1:])
    assert len(emitted) == 1  # 25 polls, one event


@pytest.mark.asyncio
async def test_changed_reason_emits_again(emitted):
    redis = FakeRedis()
    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=_schedule_reason(),
    )
    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1",
        reason=classify("out_of_minutes"),
    )

    assert len(emitted) == 2
    assert emitted[1]["metadata"]["block_code"] == BlockCode.OUT_OF_MINUTES.value
    # An error-severity reason rides the alert category the dashboard treats
    # as needs-attention.
    assert emitted[1]["category"] == "alert"
    assert emitted[1]["severity"] == "critical"


@pytest.mark.asyncio
async def test_long_running_block_reemits_after_the_heartbeat_interval(emitted):
    redis = FakeRedis()
    t0 = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1",
        reason=_schedule_reason(), now=t0,
    )
    # Well inside the window — still silent.
    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1",
        reason=_schedule_reason(), now=t0 + timedelta(minutes=5),
    )
    assert len(emitted) == 1

    later = t0 + timedelta(seconds=block_state.REEMIT_AFTER_S + 1)
    did_emit = await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1",
        reason=_schedule_reason(), now=later,
    )
    assert did_emit is True
    assert len(emitted) == 2
    # The original observation time survives, so the UI can say how long the
    # campaign has been blocked.
    assert (await block_state.read_block_reason(redis, "c1"))["observed_at"] == t0.isoformat()


@pytest.mark.asyncio
async def test_clear_removes_the_state(emitted):
    redis = FakeRedis()
    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1", reason=_schedule_reason(),
    )
    await block_state.clear_block_reason(redis, "c1")
    assert await block_state.read_block_reason(redis, "c1") is None


@pytest.mark.asyncio
async def test_testing_override_notice_is_published_with_a_loud_title(emitted):
    from app.domain.services.dialer.block_reasons import testing_override_notice

    redis = FakeRedis()
    await block_state.publish_block_reason(
        redis, object(), tenant_id="t1", campaign_id="c1",
        reason=testing_override_notice(_rules(), source="env:X"),
    )
    assert emitted[0]["title"] == "TESTING MODE: schedule bypassed"
    assert emitted[0]["metadata"]["block_code"] == (
        BlockCode.TESTING_MODE_SCHEDULE_BYPASSED.value
    )


@pytest.mark.asyncio
async def test_failures_never_propagate(emitted, monkeypatch):
    """Observability must never decide whether a call is placed."""

    class BrokenRedis(FakeRedis):
        async def get(self, key):
            raise RuntimeError("redis down")

        async def set(self, key, value, ex=None, nx=None):
            raise RuntimeError("redis down")

    async def _boom(pool, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.domain.services.event_emitter.emit_event_via_pool", _boom,
    )

    assert await block_state.publish_block_reason(
        BrokenRedis(), object(), tenant_id="t1", campaign_id="c1",
        reason=_schedule_reason(),
    ) is False
    assert await block_state.read_block_reason(BrokenRedis(), "c1") is None
    await block_state.clear_block_reason(BrokenRedis(), "c1")  # must not raise


@pytest.mark.asyncio
async def test_no_redis_means_no_event_spam(emitted):
    """Without the dedup marker we stay silent rather than flood the stream."""
    assert await block_state.publish_block_reason(
        None, object(), tenant_id="t1", campaign_id="c1", reason=_schedule_reason(),
    ) is False
    assert emitted == []
