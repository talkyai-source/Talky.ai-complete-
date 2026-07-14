"""Callee-local greeting word tests (clock injected to avoid sandbox time)."""
from datetime import datetime, timezone
from app.domain.services.voice_pipeline.time_of_day import (
    greeting_for_hour, local_hour, time_of_day_line,
)


def test_words_by_hour():
    assert greeting_for_hour(8) == "morning"
    assert greeting_for_hour(14) == "afternoon"
    assert greeting_for_hour(19) == "evening"
    assert greeting_for_hour(2) == "evening"


def test_uk_summer_offset():
    # 16:30 UTC in July = 17:30 BST → evening in London.
    now = datetime(2026, 7, 8, 16, 30, tzinfo=timezone.utc)
    assert local_hour("Europe/London", _now=now) == 17
    line = time_of_day_line("Europe/London", _now=now)
    assert "evening" in line


def test_morning_uk():
    now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)  # 09:00 BST
    assert "morning" in time_of_day_line("Europe/London", _now=now)


def test_unknown_tz_is_empty():
    assert time_of_day_line("Not/AZone") == ""


def test_none_tz_returns_none_hour_and_empty_line():
    # A None timezone (no configured campaign tz) must NOT silently assume
    # Europe/London — it should produce no time-specific greeting at all.
    assert local_hour(None) is None
    assert time_of_day_line(None) == ""


def test_empty_string_tz_returns_none_hour_and_empty_line():
    assert local_hour("") is None
    assert time_of_day_line("   ") == ""
