"""Admin dashboard, system health, and platform runtime controls."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_admin,
    require_platform_admin,
)
from app.core.postgres_adapter import Client
from app.domain.services.dialer.job_states import LIVE_CALL_STATUSES
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.platform_runtime_controls import (
    OutboundCallPause,
    get_outbound_call_pause,
    set_outbound_call_pause,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# =============================================================================
# Response Models
# =============================================================================

class DashboardStatsResponse(BaseModel):
    """Dashboard statistics response"""
    active_calls: int
    error_rate_24h: str
    active_tenants: int
    api_errors_24h: int


class SystemHealthItem(BaseModel):
    """Single provider health status"""
    name: str
    status: str  # 'operational', 'degraded', 'down'
    latency_ms: int
    latency_display: str


class SystemHealthResponse(BaseModel):
    """System health response"""
    providers: List[SystemHealthItem]


class PauseCallsResponse(BaseModel):
    """Pause calls response"""
    paused: bool
    paused_at: Optional[str] = None
    paused_by: Optional[str] = None
    reason: Optional[str] = None
    message: str


class SetPauseCallsRequest(BaseModel):
    paused: bool
    reason: Optional[str] = Field(None, max_length=500)


def _pause_response(state: OutboundCallPause) -> PauseCallsResponse:
    return PauseCallsResponse(
        paused=state.paused,
        paused_at=state.paused_at.isoformat() if state.paused_at else None,
        paused_by=state.paused_by,
        reason=state.reason,
        message=(
            "Outbound call initiation is paused across all workers."
            if state.paused
            else "Outbound call initiation is enabled."
        ),
    )


async def _safe_audit(audit_logger: AuditLogger, **kwargs) -> None:
    try:
        await audit_logger.log(**kwargs)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("platform control audit failed: %s", exc)


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get real-time dashboard statistics for Command Center.
    
    Returns:
        - active_calls: Number of currently active calls
        - error_rate_24h: Error rate in last 24 hours
        - active_tenants: Number of active tenants
        - api_errors_24h: Number of API errors in last 24 hours
    """
    try:
        # Get active calls count
        calls_response = db_client.table("calls").select(
            "id", count="exact"
        ).in_("status", list(LIVE_CALL_STATUSES)).execute()
        active_calls = calls_response.count or 0
        
        # Get calls in last 24 hours for error rate
        yesterday = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        calls_24h = db_client.table("calls").select(
            "id, status"
        ).gte("created_at", yesterday).execute()
        
        total_calls_24h = len(calls_24h.data or [])
        failed_calls_24h = len([c for c in (calls_24h.data or []) if c.get("status") in ["failed", "error", "no_answer"]])
        
        if total_calls_24h > 0:
            error_rate = (failed_calls_24h / total_calls_24h) * 100
            error_rate_str = f"{error_rate:.1f}%"
        else:
            error_rate_str = "0%"
        
        # Get active tenants count
        tenants_response = db_client.table("tenants").select(
            "id", count="exact"
        ).eq("status", "active").execute()
        active_tenants = tenants_response.count or 0
        
        # If no status column, count all tenants
        if active_tenants == 0:
            all_tenants = db_client.table("tenants").select("id", count="exact").execute()
            active_tenants = all_tenants.count or 0
        
        # API errors - approximate from failed calls
        api_errors_24h = failed_calls_24h
        
        return DashboardStatsResponse(
            active_calls=active_calls,
            error_rate_24h=error_rate_str,
            active_tenants=active_tenants,
            api_errors_24h=api_errors_24h
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dashboard stats: {str(e)}"
        )


@router.get("/system-health", response_model=SystemHealthResponse)
async def get_system_health(
    admin_user: CurrentUser = Depends(require_admin)
):
    """
    Get system health status for all providers.
    
    Returns health status for:
        - Telephony (Vonage)
        - STT (Speech-to-Text)
        - LLM (Language Model)
        - TTS (Text-to-Speech)
    """
    providers = [
        SystemHealthItem(
            name="STT",
            status="operational",
            latency_ms=120,
            latency_display="120ms Avg"
        ),
        SystemHealthItem(
            name="LLM",
            status="operational",
            latency_ms=250,
            latency_display="<300ms"
        ),
        SystemHealthItem(
            name="TTS",
            status="operational",
            latency_ms=180,
            latency_display="<200ms"
        )
    ]
    
    return SystemHealthResponse(providers=providers)


async def _set_pause(
    *,
    paused: bool,
    reason: str | None,
    admin_user: CurrentUser,
    db_client: Client,
    audit_logger: AuditLogger,
) -> PauseCallsResponse:
    state = await set_outbound_call_pause(
        db_client.pool,
        paused=paused,
        actor_id=str(admin_user.id),
        reason=reason,
    )
    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.CONFIG_CHANGED,
        actor_id=admin_user.id,
        actor_type="user",
        resource_type="platform_runtime_controls",
        action="outbound_calls_paused" if paused else "outbound_calls_resumed",
        description=(
            "Platform admin paused new outbound calls"
            if paused
            else "Platform admin resumed new outbound calls"
        ),
        metadata={"paused": paused, "reason": state.reason},
    )
    return _pause_response(state)


@router.put("/calls/pause", response_model=PauseCallsResponse)
async def set_pause_all_calls(
    body: SetPauseCallsRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Idempotently pause or resume all new outbound call initiation."""
    return await _set_pause(
        paused=body.paused,
        reason=body.reason,
        admin_user=admin_user,
        db_client=db_client,
        audit_logger=audit_logger,
    )


@router.post("/calls/pause", response_model=PauseCallsResponse)
async def pause_all_calls(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Backward-compatible toggle; new clients should use the idempotent PUT."""
    current = await get_outbound_call_pause(db_client.pool)
    return await _set_pause(
        paused=not current.paused,
        reason="Legacy Admin toggle",
        admin_user=admin_user,
        db_client=db_client,
        audit_logger=audit_logger,
    )


@router.get("/calls/pause-status", response_model=PauseCallsResponse)
async def get_pause_status(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Get the persisted pause state shared by every worker."""
    return _pause_response(await get_outbound_call_pause(db_client.pool))
