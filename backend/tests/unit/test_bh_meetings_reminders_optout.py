"""workers-jobs-ops-3: a reminder must not reach a lead that opted out / is on DNC."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.workers.reminder_worker import ReminderWorker

TENANT = '00000000-0000-0000-0000-000000000001'
LEAD = '00000000-0000-0000-0000-0000000000a1'


class Connection:
    def __init__(self):
        self.queries = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    def transaction(self):
        return self
    async def execute(self, query, *args):
        self.queries.append((query, args))
        return 'UPDATE 1'
    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return None


class Pool:
    def __init__(self):
        self.conn = Connection()
    def acquire(self):
        return self.conn


def make_worker(monkeypatch, *, on_dnc_list=False):
    worker = ReminderWorker()
    worker._db_pool = Pool()
    worker._sms_service = NS(send_sms=AsyncMock(return_value={'success': True, 'message_id': 'm'}),
                             send_meeting_reminder=AsyncMock(return_value={'success': True, 'message_id': 'm'}))
    worker._send_email_reminder = AsyncMock(return_value={'success': True, 'message_id': 'e'})
    checked = []

    class FakeDNC:
        def __init__(self, _pool): pass
        async def is_on_dnc(self, *, tenant_id, e164):
            checked.append((tenant_id, e164))
            return on_dnc_list
    monkeypatch.setattr('app.workers.reminder_worker.DNCService', FakeDNC, raising=False)
    return worker, checked


def reminder(**extra):
    return {'id': 'r1', 'tenant_id': TENANT, 'lead_id': LEAD, 'meeting_id': None,
            'email': 'person@example.invalid', 'phone_number': '+15555550101',
            'first_name': 'Test', 'last_name': 'Contact', **extra}


def cancels(worker):
    return [(q, a) for q, a in worker._db_pool.conn.queries
            if q.lstrip().startswith('UPDATE') and "status = 'cancelled'" in q]


@pytest.mark.parametrize('channel', ['sms', 'email'])
@pytest.mark.parametrize('flag', [{'lead_status': 'dnc'}, {'lead_do_not_call': True}])
async def test_opted_out_lead_gets_no_reminder_and_row_is_cancelled(monkeypatch, channel, flag):
    worker, _ = make_worker(monkeypatch)
    await worker._process_reminder(reminder(type=channel, **flag))
    worker._sms_service.send_sms.assert_not_awaited()
    worker._sms_service.send_meeting_reminder.assert_not_awaited()
    worker._send_email_reminder.assert_not_awaited()
    (query, args), = cancels(worker)
    assert 'recipient_opted_out' in query or 'recipient_opted_out' in args
    assert TENANT in args and 'tenant_id' in query
    assert not any("status = 'processing'" in q for q, _ in worker._db_pool.conn.queries)


async def test_sms_to_a_number_on_the_dnc_list_is_cancelled(monkeypatch):
    worker, checked = make_worker(monkeypatch, on_dnc_list=True)
    await worker._process_reminder(reminder(type='sms'))
    worker._sms_service.send_meeting_reminder.assert_not_awaited()
    assert checked == [(TENANT, '+15555550101')]
    assert len(cancels(worker)) == 1


async def test_clean_lead_still_gets_the_reminder(monkeypatch):
    worker, _ = make_worker(monkeypatch)
    await worker._process_reminder(reminder(type='sms', lead_status='qualified', lead_do_not_call=False))
    worker._sms_service.send_meeting_reminder.assert_awaited_once()
    assert cancels(worker) == []


async def test_dnc_lookup_failure_does_not_send(monkeypatch):
    worker, _ = make_worker(monkeypatch)
    class Broken:
        def __init__(self, _): pass
        async def is_on_dnc(self, **_): raise RuntimeError('db down')
    monkeypatch.setattr('app.workers.reminder_worker.DNCService', Broken, raising=False)
    await worker._process_reminder(reminder(type='sms'))
    worker._sms_service.send_meeting_reminder.assert_not_awaited()
