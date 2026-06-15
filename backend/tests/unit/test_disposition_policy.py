"""Unit tests for the Phase-2 disposition retry brain and the daily
per-lead cap gate.

These lock in the confirmed cadence policy (2026-06-12):

    Busy      5m → 15m → 45m   (cap 4)
    No-answer 2h → next-day    (cap 3)
    Voicemail 4h once          (cap 2)
    Rejected  no retry — stop
    Failed    30s → 2m → 10m   (cap 3)
"""
import asyncio

import pytest

from app.domain.models.dialer_job import CallOutcome
from app.workers.disposition_policy import decide, is_success
from app.domain.models.calling_rules import CallingRules
from app.domain.services.scheduling_rules import SchedulingRuleEngine


# ── busy: 5m → 15m → 45m, cap 4 ───────────────────────────────────
def test_busy_schedule_and_cap():
    assert decide(CallOutcome.BUSY, 1).delay_seconds == 300
    assert decide(CallOutcome.BUSY, 2).delay_seconds == 900
    assert decide(CallOutcome.BUSY, 3).delay_seconds == 2700
    assert decide(CallOutcome.BUSY, 1).should_retry is True
    # 4th attempt completed → cap reached, stop.
    d = decide(CallOutcome.BUSY, 4)
    assert d.should_retry is False
    assert d.reason == "busy_max_attempts"


# ── no-answer: 2h → next-day(~20h), cap 3 ─────────────────────────
def test_no_answer_schedule_and_cap():
    assert decide(CallOutcome.NO_ANSWER, 1).delay_seconds == 2 * 3600
    assert decide(CallOutcome.NO_ANSWER, 2).delay_seconds == 20 * 3600
    assert decide(CallOutcome.NO_ANSWER, 3).should_retry is False


# ── voicemail: one retry at 4h, cap 2 ─────────────────────────────
def test_voicemail_one_retry_then_stop():
    assert decide(CallOutcome.VOICEMAIL, 1).delay_seconds == 4 * 3600
    assert decide(CallOutcome.VOICEMAIL, 1).should_retry is True
    assert decide(CallOutcome.VOICEMAIL, 2).should_retry is False


# ── rejected: never retry ─────────────────────────────────────────
def test_rejected_never_retries():
    d = decide(CallOutcome.REJECTED, 1)
    assert d.should_retry is False
    assert d.is_success is False
    assert d.reason == "rejected"


# ── failed / timeout: fast geometric backoff, cap 3 (2 retries) ───
def test_failed_and_timeout_backoff():
    for oc in (CallOutcome.FAILED, CallOutcome.TIMEOUT):
        assert decide(oc, 1).delay_seconds == 30
        assert decide(oc, 2).delay_seconds == 120
        assert decide(oc, 2).should_retry is True
        # cap 3 → after the 3rd attempt completes, stop.
        assert decide(oc, 3).should_retry is False


# ── success outcomes are terminal + flagged ───────────────────────
def test_success_outcomes_terminal():
    for oc in (CallOutcome.GOAL_ACHIEVED, CallOutcome.ANSWERED):
        d = decide(oc, 1)
        assert d.should_retry is False
        assert d.is_success is True
        assert is_success(oc) is True
    assert is_success(CallOutcome.BUSY) is False


# ── terminal-no-retry outcomes ────────────────────────────────────
def test_terminal_no_retry_outcomes():
    for oc in (
        CallOutcome.GOAL_NOT_ACHIEVED,
        CallOutcome.SPAM,
        CallOutcome.INVALID,
        CallOutcome.UNAVAILABLE,
        CallOutcome.DISCONNECTED,
    ):
        d = decide(oc, 1)
        assert d.should_retry is False
        assert d.is_success is False


# ── monotonic non-increasing? no — schedules grow; assert ordering ─
def test_busy_delays_increase():
    delays = [decide(CallOutcome.BUSY, a).delay_seconds for a in (1, 2, 3)]
    assert delays == sorted(delays)


# ── daily per-lead cap gate (scheduling_rules) ────────────────────
def _all_day_rules(**kw) -> CallingRules:
    return CallingRules(
        time_window_start="00:00",
        time_window_end="23:59",
        allowed_days=[0, 1, 2, 3, 4, 5, 6],
        **kw,
    )


def test_daily_cap_blocks_at_ceiling():
    eng = SchedulingRuleEngine()
    rules = _all_day_rules(max_calls_per_lead_per_day=3)
    ok, reason = asyncio.run(
        eng.can_make_call("t", "c", rules, lead_attempts_today=3)
    )
    assert ok is False
    assert reason == "daily_lead_cap_reached_3/3"


def test_daily_cap_allows_under_ceiling():
    eng = SchedulingRuleEngine()
    rules = _all_day_rules(max_calls_per_lead_per_day=3)
    ok, reason = asyncio.run(
        eng.can_make_call("t", "c", rules, lead_attempts_today=2)
    )
    assert ok is True


def test_daily_cap_disabled_when_zero():
    eng = SchedulingRuleEngine()
    rules = _all_day_rules(max_calls_per_lead_per_day=0)
    ok, _ = asyncio.run(
        eng.can_make_call("t", "c", rules, lead_attempts_today=99)
    )
    assert ok is True


def test_daily_cap_disabled_when_count_unknown():
    eng = SchedulingRuleEngine()
    rules = _all_day_rules(max_calls_per_lead_per_day=3)
    ok, _ = asyncio.run(
        eng.can_make_call("t", "c", rules, lead_attempts_today=None)
    )
    assert ok is True
