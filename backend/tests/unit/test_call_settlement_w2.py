"""W2 settlement defects (items 3, 6, 7, 8), asserted at the settlement boundary.

Uses the same in-memory Postgres stand-in as
``test_call_service_pooled_teardown.py``; every statement the service issues is
recorded so tenant scoping is asserted on the SQL actually sent, not on source
text.
"""
from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock

import pytest

from app.core.security.tenant_isolation import (
    clear_tenant_context,
    set_bypass_rls,
    set_current_tenant_id,
)
from app.domain.models.dialer_job import CallOutcome
from app.domain.services import call_service as call_service_module
from app.workers import disposition_policy
from tests.unit.test_call_service_pooled_teardown import _FakeConn, _service
from tests.unit.test_dnc_settlement_truth import SuppressionConn

TENANT = "20000000-0000-4000-8000-000000000017"
PHONE = "+15555550107"


@pytest.fixture(autouse=True)
def _rls_bypass_context():
    set_bypass_rls(True)
    set_current_tenant_id("00000000-0000-0000-0000-000000000000")
    yield
    clear_tenant_context()


def _conn(
    *,
    lead_status: str = "calling",
    job_status: str = "processing",
    job_attempt: int = 1,
    call_attempt=None,
    phone: str = PHONE,
) -> _FakeConn:
    return _FakeConn(
        calls_row={
            "id": "call-1", "lead_id": "lead-1", "campaign_id": "camp-1",
            "dialer_job_id": "job-1", "status": "in_call",
            "tenant_id": TENANT, "phone_number": phone,
            "dialer_attempt_number": call_attempt,
        },
        leads_row={"id": "lead-1", "call_attempts": 0, "status": lead_status},
        dialer_jobs_row={
            "id": "job-1", "status": job_status, "attempt_number": job_attempt,
            "tenant_id": TENANT, "priority": 5, "phone_number": phone,
        },
    )


async def _settle(conn: _FakeConn, outcome: CallOutcome):
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)
    return await service._handle_call_status_pooled(
        "call-1", outcome, outcome.value, 0
    )


_SCOPED_TABLES = (
    "UPDATE leads",
    "SELECT call_attempts FROM leads",
    "UPDATE campaigns",
    "SELECT * FROM dialer_jobs",
    "UPDATE dialer_jobs",
)


def _assert_every_tenant_owned_statement_is_scoped(conn: _FakeConn) -> int:
    """Each statement on a tenant-owned table carries ``tenant_id = $n`` and the
    call row's tenant as that parameter. Returns how many were checked."""
    checked = 0
    for sql, args in conn.executed:
        if not any(sql.startswith(prefix) or prefix in sql for prefix in _SCOPED_TABLES):
            continue
        m = re.search(r"tenant_id\s*=\s*\$(\d+)", sql)
        assert m, f"missing tenant predicate: {sql}"
        assert args[int(m.group(1)) - 1] == TENANT, (sql, args)
        checked += 1
    return checked


# ───────────────────────────── item 8: tenant predicates ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [CallOutcome.ANSWERED, CallOutcome.REJECTED, CallOutcome.BUSY, CallOutcome.GOAL_ACHIEVED],
)
async def test_every_lead_campaign_and_job_statement_is_tenant_scoped(outcome):
    conn = _conn()
    await _settle(conn, outcome)

    checked = _assert_every_tenant_owned_statement_is_scoped(conn)
    # lead lock + lead update + job select + job update (+ campaign counters
    # when the lead finished) are all covered, none skipped.
    assert checked >= 4


# ───────────────────── item 3: a rejection is not do-not-call ────────────────


