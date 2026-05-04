"""Shared helpers for /admin/health endpoints.

Module-level mutable state lives in the module that owns it:
  - _server_start_time:  here (read by system + workers)
  - _alert_settings:     in alerts.py (so the PUT endpoint, GET endpoint,
                          and trigger_alert all share the same binding)
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from .schemas import SystemHealthItem

# Track server start time for uptime calculation
_server_start_time = datetime.utcnow()


def _format_uptime(seconds: int) -> str:
    """Format uptime as human-readable string"""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)


async def _check_provider_health(provider_name: str, check_func) -> SystemHealthItem:
    """Check health of a single provider with timeout"""
    start_time = datetime.utcnow()
    try:
        # Use asyncio.wait_for for timeout
        await asyncio.wait_for(
            asyncio.to_thread(check_func),
            timeout=5.0
        )
        latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        status = "operational"
        if latency_ms > 1000:
            status = "degraded"

        return SystemHealthItem(
            name=provider_name,
            status=status,
            latency_ms=latency_ms,
            latency_display=f"{latency_ms}ms",
            last_check=datetime.utcnow().isoformat() + "Z"
        )
    except asyncio.TimeoutError:
        return SystemHealthItem(
            name=provider_name,
            status="degraded",
            latency_ms=5000,
            latency_display=">5000ms",
            last_check=datetime.utcnow().isoformat() + "Z"
        )
    except Exception:
        return SystemHealthItem(
            name=provider_name,
            status="down",
            latency_ms=0,
            latency_display="N/A",
            last_check=datetime.utcnow().isoformat() + "Z"
        )
