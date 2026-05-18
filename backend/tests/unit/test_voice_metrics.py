"""Tests for voice-pipeline Prometheus metrics (T4-B2).

These cover three things:

1. The label-coercion contract — bounded label values that protect
   the Prometheus registry from cardinality explosions.
2. The observation API — non-negative values, missing values silently
   ignored (production code must never crash on bad numbers).
3. Read-back via the registry — confirms the metric actually fires
   and emits a sample with the right labels.

The metrics module deliberately does not expose its internal
collectors; tests reach into ``prometheus_client.REGISTRY`` directly
the same way ``prom2json`` and Grafana scrape would.
"""
from __future__ import annotations

from prometheus_client import REGISTRY

from app.infrastructure.metrics.voice_metrics import (
    _mode_label,
    _persona_label,
    _prompt_kind_label,
    observe_first_turn_latency_seconds,
    observe_turn_latency_seconds,
    record_inbound_directive_applied,
    record_prompt_cache_hit_ratio,
    record_turn_0_rejection,
)


def _read_counter(name: str, **labels: str) -> float:
    """Read the current value of a labelled counter from the registry.
    Returns 0.0 when the time-series has not been observed yet."""
    for collector in REGISTRY.collect():
        if collector.name != name:
            continue
        for sample in collector.samples:
            if sample.name == f"{name}_total" and sample.labels == labels:
                return sample.value
    return 0.0


def _histogram_count(name: str, **labels: str) -> float:
    for collector in REGISTRY.collect():
        if collector.name != name:
            continue
        for sample in collector.samples:
            if sample.name == f"{name}_count" and sample.labels == labels:
                return sample.value
    return 0.0


def _gauge_value(name: str, **labels: str) -> float | None:
    for collector in REGISTRY.collect():
        if collector.name != name:
            continue
        for sample in collector.samples:
            if sample.name == name and sample.labels == labels:
                return sample.value
    return None


# ---------------------------------------------------------------------
# Label coercion — the cardinality firewall
# ---------------------------------------------------------------------


class TestLabelCoercion:
    def test_persona_label_known_values_pass_through(self):
        for p in ("lead_gen", "customer_support", "receptionist"):
            assert _persona_label(p) == p

    def test_persona_label_unknown_becomes_none_string(self):
        assert _persona_label(None) == "none"
        assert _persona_label("") == "none"
        assert _persona_label("unknown_future_persona") == "none"

    def test_mode_label_only_two_buckets(self):
        assert _mode_label("user") == "user"
        assert _mode_label("USER") == "user"
        assert _mode_label("agent") == "agent"
        assert _mode_label(None) == "agent"
        assert _mode_label("garbage") == "agent"

    def test_prompt_kind_label_only_two_buckets(self):
        assert _prompt_kind_label("inbound") == "inbound"
        assert _prompt_kind_label("INBOUND") == "inbound"
        assert _prompt_kind_label("outbound") == "outbound"
        assert _prompt_kind_label(None) == "outbound"
        assert _prompt_kind_label("anything_else") == "outbound"


# ---------------------------------------------------------------------
# Observation API — defensive against bad numbers
# ---------------------------------------------------------------------


class TestObservationDefenses:
    def test_negative_first_turn_seconds_is_ignored(self):
        labels = {"mode": "agent", "prompt_kind": "outbound", "persona": "none"}
        before = _histogram_count("voice_first_turn_latency_seconds", **labels)
        observe_first_turn_latency_seconds(
            -0.1, mode="agent", prompt_kind="outbound", persona=None,
        )
        after = _histogram_count("voice_first_turn_latency_seconds", **labels)
        assert after == before

    def test_none_first_turn_seconds_is_ignored(self):
        labels = {"mode": "agent", "prompt_kind": "outbound", "persona": "none"}
        before = _histogram_count("voice_first_turn_latency_seconds", **labels)
        observe_first_turn_latency_seconds(
            None,  # type: ignore[arg-type]
            mode="agent", prompt_kind="outbound", persona=None,
        )
        after = _histogram_count("voice_first_turn_latency_seconds", **labels)
        assert after == before

    def test_negative_turn_seconds_is_ignored(self):
        labels = {"mode": "agent", "prompt_kind": "outbound", "persona": "none"}
        before = _histogram_count("voice_turn_latency_seconds", **labels)
        observe_turn_latency_seconds(
            -1.0, mode="agent", prompt_kind="outbound", persona=None,
        )
        assert _histogram_count("voice_turn_latency_seconds", **labels) == before

    def test_cache_hit_ratio_clamps_invalid_input(self):
        before = _gauge_value(
            "voice_prompt_cache_hit_ratio", mode="agent", persona="none",
        )
        # Out-of-range values must be silently ignored, not clamped.
        record_prompt_cache_hit_ratio(1.5, mode="agent", persona=None)
        record_prompt_cache_hit_ratio(-0.1, mode="agent", persona=None)
        record_prompt_cache_hit_ratio(None, mode="agent", persona=None)  # type: ignore[arg-type]
        after = _gauge_value(
            "voice_prompt_cache_hit_ratio", mode="agent", persona="none",
        )
        assert after == before


# ---------------------------------------------------------------------
# End-to-end registry read-back — proves the metric actually fires
# ---------------------------------------------------------------------


class TestRegistryReadBack:
    def test_first_turn_observation_is_recorded(self):
        labels = {"mode": "user", "prompt_kind": "inbound", "persona": "receptionist"}
        before = _histogram_count("voice_first_turn_latency_seconds", **labels)

        observe_first_turn_latency_seconds(
            0.42,
            mode="user",
            prompt_kind="inbound",
            persona="receptionist",
        )
        after = _histogram_count("voice_first_turn_latency_seconds", **labels)
        assert after == before + 1

    def test_turn_0_rejection_counter_increments(self):
        before = _read_counter("voice_turn_0_rejection", reason="too_short")
        record_turn_0_rejection("too_short")
        after = _read_counter("voice_turn_0_rejection", reason="too_short")
        assert after == before + 1

    def test_turn_0_rejection_unknown_reason_falls_to_other(self):
        before = _read_counter("voice_turn_0_rejection", reason="other")
        record_turn_0_rejection("a_brand_new_reason")
        after = _read_counter("voice_turn_0_rejection", reason="other")
        assert after == before + 1

    def test_inbound_directive_compose_vs_runtime(self):
        before_c = _read_counter(
            "voice_inbound_directive_applied", source="compose",
        )
        before_r = _read_counter(
            "voice_inbound_directive_applied", source="runtime",
        )
        record_inbound_directive_applied("compose")
        record_inbound_directive_applied("runtime")
        record_inbound_directive_applied("garbage")  # → unknown
        assert _read_counter(
            "voice_inbound_directive_applied", source="compose",
        ) == before_c + 1
        assert _read_counter(
            "voice_inbound_directive_applied", source="runtime",
        ) == before_r + 1
        # Garbage source coerced to "unknown" — bounded label preserved.
        assert _read_counter(
            "voice_inbound_directive_applied", source="unknown",
        ) >= 1

    def test_cache_hit_ratio_gauge_set(self):
        record_prompt_cache_hit_ratio(0.75, mode="agent", persona="lead_gen")
        v = _gauge_value(
            "voice_prompt_cache_hit_ratio", mode="agent", persona="lead_gen",
        )
        assert v == 0.75
