"""Bounded-cardinality metrics for inbound routing and accounting.

Transfer metrics deliberately describe the small, code-owned Asterisk state
machine rather than customer or carrier dimensions.  In particular, tenant,
call, trunk, ANI/DID, destination, and provider error text must never become
Prometheus labels.
"""

from __future__ import annotations

from typing import Any, Optional

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

_REASONS = {
    "routed",
    "invalid_did",
    "unknown_did",
    "ambiguous_did",
    "tenant_conflict",
    "incomplete_binding",
    "routing_dependency_unavailable",
    "global_inbound_disabled",
    "tenant_inbound_disabled",
    "tenant_inactive",
    "campaign_inactive",
    "base_campaign_inactive",
    "campaign_not_inbound",
    "assignment_inactive",
    "subscription_inactive",
    "did_not_verified",
    "trunk_not_ready",
    "billing_not_configured",
    "concurrency_policy_missing",
    "max_active_calls_reached",
    "insufficient_minutes",
    "reservation_failed",
    "settlement_disabled",
    "invalid_provider",
    "invalid_provider_call_id",
    "invalid_reservation",
    "admission_dependency_unavailable",
    "invalid_schedule",
    "invalid_timezone",
    "after_hours_closed",
    "after_hours_action_unsupported",
    "duplicate_replay",
    "allowed",
    "finalized",
    "released",
    "reversed",
    "other",
}


def _existing(name: str) -> Optional[Any]:
    return getattr(REGISTRY, "_names_to_collectors", {}).get(name)


