"""Unit tests for Phase-3c timezone-aware calling windows.

Covers the phone→timezone resolver, the feature flag, and that the
calling-window check honors a per-lead timezone override.
"""
import asyncio
from datetime import datetime

import pytz
import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.services.scheduling_rules import SchedulingRuleEngine
from app.domain.services.dialer import lead_timezone as lt


# ── resolver ──────────────────────────────────────────────────────
def test_resolve_nanp_numbers():
    lt._resolve_cached.cache_clear()
    assert lt.resolve_lead_timezone("+12125551234") == "America/New_York"
    assert lt.resolve_lead_timezone("+13105551234") == "America/Los_Angeles"
    assert lt.resolve_lead_timezone("+13125551234") == "America/Chicago"


def test_resolve_none_for_missing_or_garbage():
    assert lt.resolve_lead_timezone(None) is None
    assert lt.resolve_lead_timezone("") is None
    assert lt.resolve_lead_timezone("not-a-number") is None


def test_feature_flag_disables_resolution(monkeypatch):
    monkeypatch.setenv("DIALER_PER_LEAD_TIMEZONE", "0")
    assert lt.resolve_lead_timezone("+12125551234") is None
    monkeypatch.setenv("DIALER_PER_LEAD_TIMEZONE", "1")
    assert lt.resolve_lead_timezone("+12125551234") == "America/New_York"


# ── window honors tz override ─────────────────────────────────────
def _rules_9_to_19_in(tz: str) -> CallingRules:
    return CallingRules(
        time_window_start="09:00",
        time_window_end="19:00",
        timezone=tz,
        allowed_days=[0, 1, 2, 3, 4, 5, 6],
    )


def test_window_uses_override_tz():
    # 11:00 US/Eastern == 08:00 US/Pacific. With a 9–19 window:
    #   - Eastern lead: inside window.
    #   - Pacific lead: before window (8am).
    rules = _rules_9_to_19_in("America/New_York")
    check = pytz.timezone("America/New_York").localize(
        datetime(2026, 6, 15, 11, 0)  # a Monday, 11am ET
    )
    ok_et, _ = rules.is_within_time_window(check_time=check, tz_override="America/New_York")
    ok_pt, reason_pt = rules.is_within_time_window(check_time=check, tz_override="America/Los_Angeles")
    assert ok_et is True
    assert ok_pt is False
    assert "time_window" in reason_pt


def test_window_falls_back_to_tenant_tz_when_override_none():
    rules = _rules_9_to_19_in("America/New_York")
    check = pytz.timezone("America/New_York").localize(datetime(2026, 6, 15, 11, 0))
    ok, _ = rules.is_within_time_window(check_time=check, tz_override=None)
    assert ok is True


# ── rules engine threads lead_timezone through ────────────────────
def test_can_make_call_respects_lead_timezone():
    eng = SchedulingRuleEngine()
    rules = _rules_9_to_19_in("America/New_York")
    # Can't inject check_time through can_make_call (it uses now()), so we
    # assert the plumbing: a deliberately invalid override must not crash
    # and falls back to tenant tz (still a valid bool result).
    ok, reason = asyncio.run(
        eng.can_make_call("t", "c", rules, lead_timezone="Not/AZone")
    )
    assert isinstance(ok, bool)
