"""Worker-level wiring for "why wasn't this call placed?" + the testing override.

Companion to ``test_dialer_block_reasons.py`` (which pins the reason vocabulary
itself). Here we drive ``DialerWorker.process_job`` with every collaborator
mocked — same harness style as ``test_dialer_worker_guard_ordering.py`` — and
assert that:

  * each gate publishes a STRUCTURED reason on the campaign's live channel;
  * a day-of-week block sleeps until the next window instead of re-waking
    every 5 minutes (the old substring test missed
    ``calling_not_allowed_on_Tue`` entirely);
  * the testing override is OFF by default, and when switched ON it permits
    the dial, logs at WARNING, surfaces "TESTING MODE" through the same
    channel, and stamps the call record so it is auditable.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.dialer.block_reasons import BlockCode
from app.domain.services.dialer.testing_override import TESTING_OVERRIDE_ENV
from app.workers.dialer_worker import DialerCallIntent, DialerWorker


def _job() -> DialerJob:
    return DialerJob(
        job_id="job-abc",
        campaign_id="35c79aeb-0000-0000-0000-000000000000",
        lead_id="lead-abc",
        tenant_id="tenant-abc",
        phone_number="+447700900123",
    )


def _incident_rules() -> CallingRules:
    return CallingRules(
        timezone="Europe/London",
        time_window_start="14:00",
        time_window_end="17:00",
        allowed_days=[0, 4],
    )


def _worker(*, campaign_cfg: dict | None = None) -> DialerWorker:
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None
    w._db_pool = None

    w._get_campaign_status = AsyncMock(return_value="running")
    w._load_existing_call_intent = AsyncMock(return_value=None)
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    w._get_tenant_rules = AsyncMock(return_value=_incident_rules())
    w._get_campaign_calling_config = AsyncMock(return_value=campaign_cfg or {})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)

    w.rules_engine.can_make_call = AsyncMock(return_value=(True, "all_rules_passed"))
    w.rules_engine.get_delay_until_next_window = MagicMock(return_value=66_000)

    w._resolve_batch_size = MagicMock(return_value=0)
    w._campaign_inflight_calls = AsyncMock(return_value=0)
    w._resolve_call_gap = MagicMock(return_value=0)
    w._campaign_seconds_since_last_dial = AsyncMock(return_value=None)

    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._update_job_status = AsyncMock()
    w._make_call = AsyncMock(return_value="provider-call-1")
    intent = DialerCallIntent("call-1", "tk-1", "leg-1", "initiated", None, True)
    w._create_call_intent = AsyncMock(return_value=intent)
    w._bind_call_intent = AsyncMock()
    w._mark_call_intent_not_originated = AsyncMock(return_value=True)
    w._update_lead_status = AsyncMock()
    w._mark_campaign_dialed = AsyncMock()
    w._emit_progress_event_throttled = AsyncMock()

    # Capture published reasons instead of touching Redis / the DB.
    w._publish_reason = AsyncMock()
    return w


def _published_codes(worker) -> list[BlockCode]:
    return [c.args[1].code for c in worker._publish_reason.call_args_list]


# ── each gate publishes a structured reason ───────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [
    ("calling_not_allowed_on_Tue", BlockCode.SCHEDULE_DAY_NOT_ALLOWED),
    ("outside_time_window_14:00_17:00", BlockCode.SCHEDULE_OUTSIDE_WINDOW),
    ("max_concurrent_calls_reached_10/10", BlockCode.MAX_CONCURRENT_CALLS),
    ("daily_lead_cap_reached_3/3", BlockCode.DAILY_LEAD_CAP),
])
async def test_scheduling_gate_publishes_a_structured_reason(raw, expected, monkeypatch):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    w = _worker()
    w.rules_engine.can_make_call = AsyncMock(return_value=(False, raw))

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    assert expected in _published_codes(w)
    # The raw string is still what lands on the job row, so every existing
    # substring-matching surface (/calls/issues) keeps working.
    _, kwargs = w._update_job_status.call_args
    assert kwargs.get("reason") == raw


@pytest.mark.asyncio
async def test_out_of_minutes_and_stopped_campaign_publish_reasons(monkeypatch):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)

    w = _worker()
    w._tenant_minutes_exhausted = AsyncMock(return_value=True)
    w._emit_out_of_minutes_event = AsyncMock()
    await w.process_job(_job())
    assert BlockCode.OUT_OF_MINUTES in _published_codes(w)

    w2 = _worker()
    w2._get_campaign_status = AsyncMock(return_value="draft")
    await w2.process_job(_job())
    assert BlockCode.CAMPAIGN_NOT_RUNNING in _published_codes(w2)


@pytest.mark.asyncio
async def test_pacing_and_guard_gates_publish_reasons(monkeypatch):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)

    w = _worker()
    w._resolve_batch_size = MagicMock(return_value=1)
    w._campaign_inflight_calls = AsyncMock(return_value=1)
    await w.process_job(_job())
    assert BlockCode.BATCH_CAPACITY in _published_codes(w)

    w2 = _worker()
    w2._evaluate_call_guard = AsyncMock(return_value="block")
    await w2.process_job(_job())
    assert BlockCode.CALL_GUARD_BLOCKED in _published_codes(w2)

    w3 = _worker()
    w3._make_call = AsyncMock(return_value=DialerWorker._PIPELINE_UNAVAILABLE)
    await w3.process_job(_job())
    assert BlockCode.VOICE_PIPELINE_UNAVAILABLE in _published_codes(w3)


@pytest.mark.asyncio
async def test_day_block_sleeps_until_the_next_window_not_five_minutes(monkeypatch):
    """Regression: ``calling_not_allowed_on_Tue`` contains neither
    "time_window" nor "day", so the old substring test missed it and the job
    fell through to the generic 300s retry — re-waking every 5 minutes for
    days instead of sleeping until the window opened."""
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    w = _worker()
    w.rules_engine.can_make_call = AsyncMock(
        return_value=(False, "calling_not_allowed_on_Tue")
    )

    await w.process_job(_job())

    w.queue_service.schedule_retry.assert_awaited_once()
    _, kwargs = w.queue_service.schedule_retry.call_args
    assert kwargs["delay_seconds"] == 66_000  # the next-window delay, not 300
    w.rules_engine.get_delay_until_next_window.assert_called_once()


@pytest.mark.asyncio
async def test_successful_dial_publishes_nothing_and_clears_state(monkeypatch):
    """Non-vacuity: the worker isn't publishing a reason on every job."""
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    w = _worker()

    await w.process_job(_job())

    w._make_call.assert_awaited_once()
    w._publish_reason.assert_not_awaited()


