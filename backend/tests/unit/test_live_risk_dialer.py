"""Live-risk audit 2026-10-20, dialer packet (dialer-1,2,4,5,7,8,9,10,11)."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis as fakeredis
import pytest
import pytz
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import CampaignCallingSchedule
from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob
from app.domain.services.campaign_service import CampaignService
from app.domain.services.queue_service import DialerQueueService
from app.workers.dialer_worker import DialerCallIntent, DialerWorker


def _job(**kw) -> DialerJob:
    base = dict(
        job_id="job-abc",
        campaign_id="35c79aeb-0000-0000-0000-000000000000",
        lead_id="lead-abc",
        tenant_id="tenant-abc",
        phone_number="+447700900123",
    )
    base.update(kw)
    return DialerJob(**base)


def _worker(*, rules: CallingRules, campaign_cfg: dict | None = None) -> DialerWorker:
    w = DialerWorker()
    w.queue_service = AsyncMock()
    w._redis = None
    w._db_pool = None
    w._get_campaign_status = AsyncMock(return_value="running")
    w._stale_payload_reason = AsyncMock(return_value=None)
    w._load_existing_call_intent = AsyncMock(return_value=None)
    w._tenant_minutes_exhausted = AsyncMock(return_value=False)
    w._get_tenant_rules = AsyncMock(return_value=rules)
    w._get_campaign_calling_config = AsyncMock(return_value=campaign_cfg or {})
    w._get_lead_last_called = AsyncMock(return_value=None)
    w._get_lead_attempts_today = AsyncMock(return_value=0)
    w._get_lead_timezone = AsyncMock(return_value=None)
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
    w._publish_reason = AsyncMock()
    return w


class _Conn:
    def __init__(self, execute_result="UPDATE 1", fetchval_result=None):
        self.calls: list[tuple[str, tuple]] = []
        self._execute_result = execute_result
        self._fetchval_result = fetchval_result

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self._execute_result

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchval_result


def _use_conn(worker: DialerWorker, conn: _Conn) -> None:
    @asynccontextmanager
    async def _acquire():
        yield conn

    worker._acquire_db = _acquire  # type: ignore[assignment]


# ── dialer-1: calls dialled under ignore_schedule are audit-stamped ─────────

_CLOSED_ALWAYS = dict(
    timezone="Europe/London", time_window_start="09:00", time_window_end="19:00",
    allowed_days=[],
)
_OPEN_ALWAYS = dict(
    timezone="Europe/London", time_window_start="00:00", time_window_end="24:00",
    allowed_days=[0, 1, 2, 3, 4, 5, 6],
)


@pytest.mark.asyncio
async def test_dialer1_ignore_schedule_out_of_hours_call_is_stamped_and_logged(monkeypatch, caplog):
    monkeypatch.delenv("DIALER_ALLOW_IGNORE_SCHEDULE", raising=False)
    w = _worker(rules=CallingRules(**_CLOSED_ALWAYS), campaign_cfg={"ignore_schedule": True})
    seen = {}

    async def _capture(*_a, **_k):
        seen["override"] = w._schedule_override
        return "provider-call-1"

    w._make_call = AsyncMock(side_effect=_capture)
    with caplog.at_level(logging.WARNING):
        await w.process_job(_job())

    assert seen["override"] is not None, "out-of-hours call under ignore_schedule was not stamped"
    assert seen["override"]["source"] == "campaign:ignore_schedule"
    assert any("ignore_schedule" in r.getMessage() and "tenant-abc" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_dialer1_ignore_schedule_inside_window_is_not_stamped(monkeypatch):
    monkeypatch.delenv("DIALER_ALLOW_IGNORE_SCHEDULE", raising=False)
    w = _worker(rules=CallingRules(**_OPEN_ALWAYS), campaign_cfg={"ignore_schedule": True})
    seen = {}

    async def _capture(*_a, **_k):
        seen["override"] = w._schedule_override
        return "provider-call-1"

    w._make_call = AsyncMock(side_effect=_capture)
    await w.process_job(_job())
    assert seen["override"] is None


# ── dialer-2: unknown timezone must not silently become UTC ─────────────────

def test_dialer2_schema_rejects_unknown_timezone_and_accepts_iana():
    for bad in ("America/NewYork", "EST ", "Mars/Olympus"):
        with pytest.raises(ValidationError):
            CampaignCallingSchedule(timezone=bad)
    assert CampaignCallingSchedule(timezone="America/Los_Angeles").timezone == "America/Los_Angeles"
    assert CampaignCallingSchedule(timezone=None).timezone is None
    assert CampaignCallingSchedule(timezone="").timezone is None


def test_dialer2_unknown_timezone_fails_closed_not_utc():
    rules = CallingRules(timezone="America/NewYork", time_window_start="00:00",
                         time_window_end="24:00", allowed_days=[0, 1, 2, 3, 4, 5, 6])
    assert rules.is_within_time_window() == (False, "invalid_timezone")
    # A valid per-lead zone still decides when the account zone is bad.
    ok, _ = rules.is_within_time_window(tz_override="Europe/London")
    assert ok


def test_dialer2_valid_timezone_unchanged():
    rules = CallingRules(timezone="America/New_York", time_window_start="00:00",
                         time_window_end="24:00", allowed_days=[0, 1, 2, 3, 4, 5, 6])
    assert rules.is_within_time_window() == (True, "within_time_window")


# ── dialer-4: a held (replayed) intent must pass the stale-payload gate ─────

@pytest.mark.asyncio
async def test_dialer4_held_intent_for_removed_contact_is_not_dialled():
    w = _worker(rules=CallingRules(**_OPEN_ALWAYS))
    intent = DialerCallIntent("call-9", "tk-9", "leg-9", "initiated", None, True)
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._stale_payload_reason = AsyncMock(return_value="lead_not_dialable")
    w._drop_stale_payload = AsyncMock()

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    w._mark_call_intent_not_originated.assert_awaited_once()
    w._drop_stale_payload.assert_awaited_once()
    assert w._drop_stale_payload.await_args.args[1] == "lead_not_dialable"


@pytest.mark.asyncio
async def test_dialer4_held_intent_for_live_contact_still_resumes():
    w = _worker(rules=CallingRules(**_OPEN_ALWAYS))
    intent = DialerCallIntent("call-9", "tk-9", "leg-9", "initiated", None, True)
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._drop_stale_payload = AsyncMock()
    w._require_open_window_for_unclaimed_intent = AsyncMock()
    w._make_call = AsyncMock(return_value=DialerWorker._PIPELINE_UNAVAILABLE)
    w._defer_pipeline_unavailable = AsyncMock()

    await w.process_job(_job())

    w._make_call.assert_awaited_once()
    w._drop_stale_payload.assert_not_awaited()


# ── dialer-5: stop must not orphan leads waiting on a retry ─────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, []

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, c, v):
        self.filters.append(("eq", c, v))
        return self

    def in_(self, c, v):
        self.filters.append(("in", c, list(v)))
        return self

    def execute(self):
        self.db.log.append((self.table, self.op, self.payload, self.filters))
        data = self.db.reads.get(self.table, []) if self.op == "select" else [{"id": "x"}]
        return SimpleNamespace(data=data, error=None, count=None)


class _DB:
    def __init__(self, reads=None):
        self.log, self.reads = [], reads or {}

    def table(self, name):
        return _Q(self, name)


def test_dialer5_leads_waiting_on_retry_return_to_pending_on_stop():
    db = _DB(reads={"dialer_jobs": [{"lead_id": "L1"}, {"lead_id": "L2"}]})
    svc = CampaignService(db)
    lead_ids = svc._leads_awaiting_retry("camp-1", "tenant-1")
    assert lead_ids == ["L1", "L2"]
    job_sel = db.log[0]
    assert ("in", "status", ["retry_scheduled"]) in job_sel[3]
    assert ("eq", "tenant_id", "tenant-1") in job_sel[3]

    svc._release_leads_to_pending(lead_ids, "camp-1", "tenant-1")
    table, op, payload, filters = db.log[-1]
    assert (table, op) == ("leads", "update")
    assert payload["status"] == "pending"
    assert ("in", "id", ["L1", "L2"]) in filters
    assert ("eq", "status", "called") in filters  # never resurrect dnc/deleted/completed
    assert ("eq", "tenant_id", "tenant-1") in filters
    assert ("eq", "campaign_id", "camp-1") in filters


@pytest.mark.asyncio
async def test_dialer5_stop_campaign_snapshots_before_cancel_and_releases_after():
    order = []
    db = _DB()
    svc = CampaignService(db)
    svc.get_campaign = AsyncMock(return_value={"direction": "outbound"})
    svc._leads_awaiting_retry = MagicMock(side_effect=lambda *a, **k: order.append("snap") or ["L1"])
    svc._release_leads_to_pending = MagicMock(side_effect=lambda *a, **k: order.append("release"))
    svc._get_queue_service = AsyncMock(return_value=AsyncMock())
    svc._cleanup_queue_service = AsyncMock()
    import app.domain.services.dialer.job_lifecycle as jl

    orig = jl.cancel_active_jobs_for_campaign
    jl.cancel_active_jobs_for_campaign = lambda *a, **k: order.append("cancel") or 1
    try:
        await svc.stop_campaign("camp-1", tenant_id="tenant-1")
    finally:
        jl.cancel_active_jobs_for_campaign = orig
    assert order == ["snap", "cancel", "release"]


# ── dialer-7: clearing a campaign must not drop concurrently enqueued jobs ──

class _RacyRedis:
    """Wraps fakeredis: right after LRANGE returns, another producer pushes."""

    def __init__(self, inner, key, late_payload):
        self._inner, self._key, self._late = inner, key, late_payload

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def lrange(self, key, a, b):
        out = await self._inner.lrange(key, a, b)
        if key == self._key and self._late is not None:
            await self._inner.rpush(key, self._late)
            self._late = None
        return out


@pytest.mark.asyncio
async def test_dialer7_clear_campaign_keeps_job_pushed_during_the_scan():
    inner = fakeredis.FakeRedis(decode_responses=True)
    svc = DialerQueueService(redis_client=inner)
    await svc.initialize()
    key = "dialer:tenant:t1:queue"
    mine = json.dumps({"job_id": "a", "campaign_id": "A"})
    other = json.dumps({"job_id": "b", "campaign_id": "B"})
    late = json.dumps({"job_id": "c", "campaign_id": "B"})
    await inner.rpush(key, mine, other)
    svc._redis = _RacyRedis(inner, key, late)

    removed = await svc._remove_campaign_jobs_from_list(key, "A")

    assert removed == 1
    assert await inner.lrange(key, 0, -1) == [other, late]


# ── dialer-8: daily per-lead cap follows the tenant/lead timezone ───────────

@pytest.mark.asyncio
async def test_dialer8_daily_cap_counts_from_local_midnight():
    w = DialerWorker()
    conn = _Conn(fetchval_result=2)
    _use_conn(w, conn)
    assert await w._get_lead_attempts_today("lead-1", "America/Los_Angeles") == 2
    sql, args = conn.calls[0]
    assert "AT TIME ZONE" in sql
    assert "America/Los_Angeles" in args


@pytest.mark.asyncio
async def test_dialer8_daily_cap_defaults_to_utc_without_timezone():
    w = DialerWorker()
    conn = _Conn(fetchval_result=0)
    _use_conn(w, conn)
    await w._get_lead_attempts_today("lead-1")
    assert "UTC" in conn.calls[0][1]


@pytest.mark.asyncio
async def test_dialer8_cap_deferral_wakes_at_local_midnight():
    rules = CallingRules(timezone="Pacific/Auckland", max_calls_per_lead_per_day=1,
                         **{k: v for k, v in _OPEN_ALWAYS.items() if k != "timezone"})
    w = _worker(rules=rules)
    w.rules_engine.can_make_call = AsyncMock(return_value=(False, "daily_lead_cap_reached_1/1"))
    await w.process_job(_job())
    w._get_lead_attempts_today.assert_awaited_once_with("lead-abc", "Pacific/Auckland")
    delay = w.queue_service._redefer_inflight.await_args.kwargs["delay_seconds"]
    tz = pytz.timezone("Pacific/Auckland")
    now = datetime.now(tz)
    expected = tz.localize(
        datetime(now.year, now.month, now.day, 0, 5) + timedelta(days=1)
    ) - now
    assert abs(delay - expected.total_seconds()) < 30


# ── dialer-9: pre-answer terminal failure counts as a failed lead ───────────

def _decision(should_retry: bool):
    from app.workers.retry_policy import FailureCategory, RetryDecision

    return RetryDecision(
        should_retry=should_retry, delay_seconds=30,
        category=FailureCategory.INVALID_INPUT, reason="invalid_phone_number",
        log_message="x",
    )


@pytest.mark.asyncio
async def test_dialer9_terminal_pre_answer_failure_bumps_calls_failed(monkeypatch):
    import app.workers.retry_policy as rp

    monkeypatch.setattr(rp, "smart_decision", lambda **_k: _decision(False))
    monkeypatch.setattr(rp, "use_smart_policy", lambda: True)
    w = _worker(rules=CallingRules(**_OPEN_ALWAYS))
    w._make_call = AsyncMock(side_effect=RuntimeError("invalid number"))
    w._record_job_failure_classification = AsyncMock()
    conn = _Conn()
    _use_conn(w, conn)

    await w.process_job(_job())

    bumps = [c for c in conn.calls if "calls_failed" in c[0]]
    assert len(bumps) == 1
    sql, args = bumps[0]
    assert "tenant_id" in sql and "terminal_settled_at" in sql  # no double count
    assert "tenant-abc" in args


@pytest.mark.asyncio
async def test_dialer9_retryable_failure_does_not_bump_counters(monkeypatch):
    import app.workers.retry_policy as rp

    monkeypatch.setattr(rp, "smart_decision", lambda **_k: _decision(True))
    monkeypatch.setattr(rp, "use_smart_policy", lambda: True)
    w = _worker(rules=CallingRules(**_OPEN_ALWAYS))
    w._make_call = AsyncMock(side_effect=RuntimeError("boom"))
    w._record_job_failure_classification = AsyncMock()
    w._schedule_retry_durably = AsyncMock(return_value=True)
    conn = _Conn()
    _use_conn(w, conn)

    await w.process_job(_job())

    assert not [c for c in conn.calls if "calls_failed" in c[0]]


# ── dialer-10: restart must not shrink total_leads or reset started_at ──────

def test_dialer10_restart_total_includes_already_finished_leads():
    f = CampaignService._restart_total_leads
    assert f({"calls_completed": 500, "calls_failed": 100}, 400) == 1000
    assert f({}, 7) == 7
    assert f({"calls_completed": None, "calls_failed": None}, 7) == 7


@pytest.mark.asyncio
async def test_dialer10_started_at_is_not_restamped_on_restart():
    db = _DB()
    svc = CampaignService(db)
    await svc._update_campaign_status("c", "running", tenant_id="t", stamp_started_at=False)
    assert "started_at" not in db.log[-1][2]
    await svc._update_campaign_status("c", "running", tenant_id="t")
    assert "started_at" in db.log[-1][2]


# ── dialer-11: a worker status write must not resurrect dnc / deleted ───────

@pytest.mark.asyncio
async def test_dialer11_status_write_is_guarded_against_dnc_and_deleted():
    w = DialerWorker()
    conn = _Conn(execute_result="UPDATE 0", fetchval_result="dnc")
    _use_conn(w, conn)
    await w._update_lead_status(_job(), "pending")  # must not raise
    sql = conn.calls[0][0]
    assert "NOT IN ('dnc', 'deleted')" in sql


@pytest.mark.asyncio
async def test_dialer11_real_ownership_mismatch_still_raises():
    w = DialerWorker()
    conn = _Conn(execute_result="UPDATE 0", fetchval_result=None)
    _use_conn(w, conn)
    with pytest.raises(RuntimeError):
        await w._update_lead_status(_job(), "pending")
