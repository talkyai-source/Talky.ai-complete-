"""Actual migrated job ownership across holds; FakeRedis, no provider calls."""
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DialerWorker
from app.domain.services.dialer.stuck_job_reaper import reap_stuck_jobs, reap_orphaned_scheduled_jobs
from tests.unit.test_op05_pre_attempt_holds import held_worker

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def hold_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("OP05 accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    tenant, campaign, lead, job_id = (uuid4() for _ in range(4))
    role = "op05_" + uuid4().hex
    pool = None
    try:
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON dialer_jobs TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON leads TO "{role}"')
        # Canonical tenant-binding triggers lock the campaign parent row.
        await admin.execute(f'GRANT SELECT,UPDATE ON campaigns TO "{role}"')
        await admin.execute(f'GRANT SELECT ON calls TO "{role}"')
        await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic OP05')", tenant)
        await admin.execute(
            "INSERT INTO campaigns(id,tenant_id,name,status) VALUES($1,$2,'Synthetic OP05','running')",
            campaign, tenant,
        )
        await admin.execute(
            "INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,'+15550001001','pending')",
            lead, tenant, campaign,
        )
        await admin.execute(
            """INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status,attempt_number)
            VALUES($1,$2,$3,$4,'+15550001001','processing',1)""", job_id, tenant, campaign, lead,
        )

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, setup=setup,
                                         timeout=5, command_timeout=10)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
        yield SimpleNamespace(admin=admin, pool=pool, tenant=tenant, campaign=campaign, lead=lead,
                              job_id=job_id, role=role)
    finally:
        if pool:
            await pool.close()
        await admin.execute("DELETE FROM dialer_jobs WHERE tenant_id=$1", tenant)
        await admin.execute("DELETE FROM leads WHERE id=$1", lead)
        await admin.execute("DELETE FROM campaigns WHERE id=$1", campaign)
        await admin.execute("DELETE FROM tenants WHERE id=$1", tenant)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def worker_for(db, monkeypatch, reason):
    job = DialerJob(job_id=str(db.job_id), tenant_id=str(db.tenant), campaign_id=str(db.campaign),
                    lead_id=str(db.lead), phone_number="+15550001001")
    worker, job, queue = await held_worker(monkeypatch, reason, job=job)
    worker._db_pool = db.pool
    # Actual worker updates and its existing authorized service RLS context.
    worker._update_job_status = DialerWorker._update_job_status.__get__(worker)
    worker._update_lead_status = DialerWorker._update_lead_status.__get__(worker)
    return worker, job, queue


