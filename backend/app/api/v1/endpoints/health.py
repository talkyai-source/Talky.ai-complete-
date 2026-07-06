"""
Health Check Endpoint — API v1 Scoped

Provides detailed health status for application-level monitoring.
The root-level /health (used by Docker) is defined in main.py.
This endpoint adds API-version-scoped health info at /api/v1/health.
"""
import asyncio

from fastapi import APIRouter, Response, status
from datetime import datetime
from typing import Dict, Any

from app.core import readiness

router = APIRouter(tags=["health"])

# Per-dependency ping budget for the deep probe. Bounded so a wedged/
# blackholed DB or Redis can never hang the probe (and thus the LB's
# health decision) — a timeout is treated as "down", same as a hard error.
_DEEP_PING_TIMEOUT_S = 0.5


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


@router.get("/healthz/deep")
async def deep_readiness_probe(response: Response) -> Dict[str, Any]:
    """
    Deep readiness probe — actively pings the pod's hard dependencies.

    Unlike GET /healthz/ready (which only reports drain/capacity state and
    would happily return ready=true against a dead database), this probe
    issues a bounded SELECT 1 to Postgres and a PING to Redis so a pod with a
    dead dependency reports NOT ready and the load balancer stops routing to
    it. Point the LB at /healthz/ready for fast capacity gating and at
    /healthz/deep for dependency-aware readiness.

    Policy:
      - DB down / unreachable  → ready=false, HTTP 503 (there is NO fallback
        for Postgres; a pod that can't reach it cannot serve).
      - Redis down             → reported as "down" but ready stays true. The
        container is designed to degrade to an in-memory fallback when Redis
        is unavailable (see ServiceContainer._initialize_redis), so failing
        closed here would cause spurious pod evictions.
      - Container not started   → ready=false, HTTP 503.

    Each ping is capped at ~500ms via asyncio.wait_for; a timeout is treated
    as "down". The handler never raises — any unexpected error degrades to a
    not-ready / down report so the probe itself can't take the pod down.
    """
    from app.core.container import get_container

    container = get_container()
    result: Dict[str, Any] = {"ready": True, "db": "unknown", "redis": "unknown"}

    if not container.is_initialized:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        result.update(ready=False, db="not_initialized", redis="not_initialized")
        return result

    # DB — fatal on failure (no fallback exists).
    try:
        await asyncio.wait_for(
            container.db_pool.fetchval("SELECT 1"), timeout=_DEEP_PING_TIMEOUT_S
        )
        result["db"] = "ok"
    except Exception:
        result["db"] = "down"
        result["ready"] = False

    # Redis — report only; the in-memory fallback means a dead Redis is
    # degraded, not fatal, so it does not flip ready=false.
    try:
        redis_client = container.redis
        if redis_client is None:
            result["redis"] = "disabled"
        else:
            await asyncio.wait_for(
                redis_client.ping(), timeout=_DEEP_PING_TIMEOUT_S
            )
            result["redis"] = "ok"
    except Exception:
        result["redis"] = "down"

    if not result["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


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
