"""
Health Check Endpoint — API v1 Scoped

Provides detailed health status for application-level monitoring.
The root-level /health (used by Docker) is defined in main.py.
This endpoint adds API-version-scoped health info at /api/v1/health.
"""
from fastapi import APIRouter, status
from datetime import datetime
from typing import Dict, Any

router = APIRouter(tags=["health"])


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
