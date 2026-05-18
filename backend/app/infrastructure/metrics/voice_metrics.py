"""Voice-pipeline Prometheus metrics (T4-B2).

Five primitives operators care about when running this voice agent
in production:

* ``voice_first_turn_latency_seconds`` — speech-end → first AI audio
  on the very first user-driven turn. The cold-start signal. Bucketed
  at SLO-relevant boundaries: 0.25, 0.5, 0.8, 1.5, 2.5, 5 s.
* ``voice_turn_latency_seconds`` — same metric for every turn. The
  steady-state signal. Same buckets.
* ``voice_turn_0_rejection_total`` — count of turn-0 transcripts the
  T2.4 floor dropped (low confidence / too short). High counts mean
  the floor is too aggressive OR the STT is mis-hearing the caller.
* ``voice_inbound_directive_applied_total`` — count of inbound calls
  that received the direction directive, partitioned by whether the
  directive was injected at compose time (preferred) or runtime
  (defense-in-depth). Runtime injections climbing means the persona
  system is missing direction-awareness somewhere.
* ``voice_prompt_cache_hit_ratio`` — gauge tracking the most recent
  Groq prompt-cache hit ratio per (mode, persona). Combined with
  the structured log it tells operators if caching is firing.

Module-level idempotent registration: re-importing this module (test
hot-reload, dev server restart) does not double-register. Same
get-or-create pattern as the existing telephony_observability.
"""
from __future__ import annotations

from typing import Any, Optional

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

# SLO-relevant histogram buckets for voice latency.
# 250 ms / 500 ms / 800 ms / 1.5 s / 2.5 s / 5 s.
# 800ms is the 2026 STT-LLM-TTS pipeline target; 1.5s is "still
# acceptable"; >2.5s is "feels slow"; >5s is "broken."
_LATENCY_BUCKETS_S: tuple[float, ...] = (
    0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 3.5, 5.0,
)


# ---------------------------------------------------------------------
# Idempotent registration helpers
# ---------------------------------------------------------------------


def _existing(name: str) -> Optional[Any]:
    """Return the already-registered collector with this name, if any.
    Lets the module be re-imported (test hot-reload, gunicorn worker
    fork, dev server restart) without raising 'duplicate timeseries'."""
    return getattr(REGISTRY, "_names_to_collectors", {}).get(name)


def _histogram(name: str, doc: str, labelnames: tuple[str, ...]) -> Histogram:
    if (existing := _existing(name)) is not None:
        return existing  # type: ignore[return-value]
    return Histogram(name, doc, labelnames=labelnames, buckets=_LATENCY_BUCKETS_S)


def _counter(name: str, doc: str, labelnames: tuple[str, ...]) -> Counter:
    if (existing := _existing(name)) is not None:
        return existing  # type: ignore[return-value]
    return Counter(name, doc, labelnames=labelnames)


def _gauge(name: str, doc: str, labelnames: tuple[str, ...]) -> Gauge:
    if (existing := _existing(name)) is not None:
        return existing  # type: ignore[return-value]
    return Gauge(name, doc, labelnames=labelnames)


# ---------------------------------------------------------------------
# Metric instances
# ---------------------------------------------------------------------

_first_turn_latency = _histogram(
    "voice_first_turn_latency_seconds",
    "Speech-end to first AI audio on the FIRST user-driven turn. "
    "Cold-start signal — every per-call setup cost lands here.",
    labelnames=("mode", "prompt_kind", "persona"),
)

_turn_latency = _histogram(
    "voice_turn_latency_seconds",
    "Speech-end to first AI audio for every turn. Steady-state signal.",
    labelnames=("mode", "prompt_kind", "persona"),
)

_turn_0_rejections = _counter(
    "voice_turn_0_rejection_total",
    "Turn-0 transcripts dropped by the T2.4 floor (too short / "
    "low confidence). High counts indicate STT mis-hearing or an "
    "over-tight floor.",
    labelnames=("reason",),
)

_inbound_directive_applied = _counter(
    "voice_inbound_directive_applied_total",
    "Inbound direction directive injections, partitioned by source. "
    "compose=preferred (direction baked in at compose_prompt time); "
    "runtime=defense-in-depth fallback for non-composed prompts.",
    labelnames=("source",),
)

_prompt_cache_hit_ratio = _gauge(
    "voice_prompt_cache_hit_ratio",
    "Most-recent Groq prompt-cache hit ratio (cached / prompt) per "
    "(mode, persona). 0.0 means caching is not firing — typically "
    "because the system prompt is below Groq's 1k-token cache "
    "threshold or is changing across turns.",
    labelnames=("mode", "persona"),
)


# ---------------------------------------------------------------------
# Public observation API — call sites stay simple and typed.
# ---------------------------------------------------------------------


def _persona_label(persona: Optional[str]) -> str:
    """Coerce ``None`` / unknown persona to the literal label ``"none"``
    so Prometheus never rejects an empty label value. Bounded set:
    {lead_gen, customer_support, receptionist, none}."""
    return persona if persona in ("lead_gen", "customer_support", "receptionist") else "none"


def _mode_label(mode: Optional[str]) -> str:
    return "user" if str(mode or "").strip().lower() == "user" else "agent"


def _prompt_kind_label(prompt_kind: Optional[str]) -> str:
    return "inbound" if str(prompt_kind or "").strip().lower() == "inbound" else "outbound"


def observe_first_turn_latency_seconds(
    seconds: float,
    *,
    mode: Optional[str],
    prompt_kind: Optional[str],
    persona: Optional[str],
) -> None:
    """Record the first-turn latency for a call. Once per call lifetime
    — the corresponding LatencyTracker logging guard ensures this."""
    if seconds is None or seconds < 0:
        return
    _first_turn_latency.labels(
        mode=_mode_label(mode),
        prompt_kind=_prompt_kind_label(prompt_kind),
        persona=_persona_label(persona),
    ).observe(seconds)


def observe_turn_latency_seconds(
    seconds: float,
    *,
    mode: Optional[str],
    prompt_kind: Optional[str],
    persona: Optional[str],
) -> None:
    """Record a turn's full latency (every turn, including the first)."""
    if seconds is None or seconds < 0:
        return
    _turn_latency.labels(
        mode=_mode_label(mode),
        prompt_kind=_prompt_kind_label(prompt_kind),
        persona=_persona_label(persona),
    ).observe(seconds)


def record_turn_0_rejection(reason: str) -> None:
    """Increment the turn-0 rejection counter. ``reason`` is the small
    set returned by ``_should_reject_turn_0``: ``"too_short"`` or
    ``"low_confidence"``. Unknown reasons fall under ``"other"``."""
    safe_reason = reason if reason in ("too_short", "low_confidence") else "other"
    _turn_0_rejections.labels(reason=safe_reason).inc()


def record_inbound_directive_applied(source: str) -> None:
    """``source`` ∈ {``"compose"``, ``"runtime"``}. Anything else is
    coerced to ``"unknown"`` so unbounded label values cannot land
    here by accident."""
    safe_source = source if source in ("compose", "runtime") else "unknown"
    _inbound_directive_applied.labels(source=safe_source).inc()


def record_prompt_cache_hit_ratio(
    ratio: float,
    *,
    mode: Optional[str],
    persona: Optional[str],
) -> None:
    """Set the most-recent observed cache hit ratio. Operators read
    this with ``avg_over_time`` to spot caching regressions."""
    if ratio is None or ratio < 0 or ratio > 1:
        return
    _prompt_cache_hit_ratio.labels(
        mode=_mode_label(mode),
        persona=_persona_label(persona),
    ).set(ratio)
