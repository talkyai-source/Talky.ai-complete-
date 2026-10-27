"""Bounded API-host metrics; provider-account voice health is separate."""
from __future__ import annotations
import asyncio
import os
import platform
import time
import psutil
from fastapi import APIRouter, Depends, HTTPException
from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.postgres_adapter import Client
from ._shared import _format_uptime, _internal_dependency_health, _utc_now
from .schemas import DetailedHealthResponse

router = APIRouter()

def _host_metrics() -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    uptime = max(0, int(time.time() - psutil.Process().create_time()))
    return dict(uptime_seconds=uptime, uptime_display=_format_uptime(uptime),
        memory_usage_mb=round(memory.used / 1024**2, 1), memory_total_mb=round(memory.total / 1024**2, 1),
        memory_percent=memory.percent, cpu_usage_percent=psutil.cpu_percent(interval=0.1),
        disk_usage_percent=disk.percent, disk_free_gb=round(disk.free / 1024**3, 2))

@router.get("/health/detailed", response_model=DetailedHealthResponse)
async def get_detailed_health(admin_user: CurrentUser = Depends(require_platform_admin), db_client: Client = Depends(get_db_client)):
    try:
        metrics, providers = await asyncio.gather(
            asyncio.wait_for(asyncio.to_thread(_host_metrics), timeout=2.0),
            _internal_dependency_health(db_client.pool),
        )
        observed = _utc_now()
        return DetailedHealthResponse(**metrics, python_version=platform.python_version(),
            os_info=f"{platform.system()} {platform.release()}", environment=os.getenv("ENVIRONMENT", "unknown"),
            version=os.getenv("APP_VERSION", "unknown"), providers=providers, last_updated=observed, checked_at=observed,
            metrics_scope="Host as reported by this API process; uptime is this API process only, not platform availability.")
    except Exception:
        raise HTTPException(status_code=503, detail="API host metrics could not be measured.")
