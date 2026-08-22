"""Admin call monitoring, history, detail, and live-call control."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from app.core.postgres_adapter import Client

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.core.db_utils import acquire_with_tenant
from ._serialization import AdminResponseModel
from app.domain.services.dialer.job_states import LIVE_CALL_STATUSES
from app.domain.services.call_status import CallOutcome, CallState
from app.domain.services.audit_logger import AuditEvent, AuditLogger

router = APIRouter()
logger = logging.getLogger(__name__)


def _live_duration_seconds(started_at) -> int:
    """Calculate a live duration from either asyncpg or JSON timestamp values."""
    if not started_at:
        return 0
    try:
        if isinstance(started_at, datetime):
            start_dt = started_at
        else:
            start_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc) if start_dt.tzinfo else datetime.now()
        return max(0, int((now - start_dt).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return 0


# =============================================================================
# Response Models
# =============================================================================

class TimelineEvent(BaseModel):
    """Timeline event for call detail"""
    event: str
    timestamp: str
    status: Optional[str] = None


class LiveCallItem(AdminResponseModel):
    """Active call item for live calls table"""
    id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_name: Optional[str] = None
    status: str  # 'in_progress', 'ringing', 'queued'
    started_at: Optional[str] = None
    duration_seconds: int = 0


class CallHistoryItem(AdminResponseModel):
    """Call history item with tenant info"""
    id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_name: Optional[str] = None
    status: str
    outcome: Optional[str] = None
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: str
    has_recording: bool = False
    has_feedback: bool = False
    feedback_transcript_status: Optional[str] = None


class CallHistoryResponse(BaseModel):
    """Paginated call history response"""
    items: List[CallHistoryItem]
    page: int
    page_size: int
    total: int


class AdminCallDetail(AdminResponseModel):
    """Full call detail for admin view"""
    id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    lead_id: Optional[str] = None
    status: str
    outcome: Optional[str] = None
    goal_achieved: bool = False
    started_at: Optional[str] = None
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None
    transcript_json: Optional[list] = None
    summary: Optional[str] = None
    summary_json: Optional[dict] = None
    recording_url: Optional[str] = None
    cost: Optional[float] = None
    timeline: List[TimelineEvent]
    created_at: str
    updated_at: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/calls/live", response_model=List[LiveCallItem])
async def get_live_calls(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get list of currently active calls.
    
    Returns calls in any LIVE status (job_states.LIVE_CALL_STATUSES:
    queued, dialing, ringing, answered, in_call, initiated).
    Includes tenant name and campaign name for context.
    """
    try:
        # Query active calls
        # Shared vocabulary — the local literal was missing dialing/
        # answered/in_call and contained 'in_progress', which nothing writes.
        active_statuses = list(LIVE_CALL_STATUSES)
        calls_response = db_client.table("calls").select(
            "id, tenant_id, phone_number, status, started_at, campaign_id"
        ).in_("status", active_statuses).order("started_at", desc=True).execute()
        if getattr(calls_response, "error", None):
            raise RuntimeError(calls_response.error)
        
        if not calls_response.data:
            return []
        
        # Collect tenant IDs and campaign IDs for batch lookup
        tenant_ids = list(set(c.get("tenant_id") for c in calls_response.data if c.get("tenant_id")))
        campaign_ids = list(set(c.get("campaign_id") for c in calls_response.data if c.get("campaign_id")))
        
        # Fetch tenant names
        tenant_map = {}
        if tenant_ids:
            tenants_response = db_client.table("tenants").select("id, business_name").in_("id", tenant_ids).execute()
            if getattr(tenants_response, "error", None):
                raise RuntimeError(tenants_response.error)
            for t in (tenants_response.data or []):
                tenant_map[t["id"]] = t["business_name"]
        
        # Fetch campaign names
        campaign_map = {}
        if campaign_ids:
            campaigns_response = db_client.table("campaigns").select("id, name").in_("id", campaign_ids).execute()
            if getattr(campaigns_response, "error", None):
                raise RuntimeError(campaigns_response.error)
            for c in (campaigns_response.data or []):
                campaign_map[c["id"]] = c["name"]

        # Calculate duration for active calls
        items = []
        for call in calls_response.data:
            started_at = call.get("started_at")
            duration = _live_duration_seconds(started_at)
            
            items.append(LiveCallItem(
                id=call["id"],
                tenant_id=call.get("tenant_id", ""),
                tenant_name=tenant_map.get(call.get("tenant_id"), "Unknown"),
                phone_number=call.get("phone_number", ""),
                campaign_name=campaign_map.get(call.get("campaign_id")),
                status=call.get("status", "unknown"),
                started_at=started_at,
                duration_seconds=max(0, duration)
            ))
        
        return items
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch live calls: {str(e)}"
        )


