"""D2-7: a stored number that is not in E.164 form is skipped, with its own reason.

Contacts saved before strict validation can hold "12345" or "+07700900123".
The dialer never re-checked the number: such a job reached the legal-hours
gate, which (no timezone can be derived from an invalid number) held it every
hour with "add a timezone", and only the call guard's generic
call_guard_blocked would ever explain it. The dialer now ends the job at
once with the stable reason invalid_phone_number, using the same E.164 shape the call
guard checks, before any schedule, quota or pacing gate.
"""
from __future__ import annotations

import pytest

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.dialer.block_reasons import BlockCode, classify
from tests.unit.test_dialer_worker_block_visibility import _published_codes, _worker


def _job(phone: str) -> DialerJob:
    return DialerJob(job_id="job-abc", campaign_id="35c79aeb-0000-0000-0000-000000000000",
                     lead_id="lead-abc", tenant_id="tenant-abc", phone_number=phone)


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["12345", "+07700900123", "+1234", "not a number", ""])
async def test_an_invalid_stored_number_is_skipped_with_its_own_reason(phone):
    worker = _worker()
    await worker.process_job(_job(phone))

    assert _published_codes(worker) == [BlockCode.INVALID_PHONE_NUMBER]
    worker.queue_service.mark_skipped.assert_awaited_once_with("job-abc", reason="invalid_phone_number")
    worker._update_job_status.assert_awaited_once()
    args, kwargs = worker._update_job_status.call_args
    assert args[1] == JobStatus.SKIPPED and kwargs["error"] == "invalid_phone_number"
    # Ended before every gate that would wait or dial.
    worker._tenant_minutes_exhausted.assert_not_awaited()
    worker._get_tenant_rules.assert_not_awaited()
    worker.rules_engine.can_make_call.assert_not_awaited()
    worker._evaluate_call_guard.assert_not_awaited()
    worker._make_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["+447700900123", "+14155550123"])
async def test_a_valid_number_goes_on_through_the_normal_gates(phone):
    worker = _worker()
    await worker.process_job(_job(phone))
    assert BlockCode.INVALID_PHONE_NUMBER not in _published_codes(worker)
    worker.rules_engine.can_make_call.assert_awaited_once()


def test_the_reason_tells_the_customer_what_to_do():
    reason = classify("invalid_phone_number")
    assert reason.code is BlockCode.INVALID_PHONE_NUMBER
    assert reason.severity == "error"
    assert "not a valid" in reason.message.lower() and "fix" in reason.message.lower()
