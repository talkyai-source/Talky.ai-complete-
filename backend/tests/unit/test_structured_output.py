"""Capability-gated strict structured outputs.

Groq's ``strict: true`` json_schema mode constrains the model at the TOKEN
level, so the reply cannot miss a key, use a wrong type, or be invalid JSON.
``{"type": "json_object"}`` only guarantees syntactically valid JSON and says
nothing about the shape.

Two properties are load-bearing here:

1. A strict request to an UNSUPPORTED model is a hard API error, not a graceful
   degradation. So the capability must be checked before use, and the fallback
   must be the previous behaviour.

2. Strict mode forces the ENTIRE response to match the schema, which is why it
   is applied to discrete extraction calls and must never be applied to a
   conversational voice turn — the agent has to be free to produce speech.
"""
from __future__ import annotations

import pytest

from app.domain.services.call_summary.summarizer import (
    EMPTY_SUMMARY,
    _SUMMARY_SCHEMA_PROPERTIES,
)
from app.infrastructure.llm.structured_output import (
    STRICT_SCHEMA_MODELS,
    response_format_for,
    strict_mode_active,
    strict_object_schema,
    summariser_model,
    supports_strict_schema,
)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/gpt-oss-120b", True),
        ("openai/gpt-oss-20b", True),
        ("gpt-oss-120b", True),
        ("llama-3.3-70b-versatile", False),
        ("llama-3.1-8b-instant", False),
        ("qwen/qwen3.6-27b", False),
        ("gemini-2.5-flash", False),
        (None, False),
        ("", False),
    ],
)
def test_capability_detection(model, expected):
    """Getting this wrong in the permissive direction is an API error on every
    summarisation, not a soft degradation."""
    assert supports_strict_schema(model) is expected


def test_unsupported_model_falls_back_to_the_previous_behaviour():
    rf = response_format_for(
        "llama-3.3-70b-versatile",
        properties=_SUMMARY_SCHEMA_PROPERTIES,
        name="call_summary",
    )
    assert rf == {"type": "json_object"}


def test_supported_model_gets_constrained_decoding():
    rf = response_format_for(
        "openai/gpt-oss-120b",
        properties=_SUMMARY_SCHEMA_PROPERTIES,
        name="call_summary",
    )
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True


def test_schema_meets_groqs_strict_requirements():
    """Strict mode REQUIRES every property in `required` and
    additionalProperties=false. Those are not formalities — they are what lets
    the decoder mask invalid tokens. A schema missing either is rejected."""
    schema = strict_object_schema(
        _SUMMARY_SCHEMA_PROPERTIES, name="call_summary"
    )["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())
    assert set(schema["properties"].keys()) == set(EMPTY_SUMMARY.keys())


def test_schema_types_match_the_python_defaults():
    """Derived from EMPTY_SUMMARY so the schema cannot drift from the shape the
    rest of the pipeline coerces to."""
    for key, default in EMPTY_SUMMARY.items():
        prop = _SUMMARY_SCHEMA_PROPERTIES[key]
        if isinstance(default, list):
            assert prop["type"] == "array"
            assert prop["items"]["type"] == "string"
        else:
            assert prop["type"] == "string"


def test_strict_mode_active_matches_capability():
    assert strict_mode_active("openai/gpt-oss-120b") is True
    assert strict_mode_active("llama-3.3-70b-versatile") is False


def test_default_summariser_model_is_unchanged():
    """Changing the model that writes customer-visible summaries is an operator
    decision, not a side effect of adding this capability."""
    assert summariser_model() == "llama-3.3-70b-versatile"


def test_summariser_model_is_overridable(monkeypatch):
    monkeypatch.setenv("CALL_SUMMARY_MODEL", "openai/gpt-oss-120b")
    assert summariser_model() == "openai/gpt-oss-120b"
    assert strict_mode_active(summariser_model()) is True


def test_strict_mode_is_never_applied_to_the_conversational_turn():
    """THE load-bearing constraint.

    Strict mode forces the whole response to match the schema. On a voice turn
    the model must be free to speak, so applying it there would make the agent
    emit JSON instead of talking to the caller. The per-turn path must not
    reference this module at all.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1].parent
    for rel in (
        "app/domain/services/voice_pipeline/turn_streamer.py",
        "app/domain/services/voice_pipeline/turn_runner.py",
    ):
        src = (root / rel).read_text(encoding="utf-8")
        assert "structured_output" not in src, (
            f"{rel} references the strict structured-output helper. Forcing a "
            "conversational turn to match a schema makes the agent emit JSON "
            "instead of speech."
        )


def test_every_declared_strict_model_is_a_gpt_oss_variant():
    """Guards the list against someone adding a model Groq does not actually
    support in strict mode, which would fail every call on that model."""
    assert STRICT_SCHEMA_MODELS
    assert all("gpt-oss" in m for m in STRICT_SCHEMA_MODELS)
