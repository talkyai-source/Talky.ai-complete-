"""Tests for the typed TelephonySettings class (T4-C5).

Locks the EXACT parsing semantics of every Tier-4 telephony env knob.
Every original inline helper (in deepgram_flux, user_first,
telephony_session_config, voice_tuning, telephony.config) had its own
hand-rolled parser; this class is the consolidated source of truth.
If any of these tests fail, a refactor has silently changed an
operator-visible default — that's a production behaviour drift even
if no other test catches it.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.core.telephony_settings import (
    FluxSettings,
    TelephonySettings,
    UserFirstSettings,
    VoiceTuningSettings,
    get_telephony_settings,
    parse_bool_env,
    parse_float_env,
    parse_int_env,
    parse_optional_float_env,
    reset_telephony_settings,
)


@pytest.fixture(autouse=True)
def _scrub_env():
    """Each test starts from a known-clean env. The autouse conftest
    fixture already resets the settings cache; this strips any
    Tier-4 env vars that might leak in from the host environment."""
    keys = [k for k in os.environ if k.startswith("TELEPHONY_")]
    with patch.dict(os.environ, {}, clear=False):
        for k in keys:
            os.environ.pop(k, None)
        yield


# ---------------------------------------------------------------------
# Defaults — locks the values the codebase used pre-C5
# ---------------------------------------------------------------------


class TestDefaults:
    def test_unconfigured_env_produces_known_defaults(self):
        """Operators on a fresh deploy with no TELEPHONY_* vars set must
        see the same numbers the inline helpers were producing before
        C5 collected them. A diff here is a behaviour drift."""
        s = TelephonySettings.from_env()
        assert s.flux.eot_timeout_ms == 2000
        assert s.flux.eager_eot_threshold == 0.5
        assert s.user_first.fallback_enabled is True
        assert s.user_first.greet_on_pickup is False
        assert s.user_first.open_s == 8.0
        assert s.user_first.reprompt_s == 8.0
        assert s.user_first.farewell_s == 6.0
        assert s.user_first.max_reprompts == 2
        assert s.voice_tuning.default_json == ""
        assert s.voice_tuning.overrides_json == ""
        assert s.mute_during_tts is False
        assert s.first_speaker_default == "agent"


# ---------------------------------------------------------------------
# Bool parsing — two conventions, both preserved
# ---------------------------------------------------------------------


class TestBoolParsing:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On"])
    def test_truthy_values_flip_default_off_to_on(self, value):
        with patch.dict(os.environ, {"X": value}):
            assert parse_bool_env("X", default=False) is True

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "Off"])
    def test_falsy_values_flip_default_on_to_off(self, value):
        with patch.dict(os.environ, {"X": value}):
            assert parse_bool_env("X", default=True) is False

    def test_unset_returns_default(self):
        # Env scrubbed by the autouse fixture above.
        assert parse_bool_env("UNSET_VAR", default=True) is True
        assert parse_bool_env("UNSET_VAR", default=False) is False

    def test_garbage_value_returns_default(self):
        with patch.dict(os.environ, {"X": "garbage"}):
            assert parse_bool_env("X", default=True) is True
            assert parse_bool_env("X", default=False) is False

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"X": ""}):
            assert parse_bool_env("X", default=True) is True
            assert parse_bool_env("X", default=False) is False


# ---------------------------------------------------------------------
# Numeric parsing — defaults on bad input, log-and-fall-back never raise
# ---------------------------------------------------------------------


class TestIntParsing:
    def test_valid_value_parses(self):
        with patch.dict(os.environ, {"X": "42"}):
            assert parse_int_env("X", default=10) == 42

    def test_invalid_value_returns_default(self):
        with patch.dict(os.environ, {"X": "not-a-number"}):
            assert parse_int_env("X", default=10) == 10

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"X": ""}):
            assert parse_int_env("X", default=10) == 10

    def test_below_min_clamps_to_default(self):
        with patch.dict(os.environ, {"X": "5"}):
            assert parse_int_env("X", default=100, min_=10) == 100

    def test_above_max_clamps_to_default(self):
        with patch.dict(os.environ, {"X": "100000"}):
            assert parse_int_env("X", default=2000, max_=10000) == 2000


class TestFloatParsing:
    def test_valid_value_parses(self):
        with patch.dict(os.environ, {"X": "5.5"}):
            assert parse_float_env("X", default=1.0) == 5.5

    def test_invalid_value_returns_default(self):
        with patch.dict(os.environ, {"X": "garbage"}):
            assert parse_float_env("X", default=1.0) == 1.0

    def test_below_min_returns_default(self):
        with patch.dict(os.environ, {"X": "0.5"}):
            assert parse_float_env("X", default=2.0, min_=1.0) == 2.0


class TestOptionalFloatParsing:
    """The eager-EOT threshold is the unique field that supports an
    explicit ``None`` (eager mode disabled) via 'off' / 'none'."""

    @pytest.mark.parametrize("disabled", ["off", "none", "disabled", "OFF", "None"])
    def test_disabled_strings_return_none(self, disabled):
        with patch.dict(os.environ, {"X": disabled}):
            assert parse_optional_float_env(
                "X", default=0.5, min_=0.3, max_=0.9,
            ) is None

    def test_valid_float_in_range_parses(self):
        with patch.dict(os.environ, {"X": "0.7"}):
            assert parse_optional_float_env(
                "X", default=0.5, min_=0.3, max_=0.9,
            ) == 0.7

    def test_out_of_range_returns_default(self):
        with patch.dict(os.environ, {"X": "1.5"}):
            assert parse_optional_float_env(
                "X", default=0.5, min_=0.3, max_=0.9,
            ) == 0.5

    def test_unset_returns_default(self):
        assert parse_optional_float_env(
            "UNSET", default=0.5, min_=0.3, max_=0.9,
        ) == 0.5


# ---------------------------------------------------------------------
# OPEN_S clamp — historical behaviour preserved
# ---------------------------------------------------------------------


class TestOpenSecondsClamp:
    """Original ``_user_first_open_seconds()`` clamped sub-2.0 values
    UP to 2.0 (rather than falling back to the 8.0 default). The
    rationale is in the original docstring: a sub-second fallback
    races real callee speech and reintroduces the first-turn delay
    the mode is meant to avoid."""

    def test_subsecond_value_clamps_up_to_two(self):
        with patch.dict(os.environ, {"TELEPHONY_USER_FIRST_OPEN_S": "0.5"}):
            s = TelephonySettings.from_env()
            assert s.user_first.open_s == 2.0

    def test_value_at_two_passes(self):
        with patch.dict(os.environ, {"TELEPHONY_USER_FIRST_OPEN_S": "2.0"}):
            s = TelephonySettings.from_env()
            assert s.user_first.open_s == 2.0

    def test_value_above_two_passes(self):
        with patch.dict(os.environ, {"TELEPHONY_USER_FIRST_OPEN_S": "7.5"}):
            s = TelephonySettings.from_env()
            assert s.user_first.open_s == 7.5


# ---------------------------------------------------------------------
# First speaker — clamped to known set
# ---------------------------------------------------------------------


class TestFirstSpeakerDefault:
    def test_user_value_passes(self):
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
            s = TelephonySettings.from_env()
            assert s.first_speaker_default == "user"

    def test_agent_value_passes(self):
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "agent"}):
            s = TelephonySettings.from_env()
            assert s.first_speaker_default == "agent"

    def test_garbage_falls_back_to_agent(self):
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "sideways"}):
            s = TelephonySettings.from_env()
            assert s.first_speaker_default == "agent"

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "USER"}):
            s = TelephonySettings.from_env()
            assert s.first_speaker_default == "user"


# ---------------------------------------------------------------------
# Singleton + reset
# ---------------------------------------------------------------------


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_telephony_settings()
        b = get_telephony_settings()
        assert a is b

    def test_reset_drops_singleton(self):
        a = get_telephony_settings()
        reset_telephony_settings()
        b = get_telephony_settings()
        assert a is not b

    def test_env_change_after_first_read_is_NOT_picked_up(self):
        """Production deployments expect stable settings across the
        lifetime of a process. Env changes mid-process are a dev /
        test pattern; they only take effect after reset_..."""
        a = get_telephony_settings()
        with patch.dict(os.environ, {"TELEPHONY_FLUX_EOT_TIMEOUT_MS": "9000"}):
            b = get_telephony_settings()
            assert b is a
            assert b.flux.eot_timeout_ms == a.flux.eot_timeout_ms

    def test_reset_then_new_env_takes_effect(self):
        a = get_telephony_settings()
        reset_telephony_settings()
        with patch.dict(os.environ, {"TELEPHONY_FLUX_EOT_TIMEOUT_MS": "9000"}):
            b = get_telephony_settings()
            assert b.flux.eot_timeout_ms == 9000
            assert a.flux.eot_timeout_ms != 9000


# ---------------------------------------------------------------------
# Frozen dataclasses prevent mid-call mutation
# ---------------------------------------------------------------------


class TestImmutability:
    def test_settings_are_frozen(self):
        s = TelephonySettings()
        with pytest.raises(Exception):  # FrozenInstanceError
            s.mute_during_tts = True

    def test_nested_settings_are_frozen(self):
        s = TelephonySettings()
        with pytest.raises(Exception):
            s.flux.eot_timeout_ms = 1234


# ---------------------------------------------------------------------
# End-to-end through-the-stack — the migrated consumers read the
# settings transparently. Regression suite already covers the inline
# helpers; this just locks the new wiring.
# ---------------------------------------------------------------------


class TestIntegrationWiring:
    def test_flux_helpers_read_from_settings(self):
        """The deepgram_flux ``_env_*_default`` helpers now delegate to
        the shared settings. Asserting through the helpers ensures the
        wiring isn't accidentally bypassed in a future refactor."""
        from app.infrastructure.stt.deepgram_flux import (
            _env_eager_default,
            _env_timeout_default,
        )
        with patch.dict(os.environ, {
            "TELEPHONY_FLUX_EOT_TIMEOUT_MS": "1500",
            "TELEPHONY_FLUX_EAGER_EOT_THRESHOLD": "0.6",
        }):
            reset_telephony_settings()
            assert _env_timeout_default() == 1500
            assert _env_eager_default() == 0.6

    def test_first_speaker_helper_reads_from_settings(self):
        from app.domain.services.telephony.config import _outbound_first_speaker
        with patch.dict(os.environ, {"TELEPHONY_FIRST_SPEAKER": "user"}):
            reset_telephony_settings()
            assert _outbound_first_speaker() == "user"

    def test_mute_during_tts_helper_reads_from_settings(self):
        from app.domain.services.telephony_session_config import (
            _telephony_mute_during_tts_default,
        )
        with patch.dict(os.environ, {"TELEPHONY_MUTE_DURING_TTS": "true"}):
            reset_telephony_settings()
            assert _telephony_mute_during_tts_default() is True