# ── testing override ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_override_off_by_default_blocks_the_call(monkeypatch):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    w = _worker(campaign_cfg={})
    w.rules_engine.can_make_call = AsyncMock(
        return_value=(False, "calling_not_allowed_on_Tue")
    )

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    assert w._schedule_override is None


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["env", "campaign"])
async def test_override_permits_the_call_and_is_loud(via, monkeypatch, caplog):
    if via == "env":
        monkeypatch.setenv(TESTING_OVERRIDE_ENV, "true")
        cfg = {}
    else:
        monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
        cfg = {"testing_mode_ignore_schedule": True}

    w = _worker(campaign_cfg=cfg)
    w.rules_engine.can_make_call = AsyncMock(
        return_value=(False, "calling_not_allowed_on_Tue")
    )

    with caplog.at_level(logging.WARNING):
        await w.process_job(_job())

    # 1. It permits the call the schedule gate had refused.
    w._make_call.assert_awaited_once()
    w.queue_service.schedule_retry.assert_not_awaited()

    # 2. It logs at WARNING, naming the bypass and the compliance reason.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("TESTING MODE" in r.getMessage() for r in warnings)
    assert any("compliance" in r.getMessage().lower() for r in warnings)

    # 3. It is surfaced to the user through the SAME reason channel.
    assert BlockCode.TESTING_MODE_SCHEDULE_BYPASSED in _published_codes(w)

    # 4. It records on the call that it was placed under the override.
    assert w._schedule_override is not None
    assert w._schedule_override["blocked_reason"] == "calling_not_allowed_on_Tue"
    assert "Mon & Fri, 14:00-17:00 Europe/London" == w._schedule_override["schedule"]


