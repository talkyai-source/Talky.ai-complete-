"""Tests for the per-tenant voice tuning resolver.

Three things matter at this layer:

1. Defaults — an unconfigured deployment behaves like the pre-T3.9
   hardcoded values. No surprise behaviour change for existing tenants.
2. Override fidelity — operator JSON makes it through type coercion
   intact, partial overrides merge onto defaults (not replace them
   wholesale), and per-tenant overrides shadow the global default.
3. Resilience — bad JSON, wrong shapes, unknown keys, type errors all
   produce log warnings but never raise. A misconfigured env var must
   not be able to take a tenant's calls offline.
"""
import os
from unittest.mock import patch

import pytest

from app.domain.services.voice_tuning import (
    VoiceTuning,
    VoiceTuningResolver,
    get_voice_tuning_resolver,
    reset_voice_tuning_resolver,
)


@pytest.fixture(autouse=True)
def _clean_env_and_singleton():
    """Each test starts from a known-clean env + resolver singleton."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TELEPHONY_TUNING_DEFAULT_JSON", None)
        os.environ.pop("TELEPHONY_TUNING_OVERRIDES_JSON", None)
        reset_voice_tuning_resolver()
        yield
        reset_voice_tuning_resolver()


class TestDefaults:
    def test_unconfigured_returns_pre_t3_9_values(self):
        """Code defaults must match what build_telephony_session_config
        was passing to VoiceSessionConfig before T3.9 — otherwise this
        change silently retunes every existing tenant on deploy."""
        resolver = VoiceTuningResolver()
        tuning = resolver.for_tenant(None)
        assert tuning.stt_eot_threshold == 0.85
        assert tuning.stt_eager_eot_threshold == 0.7
        assert tuning.stt_eot_timeout_ms == 500
        assert tuning.turn_0_min_confidence == 0.4
        assert tuning.turn_0_min_alpha_chars == 2

    def test_unknown_tenant_falls_back_to_default(self):
        resolver = VoiceTuningResolver()
        assert resolver.for_tenant("never-configured-tenant") == resolver.for_tenant(None)


class TestGlobalDefaultOverride:
    def test_partial_global_override_merges_onto_code_default(self):
        """Operator only specifies one field — every other field stays
        at the code default. This is the partial-merge contract that
        makes incremental tuning practical."""
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON": '{"stt_eot_timeout_ms": 1500}',
        }):
            tuning = VoiceTuningResolver().for_tenant(None)
            assert tuning.stt_eot_timeout_ms == 1500
            # Every other field must remain at code default.
            assert tuning.stt_eot_threshold == 0.85
            assert tuning.stt_eager_eot_threshold == 0.7
            assert tuning.turn_0_min_confidence == 0.4

    def test_full_global_override(self):
        full_override = (
            '{"stt_eot_threshold": 0.9, "stt_eager_eot_threshold": 0.6, '
            '"stt_eot_timeout_ms": 800, "turn_0_min_confidence": 0.55, '
            '"turn_0_min_alpha_chars": 3}'
        )
        with patch.dict(os.environ, {"TELEPHONY_TUNING_DEFAULT_JSON": full_override}):
            tuning = VoiceTuningResolver().for_tenant(None)
            assert tuning == VoiceTuning(
                stt_eot_threshold=0.9,
                stt_eager_eot_threshold=0.6,
                stt_eot_timeout_ms=800,
                turn_0_min_confidence=0.55,
                turn_0_min_alpha_chars=3,
            )

    def test_eager_threshold_can_be_disabled_via_null(self):
        """``None`` is a meaningful value for eager_eot_threshold (it
        disables eager mode entirely). Operators must be able to opt
        into the disable via JSON null."""
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON": '{"stt_eager_eot_threshold": null}',
        }):
            tuning = VoiceTuningResolver().for_tenant(None)
            assert tuning.stt_eager_eot_threshold is None


class TestPerTenantOverride:
    def test_tenant_override_shadows_global_for_that_tenant_only(self):
        """The same resolver, queried for two different tenants, returns
        two different tunings — the override applies surgically."""
        overrides = (
            '{"tenant-A": {"stt_eot_timeout_ms": 1500}}'
        )
        with patch.dict(os.environ, {"TELEPHONY_TUNING_OVERRIDES_JSON": overrides}):
            resolver = VoiceTuningResolver()
            assert resolver.for_tenant("tenant-A").stt_eot_timeout_ms == 1500
            assert resolver.for_tenant("tenant-B").stt_eot_timeout_ms == 500
            # And tenant-A's other fields stay at default.
            assert resolver.for_tenant("tenant-A").turn_0_min_confidence == 0.4

    def test_tenant_override_layers_onto_global_default(self):
        """Global default sets one field, per-tenant override sets
        another — both must apply when resolving the tenant."""
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON": '{"stt_eot_timeout_ms": 750}',
            "TELEPHONY_TUNING_OVERRIDES_JSON": '{"tenant-X": {"turn_0_min_confidence": 0.6}}',
        }):
            tuning = VoiceTuningResolver().for_tenant("tenant-X")
            assert tuning.stt_eot_timeout_ms == 750     # from global default
            assert tuning.turn_0_min_confidence == 0.6  # from tenant override
            # Untouched fields stay at code default.
            assert tuning.stt_eager_eot_threshold == 0.7

    def test_per_tenant_override_for_specific_field_wins_over_global(self):
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON": '{"stt_eot_timeout_ms": 750}',
            "TELEPHONY_TUNING_OVERRIDES_JSON": '{"tenant-X": {"stt_eot_timeout_ms": 1500}}',
        }):
            assert VoiceTuningResolver().for_tenant("tenant-X").stt_eot_timeout_ms == 1500


class TestResilience:
    def test_invalid_json_default_falls_back_to_code_default(self):
        with patch.dict(os.environ, {"TELEPHONY_TUNING_DEFAULT_JSON": "{not json}"}):
            tuning = VoiceTuningResolver().for_tenant(None)
            assert tuning == VoiceTuning()

    def test_non_object_root_falls_back(self):
        with patch.dict(os.environ, {"TELEPHONY_TUNING_DEFAULT_JSON": "[1, 2, 3]"}):
            tuning = VoiceTuningResolver().for_tenant(None)
            assert tuning == VoiceTuning()

    def test_unknown_field_in_default_is_ignored(self):
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON":
                '{"stt_eot_timeout_ms": 1500, "fictional_field": 999}',
        }):
            tuning = VoiceTuningResolver().for_tenant(None)
            # Real field still applied.
            assert tuning.stt_eot_timeout_ms == 1500
            # Fictional field rejected at coercion (not present on dataclass).
            assert not hasattr(tuning, "fictional_field")

    def test_wrong_type_for_field_is_skipped(self):
        """A string where an int is expected should be skipped — that
        field stays at default, others on the same partial still apply."""
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_DEFAULT_JSON":
                '{"stt_eot_timeout_ms": "not-a-number", "turn_0_min_confidence": 0.6}',
        }):
            tuning = VoiceTuningResolver().for_tenant(None)
            assert tuning.stt_eot_timeout_ms == 500  # back to default
            assert tuning.turn_0_min_confidence == 0.6

    def test_overrides_with_non_dict_value_are_skipped(self):
        with patch.dict(os.environ, {
            "TELEPHONY_TUNING_OVERRIDES_JSON":
                '{"tenant-good": {"stt_eot_timeout_ms": 1500}, "tenant-bad": "not a dict"}',
        }):
            resolver = VoiceTuningResolver()
            assert resolver.for_tenant("tenant-good").stt_eot_timeout_ms == 1500
            assert resolver.for_tenant("tenant-bad").stt_eot_timeout_ms == 500

    def test_invalid_overrides_json_falls_back_to_no_overrides(self):
        with patch.dict(os.environ, {"TELEPHONY_TUNING_OVERRIDES_JSON": "garbage"}):
            resolver = VoiceTuningResolver()
            assert resolver.for_tenant("any-tenant") == VoiceTuning()


class TestSingleton:
    def test_get_voice_tuning_resolver_returns_same_instance(self):
        a = get_voice_tuning_resolver()
        b = get_voice_tuning_resolver()
        assert a is b

    def test_reset_voice_tuning_resolver_drops_singleton(self):
        a = get_voice_tuning_resolver()
        reset_voice_tuning_resolver()
        b = get_voice_tuning_resolver()
        assert a is not b
