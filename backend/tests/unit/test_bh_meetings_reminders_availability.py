"""campaigns-contacts-crm-11: the 09:00-18:00 availability window is in the caller's timezone, not UTC."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import dependencies
from app.api.v1.endpoints import meetings

TENANT = '00000000-0000-0000-0000-000000000001'


def client(monkeypatch, slots=()):
    service = MagicMock()
    service.get_availability = AsyncMock(return_value=list(slots))
    monkeypatch.setattr(meetings, 'get_meeting_service', lambda _db: service)
    app = FastAPI()
    app.include_router(meetings.router)
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(
        id='00000000-0000-0000-0000-0000000000u1', email='u@example.invalid', tenant_id=TENANT, role='user')
    app.dependency_overrides[dependencies.get_db_client] = lambda: object()
    return TestClient(app), service


def window(service):
    kwargs = service.get_availability.await_args.kwargs
    # Connectors treat a naive datetime as UTC (calendar.base.utc_datetime).
    as_utc = lambda value: (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc))
    return as_utc(kwargs['start_time']), as_utc(kwargs['end_time'])


def test_default_window_is_still_nine_to_six_utc(monkeypatch):
    http, service = client(monkeypatch)
    assert http.get('/meetings/availability', params={'date': '2030-06-03'}).status_code == 200
    assert window(service) == (datetime(2030, 6, 3, 9, tzinfo=timezone.utc), datetime(2030, 6, 3, 18, tzinfo=timezone.utc))


def test_window_is_built_in_the_requested_timezone(monkeypatch):
    http, service = client(monkeypatch)
    http.get('/meetings/availability', params={'date': '2030-06-03', 'timezone': 'Asia/Dubai'})
    assert window(service) == (datetime(2030, 6, 3, 5, tzinfo=timezone.utc), datetime(2030, 6, 3, 14, tzinfo=timezone.utc))


def test_window_follows_daylight_saving(monkeypatch):
    http, service = client(monkeypatch)
    http.get('/meetings/availability', params={'date': '2030-03-09', 'timezone': 'America/New_York'})
    assert window(service)[0] == datetime(2030, 3, 9, 14, tzinfo=timezone.utc)  # EST
    http.get('/meetings/availability', params={'date': '2030-03-11', 'timezone': 'America/New_York'})
    assert window(service)[0] == datetime(2030, 3, 11, 13, tzinfo=timezone.utc)  # EDT


def test_slots_are_returned_in_the_requested_timezone(monkeypatch):
    slot = {'start': '2030-06-03T05:00:00+00:00', 'end': '2030-06-03T05:30:00+00:00', 'duration_minutes': 30}
    http, _ = client(monkeypatch, [slot])
    body = http.get('/meetings/availability', params={'date': '2030-06-03', 'timezone': 'Asia/Dubai'}).json()
    assert body[0]['start'] == '2030-06-03T09:00:00+04:00'


def test_unknown_timezone_is_a_400(monkeypatch):
    http, service = client(monkeypatch)
    response = http.get('/meetings/availability', params={'date': '2030-06-03', 'timezone': 'Mars/Base'})
    assert response.status_code == 400
    service.get_availability.assert_not_awaited()
