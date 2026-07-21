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


# Background-worker liveness registry: worker-name → the Redis key that worker
# refreshes on each heartbeat tick. Adding voice/reminder/cleanup workers later
# is one line each once they write their own heartbeat key (see
# DialerWorker.HEARTBEAT_REDIS_KEY). A worker is "healthy" when its heartbeat is
# fresher than _WORKER_STALE_AFTER_S.
_WORKER_HEARTBEAT_KEYS: Dict[str, str] = {
    "dialer": "dialer:heartbeat_ts",
    "voice": "voice:heartbeat_ts",
    "reminder": "reminder:heartbeat_ts",
}
_WORKER_STALE_AFTER_S = 180


@router.get("/healthz/workers")
async def workers_health_probe(response: Response) -> Dict[str, Any]:
    """
    Background-worker liveness probe (unauthenticated, like the other healthz
    probes) — the single monitorable signal any uptime checker can watch, no
    Prometheus/redis-exporter required.

    Reads each registered worker's Redis heartbeat timestamp and reports
    per-worker ``{name, last_beat_epoch, age_seconds, healthy}`` where healthy
    means the heartbeat is younger than _WORKER_STALE_AFTER_S. Overall HTTP 200
    only when EVERY known worker is healthy; 503 if any is stale or missing
    (worker dead, hung, or Redis unreachable) so an alert fires.

    Each Redis read is bounded so a wedged Redis can't hang the probe — a
    timeout/error is treated as "no heartbeat" → that worker is unhealthy.
    """
    import time as _time
    from app.core.container import get_container

    container = get_container()
    redis_client = (
        getattr(container, "redis", None) if container.is_initialized else None
    )

    now = _time.time()
    workers: list[Dict[str, Any]] = []
    all_healthy = True

    for name, key in _WORKER_HEARTBEAT_KEYS.items():
        last_beat: Any = None
        if redis_client is not None:
            try:
                raw = await asyncio.wait_for(
                    redis_client.get(key), timeout=_DEEP_PING_TIMEOUT_S
                )
                if raw is not None:
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", "ignore")
                    last_beat = float(raw)
            except Exception:
                last_beat = None

        age = (now - last_beat) if last_beat is not None else None
        healthy = age is not None and age < _WORKER_STALE_AFTER_S
        if not healthy:
            all_healthy = False
        workers.append(
            {
                "name": name,
                "last_beat_epoch": last_beat,
                "age_seconds": age,
                "healthy": healthy,
            }
        )

    result: Dict[str, Any] = {"healthy": all_healthy, "workers": workers}
    if not all_healthy:
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