@pytest.mark.asyncio
async def test_carrier_rejection_does_not_mark_the_lead_do_not_call():
    conn = _conn()
    execution = await _settle(conn, CallOutcome.REJECTED)

    assert conn.leads_row["status"] == "failed"
    assert conn.leads_row["status"] != "dnc"
    assert conn.leads_row["last_call_result"] == "rejected"
    # Still non-retryable inside the campaign and counted as unreachable.
    assert execution.retry_args is None
    assert conn.dialer_jobs_row["status"] == "non_retryable"
    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    sorted(
        disposition_policy.DNC_OUTCOMES - {CallOutcome.REJECTED},
        key=lambda o: o.value,
    ),
)
async def test_no_call_outcome_ever_sets_lead_status_dnc(outcome):
    conn = _conn()
    await _settle(conn, outcome)

    assert conn.leads_row["status"] != "dnc", outcome
    assert conn.dialer_jobs_row["status"] == "non_retryable"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [CallOutcome.SPAM, CallOutcome.UNAVAILABLE, CallOutcome.REJECTED])
async def test_existing_dnc_on_the_lead_is_never_downgraded_by_a_terminal_outcome(outcome):
    # Operator spam-marking (and the opt-out purge) write 'dnc' themselves;
    # settlement must not overwrite that with 'failed'.
    conn = _conn(lead_status="dnc")
    await _settle(conn, outcome)

    assert conn.leads_row["status"] == "dnc"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [CallOutcome.REJECTED, CallOutcome.ANSWERED, CallOutcome.FAILED])
async def test_caller_opt_out_still_marks_the_lead_dnc(outcome):
    conn = SuppressionConn()
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)

    execution = await service._handle_call_status_pooled(
        "call-1", outcome, outcome.value, 0
    )

    assert conn.leads_row["status"] == "dnc"
    assert conn.leads_row["last_call_result"] == "caller_opt_out"
    assert conn.dialer_jobs_row["status"] == "non_retryable"
    assert conn.dialer_jobs_row["failure_reason"] == "caller_opt_out"
    assert execution.retry_args is None


def test_failed_lead_stays_eligible_for_manual_redial():
    from app.domain.services.call_redial_service import _local_block

    row = {
        "direction": "outbound", "campaign_direction": "outbound",
        "status": "completed", "do_not_call": False, "lead_status": "failed",
        "outcome": "rejected", "campaign_status": "running",
        "phone_number": PHONE, "original_phone": PHONE,
    }
    assert _local_block(row) is None
    # ... whereas the old status really did block it.
    assert _local_block({**row, "lead_status": "dnc"})[0] == "do_not_call"


def test_failed_lead_is_reset_by_campaign_restart():
    import inspect

    from app.domain.services.campaign_service import CampaignService

    source = inspect.getsource(CampaignService._reset_leads_for_restart)
    assert '"failed"' in source and '"dnc"' not in source


def test_rejected_is_terminal_without_retry_in_the_policy():
    decision = disposition_policy.decide(CallOutcome.REJECTED, 1)
    assert decision.should_retry is False
    assert CallOutcome.REJECTED in call_service_module.NON_RETRYABLE_OUTCOMES


# ───────────────────── item 6: short destinations do not wedge settlement ────


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["940005", "1234", "40005", "not a number"])
async def test_pbx_extension_destination_settles_with_no_dnc_claim(destination, caplog):
    conn = _conn(phone=destination)
    with caplog.at_level(logging.WARNING, logger=call_service_module.logger.name):
        execution = await _settle(conn, CallOutcome.ANSWERED)

    assert execution.result.durable is True
    assert execution.result.applied is True
    assert conn.calls_row["terminal_settled_at"] == "NOW"
    assert conn.leads_row["status"] == "contacted"
    assert conn.dialer_jobs_row["status"] == "completed"
    # No suppression lookup is possible for a non-E.164 destination.
    assert not any("FROM dnc_entries" in sql for sql, _ in conn.executed)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("call-1" in m and "E.164" in m for m in warnings), warnings


@pytest.mark.asyncio
async def test_pbx_extension_retry_is_still_booked_for_a_retryable_outcome():
    conn = _conn(phone="940005")
    service = _service(conn)
    service._effective_attempt_number = AsyncMock(return_value=1)

    result = await service.handle_call_status("call-1", CallOutcome.BUSY, 0)

    assert result.durable is True, result.error
    assert conn.dialer_jobs_row["status"] == "retry_scheduled"
    service._queue_service.schedule_retry_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_strict_e164_destination_still_performs_the_suppression_lookup():
    conn = _conn()
    await _settle(conn, CallOutcome.ANSWERED)

    assert any("FROM dnc_entries" in sql for sql, _ in conn.executed)


