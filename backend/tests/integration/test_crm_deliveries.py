"""Real Postgres checks. Only an explicit disposable local database is allowed."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.services.crm_delivery_store import CRMDeliveryStore


@pytest_asyncio.fixture
async def crm_db(monkeypatch):
    dsn = os.getenv('TALKY_CRM_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit TALKY_CRM_TEST_DATABASE_URL is required')
    parsed = urlparse(dsn)
    if parsed.hostname not in ('localhost', '127.0.0.1') or not parsed.path.endswith('_test'):
        pytest.fail('CRM integration requires a disposable local *_test database')
    suffix = uuid4().hex
    schema, role = 'crm_' + suffix, 'crm_role_' + suffix
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA {schema}')
        await admin.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'SET search_path TO {schema}')
        await admin.execute('''
            CREATE TABLE tenants (id UUID PRIMARY KEY);
            CREATE TABLE calls (id UUID PRIMARY KEY, tenant_id UUID, status TEXT, outcome TEXT,
                duration_seconds INTEGER, transcript TEXT, summary_json JSONB, recording_url TEXT,
                ended_at TIMESTAMPTZ, crm_call_id TEXT);
            CREATE TABLE connectors (tenant_id UUID, provider TEXT, type TEXT, status TEXT);
        ''')
        spec = importlib.util.spec_from_file_location('crm_migration',
            Path(__file__).resolve().parents[2] / 'Alembic/versions/0048_crm_deliveries.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        statements = []
        monkeypatch.setattr(migration, 'op', SimpleNamespace(execute=lambda stmt: statements.append(str(stmt))))
        migration.upgrade()
        async with admin.transaction():
            for statement in statements:
                await admin.execute(statement)
        await admin.execute(f'GRANT USAGE ON SCHEMA {schema} TO {role}')
        await admin.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}')
        async def init(conn):
            await conn.execute(f'SET ROLE {role}')
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init,
            server_settings={'search_path': schema})
        yield admin, pool
    finally:
        if pool:
            await pool.close()
        await admin.execute('SET search_path TO public')
        await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await admin.execute(f'DROP ROLE IF EXISTS {role}')
        await admin.close()


async def seed(admin, providers=('salesforce', 'hubspot'), *, legacy_id=None):
    tenant, call = str(uuid4()), str(uuid4())
    await admin.execute('INSERT INTO tenants VALUES ($1::uuid)', tenant)
    for provider in providers:
        await admin.execute("INSERT INTO connectors VALUES ($1::uuid,$2,'crm','active')", tenant, provider)
    await admin.execute("INSERT INTO calls (id,tenant_id,status,crm_call_id) VALUES ($1::uuid,$2::uuid,'initiated',$3)", call, tenant, legacy_id)
    return tenant, call


async def test_terminal_and_summary_transactions_queue_each_destination_with_rls(crm_db):
    admin, pool = crm_db
    tenant, call = await seed(admin)
    other_tenant, other_call = await seed(admin, ('hubspot',))
    assert await admin.fetchval('SELECT COUNT(*) FROM crm_deliveries') == 0
    await admin.execute("UPDATE calls SET status='completed',ended_at=NOW()")
    assert await admin.fetchval('SELECT COUNT(*) FROM crm_deliveries') == 3
    async with acquire_with_tenant(pool, tenant) as conn:
        assert await conn.fetchval('SELECT COUNT(*) FROM crm_deliveries') == 2
        assert await conn.fetchval('SELECT COUNT(*) FROM crm_deliveries WHERE call_id=$1::uuid', other_call) == 0
        assert await conn.execute("UPDATE crm_deliveries SET status='succeeded' WHERE call_id=$1::uuid", other_call) == 'UPDATE 0'
    await admin.execute("UPDATE calls SET summary_json='{}'::jsonb WHERE id=$1::uuid", call)
    assert await admin.fetchval('SELECT COUNT(*) FROM crm_deliveries') == 3
    assert await admin.fetchval('SELECT COUNT(*) FROM crm_deliveries WHERE desired_key IS NULL') == 3


async def test_claim_is_exclusive_and_expired_create_reconciles(crm_db):
    admin, pool = crm_db
    tenant, call = await seed(admin, ('salesforce',))
    store = CRMDeliveryStore(pool)
    await store.enqueue(tenant, call, 'salesforce', 'revision-1')
    first = await store.claim(tenant, call, 'salesforce')
    assert first and not first['reconcile']
    assert await store.claim(tenant, call, 'salesforce') is None
    await store.save(first, phase='creating_call', contact_id='sf-contact')
    await admin.execute("UPDATE crm_deliveries SET lease_expires_at=NOW()-INTERVAL '1 second'")
    restarted = CRMDeliveryStore(pool)
    recovered = await restarted.claim(tenant, call, 'salesforce')
    assert recovered['reconcile'] and recovered['remote_contact_id'] == 'sf-contact'
    with pytest.raises(RuntimeError, match='lease was lost'):
        await store.save(first, status='succeeded', call_id='stale-write')
    await restarted.save(recovered, call_id='confirmed-id')
    await restarted.save(recovered, status='succeeded', phase='complete')
    row = await admin.fetchrow('SELECT * FROM crm_deliveries')
    assert row['remote_call_id'] == 'confirmed-id' and row['completed_key'] == 'revision-1'
    assert row['status'] == 'succeeded'


async def test_summary_arriving_during_delivery_is_not_lost(crm_db):
    admin, pool = crm_db
    tenant, call = await seed(admin, ('hubspot',))
    await admin.execute("UPDATE calls SET status='completed' WHERE id=$1::uuid", call)
    store = CRMDeliveryStore(pool)
    await store.enqueue(tenant, call, 'hubspot', 'settlement')
    first = await store.claim(tenant, call, 'hubspot')
    await admin.execute("UPDATE calls SET summary_json='{}'::jsonb WHERE id=$1::uuid", call)
    await store.save(first, call_id='hs-1')
    await store.save(first, status='succeeded', phase='complete')
    row = await admin.fetchrow('SELECT * FROM crm_deliveries')
    assert row['status'] == 'pending' and row['desired_key'] is None
    await store.enqueue(tenant, call, 'hubspot', 'summary')
    second = await store.claim(tenant, call, 'hubspot')
    assert second['remote_call_id'] == 'hs-1' and second['desired_key'] == 'summary'


async def test_unknown_and_failed_retry_budget_and_legacy_do_not_reset_on_duplicate_job(crm_db):
    admin, pool = crm_db
    tenant, call = await seed(admin, ('hubspot',), legacy_id='unknown-owner')
    await admin.execute("UPDATE calls SET status='completed' WHERE id=$1::uuid", call)
    row = await admin.fetchrow('SELECT * FROM crm_deliveries')
    assert row['status'] == 'unknown' and row['phase'] == 'legacy_unverified'
    store = CRMDeliveryStore(pool)
    await store.enqueue(tenant, call, 'hubspot', 'revision', legacy_id='unknown-owner')
    receipt = await store.claim(tenant, call, 'hubspot')
    await store.save(receipt, status='unknown', error='review required')
    assert await store.claim(tenant, call, 'hubspot') is None  # backoff
    await admin.execute('UPDATE crm_deliveries SET attempts=6')
    await store.enqueue(tenant, call, 'hubspot', 'new-summary')
    assert await store.claim(tenant, call, 'hubspot') is None  # summary cannot repeat an unknown create
    assert await store.due() == []
