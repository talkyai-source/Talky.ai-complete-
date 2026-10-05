"""Actual settlement boundary: suppression is separate from call outcome."""
from unittest.mock import AsyncMock

import pytest

from app.core.security.tenant_isolation import clear_tenant_context, set_bypass_rls
from app.domain.models.dialer_job import CallOutcome
from tests.unit.test_call_service_pooled_teardown import _FakeConn, _service

TENANT = "20000000-0000-4000-8000-000000000017"
PHONE = "+15555550107"


class SuppressionConn(_FakeConn):
    def __init__(self, *, suppression=True, settled=False, phone=PHONE):
        super().__init__(
            calls_row={"id": "call-1", "tenant_id": TENANT, "phone_number": phone,
                       "lead_id": "lead-1", "campaign_id": "camp-1",
                       "dialer_job_id": "job-1", "status": "in_progress"},
            leads_row={"id": "lead-1", "status": "dnc", "last_call_result": "caller_opt_out",
                       "call_attempts": 0, "do_not_call": False},
            dialer_jobs_row={"id": "job-1", "tenant_id": TENANT, "status": "processing",
                             "attempt_number": 1, "phone_number": PHONE, "priority": 5},
        )
        self.suppression = suppression
        if settled:
            self.calls_row.update(status="completed", outcome="failed", ended_at="NOW",
                                  duration_seconds=42, terminal_settled_at="NOW",
                                  terminal_retry_payload={"job_id": "job-1", "job_data": {"phone_number": PHONE},
                                    "outcome": "failed", "campaign_id": "camp-1", "lead_id": "lead-1",
                                    "tenant_id": TENANT, "attempt_number": 1, "delay_seconds": 30})
            self.leads_row["call_attempts"] = 1
            self.dialer_jobs_row["status"] = "retry_scheduled"

    async def fetchval(self, sql, *args):
        if "FROM dnc_entries" in sql:
            self.executed.append((" ".join(sql.split()), args))
            if isinstance(self.suppression, Exception):
                raise self.suppression
            assert args == (TENANT, PHONE)
            return self.suppression
        return await super().fetchval(sql, *args)

    async def execute(self, sql, *args):
        if "UPDATE leads" in sql and "call_attempts" not in sql:
            self.leads_row.update(status="dnc", last_call_result="caller_opt_out")
            return "UPDATE 1"
        if "UPDATE dialer_jobs" in sql and "caller_opt_out" in sql:
            if self.dialer_jobs_row["status"] in args[2]:
                self.dialer_jobs_row.update(status="non_retryable", failure_reason="caller_opt_out")
            return "UPDATE 1"
        if "UPDATE calls" in sql and "terminal_retry_payload = NULL" in sql:
            self.calls_row.update(terminal_retry_payload=None, terminal_retry_enqueued_at=None)
            return "UPDATE 1"
        return await super().execute(sql, *args)


@pytest.fixture(autouse=True)
def context():
    set_bypass_rls(True)
    yield
    clear_tenant_context()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [CallOutcome.ANSWERED, CallOutcome.GOAL_ACHIEVED, CallOutcome.FAILED])
async def test_active_opt_out_preserves_projection_without_rewriting_call_outcome(outcome):
    conn = SuppressionConn()
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)
    execution = await service._handle_call_status_pooled("call-1", outcome, outcome.value, 42)
    assert conn.calls_row["outcome"] == outcome.value
    assert conn.calls_row["duration_seconds"] == 42
    assert conn.leads_row["status"] == "dnc"
    assert conn.leads_row["last_call_result"] == "caller_opt_out"
    assert conn.leads_row["do_not_call"] is False
    assert conn.leads_row["call_attempts"] == 1
    assert conn.dialer_jobs_row["last_outcome"] == outcome.value
    assert conn.dialer_jobs_row["status"] == "non_retryable"
    assert conn.dialer_jobs_row["failure_reason"] == "caller_opt_out"
    assert execution.retry_args is None
    assert conn.calls_row["terminal_retry_payload"] is None


@pytest.mark.asyncio
async def test_opt_out_blocks_pending_outbox_replay_without_recounting():
    conn = SuppressionConn(settled=True)
    execution = await _service(conn)._handle_call_status_pooled("call-1", CallOutcome.FAILED, "failed", 42)
    assert execution.retry_args is None
    assert conn.calls_row["terminal_retry_payload"] is None
    assert conn.calls_row["outcome"] == "failed"
    assert conn.leads_row["call_attempts"] == 1
    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["processing", "calling"])
async def test_old_pending_outbox_cannot_release_newer_live_attempt(status):
    conn = SuppressionConn(settled=True)
    conn.dialer_jobs_row.update(status=status, attempt_number=2)
    execution = await _service(conn)._handle_call_status_pooled("call-1", CallOutcome.FAILED, "failed", 42)
    assert execution.retry_args is None
    assert conn.dialer_jobs_row["status"] == status
    assert conn.dialer_jobs_row["attempt_number"] == 2


@pytest.mark.asyncio
async def test_removed_suppression_keeps_existing_failed_call_retry_policy():
    conn = SuppressionConn(suppression=False)
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)
    execution = await service._handle_call_status_pooled("call-1", CallOutcome.FAILED, "failed", 42)
    assert execution.retry_args is not None
    assert conn.leads_row["status"] == "called"


@pytest.mark.asyncio
@pytest.mark.parametrize("conn", [SuppressionConn(suppression=RuntimeError("synthetic storage outage")),
                                  SuppressionConn(phone="not a number")])
async def test_unavailable_suppression_proof_cannot_book_retry(conn):
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)
    result = await service.handle_call_status("call-1", CallOutcome.FAILED, 42)
    assert result.durable is False
    service._queue_service.schedule_retry.assert_not_awaited()
    assert conn.calls_row["terminal_retry_payload"] is None
