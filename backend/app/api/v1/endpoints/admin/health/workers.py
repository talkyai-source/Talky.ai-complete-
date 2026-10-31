"""GET /admin/health/workers — observed role heartbeat freshness."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from app.api.v1.dependencies import CurrentUser, require_platform_admin
from app.api.v1.endpoints.health import read_worker_heartbeats
from ._shared import _utc_now
from .schemas import WorkersResponse, WorkerStatus

router = APIRouter()

@router.get("/health/workers", response_model=WorkersResponse)
async def get_workers_status(admin_user: CurrentUser = Depends(require_platform_admin)):
    from app.core.container import get_container
    try:
        container = get_container()
        redis = getattr(container, "redis", None) if container.is_initialized else None
    except Exception:
        redis = None
    readings = await read_worker_heartbeats(redis)
    workers = []
    for item in readings:
        status = "healthy" if item["healthy"] else (
            "offline" if item["reason"] in {"heartbeat_missing", "heartbeat_stale"} else "unknown")
        beat = item["last_beat_epoch"]
        workers.append(WorkerStatus(
            id=item["name"], name=item["name"].title(), status=status,
            last_heartbeat=datetime.fromtimestamp(beat, timezone.utc).isoformat() if beat is not None else None,
            age_seconds=item["age_seconds"], detail=item["reason"].replace("_", " "),
        ))
    return WorkersResponse(workers=workers, total_workers=len(workers),
        active_workers=sum(item["healthy"] for item in readings), busy_workers=None, checked_at=_utc_now())