def _counter(name: str, documentation: str, labels: tuple[str, ...]) -> Counter:
    existing = _existing(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Counter(name, documentation, labelnames=labels)


def _gauge(name: str, documentation: str, labels: tuple[str, ...]) -> Gauge:
    existing = _existing(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Gauge(name, documentation, labelnames=labels)


def _histogram(
    name: str,
    documentation: str,
    labels: tuple[str, ...],
    *,
    buckets: tuple[float, ...],
) -> Histogram:
    existing = _existing(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Histogram(
        name,
        documentation,
        labelnames=labels,
        buckets=buckets,
    )


_decisions = _counter(
    "inbound_decisions_total",
    "Strict inbound route and admission decisions with bounded labels.",
    ("direction", "status", "reason"),
)
_usage_events = _counter(
    "inbound_usage_events_total",
    "Exactly-once inbound usage ledger events.",
    ("event", "result"),
)
_asterisk_transfer_attempts = _counter(
    "asterisk_inbound_transfer_attempts_total",
    "Asterisk supervised inbound transfers accepted into provider setup.",
    (),
)
_asterisk_transfer_outcomes = _counter(
    "asterisk_inbound_transfer_outcomes_total",
    "Exactly-once terminal outcomes of accepted Asterisk inbound transfers.",
    ("outcome", "reason"),
)
_asterisk_transfer_duration = _histogram(
    "asterisk_inbound_transfer_duration_seconds",
    "Provider setup to confirmed handoff or confirmed failed-target cleanup.",
    ("outcome", "reason"),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0),
)
_asterisk_transfer_cleanup = _counter(
    "asterisk_inbound_transfer_cleanup_total",
    "Bounded Asterisk transfer cleanup proof results.",
    ("scope", "result"),
)
_asterisk_transfer_inflight = _gauge(
    "asterisk_inbound_transfer_inflight",
    "Accepted Asterisk transfer attempts without a terminal outcome.",
    (),
)
_answer_to_first_audio = _histogram(
    "inbound_answer_to_first_audio_seconds",
    "Time from confirmed provider Answer to the first agent audio accepted by the gateway.",
    (),
    buckets=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0),
)


_TRANSFER_OUTCOMES = frozenset({"connected", "failed"})
_TRANSFER_REASONS = frozenset(
    {
        "answered",
        "busy",
        "congestion",
        "no_answer",
        "target_unavailable",
        "caller_hung_up",
        "transfer_cancelled",
        "provider_error",
        "provider_leg_id_mismatch",
        "transfer_handoff_failed",
        "other",
    }
)
_TRANSFER_REASON_ALIASES = {
    # ARI Dial status values.
    "noanswer": "no_answer",
    "cancel": "transfer_cancelled",
    "chanunavail": "target_unavailable",
    "dontcall": "target_unavailable",
    "torture": "target_unavailable",
    # Q.850 cause text emitted by Asterisk.
    "user_busy": "busy",
    "no_user_response": "no_answer",
    "switch_congestion": "congestion",
    "unallocated_number": "target_unavailable",
    # A target can disappear without a Dial result. Keep event names out of
    # labels while retaining their useful terminal category.
    "stasisend": "target_unavailable",
    "channeldestroyed": "target_unavailable",
    "channelhanguprequest": "target_unavailable",
}
_TRANSFER_CLEANUP_SCOPES = frozenset({"target", "linked"})
_TRANSFER_CLEANUP_RESULTS = frozenset(
    {"confirmed", "unconfirmed", "domain_finalize_failed", "error"}
)
_USAGE_EVENTS = frozenset({"reserve", "finalize", "release", "reverse", "recovery"})
_USAGE_RESULTS = frozenset(
    {
        "inserted",
        "replayed",
        "failed",
        "provider_answer_ambiguous",
        "settlement_switch_disabled",
        "usage_exceeded_reservation",
        "manual_hold_approval_requested",
        "manual_hold_resolution",
        "duplicate_replay",
        "queued",
    }
)


def _initialize_alert_series() -> None:
    """Expose zero baselines so the first bounded event is observable.

    A labelled Prometheus counter does not exist until ``labels`` is called.
    Without a pre-event zero sample, the first hold/recovery event can appear
    as the series' initial value and an ``increase`` rule has no earlier
    baseline from which to detect it. Only the fixed alert dimensions are
    initialized here; this adds no tenant/call/provider cardinality.
    """

    for reason in (
        "routing_dependency_unavailable",
        "admission_dependency_unavailable",
    ):
        _decisions.labels(direction="inbound", status="rejected", reason=reason).inc(0)
    for result in (
        "provider_answer_ambiguous",
        "settlement_switch_disabled",
        "usage_exceeded_reservation",
    ):
        _usage_events.labels(event="finalize", result=result).inc(0)
    _usage_events.labels(event="recovery", result="queued").inc(0)
    for scope in _TRANSFER_CLEANUP_SCOPES:
        for result in ("unconfirmed", "domain_finalize_failed", "error"):
            _asterisk_transfer_cleanup.labels(scope=scope, result=result).inc(0)


_initialize_alert_series()


def bounded_reason(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    return normalized if normalized in _REASONS else "other"


def record_route_decision(reason: str) -> None:
    safe_reason = bounded_reason(reason)
    _decisions.labels(
        direction="inbound",
        status="accepted" if safe_reason == "routed" else "rejected",
        reason=safe_reason,
    ).inc()


def record_admission_decision(provider: str, reason: str) -> None:
    # ``provider`` remains in the call signature for compatibility with the
    # admission service, but is intentionally not a metric label. Carrier,
    # tenant, ANI, DID and call identifiers are all high-cardinality or
    # identifying and do not belong in Prometheus dimensions.
    del provider
    safe_reason = bounded_reason(reason)
    _decisions.labels(
        direction="inbound",
        status="accepted" if safe_reason in {"allowed", "duplicate_replay"} else "rejected",
        reason=safe_reason,
    ).inc()


def record_usage_event(event: str, result: str) -> None:
    # These values are emitted only by code-owned accounting/recovery paths.
    # Keep the finite vocabulary explicit so alert rules can distinguish a
    # newly created billing hold or stale-reservation recovery without adding
    # tenant, call, provider, or error text as high-cardinality labels.
    safe_event = event if event in _USAGE_EVENTS else "other"
    safe_result = result if result in _USAGE_RESULTS else "other"
    _usage_events.labels(event=safe_event, result=safe_result).inc()


def record_asterisk_transfer_attempt() -> None:
    """Count one transfer only after the adapter owns its parent/target pair."""

    _asterisk_transfer_attempts.inc()


def record_asterisk_transfer_terminal(
    outcome: str,
    reason: str,
    duration_seconds: float,
) -> None:
    """Record one terminal transfer result using only bounded dimensions."""

    normalized_outcome = str(outcome or "").strip().lower()
    normalized_reason = str(reason or "").strip().lower().replace(" ", "_")
    normalized_reason = _TRANSFER_REASON_ALIASES.get(normalized_reason, normalized_reason)
    safe_outcome = normalized_outcome if normalized_outcome in _TRANSFER_OUTCOMES else "failed"
    safe_reason = normalized_reason if normalized_reason in _TRANSFER_REASONS else "other"
    labels = {"outcome": safe_outcome, "reason": safe_reason}
    _asterisk_transfer_outcomes.labels(**labels).inc()
    _asterisk_transfer_duration.labels(**labels).observe(max(0.0, float(duration_seconds)))


def record_asterisk_transfer_cleanup(scope: str, result: str) -> None:
    """Record the result of one real PBX/domain cleanup proof attempt."""

    safe_scope = scope if scope in _TRANSFER_CLEANUP_SCOPES else "linked"
    safe_result = result if result in _TRANSFER_CLEANUP_RESULTS else "error"
    _asterisk_transfer_cleanup.labels(scope=safe_scope, result=safe_result).inc()


def set_asterisk_transfer_inflight(value: int) -> None:
    """Publish accepted attempts that do not yet have a terminal outcome."""

    _asterisk_transfer_inflight.set(max(0, int(value)))


def record_inbound_answer_to_first_audio(duration_seconds: float) -> None:
    """Observe caller dead-air latency without adding call or tenant labels."""

    _answer_to_first_audio.observe(max(0.0, float(duration_seconds)))
