"""GET /admin/health/detailed — system metrics + provider health."""
from __future__ import annotations

import os
import platform
from datetime import datetime

import psutil
from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.postgres_adapter import Client

from ._shared import _format_uptime, _server_start_time
from .schemas import DetailedHealthResponse, SystemHealthItem

router = APIRouter()


@router.get("/health/detailed", response_model=DetailedHealthResponse)
async def get_detailed_health(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get comprehensive system health including:
    - System metrics (CPU, memory, disk)
    - Uptime
    - Provider health status
    """
    try:
        # Calculate uptime
        uptime_seconds = int((datetime.utcnow() - _server_start_time).total_seconds())
        uptime_display = _format_uptime(uptime_seconds)

        # Get system metrics using psutil
        memory = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.1)
        disk = psutil.disk_usage('/')

        # Only the database has a measured check here.
        providers = []

        # Check PostgreSQL/Database
        db_start = datetime.utcnow()
        try:
            db_client.table("tenants").select("id").limit(1).execute()
            db_latency = int((datetime.utcnow() - db_start).total_seconds() * 1000)
            providers.append(SystemHealthItem(
                name="Database",
                status="operational" if db_latency < 500 else "degraded",
                latency_ms=db_latency,
                latency_display=f"{db_latency}ms",
                last_check=datetime.utcnow().isoformat() + "Z"
            ))
        except Exception:
            providers.append(SystemHealthItem(
                name="Database",
                status="down",
                latency_ms=None,
                latency_display="N/A",
                last_check=datetime.utcnow().isoformat() + "Z"
            ))

        # No authoritative provider probe is wired here. Never substitute
        # fixed latency figures or a fresh timestamp for an actual observation.
        providers.extend(
            SystemHealthItem(
                name=name,
                status="unknown",
                latency_ms=None,
                latency_display="Not measured",
            )
            for name in ("STT", "LLM", "TTS")
        )

        return DetailedHealthResponse(
            uptime_seconds=uptime_seconds,
            uptime_display=uptime_display,
            memory_usage_mb=round(memory.used / (1024 * 1024), 1),
            memory_total_mb=round(memory.total / (1024 * 1024), 1),
            memory_percent=memory.percent,
            cpu_usage_percent=cpu_percent,
            disk_usage_percent=disk.percent,
            python_version=platform.python_version(),
            os_info=f"{platform.system()} {platform.release()}",
            environment=os.getenv("ENVIRONMENT", "development"),
            version=os.getenv("APP_VERSION", "1.0.0"),
            providers=providers,
            last_updated=datetime.utcnow().isoformat() + "Z"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get detailed health: {str(e)}"
        )
