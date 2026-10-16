"""Packet W6 item 3: CallingRules calling-window parsing.

Production finding (2026-10-16 outbound audit), ``CallingRules.is_within_time_window``:
  * an unparsable window ("9am", "25:00", ...) returned
    ``(True, "invalid_time_format_default_allow")`` -- a typo opened the line
    around the clock (fail OPEN on a compliance gate);
  * ``"24:00"`` raised ``ValueError`` from ``datetime.time`` OUTSIDE the
    ``try``, i.e. an exception in the dialer hot path instead of "end of day";
  * an inverted window (22:00-06:00, start > end) silently never opened.

Contract after the fix: unparsable -> not in window, reason
``invalid_time_window``; ``"24:00"`` is accepted as the END of the day only;
``start > end`` is also ``invalid_time_window`` (fail closed). Consumer calling
hours never span midnight, and reading it as "overnight" would turn a slip such
as 09:00-05:00 into 20 hours of dialing a day.
"""
from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from app.domain.models.calling_rules import CallingRules

UTC = pytz.UTC
MON, TUE, FRI, SAT, SUN = 12, 13, 16, 17, 18  # October 2026 (2026-10-12 is a Monday)


def _at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def _rules(start: str, end: str, days=(0, 1, 2, 3, 4), tz: str = "UTC") -> CallingRules:
    return CallingRules(
        timezone=tz, time_window_start=start, time_window_end=end, allowed_days=list(days)
    )


def test_fixture_days_are_the_weekdays_the_tests_assume():
    assert _at(MON, 12).weekday() == 0
    assert _at(FRI, 12).weekday() == 4
    assert _at(SAT, 12).weekday() == 5
    assert _at(SUN, 12).weekday() == 6


# ── unparsable windows fail CLOSED ─────────────────────────────────────────


@pytest.mark.parametrize(
    "start,end",
    [
        ("9am", "19:00"),
        ("09:00", "7pm"),
        ("", "19:00"),
        ("09:00", ""),
        ("25:00", "19:00"),
        ("09:00", "25:00"),
        ("09:60", "19:00"),
        ("09:00", "19:75"),
        ("-1:00", "19:00"),
        ("0900", "1900"),
        ("09:00:00", "19:00"),
        ("abc", "def"),
        ("24:00", "19:00"),  # 24:00 is only valid as the END of a window
        ("24:30", "24:30"),
    ],
)
def test_unparsable_window_is_not_in_window(start, end):
    in_window, reason = _rules(start, end).is_within_time_window(_at(MON, 12))

    assert in_window is False, "an unreadable calling window must not open the line"
    assert reason == "invalid_time_window"


def test_unparsable_window_never_raises_even_on_a_disallowed_day():
    in_window, reason = _rules("junk", "19:00", days=()).is_within_time_window(_at(MON, 12))

    assert in_window is False
    assert reason in {"invalid_time_window", "calling_not_allowed_on_Mon"}


# ── "24:00" as the end of the day ──────────────────────────────────────────


def test_24_00_is_accepted_as_end_of_day():
    rules = _rules("09:00", "24:00")

    assert rules.is_within_time_window(_at(MON, 9, 0)) == (True, "within_time_window")
    assert rules.is_within_time_window(_at(MON, 23, 59, 59)) == (True, "within_time_window")
    in_window, reason = rules.is_within_time_window(_at(MON, 8, 59, 59))
    assert in_window is False and reason == "outside_time_window_09:00_24:00"


def test_00_00_to_24_00_is_the_whole_day():
    rules = _rules("00:00", "24:00")

    for hour, minute in ((0, 0), (3, 30), (12, 0), (23, 59)):
        assert rules.is_within_time_window(_at(MON, hour, minute))[0] is True
    # the day gate still applies
    assert rules.is_within_time_window(_at(SAT, 12))[0] is False


# ── inverted windows (start after end) fail CLOSED ─────────────────────────


@pytest.mark.parametrize(
    "start,end",
    [("22:00", "06:00"), ("09:00", "05:00"), ("19:01", "19:00")],
)
@pytest.mark.parametrize(
    "when",
    [_at(MON, 23, 30), _at(TUE, 3, 0), _at(TUE, 12, 0), _at(SAT, 3, 0)],
)
def test_inverted_window_never_opens(start, end, when):
    assert _rules(start, end).is_within_time_window(when) == (False, "invalid_time_window")


def test_inverted_window_stays_closed_in_any_timezone():
    rules = _rules("22:00", "06:00", tz="America/New_York")

    # 02:00 UTC Tuesday == 22:00 Monday EDT: would be "overnight", must stay closed.
    assert rules.is_within_time_window(_at(TUE, 2, 0)) == (False, "invalid_time_window")
    assert rules.is_within_time_window(
        _at(TUE, 2, 0), tz_override="Europe/London"
    ) == (False, "invalid_time_window")


# ── regression: ordinary same-day windows are unchanged ────────────────────


def test_daytime_window_unchanged():
    rules = _rules("09:00", "19:00")

    assert rules.is_within_time_window(_at(MON, 9, 0)) == (True, "within_time_window")
    assert rules.is_within_time_window(_at(MON, 19, 0)) == (True, "within_time_window")
    assert rules.is_within_time_window(_at(MON, 19, 0, 1)) == (
        False, "outside_time_window_09:00_19:00")
    assert rules.is_within_time_window(_at(MON, 8, 59)) == (
        False, "outside_time_window_09:00_19:00")
    assert rules.is_within_time_window(_at(SAT, 12)) == (False, "calling_not_allowed_on_Sat")


# ── get_next_window_start must not raise now that invalid windows reach it ─


def test_next_window_start_survives_an_unparsable_start():
    rules = _rules("junk", "19:00")
    now = _at(MON, 12)

    nxt = rules.get_next_window_start(from_time=now)

    assert nxt > now, "an unreadable window has no known opening: re-check later, do not crash"


def test_next_window_start_for_an_inverted_window_does_not_raise():
    rules = _rules("22:00", "06:00")
    now = _at(TUE, 12, 0)

    assert rules.get_next_window_start(from_time=now) > now
