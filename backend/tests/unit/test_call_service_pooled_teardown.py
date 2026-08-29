"""FIX 1(b) (2026-07-13): the pooled call-teardown path
(`CallService._handle_call_status_pooled`) must finalize BOTH the lead
and the dialer_job on the happy path (the calls row is found — always
true for dialer calls, which pre-create it), not only on the
"call row not found" fallback branch it used to be restricted to.

Root cause recap: `job_id` was hard-coded to `None` whenever the initial
`SELECT id, lead_id, campaign_id FROM calls` found a row (the ALWAYS-true
case for dialer calls), so `_handle_job_completion_pooled` was dead and
the lead-status update (which only ran in the "not found" fallback) never
ran either. `leads.status` stayed "calling" forever, `call_attempts` never
incremented, and `dialer_jobs` stayed PROCESSING forever.

Also covers the idempotency guard: teardown can be driven twice for the
same call (ARI terminal-event bursts, or a worker restart clearing the
in-process `_ended_calls_in_flight` / adapter `_end_dispatched` guards).
Running `_handle_call_status_pooled` a second time for an already-
`completed` call must be a no-op — no double call_attempts increment, no
double campaign-counter bump, and no double-scheduled retry.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import CallOutcome
from app.domain.services.call_service import CallService
from app.core.security.tenant_isolation import (
    clear_tenant_context,
    set_bypass_rls,
    set_current_tenant_id,
)


# ──────────────────────────────────────────────────────────────────
# Fakes — a tiny in-memory Postgres stand-in for `calls` / `leads` /
# `dialer_jobs` / `campaigns`, dispatched by matching on (normalized)
# query text, the same style as `test_call_metrics_persist.py`'s
# `_FakeConn`/`_FakePool`.
# ──────────────────────────────────────────────────────────────────


class _FakeConn:
    def __init__(
        self,
        calls_row: Optional[dict],
        leads_row: Optional[dict] = None,
        dialer_jobs_row: Optional[dict] = None,
    ) -> None:
        self.calls_row = calls_row
        if self.calls_row is not None:
            self.calls_row.setdefault("outcome", None)
            self.calls_row.setdefault("ended_at", None)
            self.calls_row.setdefault("duration_seconds", None)
            self.calls_row.setdefault("terminal_settled_at", None)
            self.calls_row.setdefault("terminal_retry_payload", None)
            self.calls_row.setdefault("terminal_retry_enqueued_at", None)
        self.leads_row = leads_row
        self.dialer_jobs_row = dialer_jobs_row
        self.campaigns_row = {"calls_completed": 0, "calls_failed": 0}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._tx_lock = asyncio.Lock()

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self):
                await outer._tx_lock.acquire()
                return outer

            async def __aexit__(self, *a):
                outer._tx_lock.release()
                return None

        return _Tx()

    # -- write path -----------------------------------------------------
    async def execute(self, query: str, *args: Any) -> None:
        q = " ".join(query.split())
        self.executed.append((q, args))

        if q.startswith("SET LOCAL"):
            return

        if "UPDATE calls" in q and "status = 'completed'" in q:
            call_uuid = args[0]
            outcome_value = args[1]
            if self.calls_row and self.calls_row["id"] == call_uuid:
                self.calls_row["status"] = "completed"
                self.calls_row["outcome"] = outcome_value
                if len(args) > 2:
                    self.calls_row["duration_seconds"] = args[2]
            return

        if "UPDATE leads" in q:
            lead_id, lead_status, last_call_result, call_attempts = args
            if self.leads_row and self.leads_row["id"] == lead_id:
                self.leads_row["status"] = lead_status
                self.leads_row["last_call_result"] = last_call_result
                self.leads_row["call_attempts"] = call_attempts
            return

        if "UPDATE campaigns" in q:
            if "calls_failed" in q:
                self.campaigns_row["calls_failed"] += 1
            else:
                self.campaigns_row["calls_completed"] += 1
            return

    # -- read path --------------------------------------------------------
    async def fetchrow(self, query: str, *args: Any):
        q = " ".join(query.split())
        self.executed.append((q, args))

        if q.startswith("UPDATE calls"):
            if not self.calls_row or self.calls_row["id"] != args[0]:
                return None
            if "terminal_retry_enqueued_at = COALESCE" in q:
                self.calls_row["terminal_retry_enqueued_at"] = "NOW"
                return dict(self.calls_row)
            if "terminal_settled_at = COALESCE" in q:
                self.calls_row["terminal_settled_at"] = "NOW"
                self.calls_row["terminal_retry_payload"] = args[1]
                self.calls_row["terminal_retry_enqueued_at"] = None
                return dict(self.calls_row)
            if "status = 'completed'" in q:
                self.calls_row["status"] = "completed"
                self.calls_row["outcome"] = args[1]
                if args[2] is not None:
                    self.calls_row["duration_seconds"] = args[2]
                self.calls_row["ended_at"] = self.calls_row["ended_at"] or "NOW"
                return dict(self.calls_row)
            # Existing terminal: fill missing facts only.
            self.calls_row["outcome"] = self.calls_row["outcome"] or args[1]
            if self.calls_row["duration_seconds"] is None and args[2] is not None:
                self.calls_row["duration_seconds"] = args[2]
            self.calls_row["ended_at"] = self.calls_row["ended_at"] or "NOW"
            return dict(self.calls_row)

        if "FROM calls WHERE id" in q:
            call_uuid = args[0]
            if self.calls_row and self.calls_row["id"] == call_uuid:
                return dict(self.calls_row)
            return None

        if q.startswith("SELECT * FROM dialer_jobs WHERE id"):
            job_id = args[0]
            if self.dialer_jobs_row and self.dialer_jobs_row["id"] == job_id:
                return dict(self.dialer_jobs_row)
            return None

        return None

    async def fetchval(self, query: str, *args: Any):
        q = " ".join(query.split())
        self.executed.append((q, args))

        if "SELECT call_attempts FROM leads" in q:
            lead_id = args[0]
            if self.leads_row and self.leads_row["id"] == lead_id:
                return self.leads_row.get("call_attempts", 0)
            return None

        if q.startswith("UPDATE dialer_jobs") and "RETURNING id" in q:
            job_id, status_val, outcome_val, reason, allowed_statuses = args
            row = self.dialer_jobs_row
            if (
                row
                and row["id"] == job_id
                and row.get("status") in set(allowed_statuses)
            ):
                row["status"] = status_val
                row["last_outcome"] = outcome_val
                row["failure_reason"] = reason
                if "completed_at" in q:
                    row["completed_at"] = "NOW"
                return job_id
            return None  # already finalized — idempotency guard tripped

        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self, timeout: Optional[float] = None):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return outer._conn

            async def __aexit__(self, *a):
                return None

        return _Ctx()


def _service(conn: _FakeConn) -> CallService:
    return CallService(
        db_client=AsyncMock(),
        queue_service=AsyncMock(),
        call_repo=AsyncMock(),
        lead_repo=AsyncMock(),
        db_pool=_FakePool(conn),
    )


@pytest.fixture(autouse=True)
def _rls_bypass_context():
    """Every real teardown caller sets bypass_rls before calling
    handle_call_status; mirror that so the RuntimeError guard doesn't
    trip, and reset afterwards so tests don't leak state into each
    other via the module-level contextvars."""
    set_bypass_rls(True)
    set_current_tenant_id("00000000-0000-0000-0000-000000000000")
    yield
    # Full reset, not just bypass_rls: the previous version left
    # `_tenant_context` set to the nil UUID for the rest of the pytest
    # session (contextvars aren't per-test in this pytest-asyncio setup),
    # which leaked into any later test/code that reads
    # get_current_tenant_id() as an ambient "am I in a tenant request"
    # signal — e.g. CampaignService's defense-in-depth tenant filters.
    clear_tenant_context()


# ──────────────────────────────────────────────────────────────────
# Happy path: lead + job finalized together, in one transaction
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["processing", "calling"])
async def test_pooled_teardown_finalizes_lead_and_job_on_happy_path(job_status):
    conn = _FakeConn(
        calls_row={
            "id": "call-1", "lead_id": "lead-1", "campaign_id": "camp-1",
            "dialer_job_id": "job-1", "status": "initiated",
        },
        leads_row={
            "id": "lead-1", "call_attempts": 2, "status": "calling",
        },
        dialer_jobs_row={
            "id": "job-1", "status": job_status, "attempt_number": 1,
            "tenant_id": "tenant-1", "priority": 5, "phone_number": "+15550001111",
        },
    )
    service = _service(conn)

    job_id, campaign_id, retry_args = await service._handle_call_status_pooled(
        "call-1", CallOutcome.ANSWERED, "answered", duration=42,
    )

    # calls row updated
    assert conn.calls_row["status"] == "completed"
    assert conn.calls_row["outcome"] == "answered"
    assert conn.calls_row["duration_seconds"] == 42

    # lead finalized: THIS was dead before the fix (only ran on the
    # never-hit "call row not found" branch).
    assert conn.leads_row["status"] == "contacted"
    assert conn.leads_row["call_attempts"] == 3, "call_attempts must increment"

    # campaign counters bumped
    assert conn.campaigns_row["calls_completed"] == 1

    # dialer_job moved out of PROCESSING — also dead before the fix,
    # since job_id was hard-coded None on this path.
    assert job_id == "job-1"
    assert campaign_id == "camp-1"
    assert conn.dialer_jobs_row["status"] == "completed"
    assert conn.dialer_jobs_row.get("completed_at") is not None
    # ANSWERED is a success outcome — no retry due.
    assert retry_args is None


@pytest.mark.asyncio
async def test_pooled_teardown_schedules_retry_for_retryable_outcome():
    conn = _FakeConn(
        calls_row={
            "id": "call-2", "lead_id": "lead-2", "campaign_id": "camp-1",
            "dialer_job_id": "job-2", "status": "initiated",
        },
        leads_row={"id": "lead-2", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={
            "id": "job-2", "status": "processing", "attempt_number": 1,
            "tenant_id": "tenant-1", "priority": 5, "phone_number": "+15550002222",
        },
    )
    service = _service(conn)

    job_id, campaign_id, retry_args = await service._handle_call_status_pooled(
        "call-2", CallOutcome.BUSY, "busy", duration=0,
    )

    assert conn.dialer_jobs_row["status"] == "retry_scheduled"
    assert retry_args is not None
    # positional layout: job_id, job_data, outcome, campaign_id, lead_id,
    # tenant_id, attempt_number, delay_seconds — see
    # `_handle_job_completion_pooled`.
    assert retry_args[0] == "job-2"
    assert retry_args[2] == CallOutcome.BUSY
    assert retry_args[6] == 1  # attempt_number
    assert retry_args[7] == 5 * 60  # BUSY's first retry delay (5 minutes)


# ──────────────────────────────────────────────────────────────────
# Idempotency: running teardown twice must not double-apply
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pooled_teardown_is_idempotent_on_repeat_run():
    conn = _FakeConn(
        calls_row={
            "id": "call-3", "lead_id": "lead-3", "campaign_id": "camp-1",
            "dialer_job_id": "job-3", "status": "initiated",
        },
        leads_row={"id": "lead-3", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={
            "id": "job-3", "status": "processing", "attempt_number": 1,
            "tenant_id": "tenant-1", "priority": 5, "phone_number": "+15550003333",
        },
    )
    service = _service(conn)

    # First teardown — normal finalize.
    job_id_1, campaign_id_1, retry_args_1 = await service._handle_call_status_pooled(
        "call-3", CallOutcome.ANSWERED, "answered", duration=10,
    )
    assert job_id_1 == "job-3"
    assert conn.leads_row["call_attempts"] == 1
    assert conn.campaigns_row["calls_completed"] == 1
    assert conn.dialer_jobs_row["status"] == "completed"

    # Second, duplicate teardown for the SAME call (simulating a repeated
    # dispatch that slipped past the in-process guards).
    job_id_2, campaign_id_2, retry_args_2 = await service._handle_call_status_pooled(
        "call-3", CallOutcome.ANSWERED, "answered", duration=10,
    )

    # Nothing re-applied: no second attempts increment, no second counter
    # bump, no retry scheduled.
    assert job_id_2 is None
    assert campaign_id_2 is None
    assert retry_args_2 is None
    assert conn.leads_row["call_attempts"] == 1, "must not double-increment"
    assert conn.campaigns_row["calls_completed"] == 1, "must not double-count"


@pytest.mark.asyncio
async def test_pooled_teardown_idempotent_does_not_double_schedule_retry():
    """Same as above but with a RETRYABLE outcome — the higher-stakes
    case, since a leaked double-run here would enqueue two retry jobs
    for the same lead."""
    conn = _FakeConn(
        calls_row={
            "id": "call-4", "lead_id": "lead-4", "campaign_id": "camp-1",
            "dialer_job_id": "job-4", "status": "initiated",
        },
        leads_row={"id": "lead-4", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={
            "id": "job-4", "status": "processing", "attempt_number": 1,
            "tenant_id": "tenant-1", "priority": 5, "phone_number": "+15550004444",
        },
    )
    service = _service(conn)

    _, _, retry_args_1 = await service._handle_call_status_pooled(
        "call-4", CallOutcome.BUSY, "busy", duration=0,
    )
    assert retry_args_1 is not None

    _, _, retry_args_2 = await service._handle_call_status_pooled(
        "call-4", CallOutcome.BUSY, "busy", duration=0,
    )
    assert retry_args_2 is not None
    assert retry_args_2[0] == retry_args_1[0]
    assert retry_args_2[2:] == retry_args_1[2:]
    assert retry_args_2[1]["priority"] == retry_args_1[1]["priority"], (
        "until Redis dispatch is acknowledged, the durable outbox must replay "
        "the same retry instruction; schedule_retry_once makes that replay "
        "idempotent"
    )
    assert conn.leads_row["call_attempts"] == 1
    assert conn.campaigns_row["calls_completed"] == 0


@pytest.mark.asyncio
async def test_pooled_teardown_call_not_found_returns_none_triple():
    conn = _FakeConn(calls_row=None)
    service = _service(conn)

    result = await service._handle_call_status_pooled(
        "missing-call", CallOutcome.ANSWERED, "answered", duration=0,
    )
    assert tuple(result) == (None, None, None)
    assert result.result.durable is False


@pytest.mark.asyncio
async def test_pooled_teardown_skips_job_completion_for_non_dialer_call():
    """A call with no dialer_job_id (e.g. a manual/inbound call) must
    still get its lead + campaign counters updated, but job completion
    is correctly skipped (job_id stays None)."""
    conn = _FakeConn(
        calls_row={
            "id": "call-5", "lead_id": "lead-5", "campaign_id": "camp-1",
            "dialer_job_id": None, "status": "initiated",
        },
        leads_row={"id": "lead-5", "call_attempts": 0, "status": "new"},
    )
    service = _service(conn)

    job_id, campaign_id, retry_args = await service._handle_call_status_pooled(
        "call-5", CallOutcome.ANSWERED, "answered", duration=5,
    )

    assert job_id is None
    assert campaign_id == "camp-1"
    assert retry_args is None
    assert conn.leads_row["call_attempts"] == 1
    assert conn.campaigns_row["calls_completed"] == 1


@pytest.mark.asyncio
async def test_endpoint_terminal_then_concurrent_callbacks_settle_side_effects_once():
    """The endpoint owns terminal truth; one callback owns settlement effects."""

    conn = _FakeConn(
        calls_row={
            "id": "call-endpoint",
            "lead_id": "lead-endpoint",
            "campaign_id": "camp-1",
            "dialer_job_id": "job-endpoint",
            "status": "ended",
            "outcome": "cancelled",
            "ended_at": "ENDPOINT_TIME",
            "duration_seconds": 7,
        },
        leads_row={
            "id": "lead-endpoint", "call_attempts": 0, "status": "calling",
        },
        dialer_jobs_row={
            "id": "job-endpoint",
            "status": "processing",
            "attempt_number": 1,
            "tenant_id": "tenant-1",
            "priority": 5,
            "phone_number": "+15550009999",
        },
    )
    service = _service(conn)

    first, second = await asyncio.gather(
        service._handle_call_status_pooled(
            "call-endpoint", CallOutcome.NO_ANSWER, "no_answer", duration=30,
        ),
        service._handle_call_status_pooled(
            "call-endpoint", CallOutcome.ANSWERED, "answered", duration=31,
        ),
    )

    assert sorted([first.result.applied, second.result.applied]) == [False, True]
    assert first.result.durable and second.result.durable
    assert conn.calls_row["status"] == "ended"
    assert conn.calls_row["outcome"] == "cancelled"
    assert conn.calls_row["duration_seconds"] == 7
    assert conn.calls_row["ended_at"] == "ENDPOINT_TIME"
    assert conn.leads_row["call_attempts"] == 1
    assert conn.campaigns_row["calls_completed"] == 1
    assert conn.campaigns_row["calls_failed"] == 0
    assert conn.dialer_jobs_row["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_outbox_replays_fail_once_enqueue_and_acks_exactly_one_job():
    conn = _FakeConn(
        calls_row={
            "id": "call-retry",
            "lead_id": "lead-retry",
            "campaign_id": "camp-1",
            "dialer_job_id": "job-retry",
            "status": "initiated",
        },
        leads_row={"id": "lead-retry", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={
            "id": "job-retry",
            "status": "processing",
            "attempt_number": 1,
            "tenant_id": "tenant-1",
            "priority": 5,
            "phone_number": "+15550008888",
        },
    )

    class FailOnceQueue:
        def __init__(self):
            self.attempts = 0
            self.keys: set[str] = set()
            self.confirmed: list[str] = []

        async def get_live_attempt_number(self, _job_id):
            return None

        async def schedule_retry_once(
            self, _job, *, delay_seconds, idempotency_key,
        ):
            assert delay_seconds == 5 * 60
            self.attempts += 1
            if self.attempts == 1:
                return False
            self.keys.add(idempotency_key)
            return True

        async def confirm_retry_once(self, idempotency_key):
            self.confirmed.append(idempotency_key)
            return True

    queue = FailOnceQueue()
    service = CallService(
        db_client=AsyncMock(),
        queue_service=queue,
        call_repo=AsyncMock(),
        lead_repo=AsyncMock(),
        db_pool=_FakePool(conn),
    )

    failed = await service.handle_call_status(
        "call-retry", CallOutcome.BUSY, duration=0,
    )
    assert failed.durable is False
    assert failed.error == "retry_enqueue_failed"
    assert conn.calls_row["terminal_retry_payload"] is not None
    assert conn.calls_row["terminal_retry_enqueued_at"] is None

    recovered = await service.handle_call_status(
        "call-retry", CallOutcome.BUSY, duration=0,
    )
    assert recovered.durable is True
    assert recovered.applied is False
    assert conn.calls_row["terminal_retry_enqueued_at"] == "NOW"
    assert queue.attempts == 2
    assert len(queue.keys) == 1
    assert queue.confirmed == list(queue.keys)

    replay = await service.handle_call_status(
        "call-retry", CallOutcome.NO_ANSWER, duration=99,
    )
    assert replay.durable is True
    assert replay.applied is False
    assert queue.attempts == 2
    assert conn.leads_row["call_attempts"] == 1


@pytest.mark.asyncio
async def test_no_pool_compatibility_path_fails_closed_without_writes():
    service = CallService(
        db_client=object(),
        call_repo=object(),
        lead_repo=object(),
    )

    result = await service.handle_call_status(
        "call-without-pool",
        CallOutcome.ANSWERED,
        duration=12,
    )

    assert result.found is False
    assert result.applied is False
    assert result.durable is False
    assert result.error == "atomic_pool_required"


@pytest.mark.asyncio
async def test_pre_cutover_terminal_marker_fills_facts_without_replaying_effects():
    conn = _FakeConn(
        calls_row={
            "id": "legacy-terminal",
            "lead_id": "legacy-lead",
            "campaign_id": "legacy-campaign",
            "dialer_job_id": "legacy-job",
            "status": "ended",
            "outcome": None,
            "ended_at": None,
            "terminal_settled_at": "MIGRATION_CUTOVER",
        },
        leads_row={
            "id": "legacy-lead",
            "call_attempts": 8,
            "status": "called",
        },
        dialer_jobs_row={
            "id": "legacy-job",
            "status": "completed",
            "attempt_number": 1,
            "tenant_id": "tenant-1",
            "priority": 5,
            "phone_number": "+15550009999",
        },
    )

    result = await _service(conn)._handle_call_status_pooled(
        "legacy-terminal",
        CallOutcome.ANSWERED,
        "answered",
        duration=22,
    )

    assert result.result.applied is False
    assert result.result.durable is True
    assert conn.calls_row["status"] == "ended"
    assert conn.calls_row["outcome"] == "answered"
    assert conn.calls_row["ended_at"] == "NOW"
    assert conn.leads_row["call_attempts"] == 8
    assert conn.dialer_jobs_row["status"] == "completed"
    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 0}
