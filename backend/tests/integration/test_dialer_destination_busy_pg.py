"""Two campaigns of one tenant never hold live calls to the same number.

Runs the real intent write (``DialerWorker._create_call_intent``) against a
migrated PostgreSQL as a restricted NOSUPERUSER NOBYPASSRLS role.
"""
import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.models.dialer_job import DialerJob
from app.workers.dialer_worker import DestinationBusy, DialerWorker

pytestmark = pytest.mark.integration

NUMBER = "+15550002002"
RACE_NUMBER = "+15550002003"


@pytest_asyncio.fixture
async def busy_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    tenant, other_tenant = uuid4(), uuid4()
    campaign_a, campaign_b, campaign_c = uuid4(), uuid4(), uuid4()
    role = "dest_busy_" + uuid4().hex
    jobs = {}
    pool = None
    try:
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON calls, call_legs, call_events TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON campaigns, dialer_jobs, leads TO "{role}"')
        for t in (tenant, other_tenant):
            await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic destination busy')", t)
        for c, t in ((campaign_a, tenant), (campaign_b, tenant), (campaign_c, other_tenant)):
            await admin.execute(
                "INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES($1,$2,'Synthetic','running','outbound')",
                c, t)
        for key, t, c, number in (
            ("a", tenant, campaign_a, NUMBER), ("b", tenant, campaign_b, NUMBER),
            ("c", other_tenant, campaign_c, NUMBER),
            ("race_a", tenant, campaign_a, RACE_NUMBER), ("race_b", tenant, campaign_b, RACE_NUMBER),
        ):
            lead, job_id = uuid4(), uuid4()
            await admin.execute(
                "INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,$4,'pending')",
                lead, t, c, number)
            await admin.execute(
                """INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status,attempt_number)
                VALUES($1,$2,$3,$4,$5,'processing',1)""", job_id, t, c, lead, number)
            jobs[key] = DialerJob(job_id=str(job_id), tenant_id=str(t), campaign_id=str(c),
                                  lead_id=str(lead), phone_number=number)

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=4, setup=setup,
                                         timeout=5, command_timeout=10)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
        worker = DialerWorker()
        worker._db_pool = pool
        yield SimpleNamespace(admin=admin, worker=worker, jobs=jobs, tenant=tenant)
    finally:
        if pool:
            await pool.close()
        for t in (tenant, other_tenant):
            async with admin.transaction():
                # call_events is append-only except for an explicit bypass delete.
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


async def _calls_for(db, job):
    return await db.admin.fetchval("SELECT count(*) FROM calls WHERE dialer_job_id=$1", job.job_id)


async def test_second_campaign_waits_while_the_number_is_on_a_live_call(busy_db):
    db = busy_db
    first = await db.worker._create_call_intent(db.jobs["a"])
    assert first.created

    with pytest.raises(DestinationBusy) as busy:
        await db.worker._create_call_intent(db.jobs["b"])

    assert busy.value.call_id == first.call_id
    assert await _calls_for(db, db.jobs["b"]) == 0


async def test_a_crash_replay_of_the_same_attempt_is_not_busy(busy_db):
    db = busy_db
    first = await db.worker._create_call_intent(db.jobs["a"])

    replay = await db.worker._create_call_intent(db.jobs["a"])

    assert replay.call_id == first.call_id and not replay.created


async def test_another_tenants_live_call_on_the_number_does_not_block(busy_db):
    db = busy_db
    await db.worker._create_call_intent(db.jobs["a"])

    other = await db.worker._create_call_intent(db.jobs["c"])

    assert other.created


async def test_the_number_frees_as_soon_as_the_live_call_ends(busy_db):
    db = busy_db
    first = await db.worker._create_call_intent(db.jobs["a"])
    await db.admin.execute("UPDATE calls SET status='completed', ended_at=NOW() WHERE id=$1::uuid", first.call_id)

    second = await db.worker._create_call_intent(db.jobs["b"])

    assert second.created


async def test_a_differently_written_copy_of_the_number_is_the_same_destination(busy_db):
    db = busy_db
    await db.worker._create_call_intent(db.jobs["a"])
    job_b = db.jobs["b"]
    job_b.phone_number = "+1 (555) 000-2002"

    with pytest.raises(DestinationBusy):
        await db.worker._create_call_intent(job_b)


async def test_two_workers_racing_for_one_number_place_exactly_one_call(busy_db):
    db = busy_db

    results = await asyncio.gather(
        db.worker._create_call_intent(db.jobs["race_a"]),
        db.worker._create_call_intent(db.jobs["race_b"]),
        return_exceptions=True,
    )

    created = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, DestinationBusy)]
    assert len(created) == 1 and len(refused) == 1, results
    live = await db.admin.fetchval(
        "SELECT count(*) FROM calls WHERE tenant_id=$1 AND phone_number=$2", db.tenant, RACE_NUMBER)
    assert live == 1
