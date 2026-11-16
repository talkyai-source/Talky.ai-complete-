"""Top-level operational API endpoints."""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, FastAPI, Header, HTTPException, Response, status

from app.core.telephony_observability import (
    prometheus_content_type,
    refresh_telephony_slo_metrics,
    render_prometheus_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def root():
    return {"message": "Talky.ai API - AI Voice Dialer", "status": "running"}


@router.get("/health")
async def health_check():
    from app.core.container import get_container

    health: dict = {"status": "healthy"}
    container = get_container()
    if container.is_initialized:
        health["container"] = "initialized"
        health["redis_enabled"] = container.redis_enabled
        if container._session_manager:
            health["active_sessions"] = container.session_manager.get_active_session_count()
    else:
        health["container"] = "not_initialized"
    return health


@router.get("/metrics")
async def prometheus_metrics(
    x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token")
):
    """Prometheus scrape endpoint for telephony SLO metrics.

    Fails CLOSED: if TELEPHONY_METRICS_TOKEN is not configured, the endpoint
    is disabled (503) rather than serving internal metrics with no auth.
    When configured, the caller must present the token via the
    X-Metrics-Token header, compared in constant time.
    """
    expected = os.getenv("TELEPHONY_METRICS_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics endpoint disabled: TELEPHONY_METRICS_TOKEN is not configured",
        )
    if not x_metrics_token or not hmac.compare_digest(x_metrics_token.strip(), expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics token")
    from app.core.container import get_container

    container = get_container()
    if container.is_initialized:
        await refresh_telephony_slo_metrics(container.db_pool)
    _publish_call_capacity()
    return Response(content=render_prometheus_metrics(), media_type=prometheus_content_type())


def _publish_call_capacity() -> None:
    """Expose live calls, the call limit and provider concurrency.

    A process that does not handle calls reports 0/0 so summing the two API
    processes does not double the limit. Never breaks the scrape.
    """
    try:
        from app.core.process_role import telephony_enabled
        from app.core.telephony_observability import set_call_capacity_metrics

        active = 0
        capacity = 0
        if telephony_enabled():
            from app.domain.services.telephony.config import _MAX_TELEPHONY_SESSIONS
            from app.domain.services.telephony.state_backend import get_state_backend

            active = int(get_state_backend().voice_session_count())
            capacity = int(_MAX_TELEPHONY_SESSIONS)
        from app.infrastructure.providers.provider_concurrency import all_guards

        set_call_capacity_metrics(
            active_calls=active,
            pod_capacity=capacity,
            providers=[g.snapshot() for g in all_guards()],
        )
    except Exception:
        logger.debug("call capacity metrics unavailable", exc_info=True)


def register_operational_routes(app: FastAPI) -> None:
    """Register root-level operational endpoints on the FastAPI app."""
    app.include_router(router)
