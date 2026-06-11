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


def test_none_reason_uses_category_tiebreaker():
    a = advise(None, category="tts")
    assert a.stage == "voice"
    a2 = advise("", category="telephony")
    assert a2.stage == "carrier"
