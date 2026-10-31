"""workers-jobs-ops-6: template reminders must not say 'None', must label the time zone and the real lead time."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.sms_template_manager import get_sms_template_manager
from tests.unit.test_bh_meetings_reminders_optout import make_worker, reminder

START = datetime(2030, 1, 15, 14, 0, tzinfo=timezone.utc)


def fields(**extra):
    base = dict(type='sms', lead_status='qualified', meeting_id='00000000-0000-0000-0000-0000000000b1',
                meeting_title='Demo', meeting_status='scheduled', start_time=START, scheduled_at=START - timedelta(hours=1))
    return reminder(**{**base, **extra})


async def sms_kwargs(monkeypatch, **extra):
    worker, _ = make_worker(monkeypatch)
    monkeypatch.setattr('app.workers.reminder_worker.ReminderWorker._meeting_is_past', staticmethod(lambda r: False))
    await worker._process_reminder(fields(**extra))
    return worker._sms_service.send_meeting_reminder.await_args.kwargs


async def test_lead_without_last_name_and_meeting_without_title_never_render_none(monkeypatch):
    kw = await sms_kwargs(monkeypatch, first_name='Ahmed', last_name=None, meeting_title=None,
                          meeting_timezone='UTC')
    text = get_sms_template_manager().render_meeting_reminder(
        reminder_type=kw['reminder_type'], name=kw['name'], title=kw['title'], time=kw['time'], join_link=kw['join_link'])
    assert 'None' not in text and 'Hi Ahmed,' in text


async def test_lead_with_no_name_at_all_is_addressed_as_there(monkeypatch):
    kw = await sms_kwargs(monkeypatch, first_name=None, last_name=None)
    assert kw['name'] == 'there'


async def test_stored_content_title_is_used_when_the_meeting_row_has_none(monkeypatch):
    kw = await sms_kwargs(monkeypatch, meeting_title=None, content={'title': 'Quarterly review'})
    assert kw['title'] == 'Quarterly review'


async def test_time_is_shown_in_the_meeting_timezone_with_its_label(monkeypatch):
    kw = await sms_kwargs(monkeypatch, meeting_timezone='Asia/Dubai')
    assert kw['time'].startswith('06:00 PM') and '+04' in kw['time']


@pytest.mark.parametrize('zone', [None, '', 'Not/AZone'])
async def test_missing_or_invalid_timezone_falls_back_to_labelled_utc(monkeypatch, zone):
    kw = await sms_kwargs(monkeypatch, meeting_timezone=zone)
    assert kw['time'] == '02:00 PM UTC'


@pytest.mark.parametrize('lead, expected', [(timedelta(hours=24), '24h'), (timedelta(hours=1), '1h'),
                                            (timedelta(minutes=10), '10m')])
async def test_lead_time_wording_follows_the_real_offset_not_the_default_template(monkeypatch, lead, expected):
    kw = await sms_kwargs(monkeypatch, scheduled_at=START - lead,
                          content={'template': 'meeting_reminder_1h'})
    assert kw['reminder_type'] == expected


async def test_explicit_reminder_type_in_content_wins(monkeypatch):
    kw = await sms_kwargs(monkeypatch, scheduled_at=START - timedelta(hours=1),
                          content={'reminder_type': '10m'})
    assert kw['reminder_type'] == '10m'


@pytest.mark.parametrize('rtype, scheduled', [('24h', timedelta(hours=24)), ('1h', timedelta(hours=1))])
async def test_long_names_and_titles_still_fit_one_sms(monkeypatch, rtype, scheduled):
    kw = await sms_kwargs(monkeypatch, first_name='Bartholomew-Maximilian', last_name='Featherstonehaugh-Cholmondeley',
                          meeting_title='An extremely long meeting title about the annual quarterly planning review',
                          meeting_timezone='Asia/Kolkata', scheduled_at=START - scheduled)
    text = get_sms_template_manager().render_meeting_reminder(
        reminder_type=kw['reminder_type'], name=kw['name'], title=kw['title'], time=kw['time'], join_link=kw['join_link'])
    assert kw['reminder_type'] == rtype and len(text) <= 160, (len(text), text)
