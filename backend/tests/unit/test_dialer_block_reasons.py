"""Structured, user-facing block reasons + the opt-in testing override.

Regression anchor: campaign 35c79aeb (2026-07-28) ran 8 minutes, placed zero
calls, and the only explanation existed in a worker log line
("Cannot call now: calling_not_allowed_on_Tue"). These tests pin that every
gate now yields a stable machine code AND a human sentence, that a
schedule-blocked campaign is never described as completed, and that the
testing override is off unless deliberately switched on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints.campaigns import _campaign_activity
from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer.block_reasons import (
    BlockCode,
    SCHEDULE_BLOCK_CODES,
    classify,
    describe_days,
    describe_schedule,
    testing_override_notice,
)
from app.domain.services.dialer.testing_override import (
    SOURCE_CAMPAIGN,
    SOURCE_ENV,
    TESTING_OVERRIDE_ENV,
    override_audit_payload,
    schedule_override_source,
)


def _incident_rules() -> CallingRules:
    """The exact configuration from the real incident: Mon & Fri only,
    14:00-17:00 Europe/London."""
    return CallingRules(
        timezone="Europe/London",
        time_window_start="14:00",
        time_window_end="17:00",
        allowed_days=[0, 4],
    )


# ── every gate produces a structured code + a human sentence ──────────

@pytest.mark.parametrize("raw,expected_code,stage", [
    ("calling_not_allowed_on_Tue", BlockCode.SCHEDULE_DAY_NOT_ALLOWED, "schedule"),
    ("outside_time_window_14:00_17:00", BlockCode.SCHEDULE_OUTSIDE_WINDOW, "schedule"),
    ("out_of_minutes", BlockCode.OUT_OF_MINUTES, "quota"),
    ("campaign_not_runnable:draft", BlockCode.CAMPAIGN_NOT_RUNNING, "campaign"),
    ("campaign_stopped_before_originate", BlockCode.CAMPAIGN_NOT_RUNNING, "campaign"),
    ("lead_cooldown_0.5h_of_2h", BlockCode.LEAD_COOLDOWN, "schedule"),
    ("daily_lead_cap_reached_3/3", BlockCode.DAILY_LEAD_CAP, "schedule"),
    ("max_concurrent_calls_reached_10/10", BlockCode.MAX_CONCURRENT_CALLS, "concurrency"),
    ("batch_capacity", BlockCode.BATCH_CAPACITY, "pacing"),
    ("call_gap", BlockCode.CALL_GAP, "pacing"),
    ("tenant_gap", BlockCode.TENANT_GAP, "pacing"),
    ("call_guard_blocked", BlockCode.CALL_GUARD_BLOCKED, "safety"),
    ("call_guard_throttled", BlockCode.CALL_GUARD_THROTTLED, "safety"),
    ("call_guard_queued", BlockCode.CALL_GUARD_QUEUED, "safety"),
    ("caller_id_not_verified", BlockCode.CALLER_ID_NOT_VERIFIED, "caller_id"),
    ("voice_pipeline_unavailable", BlockCode.VOICE_PIPELINE_UNAVAILABLE, "voice"),
])
def test_every_gate_has_a_stable_code_and_a_human_message(raw, expected_code, stage):
    reason = classify(raw, rules=_incident_rules(), retry_after_seconds=30)
    assert reason.code is expected_code
    assert reason.stage == stage
    assert reason.severity in {"error", "warning", "info"}
    # Non-vacuous: a real sentence, not the raw log string echoed back.
    assert reason.message and raw not in reason.message
    assert reason.message.strip().endswith(".")
    assert len(reason.message.split()) >= 8
    # The raw string is preserved for the existing substring-matching surfaces.
    assert reason.details["raw"] == raw
    # JSON-safe shape for the UI.
    assert reason.to_dict()["code"] == expected_code.value


def test_reason_codes_are_all_distinct_values():
    """A duplicated code would silently merge two different problems in the UI."""
    values = [c.value for c in BlockCode]
    assert len(values) == len(set(values))


# ── the incident's own reason, end to end ─────────────────────────────

def test_day_block_names_the_schedule_and_the_next_window():
    rules = _incident_rules()
    # Tuesday 2026-07-28 20:41 Europe/London == 19:41 UTC.
    now = datetime(2026, 7, 28, 19, 41, tzinfo=timezone.utc)
    reason = classify("calling_not_allowed_on_Tue", rules=rules, now=now)

    assert reason.code is BlockCode.SCHEDULE_DAY_NOT_ALLOWED
    assert reason.is_schedule_block
    # Tells the user WHAT is configured…
    assert "Mon & Fri" in reason.message
    assert "14:00-17:00" in reason.message
    assert "Europe/London" in reason.message
    # …WHY today is out…
    assert "Tuesday" in reason.message
    # …WHEN it will dial…
    assert "Next window:" in reason.message
    # …and WHAT TO DO about it.
    assert "calling rules" in reason.message

    # Next eligible = Friday 14:00 London (the next allowed day), in UTC.
    assert reason.next_eligible_at is not None
    assert reason.next_eligible_at > now
    assert reason.next_eligible_at.weekday() == 4  # Friday
    assert reason.retry_after_seconds and reason.retry_after_seconds > 3600


def test_window_block_reports_next_window_on_an_allowed_day():
    rules = _incident_rules()
    # Monday 2026-07-27 08:00 UTC — allowed day, but before the 14:00 window.
    now = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
    reason = classify("outside_time_window_14:00_17:00", rules=rules, now=now)

    assert reason.code is BlockCode.SCHEDULE_OUTSIDE_WINDOW
    assert reason.next_eligible_at is not None
    assert reason.next_eligible_at > now
    assert "Next window:" in reason.message


def test_cooldown_next_eligible_is_derived_from_the_remaining_hours():
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    reason = classify("lead_cooldown_0.5h_of_2h", rules=_incident_rules(), now=now)
    assert reason.code is BlockCode.LEAD_COOLDOWN
    assert reason.details["hours_since_last_call"] == 0.5
    assert reason.details["min_hours_between_calls"] == 2.0
    # 2h required − 0.5h elapsed = 1.5h remaining.
    assert reason.next_eligible_at == now + timedelta(hours=1.5)


def test_concurrency_reason_quotes_the_actual_numbers():
    reason = classify("max_concurrent_calls_reached_7/10", rules=_incident_rules())
    assert reason.details["active_calls"] == 7
    assert reason.details["max_concurrent"] == 10
    assert "7 of 10" in reason.message


def test_pacing_reasons_are_marked_self_clearing_and_benign():
    for raw in ("batch_capacity", "call_gap", "tenant_gap", "call_guard_queued"):
        reason = classify(raw, retry_after_seconds=10)
        assert reason.is_self_clearing, raw
        assert reason.severity == "info", raw


def test_retry_after_drives_next_eligible_when_not_otherwise_computable():
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    reason = classify("call_gap", retry_after_seconds=45, now=now)
    assert reason.next_eligible_at == now + timedelta(seconds=45)


def test_unknown_and_empty_reasons_never_produce_a_blank_card():
    unknown = classify("some_reason_invented_next_year")
    assert unknown.code is BlockCode.ORIGINATE_FAILED
    assert unknown.message
    empty = classify(None)
    assert empty.code is BlockCode.UNKNOWN
    assert empty.message


def test_missing_rules_degrade_but_still_explain():
    """No effective rules available ⇒ still a code + a sentence, no crash."""
    reason = classify("calling_not_allowed_on_Sun", rules=None)
    assert reason.code is BlockCode.SCHEDULE_DAY_NOT_ALLOWED
    assert reason.message


# ── schedule descriptions ─────────────────────────────────────────────

@pytest.mark.parametrize("days,expected", [
    ([0, 4], "Mon & Fri"),
    ([0, 1, 2, 3, 4], "Mon-Fri"),
    ([2], "Wed"),
    ([0, 1, 2, 3, 4, 5, 6], "every day"),
    ([], "no days"),
    ([1, 3, 5], "Tue, Thu & Sat"),
])
def test_describe_days(days, expected):
    assert describe_days(days) == expected


def test_describe_schedule_matches_the_incident_configuration():
    assert describe_schedule(_incident_rules()) == "Mon & Fri, 14:00-17:00 Europe/London"


# ── (B) schedule-blocked must NOT read as completed ───────────────────

def test_schedule_blocked_campaign_is_waiting_not_completed():
    blocking = classify(
        "calling_not_allowed_on_Tue", rules=_incident_rules(),
    ).to_dict()
    activity = _campaign_activity("running", blocking, {"pending": 1})

    assert activity["state"] == "waiting_for_calling_window"
    assert activity["waiting_on_schedule"] is True
    assert activity["work_remaining"] is True
    assert activity["next_eligible_at"] is not None
    # The whole point: nothing about this says "completed"/"done".
    assert "complet" not in activity["state"]
    assert activity["state"] not in {"completed", "stopped", "idle"}


def test_running_campaign_with_no_blocker_reads_as_dialing():
    activity = _campaign_activity("running", None, {"pending": 3})
    assert activity["state"] == "dialing"
    assert activity["waiting_on_schedule"] is False
    assert activity["schedule_override_active"] is False


def test_explicitly_completed_campaign_still_reads_as_completed():
    """Non-vacuity guard: the helper isn't just never saying 'completed'."""
    activity = _campaign_activity("completed", None, {})
    assert activity["state"] == "completed"
    assert activity["work_remaining"] is False


