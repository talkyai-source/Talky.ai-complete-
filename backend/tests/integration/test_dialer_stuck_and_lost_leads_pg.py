"""Dialler worker: reaped never-sent attempts, stopped-campaign leads, invalid numbers.

Runs the real worker SQL (``_create_call_intent``, ``_release_intent_line_not_ready``,
``_release_lead_of_stopped_campaign_job``, ``_skip_invalid_number`` write) and the
real ``reap_stuck_calls`` against a migrated PostgreSQL as a restricted
NOSUPERUSER NOBYPASSRLS role.
"""
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.models.dialer_job import DialerJob
from app.domain.services.dialer.stuck_job_reaper import reap_stuck_calls
from app.workers.dialer_worker import DialerCallIntent, DialerWorker

pytestmark = pytest.mark.integration

NUMBER = "+15550003001"

# The bridge claim from telephony_bridge._claim_internal_dialer_intent (the only
# path from a call row to a ringing phone), reduced to its WHERE clause.
BRIDGE_CLAIM_SQL = """
    UPDATE calls SET status = 'dialing', provider_call_id = 'x', external_call_uuid = 'x'
     WHERE id = $1::uuid AND tenant_id = $2::uuid AND dialer_job_id = $3::uuid
       AND dialer_attempt_number = $4 AND direction = 'outbound'
       AND provider_call_id IS NULL AND external_call_uuid IS NULL
       AND status IN ('queued', 'initiated')
"""


@pytest_asyncio.fixture
async def lost_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    tenant, other_tenant = uuid4(), uuid4()
    campaign, other_campaign = uuid4(), uuid4()
    role = "lost_leads_" + uuid4().hex
    pool = None
    try:
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON calls, call_legs, call_events TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON campaigns, dialer_jobs, leads TO "{role}"')
        # A call that becomes terminal queues its CRM delivery through the trigger
        # calls_queue_crm_deliveries (migration 0048), which runs as the caller.
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON crm_deliveries TO "{role}"')
        for t in (tenant, other_tenant):
            await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic lost leads')", t)
        for c, t in ((campaign, tenant), (other_campaign, other_tenant)):
            await admin.execute(
                "INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES($1,$2,'Synthetic','stopped','outbound')",
                c, t)

        async def make_job(key_number, *, lead_status="pending", job_status="processing", t=tenant, c=campaign):
            lead, job_id = uuid4(), uuid4()
            await admin.execute(
                "INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,$4,$5)",
                lead, t, c, key_number, lead_status)
            await admin.execute(
                """INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status,attempt_number)
                VALUES($1,$2,$3,$4,$5,$6,1)""", job_id, t, c, lead, key_number, job_status)
            return DialerJob(job_id=str(job_id), tenant_id=str(t), campaign_id=str(c),
                             lead_id=str(lead), phone_number=key_number)

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=4, setup=setup,
                                         timeout=5, command_timeout=10)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
        worker = DialerWorker()
        worker._db_pool = pool
        yield SimpleNamespace(admin=admin, pool=pool, worker=worker, make_job=make_job,
                              tenant=tenant, other_tenant=other_tenant,
                              campaign=campaign, other_campaign=other_campaign)
    finally:
        if pool:
            await pool.close()
        for t in (tenant, other_tenant):
            async with admin.transaction():
                await admin.execute("SET LOCAL app.bypass_rls='on'")
                await admin.execute(
                    "DELETE FROM call_events WHERE call_id IN (SELECT id FROM calls WHERE tenant_id=$1)", t)
                await admin.execute(
                    "DELETE FROM call_legs WHERE call_id IN (SELECT id FROM calls WHERE tenant_id=$1)", t)
                await admin.execute("DELETE FROM crm_deliveries WHERE tenant_id=$1", t)
                await admin.execute("DELETE FROM calls WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM dialer_jobs WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM leads WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM campaigns WHERE tenant_id=$1", t)
            await admin.execute("DELETE FROM tenants WHERE id=$1", t)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def _lead_status(db, job):
    return await db.admin.fetchval("SELECT status FROM leads WHERE id=$1::uuid", job.lead_id)


# ------------------------------------------------------------------ G-F9 ----
async def test_reaped_unclaimed_intent_cannot_ring_and_is_released(lost_db):
    db = lost_db
    job = await db.make_job(NUMBER)
    intent = await db.worker._create_call_intent(job)
    await db.admin.execute(
        "UPDATE calls SET created_at = NOW() - INTERVAL '11 minutes' WHERE id=$1::uuid", intent.call_id)

    async with db.pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.bypass_rls','on',false)")
        assert await reap_stuck_calls(conn) >= 1
    status = await db.admin.fetchval("SELECT status FROM calls WHERE id=$1::uuid", intent.call_id)
    assert status == "termination_pending"

    # The safety proof: the bridge claim can no longer match this row.
    claimed = await db.admin.execute(
        BRIDGE_CLAIM_SQL, intent.call_id, str(db.tenant), job.job_id, int(job.attempt_number))
    assert claimed == "UPDATE 0"

    reaped = DialerCallIntent(intent.call_id, intent.talklee_call_id, intent.leg_id,
                              "termination_pending", None, False)
    assert await db.worker._release_intent_line_not_ready(
        job, reaped, reason="telephony_unavailable") is True
    row = await db.admin.fetchrow(
        "SELECT status, dialer_attempt_number, terminal_settled_at, failure_reason FROM calls WHERE id=$1::uuid",
        intent.call_id)
    assert row["status"] == "cancelled" and row["dialer_attempt_number"] is None
    assert row["terminal_settled_at"] is not None and row["failure_reason"] == "telephony_unavailable"

    # The same attempt gets a fresh intent.
    again = await db.worker._create_call_intent(job)
    assert again.created and again.call_id != intent.call_id


