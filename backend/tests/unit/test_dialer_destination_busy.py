"""Two campaigns of one tenant never ring the same number at the same time.

30 Oct 2026, owner: "make sure the campaigns must not collide at any point
during the calls". A person who is a contact in two running campaigns could be
dialled by both at once. The durable call-intent write now refuses while the
tenant already has a live call on that number (``DestinationBusy``); the job
waits on the same attempt instead of failing or consuming an attempt.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import JobStatus
from app.domain.services.dialer.block_reasons import BlockCode, SELF_CLEARING_CODES, classify
from app.workers.dialer_worker import DestinationBusy
from tests.unit.test_bh_dialer_guard_transient import _base_worker, _job


@pytest.mark.asyncio
async def test_a_number_already_on_a_call_waits_on_the_same_attempt(monkeypatch):
    w = _base_worker()
    w._evaluate_call_guard = AsyncMock(return_value="allow")
    w._create_call_intent = AsyncMock(side_effect=DestinationBusy("call-of-other-campaign"))
    released = []

    async def fake_claim(redis, tenant_id):
        return 0

    async def fake_release(redis, tenant_id):
        released.append(tenant_id)

    monkeypatch.setattr("app.domain.services.dialer.global_pacing.claim_tenant_dial_slot", fake_claim)
    monkeypatch.setattr("app.domain.services.dialer.global_pacing.release_tenant_dial_slot", fake_release)

    job = _job()
    await w.process_job(job)

    w._make_call.assert_not_awaited()
    assert released == [job.tenant_id]
    w.queue_service.schedule_retry.assert_not_awaited()
    w.queue_service.mark_failed.assert_not_awaited()
    w.queue_service._redefer_inflight.assert_awaited_once()
    _, kwargs = w.queue_service._redefer_inflight.call_args
    assert kwargs["delay_seconds"] >= 30
    statuses = [c.args[1] for c in w._update_job_status.call_args_list]
    assert JobStatus.BLOCKED not in statuses and JobStatus.FAILED not in statuses
    assert JobStatus.RETRY_SCHEDULED in statuses
    _, status_kwargs = w._update_job_status.call_args
    assert status_kwargs.get("reason") == "destination_busy"


def test_a_busy_number_is_explained_as_a_self_clearing_wait():
    reason = classify("destination_busy")

    assert reason.code is BlockCode.DESTINATION_BUSY
    assert reason.code in SELF_CLEARING_CODES
    assert "already on a call" in reason.message
