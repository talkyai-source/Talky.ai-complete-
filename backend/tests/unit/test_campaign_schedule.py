"""Unit tests for Phase 3c-v2 per-campaign calling schedule + override."""
import asyncio

import pytest

from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer.campaign_schedule import effective_rules, schedule_ignored
from app.domain.services.scheduling_rules import SchedulingRuleEngine


def _tenant() -> CallingRules:
    return CallingRules(
        timezone="UTC", time_window_start="09:00", time_window_end="19:00",
        allowed_days=[0, 1, 2, 3, 4],
    )


# ── effective_rules overlay ───────────────────────────────────────
def test_overlay_applies_campaign_fields():
    er = effective_rules(_tenant(), {
        "timezone": "America/New_York",
        "time_window_start": "08:00",
    })
    assert er.timezone == "America/New_York"
    assert er.time_window_start == "08:00"
    assert er.time_window_end == "19:00"  # untouched → tenant value


def test_overlay_none_or_empty_returns_tenant():
    t = _tenant()
    assert effective_rules(t, None) is t
    assert effective_rules(t, {}) is t


def test_overlay_ignores_blank_values():
    er = effective_rules(_tenant(), {"timezone": "", "allowed_days": [5, 6]})
    assert er.timezone == "UTC"            # blank ignored
    assert er.allowed_days == [5, 6]


def test_overlay_malformed_falls_back_to_tenant():
    t = _tenant()
    # A wrong-typed overlay (allowed_days must be a list of ints) breaks
    # CallingRules construction → safety net returns the tenant rules so a
    # bad config can never break dialing. (Well-formed bad time strings are
    # rejected upstream by the API schema validator, so they never get here.)
    er = effective_rules(t, {"allowed_days": "garbage"})
    assert er is t


# ── schedule_ignored ──────────────────────────────────────────────
def test_schedule_ignored():
    assert schedule_ignored({"ignore_schedule": True}) is True
    assert schedule_ignored({"ignore_schedule": False}) is False
    assert schedule_ignored({}) is False
    assert schedule_ignored(None) is False


# ── DIALER_ALLOW_IGNORE_SCHEDULE platform kill switch (packet W6 item 4) ──
# A tenant-settable "call anytime" dials around the clock. The platform can
# turn the override off for everyone with DIALER_ALLOW_IGNORE_SCHEDULE=false;
# unset keeps today's behaviour (honoured).
def test_ignore_schedule_is_honoured_when_the_switch_is_unset(monkeypatch):
    monkeypatch.delenv("DIALER_ALLOW_IGNORE_SCHEDULE", raising=False)
    assert schedule_ignored({"ignore_schedule": True}) is True


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", "", "  true  ", "anything-else"])
def test_ignore_schedule_is_honoured_unless_the_switch_is_off(monkeypatch, value):
    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", value)
    assert schedule_ignored({"ignore_schedule": True}) is True


@pytest.mark.parametrize("value", ["false", "FALSE", " False ", "0", "no", "off"])
def test_kill_switch_makes_call_anytime_a_no_op(monkeypatch, value):
    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", value)
    assert schedule_ignored({"ignore_schedule": True}) is False


def test_kill_switch_does_not_invent_an_override(monkeypatch):
    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", "false")
    assert schedule_ignored({"ignore_schedule": False}) is False
    assert schedule_ignored({}) is False
    assert schedule_ignored(None) is False


def test_kill_switch_re_enforces_the_window_in_the_rules_engine(monkeypatch):
    # The worker passes enforce_window=not schedule_ignored(cfg): with the
    # switch off a closed window must block a "call anytime" campaign again.
    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", "false")
    eng = SchedulingRuleEngine()
    rules = CallingRules(
        timezone="UTC", time_window_start="09:00", time_window_end="19:00",
        allowed_days=[],
    )
    cfg = {"ignore_schedule": True}

    allowed, reason = asyncio.run(
        eng.can_make_call("t", "c", rules, enforce_window=not schedule_ignored(cfg))
    )

    assert allowed is False
    assert reason.startswith("calling_not_allowed_on_")


# ── enforce_window override in the rules engine ───────────────────
def test_enforce_window_false_bypasses_closed_window():
    eng = SchedulingRuleEngine()
    # allowed_days=[] → the window is ALWAYS closed.
    rules = CallingRules(
        timezone="UTC", time_window_start="09:00", time_window_end="19:00",
        allowed_days=[],
    )
    blocked, reason = asyncio.run(eng.can_make_call("t", "c", rules, enforce_window=True))
    assert blocked is False                 # window gate blocks

    allowed, reason2 = asyncio.run(eng.can_make_call("t", "c", rules, enforce_window=False))
    assert allowed is True                  # override skips the window gate
    assert reason2 == "all_rules_passed"
