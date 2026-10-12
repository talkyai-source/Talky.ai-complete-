"""campaigns-contacts-crm-10: disconnect revokes the provider grant and never reports a failed delete as success."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import connectors as ce

TENANT = '44444444-4444-4444-4444-444444444444'
OTHER = '55555555-5555-5555-5555-555555555555'


class Query:
    def __init__(self, db, table):
        self.db, self.table_name, self.op, self.filters, self.payload = db, table, 'select', [], None
    def select(self, *_a, **_k): self.op = 'select'; return self
    def update(self, payload): self.op, self.payload = 'update', payload; return self
    def delete(self): self.op = 'delete'; return self
    def eq(self, col, val): self.filters.append((col, val)); return self
    def in_(self, col, val): self.filters.append((col, tuple(val))); return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self
    def single(self): return self
    def execute(self):
        self.db.calls.append((self.table_name, self.op, list(self.filters), self.payload))
        key = (self.table_name, self.op)
        if key in self.db.errors:
            return NS(data=None, error='boom')
        queue = self.db.responses.get(key) or []
        return NS(data=queue.pop(0) if queue else [], error=None)


class DB:
    def __init__(self, responses=None, errors=()):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.errors = set(errors)
        self.calls = []
    def table(self, name): return Query(self, name)


def user():
    return NS(tenant_id=TENANT, id='user-1')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(ce, 'get_encryption_service',
                        lambda: NS(decrypt=lambda v: v.removeprefix('enc:'), encrypt=lambda v: 'enc:' + v))
    revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(ce, '_revoke_token_at_provider', revoke, raising=False)
    return revoke


def active_account():
    return [{'id': 'acc1', 'access_token_encrypted': 'enc:access', 'refresh_token_encrypted': 'enc:refresh'}]


async def test_disconnect_by_card_revokes_the_refresh_token_at_the_provider(no_network):
    db = DB({('connectors', 'select'): [[{'id': 'c1', 'provider': 'gmail'}]],
             ('connector_accounts', 'select'): [active_account()]})
    out = await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db)
    no_network.assert_awaited_once()
    assert no_network.await_args.args[:2] == ('gmail', 'refresh')
    assert out['success'] is True and out['removed'] == 1
    deletes = [c for c in db.calls if c[1] == 'delete']
    assert [c[0] for c in deletes] == ['connector_accounts', 'connectors']
    assert all(('tenant_id', TENANT) in c[2] for c in deletes)


async def test_delete_by_id_revokes_the_provider_grant_too(no_network):
    db = DB({('connectors', 'select'): [{'id': 'c1', 'provider': 'hubspot'}],
             ('connector_accounts', 'select'): [active_account()]})
    await ce.delete_connector('c1', current_user=user(), db_client=db)
    assert no_network.await_args.args[:2] == ('hubspot', 'refresh')


async def test_provider_revocation_failure_does_not_block_removing_local_rows(no_network):
    no_network.side_effect = RuntimeError('provider down')
    db = DB({('connectors', 'select'): [[{'id': 'c1', 'provider': 'gmail'}]],
             ('connector_accounts', 'select'): [active_account()]})
    out = await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db)
    assert out['success'] is True and out.get('provider_revoked') is False
    assert [c[0] for c in db.calls if c[1] == 'delete'] == ['connector_accounts', 'connectors']


@pytest.mark.parametrize('failing', ['connector_accounts', 'connectors'])
async def test_failed_delete_by_card_is_a_5xx_not_success(failing):
    db = DB({('connectors', 'select'): [[{'id': 'c1', 'provider': 'gmail'}]]}, errors=[(failing, 'delete')])
    with pytest.raises(HTTPException) as exc:
        await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db)
    assert exc.value.status_code >= 500


@pytest.mark.parametrize('failing', ['connector_accounts', 'connectors'])
async def test_failed_delete_by_id_is_a_5xx_not_success(failing):
    db = DB({('connectors', 'select'): [{'id': 'c1', 'provider': 'gmail'}]}, errors=[(failing, 'delete')])
    with pytest.raises(HTTPException) as exc:
        await ce.delete_connector('c1', current_user=user(), db_client=db)
    assert exc.value.status_code >= 500


async def test_pending_reminders_of_the_connectors_meetings_are_cancelled_within_the_tenant():
    db = DB({('connectors', 'select'): [{'id': 'c1', 'provider': 'google_calendar'}],
             ('meetings', 'select'): [[{'id': 'm1'}, {'id': 'm2'}]]})
    await ce.delete_connector('c1', current_user=user(), db_client=db)
    (_, _, filters, payload), = [c for c in db.calls if c[0] == 'reminders' and c[1] == 'update']
    assert payload['status'] == 'cancelled'
    assert ('tenant_id', TENANT) in filters and ('status', 'pending') in filters
    assert ('meeting_id', ('m1', 'm2')) in filters
    meeting_select = [c for c in db.calls if c[0] == 'meetings'][0]
    assert ('tenant_id', TENANT) in meeting_select[2] and ('connector_id', 'c1') in meeting_select[2]
