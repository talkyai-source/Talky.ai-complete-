"""GET /telephony/sip/runtime/metrics/activation — windowed activation/rollback stats."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context

from ._shared import _require_tenant
from .schemas import RuntimeActivationMetricsResponse

router = APIRouter(tags=["Telephony SIP Runtime"])


@router.get("/metrics/activation", response_model=RuntimeActivationMetricsResponse)
async def get_runtime_activation_metrics(
    request: Request,
    window_hours: int = Query(default=24, ge=1, le=168),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(
            conn,
            current_user.tenant_id,
            current_user.id,
            request_id=request.headers.get("x-request-id"),
        )
        metrics = await conn.fetchrow(
            """
            WITH scoped_events AS (
                SELECT action, stage, status, request_id, created_at
                FROM tenant_runtime_policy_events
                WHERE tenant_id = $1
                  AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')
            ),
            counts AS (
                SELECT
                    COUNT(*) FILTER (
                        WHERE action = 'activate'
                          AND stage = 'commit'
                          AND status = 'succeeded'
                    ) AS activation_success_count,
                    COUNT(*) FILTER (
                        WHERE action = 'activate'
                          AND status = 'failed'
                    ) AS activation_failure_count,
                    COUNT(*) FILTER (
                        WHERE action = 'rollback'
                          AND stage = 'rollback'
                          AND status = 'succeeded'
                    ) AS rollback_success_count,
                    COUNT(*) FILTER (
                        WHERE action = 'rollback'
                          AND status = 'failed'
                    ) AS rollback_failure_count
                FROM scoped_events
            ),
            rollback_latencies AS (
                SELECT
                    EXTRACT(EPOCH FROM (done.created_at - started.created_at)) * 1000.0 AS latency_ms
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
                    percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                    MAX(latency_ms) AS max_ms
                FROM rollback_latencies
            )
            SELECT
                counts.activation_success_count,
                counts.activation_failure_count,
                counts.rollback_success_count,
                counts.rollback_failure_count,
                COALESCE(rollback_stats.p50_ms, 0)::float8 AS rollback_p50_ms,
                COALESCE(rollback_stats.p95_ms, 0)::float8 AS rollback_p95_ms,
                COALESCE(rollback_stats.max_ms, 0)::float8 AS rollback_max_ms
            FROM counts
            CROSS JOIN rollback_stats
            """,
            current_user.tenant_id,
            window_hours,
        )

    activation_success_count = int(metrics["activation_success_count"] or 0)
    activation_failure_count = int(metrics["activation_failure_count"] or 0)
    activation_attempts = activation_success_count + activation_failure_count
    activation_success_rate_pct = (
        round((activation_success_count / activation_attempts) * 100.0, 2)
        if activation_attempts > 0
        else 0.0
    )

    return RuntimeActivationMetricsResponse(
        tenant_id=current_user.tenant_id,
        window_hours=window_hours,
        generated_at=datetime.now(timezone.utc),
        activation_success_count=activation_success_count,
        activation_failure_count=activation_failure_count,
        activation_success_rate_pct=activation_success_rate_pct,
        rollback_success_count=int(metrics["rollback_success_count"] or 0),
        rollback_failure_count=int(metrics["rollback_failure_count"] or 0),
        rollback_latency_p50_ms=float(metrics["rollback_p50_ms"] or 0.0),
        rollback_latency_p95_ms=float(metrics["rollback_p95_ms"] or 0.0),
        rollback_latency_max_ms=float(metrics["rollback_max_ms"] or 0.0),
    )
