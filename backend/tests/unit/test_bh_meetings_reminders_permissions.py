"""campaigns-contacts-crm-4: meeting writes need CALENDAR_MANAGE, not just a login."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import dependencies
from app.api.v1.endpoints import meetings

TENANT = '00000000-0000-0000-0000-000000000001'
MEETING = '00000000-0000-0000-0000-000000000002'
WRITES = [
    ('post', '/meetings/', {'title': 'Demo', 'start_time': '2030-01-01T10:00:00Z', 'attendees': []}),
    ('put', f'/meetings/{MEETING}', {'title': 'Moved'}),
    ('delete', f'/meetings/{MEETING}', None),
]


def client_for(role, monkeypatch):
    service = MagicMock()
    service.create_meeting = AsyncMock(return_value={'success': True, 'meeting_id': MEETING})
    service.update_meeting = AsyncMock(return_value={'success': True})
    service.cancel_meeting = AsyncMock(return_value={'success': True})
    service.list_meetings = AsyncMock(return_value=[])
    monkeypatch.setattr(meetings, 'get_meeting_service', lambda _db: service)
    app = FastAPI()
    app.include_router(meetings.router)
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(
        id='00000000-0000-0000-0000-0000000000u1', email='u@example.invalid', tenant_id=TENANT, role=role)
    app.dependency_overrides[dependencies.get_db_client] = lambda: object()
    return TestClient(app), service


@pytest.mark.parametrize('role', ['readonly', 'user'])
@pytest.mark.parametrize('method,path,body', WRITES)
def test_low_privilege_roles_cannot_change_calendars(monkeypatch, role, method, path, body):
    client, service = client_for(role, monkeypatch)
    response = getattr(client, method)(path, **({'json': body} if body is not None else {}))
    assert response.status_code == 403, response.text
    service.create_meeting.assert_not_awaited()
    service.update_meeting.assert_not_awaited()
    service.cancel_meeting.assert_not_awaited()


@pytest.mark.parametrize('method,path,body', WRITES)
def test_tenant_admin_can_still_change_calendars(monkeypatch, method, path, body):
    client, service = client_for('tenant_admin', monkeypatch)
    response = getattr(client, method)(path, **({'json': body} if body is not None else {}))
    assert response.status_code == 200, response.text
    if method == 'post':
        service.create_meeting.assert_awaited_once()
        assert service.create_meeting.await_args.kwargs['tenant_id'] == TENANT
        assert service.create_meeting.await_args.kwargs['title'] == body['title']
    elif method == 'put':
        service.update_meeting.assert_awaited_once_with(
            tenant_id=TENANT, meeting_id=MEETING, new_start_time=None,
            new_title='Moved', new_description=None, new_attendees=None,
        )
    else:
        service.cancel_meeting.assert_awaited_once_with(
            tenant_id=TENANT, meeting_id=MEETING, reason=None,
        )


def test_ordinary_user_can_still_list_meetings(monkeypatch):
    client, _ = client_for('user', monkeypatch)
    assert client.get('/meetings/').status_code == 200


@pytest.mark.parametrize('role', ['readonly', 'user'])
def test_email_send_route_requires_email_send_even_though_it_is_unmounted(monkeypatch, role):
    from app.api.v1.endpoints import email
    backend = MagicMock()
    backend.send_email = AsyncMock(return_value={'message_id': 'm', 'status': 'accepted'})
    monkeypatch.setattr(email, 'get_email_service', lambda _db: backend)
    app = FastAPI()
    app.include_router(email.router)
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(
        id='00000000-0000-0000-0000-0000000000u1', email='u@example.invalid', tenant_id=TENANT, role=role)
    app.dependency_overrides[dependencies.get_db_client] = lambda: object()
    response = TestClient(app).post('/email/send', json={'to': ['a@example.invalid'], 'subject': 's', 'body': 'b'})
    assert response.status_code == 403
    backend.send_email.assert_not_awaited()
