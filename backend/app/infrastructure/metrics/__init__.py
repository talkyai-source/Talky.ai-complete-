"""Prometheus metrics infrastructure for the voice pipeline.

The voice-AI codebase had latency telemetry as structured logs since
T1.2; T4-B2 adds a typed metrics surface so operators can build
dashboards and SLO alerts without scraping log streams.

Cardinality discipline (read this first):
* All labels in this package are drawn from BOUNDED sets — mode,
  prompt_kind, persona, reason. Bounded means "max ~10 distinct
  values, ever."
* tenant_id is intentionally NOT a label. Per-tenant breakdowns are
  available via the structured log path; metrics stay aggregate.
* Adding a new label requires an explicit code review for cardinality.
"""
from app.infrastructure.metrics.voice_metrics import (
    observe_first_turn_latency_seconds,
    observe_turn_latency_seconds,
    record_turn_0_rejection,
    record_inbound_directive_applied,
    record_prompt_cache_hit_ratio,
)

__all__ = [
    "observe_first_turn_latency_seconds",
    "observe_turn_latency_seconds",
    "record_turn_0_rejection",
    "record_inbound_directive_applied",
    "record_prompt_cache_hit_ratio",
]
