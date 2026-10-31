"""CRM write admission against migrated public tables; provider calls are fakes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from app.services.crm_sync_service import CRMDestinationMismatch, CRMSyncService
from tests.integration.test_ag05_lead_evidence import lead_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admission_db(lead_db):  # noqa: F811
    db = lead_db
    await db.admin.execute(f'GRANT SELECT ON connectors,connector_accounts TO "{db.role}"')
    db.connector_id, db.account_id = uuid4(), uuid4()
    await db.admin.execute("""INSERT INTO connectors(id,tenant_id,type,provider,status,config)
        VALUES($1,$2,'crm','salesforce','active','{}')""", db.connector_id, db.tenants[0])
    await db.admin.execute("""INSERT INTO connector_accounts(id,connector_id,tenant_id,
        external_account_id,status,last_refreshed_at,scopes)
        VALUES($1,$2,$3,'original-account','active',NOW(),ARRAY['contacts.write'])""",
        db.account_id, db.connector_id, db.tenants[0])
    db.connector = SimpleNamespace(connector_id=str(db.connector_id), external_account_id='original-account', config={})
    db.crm = CRMSyncService(object(), db.pool)
    yield db


async def prepared(db):
    tenant = str(db.tenants[0])
    call = await db.crm._load_call(tenant, str(db.calls[0]))
    lead = await db.crm._recipient_lead(tenant, call, await db.crm._load_lead(tenant, call['lead_id']))
    return tenant, call, lead


async def capture_primary(db, value='synthetic@example.com'):
    assert await db.service.capture(tenant_id=str(db.tenants[0]), call_id=str(db.calls[0]),
        field_key='email', field_type='email', source='manual_edit', value=value,
        confirmed=value is not None, validation_status='confirmed' if value else 'cancelled')


@pytest.mark.parametrize('change', ['account', 'account_insert', 'config', 'scopes', 'call', 'lead',
                                  'contact_insert', 'contact_delete', 'contact_withdrawal'])
async def test_public_schema_final_admission_rejects_commits_during_reads(admission_db, monkeypatch, change):
    db = admission_db
    if change in ('contact_delete', 'contact_withdrawal'):
        await capture_primary(db)
    tenant, call, lead = await prepared(db)
    original = db.crm._recipient_lead

    async def concurrent_commit(*args):
        value = await original(*args)
        if change == 'account':
            await db.admin.execute("UPDATE connector_accounts SET external_account_id='new-account' WHERE id=$1", db.account_id)
        elif change == 'account_insert':
            # The old active matching account must not authorize a write when
            # the resolver would select this newer different account.
            await db.admin.execute("""INSERT INTO connector_accounts(connector_id,tenant_id,
                external_account_id,status,last_refreshed_at) VALUES($1,$2,'new-account','active',NOW()+INTERVAL '1 day')""",
                db.connector_id, db.tenants[0])
        elif change == 'config':
            await db.admin.execute("UPDATE connectors SET config='{\"log_calls\":false}' WHERE id=$1", db.connector_id)
        elif change == 'scopes':
            await db.admin.execute("UPDATE connector_accounts SET scopes=ARRAY['read'] WHERE id=$1", db.account_id)
        elif change == 'call':
            await db.admin.execute("UPDATE calls SET transcript='revised synthetic source' WHERE id=$1", db.calls[0])
        elif change == 'lead':
            await db.admin.execute("UPDATE leads SET first_name='corrected' WHERE id=$1", db.leads[0])
        elif change == 'contact_insert':
            await capture_primary(db)
        elif change == 'contact_delete':
            await db.admin.execute("DELETE FROM call_lead_details WHERE call_id=$1 AND field_key='email'", db.calls[0])
        else:
            await capture_primary(db, None)
        return value

    monkeypatch.setattr(db.crm, '_recipient_lead', concurrent_commit)
    monkeypatch.setattr(db.crm, '_connector', AsyncMock(return_value=db.connector))
    provider = AsyncMock()

    async def admit(fresh):
        await db.crm._validate_write_source(tenant, call, lead, connector=fresh,
            reviewed_settings={}, provider='salesforce')

    with pytest.raises(CRMDestinationMismatch):
        await db.crm._with_auth_retry(tenant, 'salesforce', db.connector, provider, before_write=admit)
    provider.assert_not_awaited()


async def test_public_schema_token_rotation_preserves_account_authority(admission_db, monkeypatch):
    db = admission_db
    tenant, call, lead = await prepared(db)
    original = db.crm._recipient_lead

    async def rotate(*args):
        current = await original(*args)
        await db.admin.execute("""UPDATE connector_accounts SET access_token_encrypted='synthetic',
            last_refreshed_at=NOW(),token_last_rotated_at=NOW(),rotation_count=rotation_count+1
            WHERE id=$1""", db.account_id)
        return current

    monkeypatch.setattr(db.crm, '_recipient_lead', rotate)
    monkeypatch.setattr(db.crm, '_connector', AsyncMock(return_value=db.connector))
    provider = AsyncMock(return_value='synthetic-receipt')

    async def admit(fresh):
        await db.crm._validate_write_source(tenant, call, lead, connector=fresh,
            reviewed_settings={}, provider='salesforce')

    assert await db.crm._with_auth_retry(tenant, 'salesforce', db.connector, provider, before_write=admit) == 'synthetic-receipt'
    provider.assert_awaited_once_with(db.connector)


async def test_public_schema_stamp_does_not_cross_tenant(admission_db):
    db = admission_db
    with pytest.raises(CRMDestinationMismatch):
        await db.crm._write_admission_stamp(str(db.tenants[1]), str(db.calls[0]), str(db.connector_id), 'salesforce')