async def assert_one_owner(db, status):
    async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
        rows = await conn.fetch(
            "SELECT id,status,attempt_number FROM dialer_jobs WHERE lead_id=$1", db.lead,
        )
        assert len(rows) == 1
        assert (rows[0]["id"], rows[0]["status"], rows[0]["attempt_number"]) == (db.job_id, status, 1)
        # The actual migrated partial unique index must prevent a second job.
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status)
                    VALUES($1,$2,$3,$4,'+15550001001','pending')""",
                    uuid4(), db.tenant, db.campaign, db.lead,
                )


async def age_owned_job(db, hours, *, age_due=False):
    # Simulate elapsed time only on this synthetic row. The production
    # updated_at trigger correctly replaces ordinary UPDATE timestamps.
    async with db.admin.transaction():
        await db.admin.execute("SET LOCAL session_replication_role='replica'")
        await db.admin.execute("""UPDATE dialer_jobs SET updated_at=now()-make_interval(hours=>$2),
            scheduled_at=CASE WHEN $4 THEN now()-make_interval(hours=>$2) ELSE scheduled_at END
            WHERE id=$1 AND tenant_id=$3""", db.job_id, hours, db.tenant, age_due)


@pytest.mark.parametrize("reason", [
    "outside_time_window_14:00_17:00", "daily_lead_cap_reached_3/3", "max_concurrent_calls_reached_10/10",
])
async def test_hold_and_resume_keep_one_actual_database_job(hold_db, monkeypatch, reason):
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, reason)
    try:
        await worker.process_job(job)
        await assert_one_owner(db, "retry_scheduled")
        saved = (await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1))[0]
        await queue._redis.zadd(queue.SCHEDULED_ZSET, {saved: 0})
        assert await queue.process_scheduled_jobs() == 1
        resumed = await queue.dequeue_job([str(db.tenant)])
        assert resumed.job_id == str(db.job_id) and resumed.attempt_number == 1
        # A hold that still applies when execution resumes must not consume a
        # retry or drop the database ownership, even on repeated evaluations.
        await worker.process_job(resumed)
        await assert_one_owner(db, "retry_scheduled")
        assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 1
        worker._make_call.assert_not_awaited()
    finally:
        await queue._redis.aclose()


@pytest.mark.parametrize("published", [False, True])
async def test_schedule_outage_never_terminalizes_actual_owner(hold_db, monkeypatch, published):
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, "outside_time_window_14:00_17:00")
    redis = queue._redis
    original = await redis.hget(queue.INFLIGHT_HASH, job.job_id)
    zadd = redis.zadd

    async def unavailable(key, mapping, *args, **kwargs):
        if published:
            await zadd(key, mapping, *args, **kwargs)
        raise ConnectionError("synthetic schedule handoff failure")

    monkeypatch.setattr(redis, "zadd", unavailable)
    try:
        await worker.process_job(job)
        await assert_one_owner(db, "retry_scheduled")
        assert await redis.hget(queue.INFLIGHT_HASH, job.job_id) == original
        monkeypatch.setattr(redis, "zadd", zadd)
        await worker.process_job(DialerJob.from_redis_dict(json.loads(original)))
        await assert_one_owner(db, "retry_scheduled")
        assert await redis.zcard(queue.SCHEDULED_ZSET) == 1
        worker._make_call.assert_not_awaited()
    finally:
        await redis.aclose()


async def test_actual_projection_denial_does_not_let_reaper_release_queued_owner(hold_db, monkeypatch):
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, "outside_time_window_14:00_17:00")
    try:
        await db.admin.execute(f'REVOKE UPDATE ON dialer_jobs FROM "{db.role}"')
        await worker.process_job(job)
        assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 0
        assert await queue._redis.hget(queue.INFLIGHT_HASH, job.job_id)
        await db.admin.execute(f'GRANT UPDATE ON dialer_jobs TO "{db.role}"')
        await age_owned_job(db, 1, age_due=True)
        async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
            assert await reap_stuck_jobs(conn) == 1
            assert await reap_stuck_jobs(conn) == 0  # No repeated diagnostic.
        await assert_one_owner(db, "processing")
        assert await db.admin.fetchval("SELECT failure_reason FROM dialer_jobs WHERE id=$1", db.job_id) == "stuck_reconciliation_required"
        await worker.process_job(job)
        await assert_one_owner(db, "retry_scheduled")
        assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 1
        assert job.attempt_number == 1
        worker._make_call.assert_not_awaited()
    finally:
        await queue._redis.aclose()


async def test_weekend_hold_remains_owned_after_48_hours(hold_db, monkeypatch):
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, "outside_time_window_14:00_17:00")
    worker.rules_engine.get_delay_until_next_window.return_value = 72 * 3600
    try:
        await worker.process_job(job)
        await age_owned_job(db, 49)
        async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
            assert await reap_orphaned_scheduled_jobs(conn) == 0
        await assert_one_owner(db, "retry_scheduled")
        due = await db.admin.fetchval("SELECT scheduled_at FROM dialer_jobs WHERE id=$1", db.job_id)
        assert due.timestamp() > datetime.now(timezone.utc).timestamp() + 71 * 3600
        assert (await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1, withscores=True))[0][1] == due.timestamp()
    finally:
        await queue._redis.aclose()


async def test_stale_cooldown_cleanup_retains_same_attempt_and_owner(hold_db, monkeypatch):
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, "lead_cooldown_0.1h_of_2h")
    worker._lead_has_live_or_answered_call = AsyncMock(return_value=False)
    worker._clear_lead_last_called = DialerWorker._clear_lead_last_called.__get__(worker)
    await db.admin.execute("UPDATE leads SET last_called_at=now() WHERE id=$1", db.lead)
    try:
        await worker.process_job(job)
        await assert_one_owner(db, "retry_scheduled")
        assert await db.admin.fetchval("SELECT last_called_at FROM leads WHERE id=$1", db.lead) is None
        saved, due = (await queue._redis.zrange(queue.SCHEDULED_ZSET, 0, -1, withscores=True))[0]
        assert json.loads(saved)["attempt_number"] == 1
        assert due <= datetime.now(timezone.utc).timestamp() + 1
        worker._make_call.assert_not_awaited()
    finally:
        await queue._redis.aclose()


@pytest.mark.parametrize("status,attempt", [("cancelled", 1), ("processing", 2)])
async def test_stale_deferral_cannot_revive_terminal_or_newer_attempt(hold_db, monkeypatch, status, attempt):
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, "outside_time_window_14:00_17:00")
    await db.admin.execute("UPDATE dialer_jobs SET status=$2,attempt_number=$3 WHERE id=$1", db.job_id, status, attempt)
    try:
        await worker._redefer_before_intent_resolution(job, reason="synthetic_hold", delay_seconds=10)
        assert await queue._redis.zcard(queue.SCHEDULED_ZSET) == 0
        row = await db.admin.fetchrow("SELECT status,attempt_number FROM dialer_jobs WHERE id=$1", db.job_id)
        assert tuple(row.values()) == (status, attempt)
    finally:
        await queue._redis.aclose()


async def test_overdue_schedule_is_flagged_once_and_finalizer_still_releases_owner(hold_db, monkeypatch):
    from app.domain.services.call_service import CallService
    from app.domain.models.dialer_job import CallOutcome
    db = hold_db
    worker, job, queue = await worker_for(db, monkeypatch, "outside_time_window_14:00_17:00")
    try:
        await worker.process_job(job)
        async with db.admin.transaction():
            await db.admin.execute("SET LOCAL session_replication_role='replica'")
            await db.admin.execute("UPDATE dialer_jobs SET updated_at=now()-interval '50 hours',scheduled_at=now()-interval '49 hours' WHERE id=$1 AND tenant_id=$2", db.job_id, db.tenant)
        async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
            assert await reap_orphaned_scheduled_jobs(conn) == 1
            assert await reap_orphaned_scheduled_jobs(conn) == 0
        await assert_one_owner(db, "retry_scheduled")
        # Existing authoritative job finalization, after an attempted call,
        # remains able to release the owner. No actual provider is invoked.
        await db.admin.execute("UPDATE dialer_jobs SET status='processing' WHERE id=$1", db.job_id)
        service = CallService.__new__(CallService)
        service._queue_service = queue
        async with acquire_with_tenant(db.pool, str(db.tenant)) as conn:
            terminal, retry = await service._handle_job_completion_pooled(conn, db.job_id, CallOutcome.GOAL_ACHIEVED, db.campaign, db.lead)
        assert terminal is True and retry is None
        assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.job_id) == "goal_achieved"
    finally:
        await queue._redis.aclose()
