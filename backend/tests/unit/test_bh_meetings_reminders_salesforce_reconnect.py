"""campaigns-contacts-crm-7: reconnecting Salesforce keeps the webhook token and the saved settings."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.api.v1.endpoints import connectors as ce
from app.infrastructure.connectors.base import OAuthTokens
from app.infrastructure.connectors.crm.salesforce import SalesforceConnector
from tests.unit.test_inbox_original_account import _Rows

TENANT = 'tenant-a'
OLD_CONFIG = {
    'instance_url': 'https://old.example.invalid', 'org_id': 'old-org',
    'webhook_token_hash': 'hash-of-token', 'webhook_token_encrypted': 'enc-token',
    'callback_campaign_id': 'campaign-1', 'callback_priority': 3,
    'log_calls': False, 'create_leads': False, 'sync_inbound': False,
    'access_token': 'MUST-NOT-BE-COPIED',
}


def callback_world(monkeypatch, *, old_status='active'):
    db = _Rows()
    db.rows['connectors'] = [
        {'id': 'sf-old', 'tenant_id': TENANT, 'provider': 'salesforce', 'type': 'crm',
         'status': old_status, 'created_at': '2026-01-01T00:00:00Z', 'config': dict(OLD_CONFIG)},
        {'id': 'sf-new', 'tenant_id': TENANT, 'provider': 'salesforce', 'type': 'crm',
         'status': 'pending', 'created_at': '2026-02-01T00:00:00Z', 'config': None},
    ]
    db.rows['connector_accounts'] = [
        {'id': 'acc-old', 'connector_id': 'sf-old', 'tenant_id': TENANT, 'status': 'active'}]
    enc = NS(encrypt=lambda v: 'synthetic:' + v, decrypt=lambda v: v.removeprefix('synthetic:'))
    monkeypatch.setattr(ce, 'get_encryption_service', lambda: enc)
    monkeypatch.setattr('app.core.security.tenant_isolation.set_current_tenant_id', lambda _: None)
    monkeypatch.setattr(ce, 'get_oauth_state_manager', lambda: NS(validate_state=AsyncMock(return_value={
        'tenant_id': TENANT, 'user_id': 'u', 'provider': 'salesforce',
        'redirect_uri': 'https://example.invalid/cb', 'code_verifier': 'v', 'connector_id': 'sf-new'})))
    tokens = OAuthTokens(access_token='new-access', refresh_token='new-refresh',
                         expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scope=None)
    monkeypatch.setattr(SalesforceConnector, 'exchange_code', AsyncMock(return_value=tokens))
    monkeypatch.setattr(SalesforceConnector, 'set_access_token', AsyncMock())
    monkeypatch.setattr(SalesforceConnector, 'config_from_tokens',
                        lambda self, t: {'instance_url': 'https://new.example.invalid'})
    monkeypatch.setattr(SalesforceConnector, 'fetch_account_identity', AsyncMock(return_value={
        'email': 'sf@example.invalid', 'external_account_id': 'ext', 'config': {'org_id': 'new-org'}}))
    return db


async def run_callback(db):
    return await ce.oauth_callback(
        Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []}),
        state='s', code='c', error=None, db_client=db)


def row(db, connector_id):
    return next(r for r in db.rows['connectors'] if r['id'] == connector_id)


async def test_reconnect_carries_webhook_token_and_toggles_to_the_new_row(monkeypatch):
    db = callback_world(monkeypatch)
    response = await run_callback(db)
    assert 'status=success' in response.headers['location']
    config = row(db, 'sf-new')['config']
    assert config['webhook_token_hash'] == 'hash-of-token'
    assert config['webhook_token_encrypted'] == 'enc-token'
    assert config['callback_campaign_id'] == 'campaign-1' and config['callback_priority'] == 3
    assert config['log_calls'] is False and config['create_leads'] is False and config['sync_inbound'] is False
    # the fresh identity wins, and token material is never copied across
    assert config['instance_url'] == 'https://new.example.invalid' and config['org_id'] == 'new-org'
    assert 'access_token' not in config
    assert row(db, 'sf-old')['status'] == 'revoked' and row(db, 'sf-new')['status'] == 'active'


async def test_first_connect_without_a_previous_row_is_unchanged(monkeypatch):
    db = callback_world(monkeypatch)
    db.rows['connectors'] = [r for r in db.rows['connectors'] if r['id'] == 'sf-new']
    db.rows['connector_accounts'] = []
    await run_callback(db)
    config = row(db, 'sf-new')['config']
    assert config == {'instance_url': 'https://new.example.invalid', 'org_id': 'new-org'}
