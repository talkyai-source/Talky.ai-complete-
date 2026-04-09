from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.workers.dialer_worker import DialerWorker


@pytest.mark.asyncio
async def test_process_job_skips_stopped_campaign_before_originate():
    worker = DialerWorker()
    worker.queue_service = AsyncMock()
    worker._get_campaign_status = AsyncMock(return_value="stopped")
    worker._get_tenant_rules = AsyncMock()
    worker._get_lead_last_called = AsyncMock()
    worker._make_call = AsyncMock()
    worker._update_job_status = AsyncMock()

    job = DialerJob(
        job_id="job-123",
        campaign_id="campaign-123",
        lead_id="lead-123",
        tenant_id="tenant-123",
        phone_number="+15551234567",
    )

    await worker.process_job(job)

    worker.queue_service.mark_skipped.assert_awaited_once_with(
        job.job_id,
        reason="campaign_stopped",
    )
    worker._update_job_status.assert_awaited_once_with(
        job.job_id,
        JobStatus.SKIPPED,
        error="campaign_not_runnable:stopped",
    )
    worker._make_call.assert_not_called()
