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
        spec = importlib.util.spec_from_file_location('crm_identity_migration',
            Path(__file__).resolve().parents[2] / 'Alembic/versions/0051_crm_destination_identity.py')
        identity_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(identity_migration)
        statements = []
        monkeypatch.setattr(identity_migration, 'op', SimpleNamespace(execute=lambda stmt: statements.append(str(stmt))))
        identity_migration.upgrade()
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


@pytest.mark.parametrize('arrival', ['before_enqueue', 'before_claim', 'after_claim'])
async def test_source_revision_fences_every_summary_delivery_race(crm_db, arrival):
    admin, pool = crm_db
    tenant, call = await seed(admin, ('hubspot',))
    await admin.execute("UPDATE calls SET status='completed' WHERE id=$1::uuid", call)
    revision = await admin.fetchval('SELECT xmin::text FROM calls WHERE id=$1::uuid', call)
    store = CRMDeliveryStore(pool)

    async def new_summary():
        await admin.execute("UPDATE calls SET summary_json='{\"headline\":\"newer\"}'::jsonb WHERE id=$1::uuid", call)

    if arrival == 'before_enqueue':
        await new_summary()
    queued = await store.enqueue(tenant, call, 'hubspot', 'old-body', source_revision=revision)
    assert queued['source_current'] == (arrival != 'before_enqueue')
    if arrival == 'before_claim':
        await new_summary()
    receipt = await store.claim(tenant, call, 'hubspot', source_revision=revision, expected_key='old-body')
    if arrival == 'after_claim':
        assert receipt is not None
        await new_summary()
        await store.save(receipt, status='succeeded', phase='complete', call_id='fixture-call')
    else:
        assert receipt is None
    row = await admin.fetchrow('SELECT * FROM crm_deliveries')
    assert row['status'] == 'pending' and row['desired_key'] is None
    assert len(await store.due()) == 1
    fresh_revision = await admin.fetchval('SELECT xmin::text FROM calls WHERE id=$1::uuid', call)
    await store.enqueue(tenant, call, 'hubspot', 'new-body', source_revision=fresh_revision)
    fresh = await store.claim(tenant, call, 'hubspot', source_revision=fresh_revision, expected_key='new-body')
    assert fresh['desired_key'] == 'new-body'
    await store.save(fresh, status='succeeded', phase='complete')
    assert await store.due() == []


async def test_stale_enqueue_cannot_overwrite_a_newer_completed_snapshot(crm_db):
    admin, pool = crm_db
    tenant, call = await seed(admin, ('hubspot',))
    await admin.execute("UPDATE calls SET status='completed' WHERE id=$1::uuid", call)
    old_revision = await admin.fetchval('SELECT xmin::text FROM calls WHERE id=$1::uuid', call)
    await admin.execute("UPDATE calls SET summary_json='{}'::jsonb WHERE id=$1::uuid", call)
    new_revision = await admin.fetchval('SELECT xmin::text FROM calls WHERE id=$1::uuid', call)
    store = CRMDeliveryStore(pool)
    await store.enqueue(tenant, call, 'hubspot', 'new-body', source_revision=new_revision)
    receipt = await store.claim(tenant, call, 'hubspot', source_revision=new_revision, expected_key='new-body')
    await store.save(receipt, status='succeeded', phase='complete')
    stale = await store.enqueue(tenant, call, 'hubspot', 'old-body', source_revision=old_revision)
    assert stale['source_current'] is False and stale['completed_key'] == 'new-body'
    assert stale['status'] == 'pending' and stale['desired_key'] is None
    assert await store.claim(tenant, call, 'hubspot', source_revision=old_revision, expected_key='old-body') is None


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


async def test_destination_binding_is_durable_and_cannot_adopt_other_account_or_unowned_id(crm_db):
    admin, pool = crm_db
    tenant, call = await seed(admin, ('hubspot',))
    store = CRMDeliveryStore(pool)
    await store.enqueue(tenant, call, 'hubspot', 'body')
    receipt = await store.claim(tenant, call, 'hubspot')
    connector = str(uuid4())
    assert await store.bind_destination(receipt, connector, 'hub-1')
    await store.save(receipt, contact_id='original-contact')
    restarted = CRMDeliveryStore(pool)
    assert await restarted.bind_destination(receipt, connector, 'hub-1')
    assert not await restarted.bind_destination(receipt, connector, 'hub-2')
    assert not await restarted.bind_destination(receipt, str(uuid4()), 'hub-1')
    row = await admin.fetchrow('SELECT destination_connector_id,destination_account_id,remote_contact_id FROM crm_deliveries')
    assert str(row['destination_connector_id']) == connector and row['destination_account_id'] == 'hub-1'
    assert row['remote_contact_id'] == 'original-contact'
    # No guess-backfill for a receipt created by an older application.
    await admin.execute('UPDATE crm_deliveries SET destination_connector_id=NULL,destination_account_id=NULL')
    assert not await restarted.bind_destination(receipt, connector, 'hub-1')


