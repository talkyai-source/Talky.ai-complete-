"""The legal calling-hours floor holds whatever the campaign says (QA F12-01).

A campaign window of 00:00-23:59 with "call anytime" switched on dialled a
New York number at 02:00 local time, and numbers spanning several zones were
judged on the campaign's UTC default.
"""
import asyncio
from datetime import datetime

import pytz

from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer import legal_hours
from app.domain.services.dialer.block_reasons import BlockCode, SCHEDULE_BLOCK_CODES, classify
from app.domain.services.scheduling_rules import SchedulingRuleEngine

NEW_YORK = "+12125550123"
IDAHO = "+12085550123"          # Boise (Mountain) and Los Angeles (Pacific)
TOLL_FREE = "+18005550123"      # every NANP zone, Guam to Adak
AU_MOBILE = "+61412345678"      # Perth to Lord Howe

OPEN_ALL_DAY = CallingRules(
    timezone="UTC", time_window_start="00:00", time_window_end="24:00", allowed_days=list(range(7))
)


def at(zone, y, m, d, hh, mm=0):
    return pytz.timezone(zone).localize(datetime(y, m, d, hh, mm)).astimezone(pytz.UTC)


def can_call(monkeypatch, moment, phone, lead_timezone=None, enforce_window=False):
    monkeypatch.setattr(legal_hours, "_utcnow", lambda: moment)
    return asyncio.run(SchedulingRuleEngine().can_make_call(
        "t", "c", OPEN_ALL_DAY, lead_timezone=lead_timezone,
        enforce_window=enforce_window, phone_number=phone,
    ))


def test_call_anytime_cannot_ring_a_new_yorker_at_2am(monkeypatch):
    allowed, reason = can_call(monkeypatch, at("America/New_York", 2026, 11, 3, 2), NEW_YORK)
    assert allowed is False
    assert reason == "outside_calling_window_legal_hours_America/New_York"
    assert can_call(monkeypatch, at("America/New_York", 2026, 11, 3, 10), NEW_YORK)[0] is True


def test_the_floor_also_holds_when_the_campaign_window_is_enforced(monkeypatch):
    allowed, _ = can_call(monkeypatch, at("America/New_York", 2026, 11, 3, 21, 30), NEW_YORK, enforce_window=True)
    assert allowed is False


def test_a_number_in_two_zones_must_be_daytime_in_both(monkeypatch):
    # 08:30 in Boise is 07:30 in Los Angeles.
    assert can_call(monkeypatch, at("America/Boise", 2026, 11, 3, 8, 30), IDAHO)[0] is False
    assert can_call(monkeypatch, at("America/Boise", 2026, 11, 3, 10), IDAHO)[0] is True


def test_toll_free_is_judged_on_us_zones_and_can_still_be_called(monkeypatch):
    # 12:00 New York is 07:00 Honolulu: too early. 14:00 is 09:00 there.
    assert can_call(monkeypatch, at("America/New_York", 2026, 11, 3, 12), TOLL_FREE)[0] is False
    assert can_call(monkeypatch, at("America/New_York", 2026, 11, 3, 14), TOLL_FREE)[0] is True


def test_australian_rules_close_sunday_and_saturday_evening(monkeypatch):
    sunday = at("Australia/Sydney", 2026, 11, 8, 12)
    saturday_afternoon = at("Australia/Sydney", 2026, 11, 7, 16)
    saturday_evening = at("Australia/Sydney", 2026, 11, 7, 17, 30)
    assert can_call(monkeypatch, sunday, AU_MOBILE)[0] is False
    assert can_call(monkeypatch, saturday_afternoon, AU_MOBILE)[0] is True
    assert can_call(monkeypatch, saturday_evening, AU_MOBILE)[0] is False


def test_the_leads_stated_timezone_wins_over_the_number(monkeypatch):
    # 02:00 New York is 07:00 London: still before 08:00 for this lead.
    early = at("America/New_York", 2026, 11, 3, 2)
    later = at("America/New_York", 2026, 11, 3, 10)
    assert can_call(monkeypatch, early, NEW_YORK, lead_timezone="Europe/London")[0] is False
    assert can_call(monkeypatch, later, NEW_YORK, lead_timezone="Europe/London")[0] is True


def test_an_unplaceable_number_fails_closed(monkeypatch):
    allowed, reason = can_call(monkeypatch, at("UTC", 2026, 11, 3, 12), "not-a-number")
    assert allowed is False
    assert reason == "legal_hours_unknown_timezone"


def test_a_blocked_call_sleeps_until_the_floor_opens(monkeypatch):
    monkeypatch.setattr(legal_hours, "_utcnow", lambda: at("America/New_York", 2026, 11, 3, 2))
    delay = SchedulingRuleEngine().get_delay_until_next_window(OPEN_ALL_DAY, phone_number=NEW_YORK)
    assert delay == 6 * 3600


def test_floor_reasons_are_schedule_waits_or_a_fix_request():
    waiting = classify("outside_calling_window_legal_hours_America/New_York", rules=OPEN_ALL_DAY)
    assert waiting.code in SCHEDULE_BLOCK_CODES
    assert "legal calling hours" in waiting.message
    unknown = classify("legal_hours_unknown_timezone", rules=OPEN_ALL_DAY)
    assert unknown.code is BlockCode.SCHEDULE_INVALID
