"""
WS-K telephony observability helpers.

Provides:
- Prometheus-compatible metric exposition helpers.
- Inbound-only runtime SLO metric aggregation from durable PostgreSQL state.
- Optional header-token protection for /metrics scraping.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, generate_latest

logger = logging.getLogger(__name__)

_MIN_WINDOW_MINUTES = 5
_MAX_WINDOW_MINUTES = 7 * 24 * 60
_DEFAULT_WINDOW_MINUTES = 60
_CANARY_DID_PATTERN = re.compile(r"^[1-9][0-9]{6,14}$")
_CANARY_CANDIDATE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANARY_GATE_STARTED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CANARY_MAX_RUN_AGE_SECONDS = 6 * 60 * 60
_CANARY_MAX_FUTURE_SKEW_SECONDS = 30


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


def is_metrics_request_authorized(x_metrics_token: str | None) -> bool:
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


def _get_or_create_gauge(name: str, documentation: str, labelnames: tuple[str, ...] = ()) -> Gauge:
    # Prometheus default registry deduplicates names globally. During some
    # reload/test scenarios this module can be imported more than once.
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
    if existing is not None:
        return existing
    return Gauge(name, documentation, labelnames=labelnames)


def _get_or_create_counter(
    name: str, documentation: str, labelnames: tuple[str, ...] = ()
) -> Counter:
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
    if existing is not None:
        return existing
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
    latest_call_timestamp_seconds: float = 0.0


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


@dataclass(frozen=True)
class CanaryEvidenceScope:
    tenant_id: str
    config_id: str
    did: str
    candidate_digest: str
    run_id: str
    gate_started_at: datetime

    @property
    def gate_started_at_text(self) -> str:
        return self.gate_started_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def gate_started_at_epoch(self) -> int:
        return int(self.gate_started_at.timestamp())

    @property
    def scope_hash(self) -> str:
        canonical = ":".join(
            (
                self.tenant_id,
                self.config_id,
                self.did,
                self.candidate_digest,
                self.run_id,
                self.gate_started_at_text,
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_canary_evidence_scope() -> CanaryEvidenceScope:
    """Return the one exact evidence scope or fail closed."""

    raw_tenant = os.getenv("TELEPHONY_CANARY_TENANT_ID", "").strip()
    raw_config = os.getenv("TELEPHONY_CANARY_CONFIG_ID", "").strip()
    did = os.getenv("TELEPHONY_CANARY_DID", "").strip()
    candidate_digest = os.getenv("TELEPHONY_CANARY_CANDIDATE_DIGEST", "").strip()
    raw_run_id = os.getenv("TELEPHONY_CANARY_RUN_ID", "").strip()
    raw_gate_started_at = os.getenv("TELEPHONY_CANARY_GATE_STARTED_AT", "").strip()
    try:
        tenant_id = str(UUID(raw_tenant))
        config_id = str(UUID(raw_config))
        parsed_run_id = UUID(raw_run_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("canary tenant/config/run scope is missing or invalid") from exc
    if parsed_run_id.version != 4:
        raise ValueError("canary run ID must be a UUIDv4")
    run_id = str(parsed_run_id)
    if not _CANARY_DID_PATTERN.fullmatch(did):
        raise ValueError("canary DID scope is missing or invalid")
    if not _CANARY_CANDIDATE_DIGEST_PATTERN.fullmatch(candidate_digest):
        raise ValueError("canary frozen candidate digest is missing or invalid")
    if not _CANARY_GATE_STARTED_AT_PATTERN.fullmatch(raw_gate_started_at):
        raise ValueError("canary gate start is missing or non-canonical")
    try:
        gate_started_at = datetime.strptime(raw_gate_started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as exc:
        raise ValueError("canary gate start is invalid") from exc

    now = datetime.now(UTC)
    age_seconds = (now - gate_started_at).total_seconds()
    if age_seconds < -_CANARY_MAX_FUTURE_SKEW_SECONDS:
        raise ValueError("canary gate start is in the future")
    if age_seconds > _CANARY_MAX_RUN_AGE_SECONDS:
        raise ValueError("canary evidence run is stale")

    return CanaryEvidenceScope(
        tenant_id=tenant_id,
        config_id=config_id,
        did=did,
        candidate_digest=candidate_digest,
        run_id=run_id,
        gate_started_at=gate_started_at,
    )


@asynccontextmanager
async def _acquire_metrics_connection(
    db_pool: asyncpg.Pool,
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a transaction-scoped, read-only cross-tenant metrics context."""

    async with db_pool.acquire() as conn, conn.transaction():
        # The operational metrics endpoint is service-authenticated and must
        # aggregate every inbound tenant. FORCE RLS otherwise turns the
        # queries into silent zero-evidence results.
        await conn.execute("SET LOCAL app.bypass_rls = 'on'")
        yield conn


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
    "Legacy dashboard window setting; canary evidence uses its immutable start baseline.",
)
CANARY_SCOPE_VALID = _get_or_create_gauge(
    "talky_telephony_canary_scope_valid",
    "1 when exact tenant/config/DID/candidate/run/start evidence scope is valid and fresh.",
)
CANARY_SCOPE_INFO = _get_or_create_gauge(
    "talky_telephony_canary_scope_info",
    "Exact canary evidence scope identity represented by a stable SHA-256 hash.",
    labelnames=("scope_hash",),
)
CANARY_EVIDENCE_BASELINE_TIMESTAMP_SECONDS = _get_or_create_gauge(
    "talky_telephony_canary_evidence_baseline_timestamp_seconds",
    "Immutable UTC gate-start baseline for the current canary evidence run.",
)
CANARY_UNIQUE_CALL_IDS = _get_or_create_gauge(
    "talky_telephony_canary_unique_call_ids",
    "Distinct inbound call IDs created after the current canary run baseline.",
)
CANARY_LATEST_CALL_TIMESTAMP_SECONDS = _get_or_create_gauge(
    "talky_telephony_canary_latest_call_timestamp_seconds",
    "Latest scoped inbound call creation timestamp after the canary run baseline.",
)

