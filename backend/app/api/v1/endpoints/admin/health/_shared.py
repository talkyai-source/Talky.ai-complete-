"""Shared helpers for /admin/health endpoints.

Module-level mutable state lives in the module that owns it:
  - _server_start_time:  here (read by system + workers)
  - _alert_settings:     in alerts.py (so the PUT endpoint, GET endpoint,
                          and trigger_alert all share the same binding)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter

from app.core.db_utils import acquire_with_tenant

from .schemas import SystemHealthItem

# Track server start time for uptime calculation
_server_start_time = datetime.utcnow()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _observed_providers() -> list[SystemHealthItem]:
    from app.infrastructure.metrics.provider_observations import read_recent
    observations = await read_recent()
    items = []
    for component in ("stt", "llm", "tts", "realtime"):
        if any(item.component == component for item in observations or []):
            continue
        items.append(SystemHealthItem(name=component.upper(), status="unknown",
            latency_display="Not measured", scope="Recent provider/credential observations",
            detail=("Observation storage is unavailable. No current provider status can be inferred."
                    if observations is None else "No retained request observation in the last 10 minutes. No active provider probe is run.")))
    for item in observations or []:
        status = ("observed_success" if item.outcome in {"completed", "recognized"}
                  else "observed_failure" if item.outcome == "failed" else "unknown")
        scope = f"{item.credential_source} credential"
        if item.credential_id:
            scope += f" {item.credential_id}"
        scope += f"; tenant {item.tenant_id}" if item.tenant_id else "; no retained tenant identity"
        scope += f"; observed revision {item.credential_revision[:8]}"
        details = {
            "completed": "The last observed request produced output and completed its provider protocol.",
            "recognized": "The session returned a nonempty final transcript. Recognition accuracy is not measured here.",
            "failed": f"The last observed request failed ({item.error_category}); this does not prove a provider-wide outage.",
            "cancelled": "The last attempt was cancelled; provider availability is undetermined.",
            "incomplete": "The last attempt did not prove both output and successful completion.",
            "connected": "The protocol connection was accepted; no successful output is established by this observation.",
        }
        latency = item.first_output_ms if item.component != "stt" else None
        latency_display = "Not measured"
        if latency is not None:
            latency_display = (f"{latency}ms after response acceptance" if item.component == "realtime"
                               else f"{latency}ms to first output")
        items.append(SystemHealthItem(
            name=f"{item.component.upper()} / {item.provider} / {item.reference[:12]}", status=status,
            latency_ms=latency, latency_display=latency_display,
            last_check=item.observed_at, scope=scope,
            detail=details[item.outcome] + " Recent observed scope only; current key configuration, other accounts and fleet availability are not inferred.",
        ))
    return items


async def _database_probe(pool) -> int:
    start = perf_counter()
    async with acquire_with_tenant(pool, None, timeout=2.0) as conn:
        if await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=2.0) != 1:
            raise RuntimeError("Database probe did not acknowledge SELECT 1")
    return max(0, round((perf_counter() - start) * 1000))


async def _internal_dependency_health(pool) -> list[SystemHealthItem]:
    async def database():
        try:
            latency = await asyncio.wait_for(_database_probe(pool), timeout=3.0)
            return SystemHealthItem(name="Database", status="operational" if latency < 500 else "degraded",
                latency_ms=latency, latency_display=f"{latency}ms probe", last_check=_utc_now(),
                scope="Current API process database connection", detail="Bounded SELECT 1; not voice latency.")
        except Exception:
            return SystemHealthItem(name="Database", status="down", latency_ms=None, latency_display="Unavailable",
                last_check=_utc_now(), scope="Current API process database connection", detail="Database probe failed or timed out.")
    async def redis():
        try:
            from app.core.container import get_container
            container = get_container()
            client = getattr(container, "redis", None) if container.is_initialized else None
            if client is None:
                raise RuntimeError("Redis unavailable")
            start = perf_counter()
            if not await asyncio.wait_for(client.ping(), timeout=2.0):
                raise RuntimeError("Redis did not acknowledge PING")
            latency = max(0, round((perf_counter() - start) * 1000))
            return SystemHealthItem(name="Redis", status="operational" if latency < 500 else "degraded",
                latency_ms=latency, latency_display=f"{latency}ms probe", last_check=_utc_now(),
                scope="Current API process Redis connection", detail="Bounded PING; not worker or queue health.")
        except Exception:
            return SystemHealthItem(name="Redis", status="down", latency_ms=None, latency_display="Unavailable",
                last_check=_utc_now(), scope="Current API process Redis connection", detail="Redis probe failed or timed out.")
    async def telephony():
        # Reuse the existing non-billable Asterisk PBX/gateway read probe.
        # Never instantiate an adapter or probe another account on a poll.
        from app.api.v1.endpoints import telephony_bridge
        adapter = telephony_bridge._adapter
        scope = "Configured PBX and media gateway of this API process; not tenant carrier registration or end-to-end audio."
        if adapter is None or getattr(adapter, "name", None) != "asterisk":
            return SystemHealthItem(name="Telephony", status="unknown", latency_display="Not measured",
                scope=scope, detail="This API process has no supported active PBX read probe.")
        start = perf_counter()
        try:
            ok = await asyncio.wait_for(adapter.health_check(), timeout=4.0)
            if ok is not True:
                raise RuntimeError("PBX/gateway probe rejected")
            latency = max(0, round((perf_counter() - start) * 1000))
            return SystemHealthItem(name="Telephony", status="operational" if latency < 1000 else "degraded", latency_ms=latency,
                latency_display=f"{latency}ms control probe", last_check=_utc_now(), scope=scope,
                detail="Existing ARI read and gateway protocol compatibility probe passed; not call audio latency.")
        except Exception:
            return SystemHealthItem(name="Telephony", status="down", latency_display="Unavailable",
                last_check=_utc_now(), scope=scope, detail="PBX/gateway control probe failed or timed out.")
    db, cache, pbx, providers = await asyncio.gather(database(), redis(), telephony(), _observed_providers())
    return [db, cache, pbx, *providers]


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
        result = await asyncio.wait_for(
            asyncio.to_thread(check_func),
            timeout=5.0
        )
        if getattr(result, "error", None) or result is False:
            raise RuntimeError("Probe returned an error or negative acknowledgement")
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
            latency_ms=None,
            latency_display=">5000ms",
            last_check=datetime.utcnow().isoformat() + "Z"
        )
    except Exception:
        return SystemHealthItem(
            name=provider_name,
            status="down",
            latency_ms=None,
            latency_display="N/A",
            last_check=datetime.utcnow().isoformat() + "Z"
        )
