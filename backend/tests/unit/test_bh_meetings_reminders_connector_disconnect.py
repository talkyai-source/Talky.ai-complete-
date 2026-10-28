"""Disconnect endpoint contract; actual atomic/reminder behavior has real PostgreSQL coverage."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import connectors as ce
from app.services.connector_lifecycle import ConnectorDisableResult

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
    return NS(tenant_id=TENANT, id='66666666-6666-4666-8666-666666666666', role='tenant_admin')


def authenticated_request():
    """Only synthetic caller context; lifecycle and auth behavior are separate."""
    request = Request({'type': 'http', 'method': 'POST', 'path': '/connectors', 'headers': []})
    request.state.authenticated_user_id = user().id
    request.state.authenticated_session_id = str(uuid4())
    return request


@pytest.fixture
def lifecycle_port(monkeypatch):
    service = NS(disable=AsyncMock(return_value=ConnectorDisableResult("confirmed", "receipt", "operation")))
    monkeypatch.setattr(ce, 'ConnectorLifecycle', lambda _db, **_context: service)
    return service


async def test_disconnect_by_card_passes_exact_tenant_and_connector(lifecycle_port):
    db = DB({('connectors', 'select'): [[{'id': 'c1', 'provider': 'gmail'}]]})
    out = await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db, request=authenticated_request())
    lifecycle_port.disable.assert_awaited_once_with(TENANT, 'c1', revoke_token=ce._revoke_token_at_provider)
    assert out['success'] is True and out['removed'] == 1 and out['provider_revoked'] is True
    assert not [call for call in db.calls if call[1] != 'select']


async def test_delete_by_id_uses_same_atomic_lifecycle(lifecycle_port):
    db = DB({('connectors', 'select'): [{'id': 'c1', 'provider': 'hubspot'}]})
    out = await ce.delete_connector('c1', current_user=user(), db_client=db, request=authenticated_request())
    lifecycle_port.disable.assert_awaited_once_with(TENANT, 'c1', revoke_token=ce._revoke_token_at_provider)
    assert out['provider_revoked'] is True


async def test_unconfirmed_provider_revoke_is_not_reported_as_confirmed(lifecycle_port):
    lifecycle_port.disable.return_value = ConnectorDisableResult("unconfirmed", "receipt", "operation")
    db = DB({('connectors', 'select'): [[{'id': 'c1', 'provider': 'gmail'}]]})
    out = await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db, request=authenticated_request())
    assert out['success'] is True and out['provider_revoked'] is False


@pytest.mark.parametrize('by_card', [True, False])
async def test_unacknowledged_disable_is_a_5xx_not_success(lifecycle_port, by_card):
    lifecycle_port.disable.side_effect = RuntimeError('synthetic database failure')
    row = {'id': 'c1', 'provider': 'gmail'}
    db = DB({('connectors', 'select'): [[row] if by_card else row]})
    with pytest.raises(HTTPException) as exc:
        if by_card:
            await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db, request=authenticated_request())
        else:
            await ce.delete_connector('c1', current_user=user(), db_client=db, request=authenticated_request())
    assert exc.value.status_code == 503


async def test_failed_connector_lookup_does_not_claim_nothing_to_disconnect(lifecycle_port):
    db = DB(errors=[('connectors', 'select')])
    with pytest.raises(HTTPException) as exc:
        await ce.disconnect_connector_by_type('email', current_user=user(), db_client=db, request=authenticated_request())
    assert exc.value.status_code == 503
    lifecycle_port.disable.assert_not_awaited()
