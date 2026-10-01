"""A reviewed meeting reminder must carry a real, tenant-owned recipient."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.assistant_plan_steps import schedule_reminder


class ReminderDB:
    def __init__(self, meeting_lead='lead', *, lead_tenant='tenant', email='person@example.invalid'):
        self.rows = {
            'meetings': [{'id': 'meeting', 'tenant_id': 'tenant', 'lead_id': meeting_lead}],
            'leads': [{'id': 'lead', 'tenant_id': lead_tenant, 'email': email, 'phone_number': '+15555550100'}],
            'reminders': [],
        }

    def table(self, table):
        db = self
        class Query:
            def __init__(self): self.filters = {}; self.pending = None
            def select(self, _): return self
            def eq(self, key, value): self.filters[key] = value; return self
            def limit(self, _): return self
            def insert(self, data): self.pending = deepcopy(data); return self
            def execute(self):
                if self.pending is not None:
                    db.rows[table].append({'id': 'reminder', **self.pending})
                    return SimpleNamespace(data=[db.rows[table][-1]])
                return SimpleNamespace(data=[row for row in db.rows[table]
                    if all(row.get(key) == value for key, value in self.filters.items())])
        return Query()


def reminder_params(**extra):
    return {'meeting_id': 'meeting', 'reminder_type': 'email',
        'scheduled_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(), **extra}


async def test_meeting_only_preview_freezes_linked_contact_and_apply_persists_worker_recipient():
    db = ReminderDB()
    preview = await schedule_reminder(db, 'tenant', reminder_params(), {}, preview=True)
    assert preview['preview'] and preview['_apply_args']['lead_id'] == 'lead'
    assert preview['changes'][0]['after'] == 'person@example.invalid'
    assert db.rows['reminders'] == []
    applied = await schedule_reminder(db, 'tenant', preview['_apply_args'], {})
    assert applied['status'] == 'scheduled'
    assert db.rows['reminders'][0]['lead_id'] == 'lead'
    assert db.rows['reminders'][0]['type'] == 'email'


@pytest.mark.parametrize('change', ['removed', 'reassigned', 'cross_tenant', 'missing_channel_contact', 'changed_address'])
async def test_apply_rechecks_resolved_contact_without_silently_redirecting(change):
    db = ReminderDB()
    preview = await schedule_reminder(db, 'tenant', reminder_params(), {}, preview=True)
    if change == 'removed': db.rows['leads'] = []
    elif change == 'reassigned': db.rows['meetings'][0]['lead_id'] = 'different-lead'
    elif change == 'cross_tenant': db.rows['leads'][0]['tenant_id'] = 'another-tenant'
    elif change == 'changed_address': db.rows['leads'][0]['email'] = 'replacement@example.invalid'
    else: db.rows['leads'][0]['email'] = None
    result = await schedule_reminder(db, 'tenant', preview['_apply_args'], {})
    assert result['success'] is False
    assert not db.rows['reminders']


@pytest.mark.parametrize('case', ['leadless', 'cross_tenant_meeting', 'cross_tenant_lead', 'no_channel'])
async def test_unresolvable_or_unspecified_reminders_fail_before_preview(case):
    db = ReminderDB(meeting_lead=None if case == 'leadless' else 'lead')
    params = reminder_params()
    if case == 'cross_tenant_meeting': db.rows['meetings'][0]['tenant_id'] = 'another-tenant'
    if case == 'cross_tenant_lead': db.rows['leads'][0]['tenant_id'] = 'another-tenant'
    if case == 'no_channel': params.pop('reminder_type')
    result = await schedule_reminder(db, 'tenant', params, {}, preview=True)
    assert result['success'] is False and not db.rows['reminders']