@pytest.mark.asyncio
async def test_override_does_not_bypass_non_schedule_gates(monkeypatch):
    """COMPLIANCE-adjacent guard: the override relaxes the calling WINDOW and
    nothing else. DNC / guard / quota / concurrency stay enforced."""
    monkeypatch.setenv(TESTING_OVERRIDE_ENV, "true")

    for raw in (
        "max_concurrent_calls_reached_10/10",
        "daily_lead_cap_reached_3/3",
    ):
        w = _worker()
        w.rules_engine.can_make_call = AsyncMock(return_value=(False, raw))
        await w.process_job(_job())
        assert w._make_call.await_count == 0, raw
        assert w._schedule_override is None, raw

    w2 = _worker()
    w2._evaluate_call_guard = AsyncMock(return_value="block")
    await w2.process_job(_job())
    w2._make_call.assert_not_awaited()

    w3 = _worker()
    w3._tenant_minutes_exhausted = AsyncMock(return_value=True)
    w3._emit_out_of_minutes_event = AsyncMock()
    await w3.process_job(_job())
    w3._make_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_override_is_stamped_on_the_call_record(monkeypatch):
    """The audit trail: a call placed under the override carries the marker on
    ``call_legs.metadata`` and gets its own ``schedule_override`` call event."""
    executed: list[tuple] = []

    class _Conn:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def execute(self, sql, *args):
            executed.append((sql, args))

        async def fetchrow(self, sql, *args):
            executed.append((sql, args))
            if "INSERT INTO calls" in sql:
                return {
                    "id": args[0],
                    "talklee_call_id": args[5],
                    "status": "initiated",
                    "provider_call_id": None,
                }
            raise AssertionError(sql)

    class _Pool:
        @asynccontextmanager
        async def _acquire(self):
            yield _Conn()

        def acquire(self):
            return self._acquire()

    w = _worker()
    w._db_pool = _Pool()
    w._schedule_override = {
        "source": "env:" + TESTING_OVERRIDE_ENV,
        "blocked_reason": "calling_not_allowed_on_Tue",
        "schedule": "Mon & Fri, 14:00-17:00 Europe/London",
    }

    intent = await DialerWorker._create_call_intent(w, _job())
    assert intent.call_id and intent.talklee_call_id and intent.leg_id

    legs = [a for sql, a in executed if "INSERT INTO call_legs" in sql]
    assert legs, "no call_legs insert captured"
    leg_meta = json.loads(legs[0][4])
    assert leg_meta["schedule_override"] is True
    assert leg_meta["schedule_override_blocked_reason"] == "calling_not_allowed_on_Tue"

    override_events = [
        a for sql, a in executed
        if "INSERT INTO call_events" in sql and "schedule_override" in sql
    ]
    assert len(override_events) == 1
    audit = json.loads(override_events[0][3])
    assert audit["schedule_override_source"].endswith(TESTING_OVERRIDE_ENV)


@pytest.mark.asyncio
async def test_normal_call_record_carries_no_override_marker():
    """Non-vacuity: the marker only appears when the override actually fired."""
    executed: list[tuple] = []

    class _Conn:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def execute(self, sql, *args):
            executed.append((sql, args))

        async def fetchrow(self, sql, *args):
            executed.append((sql, args))
            if "INSERT INTO calls" in sql:
                return {
                    "id": args[0],
                    "talklee_call_id": args[5],
                    "status": "initiated",
                    "provider_call_id": None,
                }
            raise AssertionError(sql)

    class _Pool:
        @asynccontextmanager
        async def _acquire(self):
            yield _Conn()

        def acquire(self):
            return self._acquire()

    w = _worker()
    w._db_pool = _Pool()
    w._schedule_override = None

    await DialerWorker._create_call_intent(w, _job())

    legs = [a for sql, a in executed if "INSERT INTO call_legs" in sql]
    assert "schedule_override" not in json.loads(legs[0][4])
    assert not [
        a for sql, a in executed
        if "INSERT INTO call_events" in sql and "schedule_override" in a
    ]
