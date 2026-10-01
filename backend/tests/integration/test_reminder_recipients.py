"""Real worker joins for an explicitly reviewed meeting-only reminder."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from tests.integration.test_crm_deliveries import crm_db  # noqa: F401
from tests.unit.test_reminder_recipient_resolution import ReminderDB, reminder_params
from app.services.assistant_plan_steps import schedule_reminder
from app.workers.reminder_worker import ReminderWorker


async def test_meeting_only_reminder_reaches_worker_with_correct_tenant_contact(crm_db):
    admin, pool = crm_db
    await admin.execute('''
        CREATE TABLE leads(id UUID PRIMARY KEY,tenant_id UUID,first_name TEXT,last_name TEXT,email TEXT,phone_number TEXT);
        CREATE TABLE meetings(id UUID PRIMARY KEY,tenant_id UUID,lead_id UUID,title TEXT,start_time TIMESTAMPTZ,end_time TIMESTAMPTZ,join_link TEXT);
        CREATE TABLE reminders(id UUID PRIMARY KEY,tenant_id UUID,lead_id UUID,meeting_id UUID,type TEXT,
            scheduled_at TIMESTAMPTZ,status TEXT,content JSONB,idempotency_key TEXT,sent_at TIMESTAMPTZ,
            channel TEXT,external_message_id TEXT,retry_count INTEGER,max_retries INTEGER,next_retry_at TIMESTAMPTZ,last_error TEXT);
    ''')
    async with pool.acquire() as conn:
        role = await conn.fetchval('SELECT current_user')
    await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON leads,meetings,reminders TO {role}')
    tenant, other_tenant, lead, meeting, reminder = [str(uuid4()) for _ in range(5)]
    start = datetime.now(timezone.utc) + timedelta(hours=3)
    await admin.execute("INSERT INTO leads VALUES($1::uuid,$2::uuid,'Test','Contact','person@example.invalid','+15555550100')", lead, tenant)
    await admin.execute("INSERT INTO meetings VALUES($1::uuid,$2::uuid,$3::uuid,'Meeting',$4,$4,NULL)", meeting, tenant, lead, start)
    db = ReminderDB()
    db.rows['meetings'] = [{'id': meeting, 'tenant_id': tenant, 'lead_id': lead}]
    db.rows['leads'] = [{'id': lead, 'tenant_id': tenant, 'email': 'person@example.invalid', 'phone_number': '+15555550100'}]
    preview = await schedule_reminder(db, tenant, reminder_params(meeting_id=meeting), {}, preview=True)
    result = await schedule_reminder(db, tenant, preview['_apply_args'], {})
    assert result['status'] == 'scheduled'
    queued = db.rows['reminders'][0]
    await admin.execute('''INSERT INTO reminders(id,tenant_id,lead_id,meeting_id,type,scheduled_at,status,content)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,NOW()-INTERVAL '1 second','pending',$6::jsonb)''',
        reminder, tenant, queued['lead_id'], queued['meeting_id'], queued['type'], json.dumps(queued['content']))
    worker = ReminderWorker()
    worker._db_pool = pool
    worker._send_email_reminder = AsyncMock(return_value={'success': True, 'message_id': 'fixture-receipt'})
    worker._sms_service = SimpleNamespace(send_sms=AsyncMock(), send_meeting_reminder=AsyncMock())
    await admin.execute("UPDATE leads SET email='new-address@example.invalid' WHERE id=$1::uuid", lead)
    assert await worker._process_due_reminders() == 1
    sent = worker._send_email_reminder.await_args.kwargs
    assert sent['to_email'] == 'person@example.invalid' and sent['lead_id'] == lead and sent['tenant_id'] == tenant
    assert await admin.fetchval('SELECT status FROM reminders WHERE id=$1::uuid', reminder) == 'sent'
    worker._sms_service.send_sms.assert_not_awaited()

    # A historical bad foreign key must not expose another tenant's recipient
    # to the worker's privileged scheduler scan.
    bad_reminder = str(uuid4())
    await admin.execute('''INSERT INTO reminders(id,tenant_id,lead_id,meeting_id,type,scheduled_at,status)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,'email',NOW()-INTERVAL '1 second','pending')''',
        bad_reminder, other_tenant, lead, meeting)
    assert await worker._process_due_reminders() == 1
    worker._send_email_reminder.assert_awaited_once()
    assert await admin.fetchval('SELECT status FROM reminders WHERE id=$1::uuid', bad_reminder) != 'sent'