@router.get("/calls/history", response_model=CallHistoryResponse)
async def get_call_history(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    tenant_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
):
    """
    Get paginated call history with filters.
    
    Query params:
        - page: Page number (1-indexed)
        - page_size: Items per page (max 100)
        - search: Search by phone number
        - status: Filter by call status
        - tenant_id: Filter by tenant
        - from_date: Start date (YYYY-MM-DD)
        - to_date: End date (YYYY-MM-DD)
    """
    try:
        # Build query
        query = db_client.table("calls").select(
            "id, tenant_id, phone_number, campaign_id, status, outcome, duration_seconds, started_at, ended_at, created_at",
            count="exact"
        )
        
        # Apply filters
        if search:
            query = query.ilike("phone_number", f"%{search}%")
        
        if status:
            query = query.eq("status", status)
        
        if tenant_id:
            query = query.eq("tenant_id", tenant_id)
        
        if from_date:
            query = query.gte("created_at", from_date)
        
        if to_date:
            query = query.lte("created_at", to_date + "T23:59:59Z")
        
        # Calculate offset
        offset = (page - 1) * min(page_size, 100)
        
        # Execute with pagination
        response = query.order("created_at", desc=True).range(offset, offset + min(page_size, 100) - 1).execute()
        if getattr(response, "error", None):
            raise RuntimeError(response.error)
        
        total = response.count if response.count else 0
        
        if not response.data:
            return CallHistoryResponse(items=[], page=page, page_size=page_size, total=total)
        
        # Collect tenant and campaign IDs
        tenant_ids = list(set(c.get("tenant_id") for c in response.data if c.get("tenant_id")))
        campaign_ids = list(set(c.get("campaign_id") for c in response.data if c.get("campaign_id")))
        
        # Fetch tenant names
        tenant_map = {}
        if tenant_ids:
            tenants_response = db_client.table("tenants").select("id, business_name").in_("id", tenant_ids).execute()
            if getattr(tenants_response, "error", None):
                raise RuntimeError(tenants_response.error)
            for t in (tenants_response.data or []):
                tenant_map[t["id"]] = t["business_name"]
        
        # Fetch campaign names
        campaign_map = {}
        if campaign_ids:
            campaigns_response = db_client.table("campaigns").select("id, name").in_("id", campaign_ids).execute()
            if getattr(campaigns_response, "error", None):
                raise RuntimeError(campaigns_response.error)
            for c in (campaigns_response.data or []):
                campaign_map[c["id"]] = c["name"]

        # Media/review indicators let an operator find calls that actually
        # need attention without opening every drawer. They are additive and
        # fail soft during a rolling migration where either table may not yet
        # exist on one deployment.
        call_ids = [str(call["id"]) for call in response.data]
        recording_call_ids: set[str] = set()
        feedback_status_by_call: dict[str, str] = {}
        try:
            recordings_response = (
                db_client.table("recordings_s3")
                .select("call_id")
                .in_("call_id", call_ids)
                .eq("status", "uploaded")
                .execute()
            )
            if getattr(recordings_response, "error", None):
                raise RuntimeError(recordings_response.error)
            recording_call_ids = {
                str(row["call_id"])
                for row in (recordings_response.data or [])
                if row.get("call_id")
            }
        except Exception as exc:
            logger.warning("admin call recording indicators unavailable: %s", exc)
        try:
            feedback_response = (
                db_client.table("call_feedback")
                .select("call_id, transcript_status")
                .in_("call_id", call_ids)
                .execute()
            )
            if getattr(feedback_response, "error", None):
                raise RuntimeError(feedback_response.error)
            feedback_status_by_call = {
                str(row["call_id"]): str(row.get("transcript_status") or "pending")
                for row in (feedback_response.data or [])
                if row.get("call_id")
            }
        except Exception as exc:
            logger.warning("admin call feedback indicators unavailable: %s", exc)
        
        # Build items
        items = []
        for call in response.data:
            items.append(CallHistoryItem(
                id=call["id"],
                tenant_id=call.get("tenant_id", ""),
                tenant_name=tenant_map.get(call.get("tenant_id"), "Unknown"),
                phone_number=call.get("phone_number", ""),
                campaign_name=campaign_map.get(call.get("campaign_id")),
                status=call.get("status", "unknown"),
                outcome=call.get("outcome"),
                duration_seconds=call.get("duration_seconds"),
                started_at=call.get("started_at"),
                ended_at=call.get("ended_at"),
                created_at=call.get("created_at", ""),
                has_recording=str(call["id"]) in recording_call_ids,
                has_feedback=str(call["id"]) in feedback_status_by_call,
                feedback_transcript_status=feedback_status_by_call.get(str(call["id"])),
            ))
        
        return CallHistoryResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch call history: {str(e)}"
        )


