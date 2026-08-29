"""Timezone, boundary, overnight, and holiday schedule decisions."""

from datetime import datetime, timezone

import pytest

from app.domain.services.telephony.business_hours import evaluate_business_hours


def _at(hour: int, minute: int, *, day: int = 24) -> datetime:
    # 2026-08-24 is Monday; day=25 is Tuesday.
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def _schedule(start="09:00", end="17:00"):
    return {
        "weekly_schedule": [
            {"day": 0, "enabled": True, "windows": [{"start": start, "end": end}]},
            *[{"day": day, "enabled": False} for day in range(1, 7)],
        ],
        "holiday_policy": "closed",
    }


@pytest.mark.parametrize(
    ("instant", "after_hours"),
    [
        (_at(8, 59), True),
        (_at(9, 0), False),
        (_at(16, 59), False),
        (_at(17, 0), True),
    ],
)
def test_windows_are_start_inclusive_and_end_exclusive(instant, after_hours):
    decision = evaluate_business_hours("UTC", _schedule(), now=instant)
    assert decision.valid is True
    assert decision.is_after_hours is after_hours


@pytest.mark.parametrize(
    ("instant", "after_hours"),
    [
        (_at(21, 59), True),
        (_at(22, 0), False),
        (_at(5, 59, day=25), False),
        (_at(6, 0, day=25), True),
    ],
)
def test_overnight_window_carries_into_following_day(instant, after_hours):
    decision = evaluate_business_hours("UTC", _schedule("22:00", "06:00"), now=instant)
    assert decision.valid is True
    assert decision.is_after_hours is after_hours


def test_timezone_is_applied_before_weekday_and_time():
    # 13:00 UTC is 09:00 in New York during August DST.
    decision = evaluate_business_hours(
        "America/New_York", _schedule(), now=_at(13, 0)
    )
    assert decision.valid is True
    assert decision.is_after_hours is False
    assert decision.local_datetime.startswith("2026-08-24T09:00:00")


def test_closed_holiday_overrides_weekly_window():
    schedule = _schedule()
    schedule["holidays"] = ["2026-08-24"]
    decision = evaluate_business_hours("UTC", schedule, now=_at(10, 0))
    assert decision.valid is True
    assert decision.is_after_hours is True
    assert decision.reason == "holiday_closed"


@pytest.mark.parametrize(
    ("zone", "schedule", "reason"),
    [
        ("Mars/Olympus", {}, "invalid_timezone"),
        ("UTC", {"weekly_schedule": [{"day": 0, "start": "9am", "end": "17:00"}]}, "invalid_schedule"),
        (
            "UTC",
            {
                "weekly_schedule": [
                    {"day": 0, "enabled": False},
                    {"day": 0, "enabled": False},
                ]
            },
            "invalid_schedule",
        ),
    ],
)
def test_invalid_timezone_or_schedule_fails_closed(zone, schedule, reason):
    decision = evaluate_business_hours(zone, schedule, now=_at(10, 0))
    assert decision.valid is False
    assert decision.is_after_hours is True
    assert decision.reason == reason
