"""Bounded-label Prometheus metrics for the C++ media-gateway boundary."""

from __future__ import annotations

from prometheus_client import REGISTRY, Counter


def _counter(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Counter:
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Counter(name, documentation, labelnames=labels)


GATEWAY_AUDIO_CALLBACK_BATCHES = _counter(
    "talky_gateway_audio_callback_batches_total",
    "Authenticated gateway callback batches by bounded delivery outcome.",
    ("outcome",),
)
GATEWAY_AUDIO_MISSING_BATCHES = _counter(
    "talky_gateway_audio_missing_batches_total",
    "Callback sequence batches proven missing between two accepted deliveries.",
)
GATEWAY_MEDIA_RECONCILIATION = _counter(
    "talky_gateway_media_reconciliation_total",
    "Answered-call media-loss reconciliation outcomes.",
    ("outcome",),
)


def record_gateway_audio_callback(outcome: str) -> None:
    safe = outcome if outcome in {"routed", "buffered", "duplicate"} else "unknown"
    GATEWAY_AUDIO_CALLBACK_BATCHES.labels(outcome=safe).inc()


def record_gateway_audio_missing_batches(count: int) -> None:
    if count > 0:
        GATEWAY_AUDIO_MISSING_BATCHES.inc(count)


def record_gateway_media_reconciliation(outcome: str) -> None:
    safe = outcome if outcome in {"detected", "ended", "end_failed"} else "unknown"
    GATEWAY_MEDIA_RECONCILIATION.labels(outcome=safe).inc()
