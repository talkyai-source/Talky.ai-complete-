"""The reason→advice map is what makes silent dial failures legible to the
operator, so the mapping for each known gate is pinned here."""
from __future__ import annotations

import pytest

from app.domain.services.call_issue_advice import advise


@pytest.mark.parametrize("code,stage,severity", [
    ("out_of_minutes", "quota", "error"),
    ("caller_id_not_verified", "caller_id", "error"),
    ("campaign_stopped", "campaign", "warning"),
    ("outside_time_window_09:00_19:00", "schedule", "warning"),
    ("max_concurrent_calls_reached_10/10", "concurrency", "warning"),
    ("pre_originate_warmup_timeout", "voice", "error"),
    ("voice_pipeline_unavailable", "voice", "error"),
    ("service_unavailable", "voice", "error"),
    ("call_guard_blocked", "safety", "error"),
    ("call_guard_throttled", "safety", "warning"),
])
def test_known_reasons_map_to_expected_stage_and_severity(code, stage, severity):
    a = advise(code)
    assert a.stage == stage
    assert a.severity == severity
    assert a.title and a.suggestion  # never blank


def test_elevenlabs_401_is_voice_error_with_switch_hint():
    a = advise("pre_originate_warmup_handshake_failed: ElevenLabs API error: 401")
    assert a.stage == "voice"
    assert a.severity == "error"
    # specific ElevenLabs rule wins over the generic warmup rule
    assert "ElevenLabs" in a.title


def test_unknown_reason_falls_back_but_is_never_blank():
    a = advise("some_brand_new_reason_we_never_saw")
    assert a.title and a.suggestion
    assert a.severity in {"error", "warning", "info"}


def test_assemblyai_failure_names_selected_stt_before_generic_warmup():
    advice = advise("pre_originate_warmup_handshake_failed: AssemblyAI authentication failed (HTTP 401)")
    assert advice.stage == "voice"
    assert "AssemblyAI" in advice.title
    assert "concurrency" in advice.suggestion
    assert "Cartesia" not in advice.suggestion


def test_none_reason_uses_category_tiebreaker():
    a = advise(None, category="tts")
    assert a.stage == "voice"
    a2 = advise("", category="telephony")
    assert a2.stage == "carrier"


# ---------------------------------------------------------------------------
# W5: every code the dialer stores must have its own title + suggestion, not
# the "held back for the reason shown" fallback.
# ---------------------------------------------------------------------------
_FALLBACK_TITLE = advise("some_brand_new_reason_we_never_saw").title

_SPECIFIC_CODES = [
    # (reason, stage, severity)
    ("tenant_pbx_required", "carrier", "error"),
    ("no_ready_trunk", "carrier", "error"),
    ("no_own_trunk", "carrier", "error"),
    ("selected_trunk_not_ready", "carrier", "error"),
    ("campaign_assigned_trunk_not_ready", "carrier", "error"),
    ("trunk_caller_id_not_verified", "caller_id", "error"),
    ("telephony_selection_unavailable", "carrier", "warning"),
    ("metering_unavailable", "quota", "warning"),
    ("rules_unavailable", "schedule", "warning"),
    ("rejected", "carrier", "warning"),
    ("failed", "other", "warning"),
    ("http_500", "other", "warning"),
    ("internal_error", "other", "warning"),
    ("origination_result_unknown", "carrier", "warning"),
    ("platform_outbound_calls_paused", "safety", "warning"),
    ("tenant_suspended", "other", "error"),
    ("subscription_past_due", "quota", "error"),
    ("outside_business_hours", "schedule", "warning"),
    ("guard_check_unavailable", "safety", "warning"),
    # reasons introduced alongside this packet
    ("line_not_ready", "carrier", "error"),
    ("telephony_unavailable", "carrier", "warning"),
    ("campaign_paused", "campaign", "warning"),
    ("job_not_active", "campaign", "info"),
    ("lead_not_dialable", "campaign", "warning"),
]


@pytest.mark.parametrize("code,stage,severity", _SPECIFIC_CODES)
def test_dialer_codes_have_specific_advice_not_the_fallback(code, stage, severity):
    a = advise(code)
    assert a.title != _FALLBACK_TITLE, code
    assert "held back for the reason shown" not in a.suggestion
    assert a.stage == stage
    assert a.severity == severity
    assert a.title and a.suggestion


@pytest.mark.parametrize("code", ["campaign_not_runnable:paused", "campaign_not_runnable:missing"])
def test_gate_stored_not_runnable_reason_keeps_its_rule(code):
    assert advise(code).title == "Campaign is not running"


def test_trunk_caller_id_rule_is_not_shadowed_by_the_account_caller_id_rule():
    assert advise("trunk_caller_id_not_verified").title != advise("caller_id_not_verified").title


def test_caller_id_copy_does_not_tell_users_to_set_a_campaign_caller_id():
    for code in ("caller_id_not_verified", "trunk_caller_id_not_verified", "line_not_ready"):
        text = advise(code).suggestion.lower()
        assert "campaign's caller id" not in text
    assert "support" in advise("caller_id_not_verified").suggestion.lower()
    assert "support" in advise("trunk_caller_id_not_verified").suggestion.lower()


def test_cloud_telephony_unavailable_is_not_called_a_self_healing_outage():
    # Contains "telephony_unavailable" as a substring but means the opposite:
    # cloud calling is switched off in production and will not come back.
    a = advise("cloud_telephony_unavailable")
    assert a.title != advise("telephony_unavailable").title
    assert "automatically" not in a.suggestion.lower()


@pytest.mark.parametrize("reason", ["failed: timeout", "rejected (403)", "http_503", "HTTP_500"])
def test_bare_generic_tokens_match_only_as_whole_codes(reason):
    assert advise(reason).title != _FALLBACK_TITLE


def test_generic_words_inside_a_specific_reason_do_not_hijack_it():
    # "failed" / "rejected" appear inside provider errors that already have a
    # more specific rule or category advice.
    a = advise("pre_originate_warmup_handshake_failed: ElevenLabs API error: 401")
    assert "ElevenLabs" in a.title
    assert advise("some_thing_rejected_oddly", category="tts").stage == "voice"


def test_short_dialer_codes_match_only_as_whole_tokens():
    # 'line_not_ready' is a substring of 'voice_pipeline_not_ready'; the line
    # advice must not claim a voice-pipeline reason.
    from app.domain.services.call_issue_advice import advise

    line = advise("line_not_ready")
    assert "line" in line.title.lower()
    assert "line" in advise("line_not_ready:no_ready_trunk").title.lower()
    assert advise("voice_pipeline_not_ready").title != line.title
    assert advise("telephony_unavailable").title != advise("cloud_telephony_unavailable").title
