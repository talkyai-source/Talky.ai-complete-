"""workers-jobs-ops-7: moving a meeting must not leave reminders on the old schedule."""
from datetime import timedelta

from tests.unit.test_calendar_delivery_contract import DB, START, TENANT, service


async def test_reschedule_cancels_pending_reminders_for_that_meeting_in_the_tenant():
    db = DB()
    subject, connector = service(db)
    result = await subject.update_meeting(TENANT, 'meeting', new_start_time=START + timedelta(days=1))
    assert result['success'] and result['status'] == 'updated'
    assert ('reminders', 'update', {'status': 'cancelled'}) in db.writes
    assert ('reminders', 'tenant_id', TENANT) in db.filters
    assert ('reminders', 'meeting_id', 'meeting') in db.filters
    assert ('reminders', 'status', 'pending') in db.filters


async def test_title_only_edit_keeps_the_reminders():
    db = DB()
    subject, _ = service(db)
    result = await subject.update_meeting(TENANT, 'meeting', new_title='New title')
    assert result['success']
    assert not any(table == 'reminders' for table, *_ in db.writes)


async def test_reminder_cancellation_failure_is_not_reported_as_a_clean_reschedule():
    db = DB(fail=('reminders', 'update'))
    subject, _ = service(db)
    result = await subject.update_meeting(TENANT, 'meeting', new_start_time=START + timedelta(days=1))
    assert not result['success'] and result['status'] == 'unknown'
