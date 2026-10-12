"""workers-jobs-ops-2: a definitive provider failure must be re-sent on the worker's next retry."""
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.action_execution import DurableActionExecutor
from app.workers.reminder_worker import ReminderWorker

TENANT = '00000000-0000-0000-0000-000000000001'


class Connection:
    """Just enough of assistant_actions + reminders to run the real DurableActionExecutor."""
    def __init__(self):
        self.actions, self.reminder_updates = {}, []
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    def transaction(self): return self
    async def fetchrow(self, query, *args):
        if 'INSERT INTO assistant_actions' in query:
            key = args[2]
            if key in self.actions:
                return None
            self.actions[key] = {'id': str(uuid4()), 'type': args[1], 'status': 'pending',
                                 'input_data': args[3], 'output_data': None}
            return dict(self.actions[key])
        if 'FROM assistant_actions' in query:
            return dict(self.actions[args[1]])
        return None
    async def fetchval(self, query, *args):
        if 'UPDATE assistant_actions SET status=\'running\'' in query:
            for row in self.actions.values():
                if row['id'] == args[1] and row['status'] == 'pending':
                    row['status'] = 'running'
                    return row['id']
        return None
    async def execute(self, query, *args):
        if 'UPDATE assistant_actions' in query:
            for row in self.actions.values():
                if row['id'] == args[1] and row['status'] == 'running':
                    row['status'], row['output_data'] = args[2], args[3]
                    return 'UPDATE 1'
            return 'UPDATE 0'
        self.reminder_updates.append((query, args))
        return 'UPDATE 1'


class Pool:
    def __init__(self): self.conn = Connection()
    def acquire(self): return self.conn


def reminder(channel, attempt):
    return {'id': 'r1', 'tenant_id': TENANT, 'lead_id': None, 'meeting_id': None, 'type': channel,
            'email': 'person@example.invalid', 'phone_number': '+15555550101',
            'retry_count': attempt, 'first_name': 'Test', 'last_name': 'Contact'}


def last_status(pool):
    sql, args = pool.conn.reminder_updates[-1]
    return 'sent' if "status = 'sent'" in sql else 'failed' if "status = 'failed'" in sql else 'pending'


async def test_email_reminder_is_re_sent_after_a_definitive_failure():
    worker = ReminderWorker()
    worker._db_pool = pool = Pool()
    send = AsyncMock(side_effect=[{'success': False, 'error': 'smtp 421'}, {'success': True, 'message_id': 'ok'}])
    worker._email_service = NS(send_templated_email=send, send_email=send)
    await worker._process_reminder(reminder('email', 0))
    assert last_status(pool) == 'pending'
    await worker._process_reminder(reminder('email', 1))
    assert send.await_count == 2, 'the retry replayed the stored failure instead of calling the provider'
    assert last_status(pool) == 'sent'


async def test_sms_reminder_is_re_sent_after_a_definitive_failure():
    worker = ReminderWorker()
    worker._db_pool = pool = Pool()
    provider = AsyncMock(side_effect=[{'success': False, 'status': 'failed', 'error': 'throttled'},
                                      {'success': True, 'status': 'accepted', 'message_id': 'ok'}])

    async def send_meeting_reminder(**kw):  # same wrapper shape as SMSService.send_sms
        return await DurableActionExecutor(pool).execute(
            tenant_id=kw['tenant_id'], idempotency_key='sms:' + kw['idempotency_key'], action='send_sms',
            payload={'to_number': kw['to_number']}, triggered_by='reminder', executor=provider)
    worker._sms_service = NS(send_meeting_reminder=send_meeting_reminder, send_sms=AsyncMock())
    await worker._process_reminder(reminder('sms', 0))
    assert last_status(pool) == 'pending'
    await worker._process_reminder(reminder('sms', 1))
    assert provider.await_count == 2
    assert last_status(pool) == 'sent'


async def test_the_same_attempt_is_still_fenced_against_a_double_send():
    worker = ReminderWorker()
    worker._db_pool = Pool()
    send = AsyncMock(return_value={'success': True, 'message_id': 'ok'})
    worker._email_service = NS(send_templated_email=send, send_email=send)
    await worker._process_reminder(reminder('email', 0))
    await worker._process_reminder(reminder('email', 0))  # e.g. a stale scan of the same attempt
    assert send.await_count == 1
