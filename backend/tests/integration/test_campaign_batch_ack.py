"""QA F6-04: opt-in real PostgreSQL bulk-ACK proof in disposable databases."""
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domain.services.campaign_service import CampaignService
from tests.integration.test_campaign_alert_migration import database  # noqa: F401 - shared owned-DB fixture
from tests.unit.test_campaign_dispatch_ack_race import TENANT, _job

pytestmark = pytest.mark.integration


async def seed(database, jobs):
    async with database.pool.acquire() as conn:
        await conn.execute('''CREATE TABLE dialer_jobs (
            id uuid PRIMARY KEY, lead_id uuid NOT NULL, tenant_id uuid NOT NULL,
            campaign_id uuid NOT NULL, status text NOT NULL, updated_at timestamptz)''')
        await conn.executemany(
            'INSERT INTO dialer_jobs(id,lead_id,tenant_id,campaign_id,status) VALUES($1,$2,$3,$4,$5)',
            [(UUID(job.job_id), UUID(job.lead_id), UUID(job.tenant_id), UUID(job.campaign_id), 'pending') for job in jobs],
        )


async def statuses(database, tenant=TENANT):
    async with database.pool.acquire() as conn:
        return dict((str(row['id']), row['status']) for row in await conn.fetch(
            'SELECT id,status FROM dialer_jobs WHERE tenant_id=$1 ORDER BY id', UUID(tenant),
        ))


async def test_real_pg_batch_ack_preserves_worker_advanced_rows(database):
    jobs = [_job(1), _job(2), _job(3)]
    await seed(database, jobs)
    async with database.pool.acquire() as conn:
        await conn.execute("UPDATE dialer_jobs SET status='processing' WHERE tenant_id=$1 AND id=$2", UUID(TENANT), UUID(jobs[1].job_id))
        await conn.execute("UPDATE dialer_jobs SET status='cancelled' WHERE tenant_id=$1 AND id=$2", UUID(TENANT), UUID(jobs[2].job_id))
    await CampaignService(SimpleNamespace(pool=database.pool))._confirm_jobs_dispatched(jobs)
    assert list((await statuses(database)).values()) == ['queued', 'processing', 'cancelled']


@pytest.mark.parametrize('field', ['tenant_id', 'campaign_id', 'lead_id', 'missing'])
async def test_real_pg_unproved_owned_pair_rolls_back_the_whole_ack(database, field):
    jobs = [_job(1), _job(2)]
    await seed(database, jobs)
    async with database.pool.acquire() as conn:
        if field == 'missing':
            await conn.execute('DELETE FROM dialer_jobs WHERE tenant_id=$1 AND id=$2', UUID(TENANT), UUID(jobs[1].job_id))
        else:
            assert field in {'tenant_id', 'campaign_id', 'lead_id'}
            await conn.execute(f'UPDATE dialer_jobs SET {field}=$1 WHERE tenant_id=$2 AND id=$3', UUID('99999999-9999-4999-8999-999999999999'), UUID(TENANT), UUID(jobs[1].job_id))
    before = await statuses(database)
    foreign_before = await statuses(database, '99999999-9999-4999-8999-999999999999')
    with pytest.raises(RuntimeError, match='not exact'):
        await CampaignService(SimpleNamespace(pool=database.pool))._confirm_jobs_dispatched(jobs)
    assert await statuses(database) == before
    assert await statuses(database, '99999999-9999-4999-8999-999999999999') == foreign_before


async def test_real_pg_pairing_rejects_swapped_leads_even_with_matching_id_sets(database):
    jobs = [_job(1), _job(2)]
    await seed(database, jobs)
    swapped = [jobs[0].model_copy(update={'lead_id': jobs[1].lead_id}),
               jobs[1].model_copy(update={'lead_id': jobs[0].lead_id})]
    with pytest.raises(RuntimeError, match='not exact'):
        await CampaignService(SimpleNamespace(pool=database.pool))._confirm_jobs_dispatched(swapped)
    assert list((await statuses(database)).values()) == ['pending', 'pending']