# ───────────────── item 7: settled before the dialer marked it processing ────


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["pending", "queued", "retry_scheduled"])
async def test_call_settled_before_job_marked_processing_still_finalizes_job(job_status):
    conn = _conn(job_status=job_status, job_attempt=1, call_attempt=1)
    execution = await _settle(conn, CallOutcome.ANSWERED)

    assert conn.dialer_jobs_row["status"] == "completed"
    assert conn.dialer_jobs_row["last_outcome"] == "answered"
    # the lead is counted exactly once
    assert conn.campaigns_row == {"calls_completed": 1, "calls_failed": 0}
    assert execution.retry_args is None


@pytest.mark.asyncio
async def test_preflight_job_gets_its_retry_when_the_outcome_is_retryable():
    conn = _conn(job_status="queued", job_attempt=1, call_attempt=1)
    execution = await _settle(conn, CallOutcome.BUSY)

    assert conn.dialer_jobs_row["status"] == "retry_scheduled"
    assert execution.retry_args is not None
    # a retry means the lead is not finished: no counter yet
    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 0}


@pytest.mark.asyncio
async def test_preflight_job_is_not_finalised_by_a_call_without_an_attempt_number():
    # Only a call PROVEN to be this job's attempt may finalise a job that has
    # not been marked processing; a legacy / released row without an attempt
    # number could otherwise close a newer pending retry of the same lead.
    conn = _conn(job_status="pending", job_attempt=1, call_attempt=None)
    await _settle(conn, CallOutcome.ANSWERED)

    assert conn.dialer_jobs_row["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["pending", "queued", "retry_scheduled"])
async def test_preflight_job_of_a_different_attempt_is_left_alone(job_status):
    # The call belongs to attempt 1 but the job already moved on to attempt 2
    # (e.g. a late orphan settlement): it must not finalise the newer attempt.
    conn = _conn(job_status=job_status, job_attempt=2, call_attempt=1)
    execution = await _settle(conn, CallOutcome.ANSWERED)

    assert conn.dialer_jobs_row["status"] == job_status
    assert execution.retry_args is None
    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["completed", "non_retryable", "failed", "goal_achieved"])
async def test_terminal_job_is_never_reopened_by_a_late_settlement(job_status):
    conn = _conn(job_status=job_status, job_attempt=1, call_attempt=1)
    await _settle(conn, CallOutcome.ANSWERED)

    assert conn.dialer_jobs_row["status"] == job_status
    assert conn.campaigns_row == {"calls_completed": 0, "calls_failed": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["processing", "calling"])
async def test_in_flight_jobs_behave_exactly_as_before(job_status):
    conn = _conn(job_status=job_status, job_attempt=1, call_attempt=7)
    await _settle(conn, CallOutcome.ANSWERED)

    # in-flight statuses are matched regardless of the attempt-number check
    assert conn.dialer_jobs_row["status"] == "completed"



@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [CallOutcome.ANSWERED, CallOutcome.BUSY, CallOutcome.NO_ANSWER])
async def test_in_call_opt_out_on_an_extension_destination_is_honoured(outcome):
    # A PBX extension cannot hold a dnc_entries row, so the opt-out purge's
    # lead projection is the only record. Settlement must keep the lead 'dnc'
    # and book no retry instead of re-opening it.
    conn = _conn(lead_status="dnc", phone="940005")
    conn.leads_row["last_call_result"] = "caller_opt_out"
    execution = await _settle(conn, outcome)

    assert conn.leads_row["status"] == "dnc", outcome
    assert conn.leads_row["last_call_result"] == "caller_opt_out"
    assert execution.retry_args is None


@pytest.mark.asyncio
async def test_extension_destination_without_an_opt_out_still_settles_normally():
    conn = _conn(lead_status="calling", phone="940005")
    await _settle(conn, CallOutcome.ANSWERED)
    assert conn.leads_row["status"] != "dnc"
