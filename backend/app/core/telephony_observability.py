"""
WS-K telephony observability helpers.

Provides:
- Prometheus-compatible metric exposition helpers.
- Runtime SLO metric aggregation from PostgreSQL and FreeSWITCH transfer state.
- Optional header-token protection for /metrics scraping.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import asyncpg
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, REGISTRY, generate_latest

logger = logging.getLogger(__name__)

_MIN_WINDOW_MINUTES = 5
_MAX_WINDOW_MINUTES = 7 * 24 * 60
_DEFAULT_WINDOW_MINUTES = 60


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using default=%s", name, raw, default)
        return default


def get_metrics_window_minutes() -> int:
    configured = _int_env("TELEPHONY_METRICS_WINDOW_MINUTES", _DEFAULT_WINDOW_MINUTES)
    if configured < _MIN_WINDOW_MINUTES:
        return _MIN_WINDOW_MINUTES
    if configured > _MAX_WINDOW_MINUTES:
        return _MAX_WINDOW_MINUTES
    return configured


def is_metrics_request_authorized(x_metrics_token: Optional[str]) -> bool:
    """
    Validate optional metrics token.

    If TELEPHONY_METRICS_TOKEN is not configured, access is allowed.
    """
    expected = os.getenv("TELEPHONY_METRICS_TOKEN", "").strip()
    if not expected:
        return True
    if not x_metrics_token:
        return False
    return hmac.compare_digest(x_metrics_token.strip(), expected)


def _float_env(name: str, default: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default=%s", name, raw, default)
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bool_env_any(names: list[str], default: bool = False) -> bool:
    for name in names:
        if os.getenv(name) is not None:
            return _bool_env(name, default=default)
    return default


def _float_env_any(names: list[str], default: float = 0.0) -> float:
    for name in names:
        if os.getenv(name) is not None:
            return _float_env(name, default=default)
    return default


def _get_or_create_gauge(name: str, documentation: str) -> Gauge:
    # Prometheus default registry deduplicates names globally. During some
    # reload/test scenarios this module can be imported more than once.
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Gauge(name, documentation)


def _get_or_create_counter(name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Counter:
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Counter(name, documentation, labelnames=labelnames)


@dataclass(frozen=True)
class RuntimeMetrics:
    activation_attempts: int
    activation_successes: int
    rollback_attempts: int
    rollback_successes: int
    rollback_p50_seconds: float
    rollback_p95_seconds: float
    rollback_max_seconds: float


@dataclass(frozen=True)
class CallMetrics:
    setup_attempts: int
    setup_successes: int
    answer_latency_p50_seconds: float
    answer_latency_p95_seconds: float
    answer_latency_max_seconds: float


@dataclass(frozen=True)
class TransferMetrics:
    attempts: int
    successes: int
    inflight: int


@dataclass(frozen=True)
class CanaryMetrics:
    enabled: bool
    percent: float
    frozen: bool


METRICS_SCRAPE_SUCCESS = _get_or_create_gauge(
    "talky_telephony_metrics_scrape_success",
    "1 when the latest SLO scrape succeeded, 0 otherwise.",
)
METRICS_SCRAPE_TIMESTAMP_SECONDS = _get_or_create_gauge(
    "talky_telephony_metrics_scrape_timestamp_seconds",
    "Unix timestamp of the latest telephony SLO metric refresh.",
)
METRICS_SCRAPE_DURATION_SECONDS = _get_or_create_gauge(
    "talky_telephony_metrics_scrape_duration_seconds",
    "Duration of the latest telephony SLO metric refresh.",
)
METRICS_WINDOW_MINUTES = _get_or_create_gauge(
    "talky_telephony_metrics_window_minutes",
    "Configured SLO aggregation window used for metric collection.",
)

CALL_SETUP_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_calls_setup_attempts",
    "Call setup attempts in the configured metrics window.",
)
CALL_SETUP_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_calls_setup_successes",
    "Successful call setups in the configured metrics window.",
)
CALL_SETUP_SUCCESS_RATIO = _get_or_create_gauge(
    "talky_telephony_calls_setup_success_ratio",
    "Call setup success ratio in the configured metrics window (0..1).",
)
CALL_ANSWER_LATENCY_P50_SECONDS = _get_or_create_gauge(
    "talky_telephony_calls_answer_latency_p50_seconds",
    "P50 answer latency in seconds for answered calls in the configured window.",
)
CALL_ANSWER_LATENCY_P95_SECONDS = _get_or_create_gauge(
    "talky_telephony_calls_answer_latency_p95_seconds",
    "P95 answer latency in seconds for answered calls in the configured window.",
)
CALL_ANSWER_LATENCY_MAX_SECONDS = _get_or_create_gauge(
    "talky_telephony_calls_answer_latency_max_seconds",
    "Max answer latency in seconds for answered calls in the configured window.",
)

TRANSFER_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_transfers_attempts",
    "Terminal transfer attempts tracked by FreeSWITCH ESL state.",
)
TRANSFER_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_transfers_successes",
    "Successful transfers tracked by FreeSWITCH ESL state.",
)
TRANSFER_SUCCESS_RATIO = _get_or_create_gauge(
    "talky_telephony_transfers_success_ratio",
    "Transfer success ratio based on terminal transfer attempts (0..1).",
)
TRANSFER_INFLIGHT = _get_or_create_gauge(
    "talky_telephony_transfers_inflight",
    "In-flight transfer attempts (pending/accepted).",
)

RUNTIME_ACTIVATION_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_runtime_activation_attempts",
    "Runtime activation attempts in the configured metrics window.",
)
RUNTIME_ACTIVATION_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_runtime_activation_successes",
    "Successful runtime activations in the configured metrics window.",
)
RUNTIME_ACTIVATION_SUCCESS_RATIO = _get_or_create_gauge(
    "talky_telephony_runtime_activation_success_ratio",
    "Runtime activation success ratio in the configured metrics window (0..1).",
)
RUNTIME_ROLLBACK_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_attempts",
    "Runtime rollback attempts in the configured metrics window.",
)
RUNTIME_ROLLBACK_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_successes",
    "Successful runtime rollbacks in the configured metrics window.",
)
RUNTIME_ROLLBACK_LATENCY_P50_SECONDS = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_latency_p50_seconds",
    "P50 rollback latency in seconds.",
)
RUNTIME_ROLLBACK_LATENCY_P95_SECONDS = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_latency_p95_seconds",
    "P95 rollback latency in seconds.",
)
RUNTIME_ROLLBACK_LATENCY_MAX_SECONDS = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_latency_max_seconds",
    "Max rollback latency in seconds.",
)

CANARY_ENABLED = _get_or_create_gauge(
    "talky_telephony_canary_enabled",
    "Canary enabled flag (1 enabled, 0 disabled).",
)
CANARY_PERCENT = _get_or_create_gauge(
    "talky_telephony_canary_percent",
    "Canary traffic percentage.",
)
CANARY_FROZEN = _get_or_create_gauge(
    "talky_telephony_canary_frozen",
    "Canary freeze flag (1 frozen, 0 unfrozen).",
)
TURN_SILENT_REASON_TOTAL = _get_or_create_counter(
    "talky_telephony_turn_silent_reason_total",
    "Count of turns that finished without outbound audio, labelled by root cause.",
    labelnames=("reason",),
)

_TERMINAL_TRANSFER_STATUSES = {"success", "failed", "cancelled", "timed_out"}
_SUCCESS_TRANSFER_STATUSES = {"success"}
_INFLIGHT_TRANSFER_STATUSES = {"accepted", "pending"}


async def _fetch_runtime_metrics(db_pool: asyncpg.Pool, window_minutes: int) -> RuntimeMetrics:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH scoped_events AS (
                SELECT action, stage, status, request_id, created_at
                FROM tenant_runtime_policy_events
                WHERE created_at >= NOW() - ($1::int * INTERVAL '1 minute')
            ),
            counts AS (
                SELECT
                    COUNT(*) FILTER (
                        WHERE action = 'activate'
                          AND stage = 'commit'
                    ) AS activation_attempts,
                    COUNT(*) FILTER (
                        WHERE action = 'activate'
                          AND stage = 'commit'
                          AND status = 'succeeded'
                    ) AS activation_successes,
                    COUNT(*) FILTER (
                        WHERE action = 'rollback'
                          AND stage = 'rollback'
                          AND status IN ('succeeded', 'failed')
                    ) AS rollback_attempts,
                    COUNT(*) FILTER (
                        WHERE action = 'rollback'
                          AND stage = 'rollback'
                          AND status = 'succeeded'
                    ) AS rollback_successes
                FROM scoped_events
            ),
            rollback_latencies AS (
                SELECT
                    EXTRACT(EPOCH FROM (done.created_at - started.created_at))::float8 AS latency_seconds
                FROM scoped_events started
                JOIN scoped_events done
                  ON done.action = 'rollback'
                 AND done.stage = 'rollback'
                 AND done.status IN ('succeeded', 'failed')
                 AND started.action = 'rollback'
                 AND started.stage = 'rollback'
                 AND started.status = 'started'
                 AND started.request_id IS NOT NULL
                 AND started.request_id = done.request_id
                 AND done.created_at >= started.created_at
            ),
            rollback_stats AS (
                SELECT
                    COALESCE(percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_seconds), 0)::float8 AS p50_seconds,
                    COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_seconds), 0)::float8 AS p95_seconds,
                    COALESCE(MAX(latency_seconds), 0)::float8 AS max_seconds
                FROM rollback_latencies
            )
            SELECT
                counts.activation_attempts,
                counts.activation_successes,
                counts.rollback_attempts,
                counts.rollback_successes,
                rollback_stats.p50_seconds,
                rollback_stats.p95_seconds,
                rollback_stats.max_seconds
            FROM counts
            CROSS JOIN rollback_stats
            """,
            window_minutes,
        )

    return RuntimeMetrics(
        activation_attempts=int(row["activation_attempts"] or 0),
        activation_successes=int(row["activation_successes"] or 0),
        rollback_attempts=int(row["rollback_attempts"] or 0),
        rollback_successes=int(row["rollback_successes"] or 0),
        rollback_p50_seconds=float(row["p50_seconds"] or 0.0),
        rollback_p95_seconds=float(row["p95_seconds"] or 0.0),
        rollback_max_seconds=float(row["max_seconds"] or 0.0),
    )