async def test_lead_crm_projection_uses_requested_call_or_latest_lead_call_and_tenant(crm_db):
    from app.domain.services.lead_capture_service import LeadCaptureService
    admin, pool = crm_db
    await admin.execute('ALTER TABLE calls ADD COLUMN lead_id UUID, ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW()')
    tenant, older_call = await seed(admin)
    other_tenant, other_call = await seed(admin)
    lead, latest_call = str(uuid4()), str(uuid4())
    await admin.execute("UPDATE calls SET lead_id=$1::uuid,created_at=NOW()-INTERVAL '1 hour' WHERE id=$2::uuid", lead, older_call)
    await admin.execute("UPDATE calls SET lead_id=$1::uuid WHERE id=$2::uuid", lead, other_call)
    await admin.execute("INSERT INTO calls(id,tenant_id,lead_id,status) VALUES($1::uuid,$2::uuid,$3::uuid,'initiated')", latest_call, tenant, lead)
    for call, owner, provider, status, attempts in [
        (older_call, tenant, 'hubspot', 'succeeded', 1),
        (latest_call, tenant, 'hubspot', 'unknown', 2),
        (latest_call, tenant, 'salesforce', 'failed', 3),
        (other_call, other_tenant, 'hubspot', 'succeeded', 4),
    ]:
        await admin.execute('''INSERT INTO crm_deliveries(tenant_id,call_id,provider,status,attempts)
            VALUES($1::uuid,$2::uuid,$3,$4,$5)''', owner, call, provider, status, attempts)
    service = LeadCaptureService(pool)
    latest = await service.crm_deliveries(tenant, lead_id=lead)
    assert [(row['provider'], row['status'], row['attempts']) for row in latest] == [
        ('hubspot', 'unknown', 2), ('salesforce', 'failed', 3)]
    assert all(set(row) == {'provider', 'status', 'attempts', 'updated_at'} and row['updated_at'] for row in latest)
    old = await service.crm_deliveries(tenant, call_id=older_call, lead_id=lead)
    assert [(row['provider'], row['status']) for row in old] == [('hubspot', 'succeeded')]
    assert await service.crm_deliveries(tenant, call_id=other_call) == []
    assert await service.crm_deliveries(other_tenant, call_id=latest_call) == []
    assert await service.crm_deliveries(tenant) == []
    await admin.execute("UPDATE crm_deliveries SET status='processing' WHERE call_id=$1::uuid AND provider='hubspot'", latest_call)
    assert (await service.crm_deliveries(tenant, lead_id=lead))[0]['status'] == 'processing'
    await admin.execute("UPDATE crm_deliveries SET status='pending' WHERE call_id=$1::uuid AND provider='hubspot'", latest_call)
    assert (await service.crm_deliveries(tenant, lead_id=lead))[0]['status'] == 'pending'


async def test_execution_permission_migration_grants_only_admins_and_is_idempotent(crm_db, monkeypatch):
    admin, _ = crm_db
    await admin.execute('''
        CREATE TABLE permissions(id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT UNIQUE NOT NULL,
            description TEXT, resource TEXT NOT NULL, action TEXT NOT NULL, is_system BOOLEAN DEFAULT FALSE);
        CREATE TABLE roles(id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT UNIQUE NOT NULL);
        CREATE TABLE role_permissions(id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            role_id UUID REFERENCES roles(id), permission_id UUID REFERENCES permissions(id),
            UNIQUE(role_id,permission_id));
        INSERT INTO roles(name) VALUES('tenant_admin'),('partner_admin'),('platform_admin'),('viewer'),('agent'),('custom_operator');
        INSERT INTO permissions(name,resource,action) VALUES('connectors:manage','connectors','manage');
        INSERT INTO role_permissions(role_id,permission_id)
            SELECT r.id,p.id FROM roles r CROSS JOIN permissions p WHERE r.name='custom_operator';
    ''')
    spec = importlib.util.spec_from_file_location('execution_permissions_migration',
        Path(__file__).resolve().parents[2] / 'Alembic/versions/0050_assistant_execution_permissions.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration, 'op', SimpleNamespace(execute=lambda sql: statements.append(str(sql))))
    migration.upgrade()
    for _ in range(2):
        async with admin.transaction():
            for statement in statements:
                await admin.execute(statement)
    expected = {'email:send', 'sms:send', 'calendar:read', 'calendar:manage', 'reminders:manage', 'support:report'}
    permissions = await admin.fetch('SELECT name,resource,action,is_system FROM permissions WHERE is_system')
    assert {row['name'] for row in permissions} == expected
    assert all(row['name'] == row['resource']+':'+row['action'] for row in permissions)
    grants = await admin.fetch('''SELECT r.name AS role,p.name AS permission FROM role_permissions rp
        JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id''')
    for role in ('tenant_admin', 'partner_admin', 'platform_admin'):
        assert {row['permission'] for row in grants if row['role'] == role} == expected
    assert not [row for row in grants if row['role'] in ('viewer', 'agent')]
    assert {row['permission'] for row in grants if row['role'] == 'custom_operator'} == {'connectors:manage'}
    assert len(grants) == 19  # 18 new narrow grants, one existing unrelated grant.
