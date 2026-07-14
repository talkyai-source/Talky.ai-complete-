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


def _histogram_buckets(
    name: str, doc: str, labelnames: tuple[str, ...], buckets: tuple[float, ...]
) -> Histogram:
    """Histogram with caller-chosen buckets (for metrics whose scale differs
    from the per-turn latency buckets — e.g. barge-in stop time in ms)."""
    if (existing := _existing(name)) is not None:
        return existing  # type: ignore[return-value]
    return Histogram(name, doc, labelnames=labelnames, buckets=buckets)


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

# Per-component splits of the turn budget. The total-latency alert can't tell a
# slow LLM from slow TTS — these let operators alert on the right component
# (e.g. histogram_quantile(0.95, voice_llm_ttft_seconds) > 0.6).
_llm_ttft = _histogram(
    "voice_llm_ttft_seconds",
    "LLM time-to-first-token (LLM start → first streamed token), per turn.",
    labelnames=(),
)

_tts_first_chunk = _histogram(
    "voice_tts_first_chunk_seconds",
    "TTS time-to-first-chunk (TTS start → first audio chunk), per turn.",
    labelnames=(),
)

# Barge-in responsiveness, in MILLISECONDS (not the seconds latency buckets —
# this is a tens-of-ms signal). 2026 best practice: TTS must go silent within
# ~60ms of the caller interrupting, or the agent feels like it ignored them.
_BARGE_IN_STOP_BUCKETS_MS: tuple[float, ...] = (
    10.0, 25.0, 50.0, 60.0, 100.0, 200.0, 400.0, 800.0,
)
_barge_in_stop_ms = _histogram_buckets(
    "voice_barge_in_stop_ms",
    "Time from barge-in detection to the caller's audio actually being "
    "silenced (output buffer cleared) while the agent was speaking. Target "
    "<60ms; the p95 climbing past that means the agent talks over callers "
    "after they interrupt.",
    labelnames=(),
    buckets=_BARGE_IN_STOP_BUCKETS_MS,
)

# Event-loop scheduling lag, in SECONDS. The primary "knee" metric for load
# testing and a standing production signal: a 10ms heartbeat measures how far
# past its deadline the loop actually wakes it. Under CPU contention / a
# blocking call this climbs long before per-turn latency degrades. The per-turn
# latency buckets start at 250ms and cannot resolve a 20ms p99, so loop lag gets
# its own fine-grained buckets (1ms → 320ms).
_EVENT_LOOP_LAG_BUCKETS_S: tuple[float, ...] = (
    0.001, 0.0025, 0.005, 0.010, 0.020, 0.040, 0.080, 0.160, 0.320,
)
_event_loop_lag = _histogram_buckets(
    "voice_event_loop_lag_seconds",
    "Event-loop scheduling lag: how long past its 10ms deadline the heartbeat "
    "task actually woke (seconds). The knee metric for loop saturation — climbs "
    "under CPU contention / blocking calls before per-turn latency degrades.",
    labelnames=(),
    buckets=_EVENT_LOOP_LAG_BUCKETS_S,
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

_turn_latency_p95 = _gauge(
    "voice_turn_latency_p95_ms",
    "Rolling cross-call P95 of per-turn speech-to-audio latency (ms), "
    "computed in-process over a sliding window. The paging signal for "
    "gradual latency regressions (provider slowdown, prompt bloat, "
    "contention) that per-turn logs alone make easy to miss.",
    labelnames=(),
)

_p95_latency_alert = _counter(
    "voice_p95_latency_alert_total",
    "Transitions of the rolling-P95 latency alert. state=firing when "
    "P95 crosses the alert threshold; state=cleared when it recovers.",
    labelnames=("state",),
)

_interruption = _counter(
    "voice_interruption_total",
    "Caller interruptions of ACTIVE agent speech, by classified type "
    "(backchannel/correction/question/escalation/dtmf/noise/statement) and "
    "whether the interruption was 'false' — the agent was stopped for a "
    "backchannel/noise that didn't need a stop. A rising false_interrupt=true "
    "share means the barge-in guard is too eager; escalation>0 means callers "
    "are asking for a human mid-call.",
    labelnames=("type", "false_interrupt"),
)

_llm_failover = _counter(
    "voice_llm_failover_total",
    "LLM first-token failover events on the voice path. outcome="
    "primary_missed (primary missed the first-token deadline → secondary "
    "took the turn), primary_circuit_open (primary skipped, breaker open → "
    "secondary), secondary_missed (secondary also missed → fallback line "
    "spoken). A non-zero rate means the primary LLM is stalling first tokens.",
    labelnames=("outcome",),
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


def observe_llm_ttft_seconds(seconds: float) -> None:
    """Record one turn's LLM time-to-first-token (seconds)."""
    if seconds is None or seconds < 0:
        return
    _llm_ttft.observe(seconds)


def observe_tts_first_chunk_seconds(seconds: float) -> None:
    """Record one turn's TTS time-to-first-chunk (seconds)."""
    if seconds is None or seconds < 0:
        return
    _tts_first_chunk.observe(seconds)


def observe_event_loop_lag_seconds(lag_s: float) -> None:
    """Record one event-loop scheduling-lag sample (seconds).

    Fail-soft by contract: this is driven by a hot heartbeat task, so a bad
    value or any metrics-layer error must never propagate back and disturb the
    loop it is measuring. All failures are swallowed."""
    try:
        if lag_s is None or lag_s < 0:
            return
        _event_loop_lag.observe(lag_s)
    except Exception:
        return


def observe_barge_in_stop_ms(ms: float) -> None:
    """Record how long after a barge-in the agent's audio was silenced (ms).
    Only meaningful while the agent was actively speaking."""
    if ms is None or ms < 0:
        return
    _barge_in_stop_ms.observe(ms)


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


def set_turn_latency_p95_ms(ms: float) -> None:
    """Publish the current rolling-window P95 turn latency (ms)."""
    if ms is None or ms < 0:
        return
    _turn_latency_p95.set(ms)


def record_p95_alert(state: str) -> None:
    """Count a rolling-P95 alert transition. ``state`` ∈ {firing, cleared}."""
    safe = state if state in ("firing", "cleared") else "unknown"
    _p95_latency_alert.labels(state=safe).inc()


_INTERRUPTION_TYPES = (
    "backchannel", "correction", "question", "escalation",
    "dtmf", "noise", "statement",
)


def record_interruption(itype: str, *, false_interrupt: bool) -> None:
    """Count one interruption of active agent speech. ``itype`` is an
    ``InterruptionType`` value; unknown values coerce to ``"other"`` to keep
    the label set bounded. ``false_interrupt`` flags backchannel/noise stops."""
    safe_type = itype if itype in _INTERRUPTION_TYPES else "other"
    _interruption.labels(
        type=safe_type,
        false_interrupt="true" if false_interrupt else "false",
    ).inc()


def record_llm_failover(outcome: str) -> None:
    """Count an LLM first-token failover event. ``outcome`` ∈
    {primary_missed, primary_circuit_open, secondary_missed}; anything else
    is coerced to ``"other"`` to keep the label set bounded."""
    safe = outcome if outcome in (
        "primary_missed", "primary_circuit_open", "secondary_missed",
    ) else "other"
    _llm_failover.labels(outcome=safe).inc()
