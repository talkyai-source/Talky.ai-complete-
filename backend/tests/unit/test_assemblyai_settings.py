import pytest
from pydantic import ValidationError

from app.domain.models.assemblyai_config import AssemblyAISettings


@pytest.mark.parametrize(
    "settings",
    [
        {"speech_model": "u3-rt-pro"},
        {"language_codes": ["es"]},
        {"mode": "fast"},
        {"region": "custom-url"},
        {"min_turn_silence": 49},
        {"min_turn_silence": 10001},
        {"max_turn_silence": 0},
        {"min_turn_silence": 800, "max_turn_silence": 500},
        {"interruption_delay": -1},
        {"interruption_delay": 1001},
        {"vad_threshold": 1.01},
        {"prompt": "x" * 1751},
        {"keyterms_prompt": ["x"] * 101},
        {"keyterms_prompt": ["x" * 51]},
        {"keyterms_prompt": ["   "]},
        {"previous_context_n_turns": 101},
        {"max_speakers": 11},
        {"speaker_labels_revision_interval_ms": 119999},
        {"voice_focus": "custom"},
        {"voice_focus_threshold": -0.1},
        {"domain": "legal"},
        {"redact_pii_policies": ["not_a_policy"]},
        {"redact_pii_sub": "replace"},
        {"inactivity_timeout": 4},
        {"inactivity_timeout": 3601},
    ],
)
def test_invalid_provider_settings_rejected_at_save_boundary(settings):
    with pytest.raises(ValidationError):
        AssemblyAISettings.model_validate(settings)


def test_defaults_keep_mode_defaults_and_paid_addons_off():
    params = AssemblyAISettings().connection_parameters()
    assert params["mode"] == "balanced"
    assert not params["speaker_labels"] and not params["redact_pii"]
    assert not params["filter_profanity"]
    # The one non-provider default: first words early enough to stop the
    # agent when the caller interrupts (see VOICE_AGENT_INTERRUPTION_DELAY_MS).
    assert params["interruption_delay"] == 120
    for field in (
        "min_turn_silence",
        "max_turn_silence",
        "vad_threshold",
        "include_partial_turns",
        "prompt",
        "keyterms_prompt",
        "voice_focus",
        "domain",
        "max_speakers",
        "redact_pii_sub",
        "redact_pii_policies",
    ):
        assert field not in params


def test_disabled_dependent_settings_persist_but_do_not_go_upstream():
    settings = AssemblyAISettings(
        voice_focus_threshold=0.8,
        max_speakers=3,
        speaker_labels_revision_interval_ms=300000,
        redact_pii_policies=["email_address"],
        redact_pii_sub="entity_name",
    )
    saved = settings.model_dump()
    params = settings.connection_parameters()
    for field in (
        "voice_focus_threshold",
        "max_speakers",
        "speaker_labels_revision_interval_ms",
        "redact_pii_policies",
        "redact_pii_sub",
    ):
        assert saved[field] is not None and field not in params


def test_pii_default_does_not_enable_unredacted_partials():
    params = AssemblyAISettings(redact_pii=True).connection_parameters()
    assert params["redact_pii"] is True
    assert "include_partial_turns" not in params
    assert "redact_pii_policies" not in params  # all provider policies by default
    explicit = AssemblyAISettings(redact_pii=True, include_partial_turns=True)
    assert explicit.connection_parameters()["include_partial_turns"] is True


def test_advanced_boundary_values_and_no_invented_max_silence_cap():
    settings = AssemblyAISettings(
        min_turn_silence=10000,
        max_turn_silence=20000,
        previous_context_n_turns=0,
        interruption_delay=0,
        speaker_labels_revision_interval_ms=0,
        vad_threshold=0,
        keyterms_prompt=["  Acme  "],
        redact_pii_policies=["email_address", "email_address"],
    )
    assert settings.keyterms_prompt == ["Acme"]
    assert settings.redact_pii_policies == ["email_address"]
    assert settings.connection_parameters()["previous_context_n_turns"] == 0



@pytest.mark.parametrize(("mode", "expected"), [("min_latency", 0), ("balanced", 120), ("max_accuracy", 250)])
def test_a_blank_interruption_delay_gets_the_voice_agent_value_for_its_mode(mode, expected):
    # AssemblyAI's balanced preset waits 500 + 256 ms for the first words, so
    # test call 850b885e (2026-10-13) took 1.2-3.1 s to stop on half its
    # interruptions. Fillers are filtered downstream, so early words are safe.
    assert AssemblyAISettings(mode=mode).connection_parameters()["interruption_delay"] == expected


def test_an_explicit_interruption_delay_is_sent_unchanged():
    assert AssemblyAISettings(interruption_delay=750).connection_parameters()["interruption_delay"] == 750
    assert AssemblyAISettings(interruption_delay=0, mode="max_accuracy").connection_parameters()["interruption_delay"] == 0
