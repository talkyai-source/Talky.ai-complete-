import json

import httpx
import pytest

import app.infrastructure.connectors.crm.hubspot as module
from app.infrastructure.connectors.crm.hubspot import HubSpotConnector
from app.infrastructure.connectors.base import ConnectorProviderError

REFERENCE = '77777777-7777-7777-7777-777777777777'


async def install(monkeypatch, replies):
    requests = []
    def respond(request):
        requests.append(request)
        code, body = replies.pop(0)
        return httpx.Response(code, json=body)
    client_class = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    connector = HubSpotConnector('tenant', 'connector')
    await connector.set_access_token('test-token')
    return connector, requests


async def test_hubspot_summary_patch_is_real_and_uses_destination_id(monkeypatch):
    connector, requests = await install(monkeypatch, [(200, {'id': 'hs-call'})])
    assert await connector.update_call_log('hs-call', call_body='Confirmed summary', outcome='COMPLETED')
    request = requests[0]
    assert request.method == 'PATCH' and request.url.path.endswith('/calls/hs-call')
    assert json.loads(request.content) == {'properties': {
        'hs_call_body': 'Confirmed summary', 'hs_call_status': 'COMPLETED'}}


async def test_hubspot_failed_search_does_not_become_not_found(monkeypatch):
    connector, requests = await install(monkeypatch, [(401, {'message': 'expired'})])
    with pytest.raises(ConnectorProviderError) as error:
        await connector.search_contact(email='test@example.invalid')
    assert error.value.category == 'authentication'
    assert len(requests) == 1


async def test_hubspot_create_and_reconcile_share_reference(monkeypatch):
    connector, requests = await install(monkeypatch, [(201, {'id': 'hs-call'}), (200, {'results': [{'id': 'hs-call'}]})])
    assert await connector.log_call('contact', f'Talky.ai call id: {REFERENCE}', 10) == 'hs-call'
    assert await connector.find_call_by_reference(REFERENCE) == 'hs-call'
    title = json.loads(requests[0].content)['properties']['hs_call_title']
    search = json.loads(requests[1].content)['filterGroups'][0]['filters'][0]
    assert search == {'propertyName': 'hs_call_title', 'operator': 'EQ', 'value': title}


async def test_hubspot_ambiguous_reconciliation_never_selects_first(monkeypatch):
    connector, _ = await install(monkeypatch, [(200, {'results': [{'id': 'one'}, {'id': 'two'}]})])
    with pytest.raises(ValueError, match='Multiple'):
        await connector.find_call_by_reference(REFERENCE)
