"""Top-level operational API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, Header, HTTPException, Response, status

from app.core.telephony_observability import (
    is_metrics_request_authorized,
    prometheus_content_type,
    refresh_telephony_slo_metrics,
    render_prometheus_metrics,
)

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
    """Prometheus scrape endpoint for telephony SLO metrics."""
    if not is_metrics_request_authorized(x_metrics_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics token")
    from app.core.container import get_container

    container = get_container()
    if container.is_initialized:
        await refresh_telephony_slo_metrics(container.db_pool)
    return Response(content=render_prometheus_metrics(), media_type=prometheus_content_type())


def register_operational_routes(app: FastAPI) -> None:
    """Register root-level operational endpoints on the FastAPI app."""
    app.include_router(router)
