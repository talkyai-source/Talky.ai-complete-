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
@pytest.mark.parametrize("conn", [SuppressionConn(suppression=RuntimeError("synthetic storage outage"))])
async def test_unavailable_suppression_proof_cannot_book_retry(conn):
    # A storage failure while reading suppression still aborts settlement (we
    # cannot prove the number is not opted out). A destination that is not
    # strict E.164 (``phone="not a number"``, PBX extensions) is NOT this case
    # any more: no suppression row can exist for it, so settlement proceeds --
    # see tests/unit/test_call_settlement_w2.py.
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)
    result = await service.handle_call_status("call-1", CallOutcome.FAILED, 42)
    assert result.durable is False
    service._queue_service.schedule_retry.assert_not_awaited()
    assert conn.calls_row["terminal_retry_payload"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("direction,is_test,phone", [("inbound", False, "anonymous"),
                                                    ("outbound", True, "browser-test")])
async def test_non_dialable_inbound_and_browser_records_can_settle_without_dnc_claim(direction, is_test, phone):
    conn = SuppressionConn(suppression=AssertionError("DNC lookup is inapplicable"), phone=phone)
    conn.calls_row.update(direction=direction, is_test=is_test, lead_id=None, dialer_job_id=None)
    execution = await _service(conn)._handle_call_status_pooled("call-1", CallOutcome.ANSWERED, "answered", 42)
    assert execution.result.durable
    assert execution.retry_args is None
    assert conn.calls_row["outcome"] == "answered"
    assert conn.calls_row["duration_seconds"] == 42
    assert not any("FROM dnc_entries" in query for query, _ in conn.executed)


@pytest.mark.asyncio
async def test_inbound_record_with_dialer_owner_and_non_dialable_number_still_fails_closed():
    # An INBOUND row that owns dialer retry work but carries a non-dialable
    # number is corrupt data; settlement still refuses it.
    conn = SuppressionConn(phone="anonymous")
    conn.calls_row.update(direction="inbound", is_test=False)
    result = await _service(conn).handle_call_status("call-1", CallOutcome.FAILED, 42)
    assert not result.durable
    assert conn.calls_row["terminal_retry_payload"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("is_test", [True, False])
async def test_outbound_non_dialable_destination_settles_without_dnc_claim(is_test, caplog):
    # W2 item 6: an OUTBOUND destination that is not strict E.164 (a PBX
    # extension such as 940005, or "anonymous") can never have a suppression
    # row. It used to abort settlement on every retry, leaving the job
    # 'processing' and the lead 'calling' forever; it now settles and says why.
    conn = SuppressionConn(phone="anonymous")
    conn.calls_row.update(direction="outbound", is_test=is_test)
    # This lead never opted out (the fixture's default lead row is an
    # opted-out projection, which a non-E.164 destination now honours).
    conn.leads_row.update(status="calling", last_call_result=None)
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)
    with caplog.at_level("WARNING"):
        result = await service.handle_call_status("call-1", CallOutcome.FAILED, 42)
    assert result.durable, result.error
    assert conn.calls_row["terminal_settled_at"] == "NOW"
    assert not any("FROM dnc_entries" in query for query, _ in conn.executed)
    assert any("not strict E.164" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_test,payload", [(False, None), (True, {"pending": "retry"})])
async def test_absent_job_with_non_dialable_outbound_destination_still_settles(is_test, payload):
    conn = SuppressionConn(phone="anonymous")
    conn.calls_row.update(direction="outbound", is_test=is_test, lead_id=None,
                         dialer_job_id=None, terminal_retry_payload=payload)
    result = await _service(conn).handle_call_status("call-1", CallOutcome.FAILED, 42)
    assert result.durable, result.error
    assert conn.calls_row["terminal_settled_at"] == "NOW"
    assert not any("FROM dnc_entries" in query for query, _ in conn.executed)
