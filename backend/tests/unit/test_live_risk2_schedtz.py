"""invalid_timezone (CallingRules fail-closed) must map to SCHEDULE_INVALID
with a message naming the timezone, and have matching Call Issues advice."""
from __future__ import annotations

from app.domain.models.calling_rules import CallingRules
from app.domain.services.call_issue_advice import advise
from app.domain.services.dialer.block_reasons import (
    SCHEDULE_BLOCK_CODES,
    BlockCode,
    classify,
)


def test_rules_emit_invalid_timezone_reason():
    ok, reason = CallingRules(timezone="Not/AZone").is_within_time_window()
    assert ok is False and reason == "invalid_timezone"


def test_classify_invalid_timezone_is_schedule_invalid():
    r = classify("invalid_timezone")
    assert r.code is BlockCode.SCHEDULE_INVALID
    assert r.code not in SCHEDULE_BLOCK_CODES  # never bypassed by the testing override
    assert "timezone" in r.message.lower()
    assert "Europe/London" in r.message
    assert "calling hours are invalid" not in r.message


def test_invalid_time_window_wording_unchanged():
    r = classify("invalid_time_window")
    assert r.code is BlockCode.SCHEDULE_INVALID
    assert "start time must be before the end time" in r.message


def test_call_issue_advice_for_invalid_timezone():
    a = advise("invalid_timezone")
    assert a.stage == "schedule" and a.severity == "error"
    assert "timezone" in (a.title + a.suggestion).lower()
    assert "start time must be before" not in a.suggestion
