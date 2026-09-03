"""Durable, idempotent dialer-to-bridge origination contract."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import DialerJob, JobStatus
from app.workers.dialer_worker import (
    CampaignStatusUnavailable,
    DialerCallIntent,
    DialerWorker,
)


TENANT_ID = "11111111-1111-4111-8111-111111111111"
CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
LEAD_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"
CALL_ID = "55555555-5555-4555-8555-555555555555"
LEG_ID = "66666666-6666-4666-8666-666666666666"


@asynccontextmanager
async def _fake_acquire(conn):
    yield conn


def _job(*, attempt_number: int = 1) -> DialerJob:
    return DialerJob(
        job_id=JOB_ID,
        campaign_id=CAMPAIGN_ID,
        lead_id=LEAD_ID,
        tenant_id=TENANT_ID,
        phone_number="+15551234567",
        attempt_number=attempt_number,
    )


def _intent(*, status: str = "initiated", provider_call_id: str | None = None):
    return DialerCallIntent(
        call_id=CALL_ID,
        talklee_call_id="TKY-DURABLE-1",
        leg_id=LEG_ID,
        status=status,
        provider_call_id=provider_call_id,
        created=False,
    )


def _ready_worker() -> DialerWorker:
    worker = DialerWorker()
    worker.queue_service = AsyncMock()
    worker._redis = None
    worker._db_pool = None
    worker._get_campaign_status = AsyncMock(return_value="running")
    worker._load_existing_call_intent = AsyncMock(return_value=None)
    worker._tenant_minutes_exhausted = AsyncMock(return_value=False)
    worker._get_tenant_rules = AsyncMock(return_value=CallingRules.default())
    worker._get_campaign_calling_config = AsyncMock(return_value={})
    worker._get_lead_last_called = AsyncMock(return_value=None)
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker._get_lead_attempts_today = AsyncMock(return_value=0)
    worker.rules_engine.can_make_call = AsyncMock(return_value=(True, ""))
    worker._resolve_batch_size = MagicMock(return_value=0)
    worker._resolve_call_gap = MagicMock(return_value=0)
    worker._campaign_seconds_since_last_dial = AsyncMock(return_value=None)
    worker._evaluate_call_guard = AsyncMock(return_value="allow")
    worker._update_job_status = AsyncMock()
    worker._update_lead_status = AsyncMock()
    worker._mark_campaign_dialed = AsyncMock()
    worker._emit_progress_event_throttled = AsyncMock()
    worker._publish_reason = AsyncMock()
    worker._record_ambiguous_attempt_state = AsyncMock(return_value=True)
    return worker


@pytest.mark.asyncio
async def test_process_job_commits_intent_before_bridge_and_never_post_inserts(monkeypatch):
    worker = _ready_worker()
    events: list[str] = []
    intent = _intent()

    async def create_intent(job):
        assert job.job_id == JOB_ID
        events.append("intent")
        return intent

    async def make_call(job, rules, *, call_intent):
        del rules
        assert job.job_id == JOB_ID
        assert call_intent is intent
        events.append("bridge")
        return "provider-call-1"

    async def bind_intent(job, call_intent, provider_call_id):
        assert job.job_id == JOB_ID
        assert call_intent is intent
        assert provider_call_id == "provider-call-1"
        events.append("bind")

    worker._create_call_intent = create_intent
    worker._make_call = make_call
    worker._bind_call_intent = bind_intent
    worker._create_call_record = AsyncMock(
        side_effect=AssertionError("post-provider INSERT must not run")
    )

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", claim
    )

    await worker.process_job(_job())

    assert events == ["intent", "bridge", "bind"]
    worker._create_call_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_bridge_result_parks_same_attempt_without_scheduling_retry(monkeypatch):
    worker = _ready_worker()
    intent = _intent()
    worker._create_call_intent = AsyncMock(return_value=intent)
    worker._make_call = AsyncMock(return_value=DialerWorker._ORIGINATION_UNCERTAIN)
    worker._bind_call_intent = AsyncMock()
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", claim
    )

    job = _job(attempt_number=2)
    await worker.process_job(job)

    worker.queue_service.schedule_retry.assert_not_awaited()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "origination_result_unknown",
    )
    worker._bind_call_intent.assert_not_awaited()
    worker._record_ambiguous_attempt_state.assert_awaited_once_with(
        job,
        intent,
        parked_status=JobStatus.RETRY_SCHEDULED,
    )
    assert job.attempt_number == 2
    assert job.call_id == CALL_ID


@pytest.mark.asyncio
async def test_same_attempt_replay_bypasses_batch_pacing_and_call_guard(monkeypatch):
    worker = _ready_worker()
    intent = _intent(status="dialing", provider_call_id="talky-out-planned")
    worker._load_existing_call_intent = AsyncMock(return_value=intent)
    worker._create_call_intent = AsyncMock(
        side_effect=AssertionError("replay must not create another intent")
    )
    worker._campaign_inflight_calls = AsyncMock(
        side_effect=AssertionError("existing intent must not consume its own batch slot")
    )
    worker._evaluate_call_guard = AsyncMock(
        side_effect=AssertionError("CallGuard side effects must not run twice")
    )
    worker.rules_engine.can_make_call = AsyncMock(
        side_effect=AssertionError("schedule gate must not rerun for a committed attempt")
    )
    worker._make_call = AsyncMock(return_value=DialerWorker._ORIGINATION_UNCERTAIN)
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)

    async def unexpected_claim(_redis, _tenant_id):
        raise AssertionError("tenant pacing must not be claimed twice")

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        unexpected_claim,
    )

    job = _job(attempt_number=2)
    await worker.process_job(job)

    worker._make_call.assert_awaited_once()
    assert worker._make_call.await_args.kwargs["call_intent"] is intent
    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "origination_result_unknown",
    )
    worker.queue_service.schedule_retry.assert_not_awaited()
    worker._create_call_intent.assert_not_awaited()
    worker._tenant_minutes_exhausted.assert_not_awaited()
    assert job.attempt_number == 2
    assert job.call_id == CALL_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("campaign_status", ["paused", "stopped"])
async def test_nonrunnable_campaign_retains_provider_bound_attempt_ownership(
    campaign_status,
):
    worker = _ready_worker()
    intent = _intent(status="termination_pending", provider_call_id="talky-out-planned")
    worker._load_existing_call_intent = AsyncMock(return_value=intent)
    worker._get_campaign_status = AsyncMock(return_value=campaign_status)
    worker._park_uncertain_origination = AsyncMock()
    worker._resume_existing_call_intent = AsyncMock()

    await worker.process_job(_job(attempt_number=2))

    worker._park_uncertain_origination.assert_awaited_once()
    worker._resume_existing_call_intent.assert_not_awaited()
    worker.queue_service.mark_skipped.assert_not_awaited()
    worker._update_job_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_campaign_terminalizes_only_provider_null_intent():
    worker = _ready_worker()
    intent = _intent(status="initiated", provider_call_id=None)
    worker._load_existing_call_intent = AsyncMock(return_value=intent)
    worker._get_campaign_status = AsyncMock(return_value="stopped")
    worker._mark_call_intent_not_originated = AsyncMock(return_value=True)
    worker._park_uncertain_origination = AsyncMock()

    await worker.process_job(_job(attempt_number=2))

    worker._mark_call_intent_not_originated.assert_awaited_once()
    worker.queue_service.mark_skipped.assert_awaited_once()
    worker._update_job_status.assert_awaited_once()
    worker._park_uncertain_origination.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_number", [1, 2])
async def test_campaign_status_outage_redefers_same_payload_without_terminal_skip(
    lookup_number,
):
    worker = _ready_worker()
    side_effect = (
        [CampaignStatusUnavailable("db unavailable")]
        if lookup_number == 1
        else ["running", CampaignStatusUnavailable("db unavailable")]
    )
    worker._get_campaign_status = AsyncMock(side_effect=side_effect)
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)
    job = _job(attempt_number=2)

    await worker.process_job(job)

    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "campaign_status_unavailable",
    )
    worker.queue_service.mark_skipped.assert_not_awaited()
    worker.queue_service.schedule_retry.assert_not_awaited()
    assert job.attempt_number == 2


@pytest.mark.asyncio
async def test_explicitly_missing_campaign_still_terminally_skips_job():
    worker = _ready_worker()
    worker._get_campaign_status = AsyncMock(return_value=None)

    await worker.process_job(_job())

    worker.queue_service.mark_skipped.assert_awaited_once()
    worker._update_job_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_provider_metadata_failure_never_schedules_attempt_n_plus_one(
    monkeypatch,
):
    """A live provider leg must stay on the durable attempt after local DB failure."""
    worker = _ready_worker()
    intent = _intent()
    worker._create_call_intent = AsyncMock(return_value=intent)
    worker._make_call = AsyncMock(return_value="talky-out-planned")
    worker._bind_call_intent = AsyncMock()
    worker._mark_call_intent_not_originated = AsyncMock(return_value=False)
    worker._update_lead_status = AsyncMock(
        side_effect=RuntimeError("post-provider metadata unavailable")
    )
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        claim,
    )

    job = _job(attempt_number=2)
    await worker.process_job(job)

    worker._mark_call_intent_not_originated.assert_awaited_once()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "origination_result_unknown",
    )
    worker.queue_service.schedule_retry.assert_not_awaited()
    assert job.attempt_number == 2
    assert job.call_id == CALL_ID


@pytest.mark.asyncio
async def test_cancellation_after_intent_redefers_same_attempt_before_propagating(
    monkeypatch,
):
    worker = _ready_worker()
    intent = _intent()
    worker._create_call_intent = AsyncMock(return_value=intent)
    worker._make_call = AsyncMock(side_effect=asyncio.CancelledError())
    worker._bind_call_intent = AsyncMock()
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", claim
    )

    job = _job(attempt_number=2)
    with pytest.raises(asyncio.CancelledError):
        await worker.process_job(job)

    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "origination_result_unknown",
    )
    worker.queue_service.schedule_retry.assert_not_awaited()
    worker._record_ambiguous_attempt_state.assert_awaited_once_with(
        job,
        intent,
        parked_status=JobStatus.RETRY_SCHEDULED,
    )
    assert job.attempt_number == 2
    assert job.call_id == CALL_ID


@pytest.mark.asyncio
async def test_cancellation_before_intent_redefers_same_payload_before_propagating(
    monkeypatch,
):
    worker = _ready_worker()
    worker._create_call_intent = AsyncMock(side_effect=asyncio.CancelledError())
    worker._make_call = AsyncMock()
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", claim
    )

    job = _job(attempt_number=2)
    with pytest.raises(asyncio.CancelledError):
        await worker.process_job(job)

    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "worker_cancelled_before_origination",
    )
    worker.queue_service.schedule_retry.assert_not_awaited()
    worker._make_call.assert_not_awaited()
    worker._update_job_status.assert_awaited_with(
        job,
        JobStatus.RETRY_SCHEDULED,
        error="worker_cancelled_before_origination",
    )
    assert job.attempt_number == 2
    assert job.call_id is None


@pytest.mark.asyncio
async def test_real_task_cancellation_after_intent_waits_for_same_attempt_redefer(
    monkeypatch,
):
    worker = _ready_worker()
    intent = _intent()
    worker._create_call_intent = AsyncMock(return_value=intent)
    entered_bridge = asyncio.Event()
    redefer_finished = asyncio.Event()

    async def blocked_bridge(*_args, **_kwargs):
        entered_bridge.set()
        await asyncio.Event().wait()

    async def redefer(*_args):
        await asyncio.sleep(0)
        redefer_finished.set()
        return True

    worker._make_call = blocked_bridge
    worker.queue_service._redefer_inflight = AsyncMock(side_effect=redefer)

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        claim,
    )

    job = _job(attempt_number=2)
    task = asyncio.create_task(worker.process_job(job))
    await entered_bridge.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert redefer_finished.is_set()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "origination_result_unknown",
    )
    worker.queue_service.schedule_retry.assert_not_awaited()
    assert job.attempt_number == 2
    assert job.call_id == CALL_ID


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_same_attempt_redefer(monkeypatch):
    worker = _ready_worker()
    intent = _intent()
    worker._create_call_intent = AsyncMock(return_value=intent)
    entered_bridge = asyncio.Event()
    redefer_started = asyncio.Event()
    release_redefer = asyncio.Event()
    redefer_finished = asyncio.Event()

    async def blocked_bridge(*_args, **_kwargs):
        entered_bridge.set()
        await asyncio.Event().wait()

    async def blocked_redefer(*_args):
        redefer_started.set()
        await release_redefer.wait()
        redefer_finished.set()
        return True

    worker._make_call = blocked_bridge
    worker.queue_service._redefer_inflight = AsyncMock(side_effect=blocked_redefer)

    async def claim(_redis, _tenant_id):
        return 0

    monkeypatch.setattr(
        "app.domain.services.dialer.global_pacing.claim_tenant_dial_slot",
        claim,
    )

    job = _job(attempt_number=2)
    task = asyncio.create_task(worker.process_job(job))
    await entered_bridge.wait()
    task.cancel()
    await redefer_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    parent_waited_for_handoff = not task.done()

    release_redefer.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert parent_waited_for_handoff
    assert redefer_finished.is_set()
    assert job.attempt_number == 2


@pytest.mark.asyncio
async def test_real_task_cancellation_before_intent_resolution_redefers_same_payload():
    worker = _ready_worker()
    entered_lookup = asyncio.Event()
    redefer_finished = asyncio.Event()

    async def blocked_lookup(_job):
        entered_lookup.set()
        await asyncio.Event().wait()

    async def redefer(*_args):
        await asyncio.sleep(0)
        redefer_finished.set()
        return True

    worker._load_existing_call_intent = blocked_lookup
    worker.queue_service._redefer_inflight = AsyncMock(side_effect=redefer)

    job = _job(attempt_number=2)
    task = asyncio.create_task(worker.process_job(job))
    await entered_lookup.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert redefer_finished.is_set()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        JOB_ID,
        "worker_cancelled_before_origination",
    )
    worker.queue_service.schedule_retry.assert_not_awaited()
    assert job.attempt_number == 2
    assert job.call_id is None


class _IntentConn:
    def __init__(self, *, inserted: bool) -> None:
        self.inserted = inserted
        self.queries: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, query, *args):
        normalized = " ".join(query.split()).lower()
        self.queries.append((normalized, args))
        if normalized.startswith("set local") or normalized.startswith("select set_config"):
            return "SELECT 1"
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        normalized = " ".join(query.split()).lower()
        self.queries.append((normalized, args))
        if "insert into calls" in normalized:
            if not self.inserted:
                return None
            return {
                "id": CALL_ID,
                "talklee_call_id": "TKY-DURABLE-1",
                "status": "initiated",
                "provider_call_id": None,
            }
        if "from calls" in normalized:
            return {
                "id": CALL_ID,
                "talklee_call_id": "TKY-DURABLE-1",
                "status": "initiated",
                "provider_call_id": None,
                "leg_id": LEG_ID,
                "tenant_id": TENANT_ID,
                "campaign_id": CAMPAIGN_ID,
                "lead_id": LEAD_ID,
                "phone_number": "+15551234567",
                "direction": "outbound",
            }
        raise AssertionError(normalized)


class _Pool:
    def __init__(self, conn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        yield self.conn


@pytest.mark.asyncio
async def test_call_intent_is_unique_per_job_attempt_and_contains_no_provider_identity(
):
    worker = DialerWorker()
    conn = _IntentConn(inserted=True)
    worker._db_pool = _Pool(conn)
    # Keep deterministic IDs without depending on UUID's implementation details.
    worker._new_call_identity = lambda: (CALL_ID, "TKY-DURABLE-1", LEG_ID)
    intent = await worker._create_call_intent(_job(attempt_number=3))

    insert_query, insert_args = next(
        (query, args) for query, args in conn.queries if "insert into calls" in query
    )
    assert "dialer_attempt_number" in insert_query
    assert "on conflict (dialer_job_id, dialer_attempt_number)" in insert_query
    assert "provider_call_id" not in insert_query.split("returning", 1)[0]
    assert JOB_ID in insert_args
    assert 3 in insert_args
    assert intent.call_id == CALL_ID
    assert intent.provider_call_id is None


@pytest.mark.asyncio
async def test_replayed_same_attempt_reuses_existing_row_without_duplicate_leg_or_event():
    worker = DialerWorker()
    conn = _IntentConn(inserted=False)
    worker._db_pool = _Pool(conn)
    worker._new_call_identity = lambda: (
        "77777777-7777-4777-8777-777777777777",
        "TKY-THROWN-AWAY",
        "88888888-8888-4888-8888-888888888888",
    )

    intent = await worker._create_call_intent(_job())

    assert intent.call_id == CALL_ID
    assert intent.leg_id == LEG_ID
    assert intent.created is False
    assert not [
        query
        for query, _args in conn.queries
        if "insert into call_legs" in query or "insert into call_events" in query
    ]


@pytest.mark.asyncio
async def test_call_intent_database_failure_is_fail_closed():
    class _BrokenConn(_IntentConn):
        async def fetchrow(self, query, *args):
            raise RuntimeError("database unavailable")

    worker = DialerWorker()
    worker._db_pool = _Pool(_BrokenConn(inserted=False))

    with pytest.raises(RuntimeError, match="durable call intent"):
        await worker._create_call_intent(_job())


class _ScopedWriteConn:
    def __init__(self, result: str = "UPDATE 1"):
        self.result = result
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected_table"),
    [
        ("lead", "UPDATE leads"),
        ("clear", "UPDATE leads"),
        ("classification", "UPDATE dialer_jobs"),
    ],
)
async def test_bypass_worker_writes_require_full_job_ownership(
    operation,
    expected_table,
):
    worker = DialerWorker()
    conn = _ScopedWriteConn()
    worker._acquire_db = lambda: _fake_acquire(conn)
    job = _job()

    if operation == "lead":
        await worker._update_lead_status(job, "calling")
    elif operation == "clear":
        await worker._clear_lead_last_called(job)
    else:
        await worker._record_job_failure_classification(
            job=job,
            category="provider",
            reason="failed",
        )

    sql, args = conn.calls[-1]
    assert expected_table in sql
    assert "tenant_id" in sql
    assert "campaign_id" in sql
    assert str(job.tenant_id) in args
    assert str(job.campaign_id) in args


@pytest.mark.asyncio
async def test_scoped_worker_update_zero_is_not_reported_as_success():
    worker = DialerWorker()
    conn = _ScopedWriteConn(result="UPDATE 0")
    worker._acquire_db = lambda: _fake_acquire(conn)

    with pytest.raises(RuntimeError, match="ownership"):
        await worker._update_lead_status(_job(), "calling")


@pytest.mark.asyncio
async def test_provider_bind_requires_exact_outbound_leg_update():
    class _MissingLegConn(_IntentConn):
        async def execute(self, query, *args):
            normalized = " ".join(query.split()).lower()
            if "update calls" in normalized:
                return "UPDATE 1"
            if "update call_legs" in normalized:
                return "UPDATE 0"
            return await super().execute(query, *args)

    worker = DialerWorker()
    worker._db_pool = _Pool(_MissingLegConn(inserted=False))
    worker._last_provider_name = "asterisk"

    with pytest.raises(RuntimeError, match="durable call leg bind affected UPDATE 0"):
        await worker._bind_call_intent(
            _job(attempt_number=2),
            _intent(),
            "talky-out-planned",
        )


@pytest.mark.asyncio
async def test_ambiguous_redefer_cannot_reactivate_bridge_settled_job_or_lead():
    class _AlreadySettledConn:
        def __init__(self):
            self.queries = []

        @asynccontextmanager
        async def transaction(self):
            yield self

        async def execute(self, query, *args):
            normalized = " ".join(query.split()).lower()
            if normalized.startswith("set local") or normalized.startswith(
                "select set_config"
            ):
                return "SELECT 1"
            self.queries.append((normalized, args))
            if "update dialer_jobs" in normalized:
                # The real predicate sees calls.status='failed' and therefore
                # refuses to resurrect the already-settled job.
                assert "call_row.status <> all" in normalized
                return "UPDATE 0"
            raise AssertionError("terminal call must prevent the lead update")

    conn = _AlreadySettledConn()
    worker = DialerWorker()
    worker._db_pool = _Pool(conn)

    recorded = await worker._record_ambiguous_attempt_state(
        _job(attempt_number=2),
        _intent(status="failed", provider_call_id="talky-out-planned"),
        parked_status=JobStatus.RETRY_SCHEDULED,
    )

    assert recorded is False
    assert len(conn.queries) == 1


def test_migration_enforces_one_call_row_per_dialer_attempt():
    source = (
        __import__("pathlib").Path(__file__).parents[2]
        / "Alembic"
        / "versions"
        / "0042_dialer_origination_guard.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "0042_dialer_origination_guard"' in source
    assert len("0042_dialer_origination_guard") <= 32
    assert 'down_revision = "0041_tenant_campaign_fk"' in source
    assert "dialer_attempt_number" in source
    assert "UNIQUE INDEX" in source.upper()
    assert "dialer_job_id, dialer_attempt_number" in source
