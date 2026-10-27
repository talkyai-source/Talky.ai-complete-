"""Admin dashboard, system health, and platform runtime controls."""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from .health._shared import _internal_dependency_health
from .health.schemas import SystemHealthItem
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
    failed_call_rate_24h: Optional[str] = None
    active_tenants: int
    failed_calls_24h: int
    total_calls_24h: int
    observed_at: str


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
    # Platform-wide totals (no tenant predicate): platform_admin only.
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """Counts from one database snapshot, not truncated client-side row scans.

    Failed calls means status failed/error among calls created in the preceding
    24 hours. It is not an API error count; no-answer is a separate disposition.
    """
    try:
        async with acquire_with_tenant(db_client.pool, None, timeout=3.0) as conn:
            row = await asyncio.wait_for(conn.fetchrow("""
                SELECT COUNT(*) FILTER (WHERE status = ANY($1::text[])) AS active_calls,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS total_calls_24h,
                       COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'
                                         AND status IN ('failed','error')) AS failed_calls_24h,
                       (SELECT COUNT(*) FROM tenants WHERE status='active'
                           AND COALESCE(NULLIF(subscription_status, ''), 'active') IN ('active','trialing')) AS active_tenants
                FROM calls
            """, list(LIVE_CALL_STATUSES)), timeout=3.0)
        if row is None:
            raise RuntimeError("Missing aggregate result")
        total = int(row["total_calls_24h"])
        failed = int(row["failed_calls_24h"])
        return DashboardStatsResponse(
            active_calls=int(row["active_calls"]), active_tenants=int(row["active_tenants"]),
            failed_calls_24h=failed, total_calls_24h=total,
            failed_call_rate_24h=f"{failed / total * 100:.1f}%" if total else None,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
    
    except Exception:
        logger.exception("Admin dashboard aggregate unavailable")
        raise HTTPException(
            status_code=503, detail="Dashboard counts could not be measured."
        )


@router.get("/system-health", response_model=SystemHealthResponse)
async def get_system_health(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """
    Get system health status for all providers.
    
    Returns health status for:
        - Telephony (Vonage)
        - STT (Speech-to-Text)
        - LLM (Language Model)
        - TTS (Text-to-Speech)
    """
    return SystemHealthResponse(providers=await _internal_dependency_health(db_client.pool))


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
