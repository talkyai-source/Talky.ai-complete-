"""workers-jobs-ops-7 (worker half): never send a reminder for a meeting that is over, cancelled or has started."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.unit.test_bh_meetings_reminders_optout import TENANT, cancels, make_worker, reminder

NOW = datetime.now(timezone.utc)


def meeting_reminder(**extra):
    fields = dict(meeting_id='00000000-0000-0000-0000-0000000000b1', type='sms',
                  meeting_title='Demo', start_time=NOW + timedelta(hours=1), meeting_status='scheduled',
                  lead_status='qualified')
    return reminder(**{**fields, **extra})


@pytest.mark.parametrize('stale', [
    {'meeting_status': 'cancelled'},
    {'meeting_status': 'completed'},
    {'start_time': NOW - timedelta(minutes=5)},
])
async def test_stale_meeting_reminder_is_cancelled_not_sent(monkeypatch, stale):
    worker, _ = make_worker(monkeypatch)
    await worker._process_reminder(meeting_reminder(**stale))
    worker._sms_service.send_meeting_reminder.assert_not_awaited()
    worker._sms_service.send_sms.assert_not_awaited()
    (query, args), = cancels(worker)
    assert TENANT in args and 'tenant_id' in query


async def test_upcoming_scheduled_meeting_still_gets_its_reminder(monkeypatch):
    worker, _ = make_worker(monkeypatch)
    await worker._process_reminder(meeting_reminder())
    worker._sms_service.send_meeting_reminder.assert_awaited_once()
    assert cancels(worker) == []


async def test_lead_only_reminder_without_a_meeting_is_not_treated_as_stale(monkeypatch):
    worker, _ = make_worker(monkeypatch)
    await worker._process_reminder(reminder(type='sms', meeting_id=None, start_time=None, lead_status='qualified'))
    worker._sms_service.send_meeting_reminder.assert_awaited_once()
