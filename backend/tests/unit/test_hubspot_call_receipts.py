import json

import httpx
import pytest

import app.infrastructure.connectors.crm.hubspot as module
from app.infrastructure.connectors.crm.base import AmbiguousCRMCallReference
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
    assert title == f'Talk-Lee call {REFERENCE}'
    assert json.loads(requests[0].content)['properties']['hs_call_body'] == f'Talky.ai call id: {REFERENCE}'
    assert json.loads(requests[1].content)['filterGroups'] == [
        {'filters': [{'propertyName': 'hs_call_title', 'operator': 'EQ', 'value': heading}]}
        for heading in (title, f'Talky.ai call {REFERENCE}')
    ]


async def _hubspot_holding(monkeypatch, activities):
    """A stand-in for HubSpot that holds `activities`: the search answers by exact title
    (any of the groups, up to the limit), and a PATCH changes only the properties it sends."""
    def respond(request):
        payload = json.loads(request.content)
        if request.method == 'POST' and request.url.path.endswith('/calls/search'):
            titles = {group['filters'][0]['value'] for group in payload['filterGroups']}
            rows = [{'id': key} for key, properties in activities.items() if properties['hs_call_title'] in titles]
            return httpx.Response(200, json={'results': rows[:payload['limit']]})
        key = request.url.path.rsplit('/', 1)[-1]
        assert request.method == 'PATCH' and key in activities
        activities[key].update(payload['properties'])
        return httpx.Response(200, json={'id': key})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    connector = HubSpotConnector('tenant', 'connector')
    await connector.set_access_token('dummy')
    return connector


async def test_hubspot_finds_a_call_under_the_previous_title_and_an_update_keeps_that_title(monkeypatch):
    # The look-up serves the read-only admin inspection; the sync itself updates by the stored id.
    old_title = f'Talky.ai call {REFERENCE}'
    activities = {'old-activity': {'hs_call_title': old_title, 'hs_call_body': 'Original'}}
    connector = await _hubspot_holding(monkeypatch, activities)
    found = await connector.find_call_by_reference(REFERENCE)
    assert found == 'old-activity'
    assert await connector.update_call_log(found, call_body=f'Updated\nTalky.ai call id: {REFERENCE}')
    assert activities[found]['hs_call_body'] == f'Updated\nTalky.ai call id: {REFERENCE}'
    assert activities[found]['hs_call_title'] == old_title      # an update never renames the record


async def test_hubspot_finds_a_call_under_the_new_title(monkeypatch):
    activities = {'new-activity': {'hs_call_title': f'Talk-Lee call {REFERENCE}', 'hs_call_body': 'Body'}}
    connector = await _hubspot_holding(monkeypatch, activities)
    assert await connector.find_call_by_reference(REFERENCE) == 'new-activity'


async def test_hubspot_reports_one_call_under_both_titles_as_ambiguous(monkeypatch):
    # One record under each title for the same call: the look-up must see both and refuse to pick.
    activities = {
        'old-activity': {'hs_call_title': f'Talky.ai call {REFERENCE}', 'hs_call_body': 'First'},
        'new-activity': {'hs_call_title': f'Talk-Lee call {REFERENCE}', 'hs_call_body': 'Second'},
    }
    connector = await _hubspot_holding(monkeypatch, activities)
    with pytest.raises(AmbiguousCRMCallReference):
        await connector.find_call_by_reference(REFERENCE)


async def test_hubspot_ambiguous_reconciliation_never_selects_first(monkeypatch):
    connector, _ = await install(monkeypatch, [(200, {'results': [{'id': 'one'}, {'id': 'two'}]})])
    with pytest.raises(ValueError, match='Multiple'):
        await connector.find_call_by_reference(REFERENCE)


async def test_hubspot_identity_uses_stable_hub_id_and_never_puts_token_in_url(monkeypatch):
    monkeypatch.setenv('HUBSPOT_CLIENT_ID', 'fixture-client')
    monkeypatch.setenv('HUBSPOT_CLIENT_SECRET', 'fixture-secret')
    connector, requests = await install(monkeypatch, [(200, {'active': True, 'hub_id': 12345, 'user': 'user@example.invalid'})])
    identity = await connector.fetch_account_identity()
    assert identity['external_account_id'] == '12345'
    request = requests[0]
    assert request.method == 'POST' and request.url.path == '/oauth/2026-03/token/introspect'
    assert 'test-token' not in str(request.url) and 'fixture-secret' not in str(request.url)
    assert b'token=test-token' in request.content and b'token_type_hint=access_token' in request.content


@pytest.mark.parametrize('body', [{'active': False, 'hub_id': 12345}, {'active': True}])
async def test_hubspot_identity_requires_active_token_and_stable_account(monkeypatch, body):
    monkeypatch.setenv('HUBSPOT_CLIENT_ID', 'fixture-client')
    monkeypatch.setenv('HUBSPOT_CLIENT_SECRET', 'fixture-secret')
    connector, _ = await install(monkeypatch, [(200, body)])
    with pytest.raises(ConnectorProviderError, match='account identity'):
        await connector.fetch_account_identity()
