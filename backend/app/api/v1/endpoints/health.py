"""
Health Check Endpoint — API v1 Scoped

Provides detailed health status for application-level monitoring.
The root-level /health (used by Docker) is defined in main.py.
This endpoint adds API-version-scoped health info at /api/v1/health.
"""
from fastapi import APIRouter, Response, status
from datetime import datetime
from typing import Dict, Any

from app.core import readiness

router = APIRouter(tags=["health"])


@router.get("/healthz/ready")
async def readiness_probe(response: Response) -> Dict[str, Any]:
    """
    Kubernetes-style readiness probe.

    Returns 200 when the pod is ready to take new calls, 503 when:
      - the pod is draining (graceful shutdown started), or
      - the pod is at capacity (active_sessions >= MAX_TELEPHONY_SESSIONS)

    The load balancer reads this to decide whether to route to this pod.
    """
    snap = readiness.snapshot()
    if not snap["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        response.headers["Retry-After"] = str(
            readiness.retry_after_seconds_for_capacity()
        )
    return snap


@router.get("/healthz/live")
async def liveness_probe() -> Dict[str, Any]:
    """Kubernetes liveness — always 200 if the event loop is alive."""
    return {"alive": True, "ts": datetime.utcnow().isoformat() + "Z"}


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, Any]:
    """
    Basic API-scoped health check.

    Frontend and monitoring probes expect this route at /api/v1/health.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "talky-backend",
    }


@router.get("/health/detailed", status_code=status.HTTP_200_OK)
async def detailed_health_check() -> Dict[str, Any]:
    """
    Detailed health check for application monitoring.

    Includes container, Redis, and session status.
    Docker uses the simpler GET /health in main.py.
    """
    from app.core.container import get_container

    health: Dict[str, Any] = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "talky-backend",
    }

    container = get_container()
    if container.is_initialized:
        health["container"] = "initialized"
        health["redis_enabled"] = container.redis_enabled
        if container._session_manager:
            health["active_sessions"] = (
                container.session_manager.get_active_session_count()
            )
    else:
        health["container"] = "not_initialized"

    return health