@router.get("/calls/{call_id}", response_model=AdminCallDetail)
async def get_admin_call_detail(
    call_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get full call details for admin view.
    
    Returns complete call information including:
    - Transcript (text and JSON)
    - Timeline of events
    - Recording URL
    - Cost
    """
    try:
        # Fetch call
        call_response = db_client.table("calls").select("*").eq("id", call_id).single().execute()
        if getattr(call_response, "error", None):
            raise RuntimeError(call_response.error)
        
        if not call_response.data:
            raise HTTPException(status_code=404, detail="Call not found")
        
        call = call_response.data
        
        # Fetch tenant name
        tenant_name = "Unknown"
        if call.get("tenant_id"):
            tenant_response = db_client.table("tenants").select("business_name").eq("id", call["tenant_id"]).single().execute()
            if getattr(tenant_response, "error", None):
                raise RuntimeError(tenant_response.error)
            if tenant_response.data:
                tenant_name = tenant_response.data.get("business_name", "Unknown")
        
        # Fetch campaign name
        campaign_name = None
        if call.get("campaign_id"):
            campaign_response = db_client.table("campaigns").select("name").eq("id", call["campaign_id"]).single().execute()
            if getattr(campaign_response, "error", None):
                raise RuntimeError(campaign_response.error)
            if campaign_response.data:
                campaign_name = campaign_response.data.get("name")
        
        # Build timeline
        timeline = []
        if call.get("created_at"):
            timeline.append(TimelineEvent(
                event="Call Initiated",
                timestamp=call["created_at"],
                status="initiated"
            ))
        if call.get("started_at"):
            timeline.append(TimelineEvent(
                event="Call Started",
                timestamp=call["started_at"],
                status="ringing"
            ))
        if call.get("answered_at"):
            timeline.append(TimelineEvent(
                event="Call Answered",
                timestamp=call["answered_at"],
                status="in_progress"
            ))
        if call.get("ended_at"):
            timeline.append(TimelineEvent(
                event="Call Ended",
                timestamp=call["ended_at"],
                status=call.get("status", "completed")
            ))
        
        # Parse transcript_json if string
        transcript_json = call.get("transcript_json")
        if isinstance(transcript_json, str):
            try:
                transcript_json = json.loads(transcript_json)
            except (json.JSONDecodeError, TypeError):
                transcript_json = None

        summary_json = call.get("summary_json")
        if isinstance(summary_json, str):
            try:
                summary_json = json.loads(summary_json)
            except (json.JSONDecodeError, TypeError):
                summary_json = None
        if not isinstance(summary_json, dict):
            summary_json = None
        
        return AdminCallDetail(
            id=call["id"],
            tenant_id=call.get("tenant_id", ""),
            tenant_name=tenant_name,
            phone_number=call.get("phone_number", ""),
            campaign_id=call.get("campaign_id"),
            campaign_name=campaign_name,
            lead_id=call.get("lead_id"),
            status=call.get("status", "unknown"),
            outcome=call.get("outcome"),
            goal_achieved=call.get("goal_achieved", False),
            started_at=call.get("started_at"),
            answered_at=call.get("answered_at"),
            ended_at=call.get("ended_at"),
            duration_seconds=call.get("duration_seconds"),
            transcript=call.get("transcript"),
            transcript_json=transcript_json,
            summary=call.get("summary"),
            summary_json=summary_json,
            recording_url=call.get("recording_url"),
            cost=float(call["cost"]) if call.get("cost") is not None else None,
            timeline=timeline,
            created_at=call.get("created_at", ""),
            updated_at=call.get("updated_at")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch call detail: {str(e)}"
        )


@router.post("/calls/{call_id}/terminate")
async def terminate_call(
    call_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Best-effort hang up the provider channel, then close the call row.

    The provider action is attempted before the database transition. If the
    channel is already gone (or this API process has no live adapter), the
    stale row is still closed and the response truthfully reports that no
    provider hangup was confirmed.
    """
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            call = await conn.fetchrow(
                """
                SELECT id, tenant_id, external_call_uuid, status, answered_at
                FROM calls
                WHERE id = $1::uuid
                """,
                call_id,
            )

        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        current_status = str(call["status"] or "")
        active_statuses = list(LIVE_CALL_STATUSES)
        if current_status not in active_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Call is not active. Current status: {current_status}",
            )

        provider_hangup_requested = False
        provider_hangup_error: str | None = None
        external_call_id = call["external_call_uuid"]
        if external_call_id:
            try:
                from app.api.v1.endpoints import telephony_bridge

                if telephony_bridge._adapter is not None:
                    await telephony_bridge._adapter.hangup(str(external_call_id))
                    provider_hangup_requested = True
                else:
                    provider_hangup_error = "Telephony adapter is not connected on this worker"
            except Exception as exc:
                provider_hangup_error = str(exc)[:300]
                logger.warning(
                    "admin provider hangup failed call=%s external=%s: %s",
                    call_id,
                    external_call_id,
                    exc,
                )

        async with acquire_with_tenant(db_client.pool, None) as conn:
            updated = await conn.fetchrow(
                """
                UPDATE calls
                   SET status = 'ended',
                       ended_at = COALESCE(ended_at, NOW()),
                       outcome = COALESCE(
                           outcome,
                           CASE WHEN answered_at IS NOT NULL
                                      OR status IN ('answered', 'in_call')
                                THEN $2
                                ELSE $3
                           END
                       ),
                       duration_seconds = COALESCE(
                           duration_seconds,
                           CASE WHEN answered_at IS NOT NULL
                                THEN GREATEST(
                                    0,
                                    EXTRACT(EPOCH FROM (NOW() - answered_at))::int
                                )
                                ELSE 0
                           END
                       ),
                       updated_at = NOW()
                 WHERE id = $1::uuid
                   AND status = ANY($4::text[])
                RETURNING status, outcome, duration_seconds
                """,
                call_id,
                CallOutcome.AGENT_HUNG_UP.value,
                CallOutcome.CANCELLED.value,
                active_statuses,
            )
        if not updated:
            raise HTTPException(status_code=409, detail="Call ended before termination completed")

        try:
            await audit_logger.log(
                event_type=AuditEvent.CONFIG_CHANGED,
                actor_id=admin_user.id,
                actor_type="user",
                tenant_id=call["tenant_id"],
                resource_type="call",
                resource_id=call["id"],
                action="admin_ended_call",
                description="Platform admin ended a live call",
                metadata={
                    "previous_status": current_status,
                    "provider_hangup_requested": provider_hangup_requested,
                    "provider_hangup_error": provider_hangup_error,
                },
            )
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("admin call termination audit failed: %s", exc)

        return {
            "detail": (
                "Provider hangup requested and call closed"
                if provider_hangup_requested
                else "Call row closed; provider channel hangup was not confirmed"
            ),
            "call_id": call_id,
            "previous_status": current_status,
            "new_status": CallState.ENDED.value,
            "provider_hangup_requested": provider_hangup_requested,
            "provider_hangup_error": provider_hangup_error,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("admin call termination failed call=%s", call_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to terminate call: {str(e)}",
        ) from e