async def _fetch_call_metrics(db_pool: asyncpg.Pool, window_minutes: int) -> CallMetrics:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH scoped_calls AS (
                SELECT created_at, answered_at, status
                FROM calls
                WHERE created_at >= NOW() - ($1::int * INTERVAL '1 minute')
            ),
            counts AS (
                SELECT
                    COUNT(*)::int AS setup_attempts,
                    COUNT(*) FILTER (
                        WHERE answered_at IS NOT NULL
                           OR status IN ('answered', 'completed', 'in_progress')
                    )::int AS setup_successes
                FROM scoped_calls
            ),
            answer_latencies AS (
                SELECT
                    EXTRACT(EPOCH FROM (answered_at - created_at))::float8 AS latency_seconds
                FROM scoped_calls
                WHERE answered_at IS NOT NULL
                  AND answered_at >= created_at
            ),
            latency_stats AS (
                SELECT
                    COALESCE(percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_seconds), 0)::float8 AS p50_seconds,
                    COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_seconds), 0)::float8 AS p95_seconds,
                    COALESCE(MAX(latency_seconds), 0)::float8 AS max_seconds
                FROM answer_latencies
            )
            SELECT
                counts.setup_attempts,
                counts.setup_successes,
                latency_stats.p50_seconds,
                latency_stats.p95_seconds,
                latency_stats.max_seconds
            FROM counts
            CROSS JOIN latency_stats
            """,
            window_minutes,
        )

    return CallMetrics(
        setup_attempts=int(row["setup_attempts"] or 0),
        setup_successes=int(row["setup_successes"] or 0),
        answer_latency_p50_seconds=float(row["p50_seconds"] or 0.0),
        answer_latency_p95_seconds=float(row["p95_seconds"] or 0.0),
        answer_latency_max_seconds=float(row["max_seconds"] or 0.0),
    )


def _read_transfer_metrics() -> TransferMetrics:
    """
    Read transfer metrics via the generic CallControlAdapter interface.

    The active adapter (Asterisk or FreeSWITCH) implements
    ``get_transfer_metrics()`` which returns its internal state
    without the observability layer needing to know adapter internals.
    """
    try:
        from app.api.v1.endpoints import telephony_bridge
    except Exception:
        return TransferMetrics(attempts=0, successes=0, inflight=0)

    adapter = getattr(telephony_bridge, "_adapter", None)
    if not adapter or not getattr(adapter, "connected", False):
        return TransferMetrics(attempts=0, successes=0, inflight=0)

    try:
        metrics = adapter.get_transfer_metrics()
    except Exception:
        logger.warning("Unable to read transfer results for metrics", exc_info=True)
        return TransferMetrics(attempts=0, successes=0, inflight=0)

    return TransferMetrics(
        attempts=metrics.get("attempts", 0),
        successes=metrics.get("successes", 0),
        inflight=metrics.get("inflight", 0),
    )


def _read_canary_metrics() -> CanaryMetrics:
    return CanaryMetrics(
        enabled=_bool_env_any(["KAMAILIO_CANARY_ENABLED", "OPENSIPS_CANARY_ENABLED"], default=False),
        percent=max(
            0.0,
            min(100.0, _float_env_any(["KAMAILIO_CANARY_PERCENT", "OPENSIPS_CANARY_PERCENT"], default=0.0)),
        ),
        frozen=_bool_env_any(["KAMAILIO_CANARY_FREEZE", "OPENSIPS_CANARY_FREEZE"], default=False),
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return max(0.0, min(1.0, float(numerator) / float(denominator)))


async def refresh_telephony_slo_metrics(
    db_pool: asyncpg.Pool,
    window_minutes: Optional[int] = None,
) -> None:
    """
    Refresh WS-K SLO gauges from runtime data sources.

    Safe to call on each /metrics scrape.
    """
    started = time.monotonic()
    now = time.time()
    success = True
    effective_window = window_minutes if window_minutes is not None else get_metrics_window_minutes()
    if effective_window < _MIN_WINDOW_MINUTES:
        effective_window = _MIN_WINDOW_MINUTES
    elif effective_window > _MAX_WINDOW_MINUTES:
        effective_window = _MAX_WINDOW_MINUTES

    METRICS_WINDOW_MINUTES.set(float(effective_window))

    try:
        runtime = await _fetch_runtime_metrics(db_pool=db_pool, window_minutes=effective_window)
    except Exception:
        logger.warning("Failed to collect runtime activation metrics", exc_info=True)
        runtime = RuntimeMetrics(0, 0, 0, 0, 0.0, 0.0, 0.0)
        success = False

    try:
        calls = await _fetch_call_metrics(db_pool=db_pool, window_minutes=effective_window)
    except Exception:
        logger.warning("Failed to collect call setup metrics", exc_info=True)
        calls = CallMetrics(0, 0, 0.0, 0.0, 0.0)
        success = False

    try:
        transfers = _read_transfer_metrics()
    except Exception:
        logger.warning("Failed to collect transfer metrics", exc_info=True)
        transfers = TransferMetrics(0, 0, 0)
        success = False

    canary = _read_canary_metrics()

    RUNTIME_ACTIVATION_ATTEMPTS.set(float(runtime.activation_attempts))
    RUNTIME_ACTIVATION_SUCCESSES.set(float(runtime.activation_successes))
    RUNTIME_ACTIVATION_SUCCESS_RATIO.set(
        _safe_ratio(runtime.activation_successes, runtime.activation_attempts)
    )
    RUNTIME_ROLLBACK_ATTEMPTS.set(float(runtime.rollback_attempts))
    RUNTIME_ROLLBACK_SUCCESSES.set(float(runtime.rollback_successes))
    RUNTIME_ROLLBACK_LATENCY_P50_SECONDS.set(runtime.rollback_p50_seconds)
    RUNTIME_ROLLBACK_LATENCY_P95_SECONDS.set(runtime.rollback_p95_seconds)
    RUNTIME_ROLLBACK_LATENCY_MAX_SECONDS.set(runtime.rollback_max_seconds)

    CALL_SETUP_ATTEMPTS.set(float(calls.setup_attempts))
    CALL_SETUP_SUCCESSES.set(float(calls.setup_successes))
    CALL_SETUP_SUCCESS_RATIO.set(_safe_ratio(calls.setup_successes, calls.setup_attempts))
    CALL_ANSWER_LATENCY_P50_SECONDS.set(calls.answer_latency_p50_seconds)
    CALL_ANSWER_LATENCY_P95_SECONDS.set(calls.answer_latency_p95_seconds)
    CALL_ANSWER_LATENCY_MAX_SECONDS.set(calls.answer_latency_max_seconds)

    TRANSFER_ATTEMPTS.set(float(transfers.attempts))
    TRANSFER_SUCCESSES.set(float(transfers.successes))
    TRANSFER_SUCCESS_RATIO.set(_safe_ratio(transfers.successes, transfers.attempts))
    TRANSFER_INFLIGHT.set(float(transfers.inflight))

    CANARY_ENABLED.set(1.0 if canary.enabled else 0.0)
    CANARY_PERCENT.set(canary.percent)
    CANARY_FROZEN.set(1.0 if canary.frozen else 0.0)

    METRICS_SCRAPE_SUCCESS.set(1.0 if success else 0.0)
    METRICS_SCRAPE_TIMESTAMP_SECONDS.set(now)
    METRICS_SCRAPE_DURATION_SECONDS.set(max(0.0, time.monotonic() - started))


def render_prometheus_metrics() -> bytes:
    return generate_latest()


def prometheus_content_type() -> str:
    return CONTENT_TYPE_LATEST


def record_turn_silent_reason(reason: str) -> None:
    TURN_SILENT_REASON_TOTAL.labels(reason=reason).inc()