CALL_SETUP_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_calls_setup_attempts",
    "Distinct inbound call IDs created after the current canary run baseline.",
)
CALL_SETUP_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_calls_setup_successes",
    "Distinct successful inbound call IDs created after the canary run baseline.",
)
CALL_SETUP_SUCCESS_RATIO = _get_or_create_gauge(
    "talky_telephony_calls_setup_success_ratio",
    "Run-scoped distinct-call setup success ratio (0..1).",
)
CALL_ANSWER_LATENCY_P50_SECONDS = _get_or_create_gauge(
    "talky_telephony_calls_answer_latency_p50_seconds",
    "P50 answer latency for scoped calls created after the canary run baseline.",
)
CALL_ANSWER_LATENCY_P95_SECONDS = _get_or_create_gauge(
    "talky_telephony_calls_answer_latency_p95_seconds",
    "P95 answer latency for scoped calls created after the canary run baseline.",
)
CALL_ANSWER_LATENCY_MAX_SECONDS = _get_or_create_gauge(
    "talky_telephony_calls_answer_latency_max_seconds",
    "Max answer latency for scoped calls created after the canary run baseline.",
)

TRANSFER_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_transfers_attempts",
    "Distinct inbound parent call IDs with a post-baseline transfer leg.",
)
TRANSFER_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_transfers_successes",
    "Distinct inbound parent call IDs with a persisted post-baseline handoff.",
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
    "Distinct runtime activation request IDs created after the canary run baseline.",
)
RUNTIME_ACTIVATION_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_runtime_activation_successes",
    "Successful full runtime activation request IDs after the canary run baseline.",
)
RUNTIME_ACTIVATION_SUCCESS_RATIO = _get_or_create_gauge(
    "talky_telephony_runtime_activation_success_ratio",
    "Run-scoped full activation success ratio (0..1).",
)
RUNTIME_ROLLBACK_ATTEMPTS = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_attempts",
    "Distinct terminal rollback request IDs created after the canary run baseline.",
)
RUNTIME_ROLLBACK_SUCCESSES = _get_or_create_gauge(
    "talky_telephony_runtime_rollback_successes",
    "Successful paired rollback request IDs after the canary run baseline.",
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


async def _fetch_runtime_metrics(
    db_pool: asyncpg.Pool,
    scope: CanaryEvidenceScope,
) -> RuntimeMetrics:
    async with _acquire_metrics_connection(db_pool) as conn:
        row = await conn.fetchrow(
            """
            WITH scoped_events AS (
                SELECT event.action, event.stage, event.status,
                       event.request_id, event.created_at
                FROM tenant_runtime_policy_events event
                JOIN tenant_runtime_policy_versions version
                  ON version.id = event.policy_version_id
                 AND version.tenant_id = event.tenant_id
                WHERE event.created_at >= $1::timestamptz
                  AND event.tenant_id = $2::uuid
                  AND event.request_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          COALESCE(
                              version.input_snapshot->'route_policies',
                              '[]'::jsonb
                          )
                      ) AS route
                      WHERE route->>'route_type' = 'inbound'
                        AND route->'metadata'->'canary_scope'
                                  ->>'inbound_config_id' = $3
                        AND route->'metadata'->'canary_scope'->>'did' = $4
                        AND route->'metadata'->'canary_scope'
                                  ->>'candidate_digest' = $5
                        AND route->'metadata'->'canary_scope'->>'run_id' = $6
                        AND $4 ~ (route->>'match_pattern')
                  )
            ),
            activation_results AS (
                SELECT terminal.request_id,
                       BOOL_OR(
                           terminal.stage = 'commit'
                           AND terminal.status = 'succeeded'
                       ) AS succeeded
                FROM scoped_events terminal
                WHERE terminal.action = 'activate'
                  AND (
                      (
                          terminal.stage = 'commit'
                          AND terminal.status = 'succeeded'
                      )
                      OR (
                          terminal.stage IN ('apply', 'verify')
                          AND terminal.status = 'failed'
                      )
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM scoped_events started
                      WHERE started.action = 'activate'
                        AND started.stage = 'precheck'
                        AND started.status = 'succeeded'
                        AND started.request_id = terminal.request_id
                        AND started.created_at <= terminal.created_at
                  )
                GROUP BY terminal.request_id
            ),
            rollback_results AS (
                SELECT started.request_id,
                       BOOL_OR(done.status = 'succeeded') AS succeeded,
                       MIN(
                           EXTRACT(EPOCH FROM (done.created_at - started.created_at))
                       )::float8 AS latency_seconds
                FROM scoped_events started
                JOIN scoped_events done
                  ON done.action = 'rollback'
                 AND done.stage = 'rollback'
                 AND done.status IN ('succeeded', 'failed')
                 AND started.action = 'rollback'
                 AND started.stage = 'rollback'
                 AND started.status = 'started'
                 AND started.request_id = done.request_id
                 AND done.created_at >= started.created_at
                GROUP BY started.request_id
            ),
            counts AS (
                SELECT
                    (SELECT COUNT(*) FROM activation_results) AS activation_attempts,
                    (SELECT COUNT(*) FROM activation_results WHERE succeeded)
                        AS activation_successes,
                    (SELECT COUNT(*) FROM rollback_results) AS rollback_attempts,
                    (SELECT COUNT(*) FROM rollback_results WHERE succeeded)
                        AS rollback_successes
            ),
            rollback_stats AS (
                SELECT
                    COALESCE(percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_seconds), 0)::float8 AS p50_seconds,
                    COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_seconds), 0)::float8 AS p95_seconds,
                    COALESCE(MAX(latency_seconds), 0)::float8 AS max_seconds
                FROM rollback_results
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
            scope.gate_started_at,
            scope.tenant_id,
            scope.config_id,
            scope.did,
            scope.candidate_digest,
            scope.run_id,
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


async def _fetch_call_metrics(
    db_pool: asyncpg.Pool,
    scope: CanaryEvidenceScope,
) -> CallMetrics:
    async with _acquire_metrics_connection(db_pool) as conn:
        row = await conn.fetchrow(
            """
            WITH scoped_calls AS (
                SELECT id, created_at, answered_at, status
                FROM calls
                WHERE created_at >= $1::timestamptz
                  AND NOT is_test
                  AND direction = 'inbound'
                  AND tenant_id = $2::uuid
                  AND called_did = ('+' || $3)
                  AND COALESCE(
                      route_snapshot #>> '{inbound_config,id}',
                      route_snapshot #>> '{route,config_id}'
                  ) = $4
            ),
            counts AS (
                SELECT
                    COUNT(DISTINCT id)::int AS setup_attempts,
                    COUNT(DISTINCT id) FILTER (
                        WHERE answered_at IS NOT NULL
                           OR status IN ('answered', 'completed', 'in_progress')
                    )::int AS setup_successes,
                    COALESCE(EXTRACT(EPOCH FROM MAX(created_at)), 0)::float8
                        AS latest_call_timestamp_seconds
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
                counts.latest_call_timestamp_seconds,
                latency_stats.p50_seconds,
                latency_stats.p95_seconds,
                latency_stats.max_seconds
            FROM counts
            CROSS JOIN latency_stats
            """,
            scope.gate_started_at,
            scope.tenant_id,
            scope.did,
            scope.config_id,
        )

    return CallMetrics(
        setup_attempts=int(row["setup_attempts"] or 0),
        setup_successes=int(row["setup_successes"] or 0),
        answer_latency_p50_seconds=float(row["p50_seconds"] or 0.0),
        answer_latency_p95_seconds=float(row["p95_seconds"] or 0.0),
        answer_latency_max_seconds=float(row["max_seconds"] or 0.0),
        latest_call_timestamp_seconds=float(row["latest_call_timestamp_seconds"] or 0.0),
    )


async def _fetch_transfer_metrics(
    db_pool: asyncpg.Pool,
    scope: CanaryEvidenceScope,
) -> TransferMetrics:
    """Read durable transfer evidence for non-test inbound parent calls only."""

    async with _acquire_metrics_connection(db_pool) as conn:
        row = await conn.fetchrow(
            """
            WITH scoped_legs AS (
                SELECT parent.id AS parent_call_id, leg.status, leg.metadata
                FROM call_legs leg
                JOIN calls parent ON parent.id = leg.call_id
                WHERE leg.created_at >= $1::timestamptz
                  AND parent.created_at >= $1::timestamptz
                  AND leg.leg_type = 'transfer'
                  AND parent.direction = 'inbound'
                  AND NOT parent.is_test
                  AND parent.tenant_id = $2::uuid
                  AND parent.called_did = ('+' || $3)
                  AND COALESCE(
                      parent.route_snapshot #>> '{inbound_config,id}',
                      parent.route_snapshot #>> '{route,config_id}'
                  ) = $4
            )
            SELECT
                COUNT(DISTINCT parent_call_id)::int AS attempts,
                COUNT(DISTINCT parent_call_id) FILTER (
                    WHERE COALESCE(metadata->>'handoff_persisted', 'false') = 'true'
                )::int AS successes,
                COUNT(DISTINCT parent_call_id) FILTER (
                    WHERE status IN ('initiated', 'ringing')
                       OR (
                           status = 'answered'
                           AND COALESCE(metadata->>'handoff_persisted', 'false') <> 'true'
                       )
                )::int AS inflight
            FROM scoped_legs
            """,
            scope.gate_started_at,
            scope.tenant_id,
            scope.did,
            scope.config_id,
        )

    return TransferMetrics(
        attempts=int(row["attempts"] or 0),
        successes=int(row["successes"] or 0),
        inflight=int(row["inflight"] or 0),
    )


def _read_canary_metrics() -> CanaryMetrics:
    return CanaryMetrics(
        enabled=_bool_env_any(
            ["KAMAILIO_CANARY_ENABLED", "OPENSIPS_CANARY_ENABLED"], default=False
        ),
        percent=max(
            0.0,
            min(
                100.0,
                _float_env_any(["KAMAILIO_CANARY_PERCENT", "OPENSIPS_CANARY_PERCENT"], default=0.0),
            ),
        ),
        frozen=_bool_env_any(["KAMAILIO_CANARY_FREEZE", "OPENSIPS_CANARY_FREEZE"], default=False),
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        # Absence of evidence is never perfect evidence. The controller also
        # enforces explicit sample floors before a stage increase.
        return 0.0
    return max(0.0, min(1.0, float(numerator) / float(denominator)))


async def refresh_telephony_slo_metrics(
    db_pool: asyncpg.Pool,
    window_minutes: int | None = None,
) -> None:
    """
    Refresh WS-K SLO gauges from runtime data sources.

    Safe to call on each /metrics scrape.
    """
    started = time.monotonic()
    now = time.time()
    success = True
    effective_window = (
        window_minutes if window_minutes is not None else get_metrics_window_minutes()
    )
    if effective_window < _MIN_WINDOW_MINUTES:
        effective_window = _MIN_WINDOW_MINUTES
    elif effective_window > _MAX_WINDOW_MINUTES:
        effective_window = _MAX_WINDOW_MINUTES

    METRICS_WINDOW_MINUTES.set(float(effective_window))

    try:
        scope = _read_canary_evidence_scope()
    except ValueError:
        logger.warning("Canary evidence scope is missing or invalid; SLO scrape rejected")
        scope = None
        success = False
        CANARY_SCOPE_VALID.set(0.0)
        CANARY_SCOPE_INFO.clear()
        CANARY_EVIDENCE_BASELINE_TIMESTAMP_SECONDS.set(0.0)
    else:
        assert scope is not None
        CANARY_SCOPE_VALID.set(1.0)
        CANARY_SCOPE_INFO.clear()
        CANARY_SCOPE_INFO.labels(scope_hash=scope.scope_hash).set(1.0)
        CANARY_EVIDENCE_BASELINE_TIMESTAMP_SECONDS.set(float(scope.gate_started_at_epoch))

    runtime = RuntimeMetrics(0, 0, 0, 0, 0.0, 0.0, 0.0)
    calls = CallMetrics(0, 0, 0.0, 0.0, 0.0)
    transfers = TransferMetrics(0, 0, 0)

    if scope is not None:
        try:
            runtime = await _fetch_runtime_metrics(
                db_pool=db_pool,
                scope=scope,
            )
        except Exception:
            logger.warning("Failed to collect scoped runtime activation metrics", exc_info=True)
            success = False

        try:
            calls = await _fetch_call_metrics(
                db_pool=db_pool,
                scope=scope,
            )
        except Exception:
            logger.warning("Failed to collect scoped call setup metrics", exc_info=True)
            success = False

        try:
            transfers = await _fetch_transfer_metrics(
                db_pool=db_pool,
                scope=scope,
            )
        except Exception:
            logger.warning("Failed to collect scoped transfer metrics", exc_info=True)
            success = False

    if not success:
        # Never publish a partial successful query alongside a failed scrape.
        # Controllers must see a zero evidence delta unless every scoped data
        # source was collected under the same immutable run identity.
        runtime = RuntimeMetrics(0, 0, 0, 0, 0.0, 0.0, 0.0)
        calls = CallMetrics(0, 0, 0.0, 0.0, 0.0, 0.0)
        transfers = TransferMetrics(0, 0, 0)

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
    CANARY_UNIQUE_CALL_IDS.set(float(calls.setup_attempts))
    CANARY_LATEST_CALL_TIMESTAMP_SECONDS.set(calls.latest_call_timestamp_seconds)

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
