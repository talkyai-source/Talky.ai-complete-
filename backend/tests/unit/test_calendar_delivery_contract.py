"""Calendar effects require the selected account and durable provider receipts."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import json

import httpx
import pytest
from cryptography.fernet import Fernet

from app.services.meeting_service import MeetingService, CalendarNotConnectedError
from app.services.connector_resolver import resolve_active_connector, ConnectorNotConnectedError
from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector

TENANT = '00000000-0000-0000-0000-000000000001'
START = datetime.now(timezone.utc) + timedelta(days=2)


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv('CONNECTOR_ENCRYPTION_KEY', Fernet.generate_key().decode())


class DB:
    def __init__(self, fail=None, *, tenant=TENANT):
        self.fail, self.writes, self.filters = fail, [], []
        self.tenant = tenant
        self.authorization_rows = {
            'connectors': [{'id': 'pinned-calendar', 'tenant_id': tenant,
                'provider': 'google_calendar', 'status': 'active'}],
            'connector_accounts': [{'id': 'calendar-row', 'tenant_id': tenant,
                'connector_id': 'pinned-calendar', 'status': 'active',
                'created_at': '2026-01-01T00:00:00+00:00',
                'external_account_id': 'calendar-account'}],
        }
        self.meeting = {'id': 'meeting', 'connector_id': 'pinned-calendar', 'external_event_id': 'event',
            'metadata': {'provider': 'google_calendar', 'external_account_id': 'calendar-account'},
            'start_time': START.isoformat(), 'end_time': (START + timedelta(minutes=30)).isoformat()}
    def table(self, table):
        return Query(self, table)


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.operation, self.payload = db, table, 'select', None
        self.filters = []
    def select(self, *_): return self
    def single(self): return self
    def limit(self, *_): return self
    def order(self, *_, **__): return self
    def eq(self, key, value):
        self.filters.append((key, value))
        self.db.filters.append((self.table, key, value))
        return self
    def insert(self, payload):
        self.operation, self.payload = 'insert', payload
        return self
    def update(self, payload):
        self.operation, self.payload = 'update', payload
        return self
    def execute(self):
        if self.operation != 'select':
            self.db.writes.append((self.table, self.operation, self.payload))
        if self.db.fail == (self.table, self.operation):
            return NS(data=[], error='write failed')
        if self.operation == 'select':
            if self.table in self.db.authorization_rows:
                rows = [row for row in self.db.authorization_rows[self.table]
                    if all(row.get(key) == value for key, value in self.filters)]
                return NS(data=rows, error=None)
            return NS(data=self.db.meeting if self.table == 'meetings' else [], error=None)
        return NS(data=[{'id': 'audit' if self.table == 'assistant_actions' else 'meeting', **self.payload}], error=None)


def service(db):
    subject = MeetingService(db)
    connector = NS(tenant_id=db.tenant, account_row_id='calendar-row',
        external_account_id='calendar-account', create_event=AsyncMock(return_value=NS(id='event', video_link='https://example.invalid/meet', metadata={})),
        update_event=AsyncMock(return_value=NS(id='event')), delete_event=AsyncMock(return_value=True))
    subject._get_active_calendar_connector = AsyncMock(return_value=(connector, 'pinned-calendar', 'google_calendar'))
    return subject, connector


async def create(subject):
    return await subject.create_meeting(TENANT, 'Synthetic meeting', START, 30, ['test@example.invalid'])


async def test_calendar_requires_durable_intent_before_creating_external_event():
    subject, connector = service(DB(fail=('assistant_actions', 'insert')))
    with pytest.raises(RuntimeError):
        await create(subject)
    connector.create_event.assert_not_awaited()


@pytest.mark.parametrize('failure', [('meetings', 'insert'), ('assistant_actions', 'update')])
async def test_ack_with_missing_local_receipt_returns_unknown_not_success(failure):
    subject, connector = service(DB(fail=failure))
    result = await create(subject)
    assert not result['success'] and result['status'] == 'unknown'
    assert result['external_event_id'] == 'event'
    connector.create_event.assert_awaited_once()


async def test_calendar_creation_with_offset_creates_no_unapproved_sms_reminders():
    db = DB()
    subject, _ = service(db)
    result = await create(subject)
    assert result['success'] and result['meeting_id'] and result['confirmation_allowed']
    assert result['reminders_created'] == 0
    assert not any(table == 'reminders' for table, *_ in db.writes)
    assert db.writes[0][0] == 'assistant_actions'


async def test_calendar_missing_provider_receipt_is_unknown():
    subject, connector = service(DB())
    connector.create_event.return_value.id = None
    result = await create(subject)
    assert result['status'] == 'unknown' and not result['success']


@pytest.mark.parametrize('operation', ['update', 'cancel'])
async def test_calendar_mutations_pin_original_account_and_require_ack(operation):
    db = DB()
    subject, connector = service(db)
    if operation == 'cancel':
        connector.delete_event.return_value = False
        result = await subject.cancel_meeting(TENANT, 'meeting')
    else:
        connector.update_event.return_value = NS(id=None)
        result = await subject.update_meeting(TENANT, 'meeting', new_title='Reviewed title')
    assert result['status'] == 'unknown' and not result['success']
    subject._get_active_calendar_connector.assert_awaited_once_with(TENANT,
        connector_id='pinned-calendar', account_id=None, reviewed_authorization=True)
    assert not any(table == 'meetings' for table, *_ in db.writes)


async def test_disconnected_original_calendar_does_not_mark_cancelled():
    db = DB()
    subject, connector = service(db)
    subject._get_active_calendar_connector.side_effect = CalendarNotConnectedError()
    with pytest.raises(CalendarNotConnectedError):
        await subject.cancel_meeting(TENANT, 'meeting')
    connector.delete_event.assert_not_awaited()
    assert not db.writes


async def test_successful_cancellation_stops_pending_reminders_and_saves_receipt():
    db = DB()
    subject, _ = service(db)
    result = await subject.cancel_meeting(TENANT, 'meeting')
    assert result['success'] and result['confirmation_allowed']
    assert ('reminders', 'update', {'status': 'cancelled'}) in db.writes
    assert db.writes[-1][2]['output_data']['external_event_id'] == 'event'


async def test_requested_connector_is_scoped_and_never_falls_back_to_latest():
    db = DB()
    with pytest.raises(ConnectorNotConnectedError):
        await resolve_active_connector(db, TENANT, 'calendar', connector_id='gone-calendar')
    assert ('connectors', 'tenant_id', TENANT) in db.filters
    assert ('connectors', 'id', 'gone-calendar') in db.filters


def wire(monkeypatch, module_name, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(module_name + '.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


async def test_google_freebusy_includes_all_day_busy_and_valid_offset(monkeypatch):
    start = datetime(2027, 1, 10, 9, tzinfo=timezone(timedelta(hours=5)))
    end = start + timedelta(hours=8)
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path.endswith('/freeBusy')
        assert datetime.fromisoformat(body['timeMin']) == start
        return httpx.Response(200, json={'calendars': {'primary': {'busy': [
            {'start': '2027-01-09T19:00:00Z', 'end': '2027-01-10T19:00:00Z'}]}}})
    wire(monkeypatch, 'app.infrastructure.connectors.calendar.google_calendar', handle)
    connector = GoogleCalendarConnector(TENANT, 'calendar')
    await connector.set_access_token('test-token')
    assert await connector.get_availability(start, end) == []
    assert len(seen) == 1


async def test_google_calendar_errors_never_become_empty_free_day(monkeypatch):
    wire(monkeypatch, 'app.infrastructure.connectors.calendar.google_calendar',
        lambda _: httpx.Response(200, json={'calendars': {'primary': {'errors': [{'reason': 'notFound'}]}}}))
    connector = GoogleCalendarConnector(TENANT, 'calendar')
    await connector.set_access_token('test-token')
    with pytest.raises(ValueError, match='not confirmed'):
        await connector.get_availability(START, START + timedelta(hours=1))


async def test_outlook_offset_is_converted_before_utc_label_and_availability_is_aware(monkeypatch):
    start = datetime(2027, 1, 10, 9, tzinfo=timezone(timedelta(hours=5)))
    seen = []
    def handle(request):
        if request.method == 'POST':
            body = json.loads(request.content)
            seen.append(body)
            assert body['start'] == {'dateTime': '2027-01-10T04:00:00', 'timeZone': 'UTC'}
            return httpx.Response(201, json={**body, 'id': 'event'})
        assert '+00:00Z' not in str(request.url)
        assert request.headers['prefer'] == 'outlook.timezone="UTC"'
        return httpx.Response(200, json={'value': [{'id': 'busy', 'start': {'dateTime': '2027-01-10T04:00:00', 'timeZone': 'UTC'},
            'end': {'dateTime': '2027-01-10T05:00:00', 'timeZone': 'UTC'}}]})
    wire(monkeypatch, 'app.infrastructure.connectors.calendar.outlook_calendar', handle)
    connector = OutlookCalendarConnector(TENANT, 'calendar')
    await connector.set_access_token('test-token')
    await connector.create_event('Test', start, start+timedelta(minutes=30))
    available = await connector.get_availability(start, start+timedelta(hours=2))
    assert available == [{'start': start + timedelta(hours=1), 'end': start + timedelta(hours=2)}]


async def test_incomplete_outlook_calendar_page_is_not_reported_as_free(monkeypatch):
    wire(monkeypatch, 'app.infrastructure.connectors.calendar.outlook_calendar',
        lambda _: httpx.Response(200, json={'value': [], '@odata.nextLink': 'https://example.invalid/next'}))
    connector = OutlookCalendarConnector(TENANT, 'calendar')
    await connector.set_access_token('test-token')
    with pytest.raises(ValueError, match='complete lookup'):
        await connector.get_availability(START, START + timedelta(hours=1))
