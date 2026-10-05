"""Pacing uses the migrated durable hold boundary before Redis publication."""
import json

import pytest

from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker
from tests.integration import test_op05_pre_attempt_holds as hold_contract
from tests.unit.test_op05_pacing_holds import CASES, pacing_worker

pytestmark = pytest.mark.integration
hold_db = hold_contract.hold_db


@pytest.mark.parametrize("reason,delay", CASES)
@pytest.mark.parametrize("denied", [False, True])
async def test_pacing_retains_database_owner_and_original_redis_attempt(hold_db, monkeypatch, reason, delay, denied):
    db = hold_db
    original_job = DialerJob(job_id=str(db.job_id), tenant_id=str(db.tenant),
        campaign_id=str(db.campaign), lead_id=str(db.lead), phone_number="+15550001001")
    worker, job, queue, _ = await pacing_worker(monkeypatch, reason, job=original_job)
    worker._db_pool = db.pool
    worker._update_job_status = DialerWorker._update_job_status.__get__(worker)
    worker._update_lead_status = DialerWorker._update_lead_status.__get__(worker)
    original_payload = await queue._redis.hget(queue.INFLIGHT_HASH, job.job_id)
    try:
        if denied:
            await db.admin.execute(f'REVOKE UPDATE ON dialer_jobs FROM "{db.role}"')
        await worker.process_job(job)
        assert job.attempt_number == 1
        if denied:
            assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 0
            assert await queue._redis.hget(queue.INFLIGHT_HASH, job.job_id) == original_payload
            await hold_contract.assert_one_owner(db, "processing")
            await db.admin.execute(f'GRANT UPDATE ON dialer_jobs TO "{db.role}"')
            await worker.process_job(job)
        await hold_contract.assert_one_owner(db, "retry_scheduled")
        entries = await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1, withscores=True)
        assert len(entries) == 1
        payload, due = entries[0]
        assert json.loads(payload)["attempt_number"] == 1
        row = await db.admin.fetchrow("SELECT scheduled_at,failure_reason FROM dialer_jobs WHERE id=$1", db.job_id)
        assert row["scheduled_at"].timestamp() == due
        assert row["failure_reason"] == reason
        worker._create_call_intent.assert_not_awaited()
        worker._make_call.assert_not_awaited()
    finally:
        await queue._redis.aclose()