def test_override_campaign_activity_flags_testing_mode():
    blocking = testing_override_notice(
        _incident_rules(), source=SOURCE_ENV,
    ).to_dict()
    activity = _campaign_activity("running", blocking, {"pending": 1})
    assert activity["state"] == "dialing_testing_override"
    assert activity["schedule_override_active"] is True
    assert activity["waiting_on_schedule"] is False


# ── testing override: OFF by default ──────────────────────────────────

def test_override_is_off_by_default(monkeypatch):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    assert schedule_override_source(None) is None
    assert schedule_override_source({}) is None
    # A campaign config with other keys set must not turn it on.
    assert schedule_override_source(
        {"ignore_schedule": True, "batch_size": 5, "allowed_days": [0]}
    ) is None


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe", "None"])
def test_override_stays_off_for_non_affirmative_values(monkeypatch, value):
    """Ambiguity must never widen a compliance gate."""
    monkeypatch.setenv(TESTING_OVERRIDE_ENV, value)
    assert schedule_override_source(None) is None
    assert schedule_override_source({"testing_mode_ignore_schedule": value}) is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", True])
def test_override_switches_on_only_when_deliberately_set(monkeypatch, value):
    monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
    assert schedule_override_source({"testing_mode_ignore_schedule": value}) == SOURCE_CAMPAIGN

    monkeypatch.setenv(TESTING_OVERRIDE_ENV, str(value))
    assert schedule_override_source(None) == SOURCE_ENV


def test_campaign_switch_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv(TESTING_OVERRIDE_ENV, "true")
    assert schedule_override_source({"testing_mode_ignore_schedule": True}) == SOURCE_CAMPAIGN


def test_override_notice_is_loud_and_names_the_schedule():
    notice = testing_override_notice(_incident_rules(), source=SOURCE_ENV)
    assert notice.code is BlockCode.TESTING_MODE_SCHEDULE_BYPASSED
    assert "TESTING MODE" in notice.message
    assert "Mon & Fri, 14:00-17:00 Europe/London" in notice.message
    assert "compliance" in notice.message.lower() or "legal" in notice.message.lower()
    # It is NOT a schedule block — it must not be counted as "waiting".
    assert notice.code not in SCHEDULE_BLOCK_CODES


def test_override_audit_payload_is_self_describing():
    payload = override_audit_payload(
        source=SOURCE_CAMPAIGN,
        blocked_reason="calling_not_allowed_on_Tue",
        schedule="Mon & Fri, 14:00-17:00 Europe/London",
    )
    assert payload["schedule_override"] is True
    assert payload["schedule_override_source"] == SOURCE_CAMPAIGN
    assert payload["schedule_override_blocked_reason"] == "calling_not_allowed_on_Tue"