async def test_reaped_row_with_a_provider_id_is_not_released(lost_db):
    db = lost_db
    job = await db.make_job(NUMBER)
    intent = await db.worker._create_call_intent(job)
    await db.admin.execute(
        "UPDATE calls SET status='termination_pending', provider_call_id='p1', external_call_uuid='p1' "
        "WHERE id=$1::uuid", intent.call_id)
    bound = DialerCallIntent(intent.call_id, intent.talklee_call_id, intent.leg_id,
                             "termination_pending", "p1", False)

    assert await db.worker._release_intent_line_not_ready(job, bound, reason="telephony_unavailable") is False
    assert await db.worker._mark_call_intent_not_originated(job, bound, reason="x") is False
    status = await db.admin.fetchval("SELECT status FROM calls WHERE id=$1::uuid", intent.call_id)
    assert status == "termination_pending"


async def test_release_is_tenant_scoped(lost_db):
    db = lost_db
    job = await db.make_job(NUMBER)
    intent = await db.worker._create_call_intent(job)
    await db.admin.execute("UPDATE calls SET status='termination_pending' WHERE id=$1::uuid", intent.call_id)
    reaped = DialerCallIntent(intent.call_id, intent.talklee_call_id, intent.leg_id,
                              "termination_pending", None, False)
    foreign = DialerJob(job_id=job.job_id, tenant_id=str(db.other_tenant), campaign_id=job.campaign_id,
                        lead_id=job.lead_id, phone_number=NUMBER)

    assert await db.worker._release_intent_line_not_ready(foreign, reaped) is False
    status = await db.admin.fetchval("SELECT status FROM calls WHERE id=$1::uuid", intent.call_id)
    assert status == "termination_pending"


# ----------------------------------------------------------------- G-F10 ----
async def test_called_lead_of_a_skipped_retry_returns_to_pending(lost_db):
    db = lost_db
    job = await db.make_job("+15550003002", lead_status="called", job_status="skipped")
    await db.worker._release_lead_of_stopped_campaign_job(job)
    assert await _lead_status(db, job) == "pending"


async def test_lead_with_another_active_job_stays_called(lost_db):
    db = lost_db
    job = await db.make_job("+15550003003", lead_status="called", job_status="skipped")
    await db.admin.execute(
        """INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status,attempt_number)
        VALUES($1,$2,$3,$4,$5,'retry_scheduled',2)""",
        uuid4(), db.tenant, db.campaign, job.lead_id, "+15550003003")
    await db.worker._release_lead_of_stopped_campaign_job(job)
    assert await _lead_status(db, job) == "called"


@pytest.mark.parametrize("status", ["contacted", "completed", "dnc", "deleted", "failed"])
async def test_only_a_called_lead_moves(lost_db, status):
    db = lost_db
    job = await db.make_job("+15550003004", lead_status=status, job_status="skipped")
    await db.worker._release_lead_of_stopped_campaign_job(job)
    assert await _lead_status(db, job) == status


async def test_do_not_call_flag_and_other_tenants_are_never_touched(lost_db):
    db = lost_db
    flagged = await db.make_job("+15550003005", lead_status="called", job_status="skipped")
    await db.admin.execute("UPDATE leads SET do_not_call = TRUE WHERE id=$1::uuid", flagged.lead_id)
    await db.worker._release_lead_of_stopped_campaign_job(flagged)
    assert await _lead_status(db, flagged) == "called"

    foreign_job = await db.make_job("+15550003006", lead_status="called", job_status="skipped",
                                    t=db.other_tenant, c=db.other_campaign)
    wrong_tenant = DialerJob(job_id=foreign_job.job_id, tenant_id=str(db.tenant),
                             campaign_id=foreign_job.campaign_id, lead_id=foreign_job.lead_id,
                             phone_number="+15550003006")
    await db.worker._release_lead_of_stopped_campaign_job(wrong_tenant)
    assert await _lead_status(db, foreign_job) == "called"


# ----------------------------------------------------------------- G-F7a ----
async def test_invalid_number_skip_marks_the_lead_skipped_and_ends_the_job(lost_db):
    db = lost_db
    job = await db.make_job("+07700900123")
    db.worker.queue_service = SimpleNamespace(mark_skipped=_noop)
    db.worker._publish_block = _noop
    await db.worker._skip_invalid_number(job)
    row = await db.admin.fetchrow(
        "SELECT status, last_call_result FROM leads WHERE id=$1::uuid", job.lead_id)
    assert row["status"] == "skipped" and row["last_call_result"] == "invalid_phone_number"
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1::uuid", job.job_id) == "skipped"


async def _noop(*_a, **_k):
    return None
