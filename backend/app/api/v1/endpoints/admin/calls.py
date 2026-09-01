"""Admin call monitoring, history, detail, and live-call control."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, List, Literal, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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
from app.domain.services.call_status import CallOutcome
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.telephony.termination import (
    finalize_proven_inbound_termination,
    mark_termination_pending_and_load_context,
    request_confirmed_hangup,
)

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


def _display_caller_ani(call: dict) -> str | None:
    """Return ANI only when the carrier did not mark caller identity private."""
    if call.get("caller_ani_private"):
        return None
    return call.get("caller_ani") or call.get("phone_number")


def _admin_call_money_projection(
    call: Mapping[str, Any],
    usage_rows: list[Mapping[str, Any]],
) -> tuple[float | None, str | None]:
    """Project display money without inventing an inbound currency.

    Outbound rows retain their historical ``calls.cost`` projection. Inbound
    terminal billing is ledger-authoritative: finalized money is the sum of
    the parent settlement and any compensating reversal. A missing or mixed
    currency stays explicit instead of being mislabeled as USD.
    """

    raw_call_cost = call.get("cost")
    outbound_cost = float(raw_call_cost) if raw_call_cost is not None else None
    if str(call.get("direction") or "outbound").strip().lower() != "inbound":
        return outbound_cost, None

    if str(call.get("billing_status") or "").strip().lower() not in {
        "finalized",
        "reversed",
    }:
        return None, None

    amounts: list[tuple[Decimal, str | None]] = []
    for row in usage_rows:
        if row.get("call_leg_id") is not None:
            continue
        if str(row.get("transaction_type") or "").strip().lower() not in {
            "finalize",
            "reverse",
        }:
            continue
        raw_amount = row.get("amount")
        if raw_amount is None:
            continue
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            return None, None
        if not amount.is_finite():
            return None, None
        currency = str(row.get("currency") or "").strip().upper() or None
        if currency is not None and (
            len(currency) != 3 or not currency.isascii() or not currency.isalpha()
        ):
            return None, None
        amounts.append((amount, currency))

    if not amounts:
        return None, None
    currencies = {currency for _, currency in amounts}
    if len(currencies) != 1:
        # Summing unlike/partially-labelled currencies would fabricate a
        # monetary value. The immutable rows remain available to operators.
        return None, None
    total = sum((amount for amount, _ in amounts), Decimal("0"))
    return float(total), next(iter(currencies))


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
    direction: str = "outbound"
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    admission_status: Optional[str] = None
    termination_status: Literal["none", "requested", "confirmed", "failed"] = "none"
    termination_requested_at: Optional[str] = None
    termination_error: Optional[str] = None
    provider_hangup_requested: Optional[bool] = None
    provider_hangup_confirmed: bool = False


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
    direction: str = "outbound"
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    admission_status: Optional[str] = None
    admission_reason: Optional[str] = None
    consent_status: Optional[str] = None
    processing_status: Optional[str] = None
    billing_status: Optional[str] = None
    billing_hold_reason: Optional[str] = None


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
    currency: Optional[str] = None
    timeline: List[TimelineEvent]
    created_at: str
    updated_at: Optional[str] = None
    direction: str = "outbound"
    provider: Optional[str] = None
    provider_call_id: Optional[str] = None
    caller_ani: Optional[str] = None
    called_did: Optional[str] = None
    ingress: Optional[str] = None
    route_version: Optional[int] = None
    config_version: Optional[int] = None
    route_snapshot: Optional[dict] = None
    admission_status: Optional[str] = None
    admission_reason: Optional[str] = None
    consent_status: Optional[str] = None
    processing_status: Optional[str] = None
    billing_status: Optional[str] = None
    billing_hold_reason: Optional[str] = None
    reserved_seconds: Optional[int] = None
    transfer_legs: List[dict] = Field(default_factory=list)


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/calls/live", response_model=List[LiveCallItem])
async def get_live_calls(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
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
        calls_response = (
            db_client.table("calls")
            .select(
                "id, tenant_id, phone_number, status, started_at, campaign_id, "
                "direction, caller_ani, caller_ani_private, called_did, "
                "admission_status, updated_at"
            )
            .in_("status", active_statuses)
            .order("started_at", desc=True)
            .execute()
        )
        if getattr(calls_response, "error", None):
            raise RuntimeError(calls_response.error)

        if not calls_response.data:
            return []

        # Collect tenant IDs and campaign IDs for batch lookup
        tenant_ids = list(
            set(c.get("tenant_id") for c in calls_response.data if c.get("tenant_id"))
        )
        campaign_ids = list(
            set(c.get("campaign_id") for c in calls_response.data if c.get("campaign_id"))
        )

        # Fetch tenant names
        tenant_map = {}
        if tenant_ids:
            tenants_response = (
                db_client.table("tenants")
                .select("id, business_name")
                .in_("id", tenant_ids)
                .execute()
            )
            if getattr(tenants_response, "error", None):
                raise RuntimeError(tenants_response.error)
            for t in tenants_response.data or []:
                tenant_map[t["id"]] = t["business_name"]

        # Fetch campaign names
        campaign_map = {}
        if campaign_ids:
            campaigns_response = (
                db_client.table("campaigns").select("id, name").in_("id", campaign_ids).execute()
            )
            if getattr(campaigns_response, "error", None):
                raise RuntimeError(campaigns_response.error)
            for c in campaigns_response.data or []:
                campaign_map[c["id"]] = c["name"]

        # Calculate duration for active calls
        items = []
        for call in calls_response.data:
            started_at = call.get("started_at")
            duration = _live_duration_seconds(started_at)
            call_status = str(call.get("status") or "unknown")
            termination_requested = call_status == "termination_pending"

            items.append(
                LiveCallItem(
                    id=call["id"],
                    tenant_id=call.get("tenant_id", ""),
                    tenant_name=tenant_map.get(call.get("tenant_id"), "Unknown"),
                    phone_number=call.get("phone_number", ""),
                    campaign_name=campaign_map.get(call.get("campaign_id")),
                    status=call_status,
                    started_at=started_at,
                    duration_seconds=max(0, duration),
                    direction=call.get("direction") or "outbound",
                    caller_ani=_display_caller_ani(call),
                    called_did=call.get("called_did"),
                    admission_status=call.get("admission_status"),
                    termination_status=("requested" if termination_requested else "none"),
                    termination_requested_at=(
                        call.get("updated_at") if termination_requested else None
                    ),
                    termination_error=None,
                    provider_hangup_requested=None,
                    provider_hangup_confirmed=False,
                )
            )

        return items

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch live calls: {str(e)}")


@router.get("/calls/history", response_model=CallHistoryResponse)
async def get_call_history(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    tenant_id: Optional[str] = None,
    direction: Optional[Literal["inbound", "outbound"]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
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
            "id, tenant_id, phone_number, campaign_id, status, outcome, duration_seconds, "
            "started_at, ended_at, created_at, direction, caller_ani, "
            "caller_ani_private, called_did, "
            "admission_status, admission_reason, consent_status, processing_status, "
            "billing_status, billing_hold_reason",
            count="exact",
        )

        # Apply filters
        if search:
            query = query.ilike("phone_number", f"%{search}%")

        if status:
            query = query.eq("status", status)

        if tenant_id:
            query = query.eq("tenant_id", tenant_id)

        if direction:
            query = query.eq("direction", direction)

        if from_date:
            query = query.gte("created_at", from_date)

        if to_date:
            query = query.lte("created_at", to_date + "T23:59:59Z")

        # Calculate offset
        offset = (page - 1) * min(page_size, 100)

        # Execute with pagination
        response = (
            query.order("created_at", desc=True)
            .range(offset, offset + min(page_size, 100) - 1)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(response.error)

        total = response.count if response.count else 0

        if not response.data:
            return CallHistoryResponse(items=[], page=page, page_size=page_size, total=total)

        # Collect tenant and campaign IDs
        tenant_ids = list(set(c.get("tenant_id") for c in response.data if c.get("tenant_id")))
        campaign_ids = list(
            set(c.get("campaign_id") for c in response.data if c.get("campaign_id"))
        )

        # Fetch tenant names
        tenant_map = {}
        if tenant_ids:
            tenants_response = (
                db_client.table("tenants")
                .select("id, business_name")
                .in_("id", tenant_ids)
                .execute()
            )
            if getattr(tenants_response, "error", None):
                raise RuntimeError(tenants_response.error)
            for t in tenants_response.data or []:
                tenant_map[t["id"]] = t["business_name"]

        # Fetch campaign names
        campaign_map = {}
        if campaign_ids:
            campaigns_response = (
                db_client.table("campaigns").select("id, name").in_("id", campaign_ids).execute()
            )
            if getattr(campaigns_response, "error", None):
                raise RuntimeError(campaigns_response.error)
            for c in campaigns_response.data or []:
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
            items.append(
                CallHistoryItem(
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
                    direction=call.get("direction") or "outbound",
                    caller_ani=_display_caller_ani(call),
                    called_did=call.get("called_did"),
                    admission_status=call.get("admission_status"),
                    admission_reason=call.get("admission_reason"),
                    consent_status=call.get("consent_status"),
                    processing_status=call.get("processing_status"),
                    billing_status=call.get("billing_status"),
                    billing_hold_reason=call.get("billing_hold_reason"),
                )
            )

        return CallHistoryResponse(items=items, page=page, page_size=page_size, total=total)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch call history: {str(e)}")


@router.get("/calls/{call_id}", response_model=AdminCallDetail)
async def get_admin_call_detail(
    call_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
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

        usage_rows: list[dict] = []
        if str(call.get("direction") or "outbound").strip().lower() == "inbound" and str(
            call.get("billing_status") or ""
        ).strip().lower() in {"finalized", "reversed"}:
            usage_response = (
                db_client.table("inbound_usage_transactions")
                .select("call_leg_id, transaction_type, amount, currency")
                .eq("tenant_id", call.get("tenant_id"))
                .eq("call_id", call_id)
                .execute()
            )
            if getattr(usage_response, "error", None):
                raise RuntimeError(usage_response.error)
            usage_rows = [dict(row) for row in usage_response.data or []]
        projected_cost, projected_currency = _admin_call_money_projection(
            call,
            usage_rows,
        )

        # Fetch tenant name
        tenant_name = "Unknown"
        if call.get("tenant_id"):
            tenant_response = (
                db_client.table("tenants")
                .select("business_name")
                .eq("id", call["tenant_id"])
                .single()
                .execute()
            )
            if getattr(tenant_response, "error", None):
                raise RuntimeError(tenant_response.error)
            if tenant_response.data:
                tenant_name = tenant_response.data.get("business_name", "Unknown")

        # Fetch campaign name
        campaign_name = None
        if call.get("campaign_id"):
            campaign_response = (
                db_client.table("campaigns")
                .select("name")
                .eq("id", call["campaign_id"])
                .single()
                .execute()
            )
            if getattr(campaign_response, "error", None):
                raise RuntimeError(campaign_response.error)
            if campaign_response.data:
                campaign_name = campaign_response.data.get("name")

        # Build timeline
        timeline = []
        if call.get("created_at"):
            timeline.append(
                TimelineEvent(
                    event="Call Initiated", timestamp=call["created_at"], status="initiated"
                )
            )
        if call.get("started_at"):
            timeline.append(
                TimelineEvent(event="Call Started", timestamp=call["started_at"], status="ringing")
            )
        if call.get("answered_at"):
            timeline.append(
                TimelineEvent(
                    event="Call Answered", timestamp=call["answered_at"], status="in_progress"
                )
            )
        if call.get("ended_at"):
            timeline.append(
                TimelineEvent(
                    event="Call Ended",
                    timestamp=call["ended_at"],
                    status=call.get("status", "completed"),
                )
            )

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

        legs_response = (
            db_client.table("call_legs")
            .select(
                "id, leg_type, direction, provider, provider_leg_id, "
                "from_number, to_number, status, started_at, answered_at, "
                "ended_at, duration_seconds, metadata"
            )
            .eq("call_id", call_id)
            .eq("leg_type", "transfer")
            .order("created_at", desc=False)
            .execute()
        )
        if getattr(legs_response, "error", None):
            raise RuntimeError(legs_response.error)
        transfer_legs = []
        for raw_leg in legs_response.data or []:
            leg = dict(raw_leg)
            metadata = leg.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            leg["metadata"] = metadata if isinstance(metadata, dict) else {}
            transfer_legs.append(leg)

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
            cost=projected_cost,
            currency=projected_currency,
            timeline=timeline,
            created_at=call.get("created_at", ""),
            updated_at=call.get("updated_at"),
            direction=call.get("direction") or "outbound",
            provider=call.get("provider"),
            provider_call_id=call.get("provider_call_id"),
            caller_ani=_display_caller_ani(call),
            called_did=call.get("called_did"),
            ingress=call.get("ingress"),
            route_version=call.get("route_version"),
            config_version=call.get("config_version"),
            route_snapshot=(
                call.get("route_snapshot") if isinstance(call.get("route_snapshot"), dict) else None
            ),
            admission_status=call.get("admission_status"),
            admission_reason=call.get("admission_reason"),
            consent_status=call.get("consent_status"),
            processing_status=call.get("processing_status"),
            billing_status=call.get("billing_status"),
            billing_hold_reason=call.get("billing_hold_reason"),
            reserved_seconds=call.get("reserved_seconds"),
            transfer_legs=transfer_legs,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch call detail: {str(e)}")


@router.post("/calls/{call_id}/terminate")
async def terminate_call(
    call_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Terminate a live call only after provider/PBX absence is proven."""
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            call = await conn.fetchrow(
                """
                SELECT id, tenant_id, external_call_uuid, provider_call_id,
                       provider, direction, status, answered_at, campaign_id
                FROM calls
                WHERE id = $1::uuid
                """,
                call_id,
            )

        if not call:
            raise HTTPException(status_code=404, detail="Call not found")

        current_status = str(call["status"] or "")
        from app.domain.services.call_status import TERMINAL_CALL_STATUSES

        terminal_statuses = TERMINAL_CALL_STATUSES
        active_statuses = list(LIVE_CALL_STATUSES)
        if current_status not in terminal_statuses and current_status not in active_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Call is not active. Current status: {current_status}",
            )

        external_call_id = call["provider_call_id"] or call["external_call_uuid"]
        from app.api.v1.endpoints import telephony_bridge

        try:
            termination_context = await mark_termination_pending_and_load_context(
                db_client.pool,
                call_reference=call_id,
                tenant_id=None,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed at DB boundary
            logger.error(
                "admin_call_termination_leg_lookup_failed call=%s err_type=%s",
                call_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "termination_context_unavailable",
                    "call_id": call_id,
                    "call_status": current_status,
                    "termination_status": "failed",
                    "provider_hangup_requested": False,
                    "provider_hangup_confirmed": False,
                    "provider_hangup_error": "linked_leg_lookup_failed",
                },
            ) from exc

        proof = await request_confirmed_hangup(
            telephony_bridge._adapter,
            str(termination_context.provider_call_id or external_call_id or ""),
            provider_leg_ids=termination_context.provider_leg_ids,
        )
        if not proof.confirmed:
            retry_is_durable = current_status not in terminal_statuses
            try:
                await audit_logger.log(
                    event_type=AuditEvent.CONFIG_CHANGED,
                    actor_id=admin_user.id,
                    actor_type="user",
                    tenant_id=call["tenant_id"],
                    resource_type="call",
                    resource_id=call["id"],
                    action="admin_call_termination_unconfirmed",
                    description="Platform admin termination could not be confirmed",
                    metadata={
                        "previous_status": current_status,
                        "provider_hangup_requested": proof.requested,
                        "provider_hangup_confirmed": False,
                        "reason": proof.code,
                    },
                )
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("admin termination failure audit failed: %s", exc)
            raise HTTPException(
                status_code=(
                    504 if proof.code in {"confirmation_timeout", "hangup_unconfirmed"} else 503
                ),
                detail={
                    "error": "termination_unconfirmed",
                    "reason": proof.code,
                    "call_id": call_id,
                    "call_status": ("termination_pending" if retry_is_durable else current_status),
                    "termination_status": ("requested" if retry_is_durable else "failed"),
                    "provider_hangup_requested": proof.requested,
                    "provider_hangup_confirmed": False,
                    "provider_hangup_error": proof.error or proof.code,
                },
            )

        # Replay the shared idempotent finalizer even for a terminal parent:
        # persisted transfer legs and global capacity can remain active after a
        # crash despite the parent row's terminal-looking label.
        settlement_deferred = False
        if call["direction"] == "inbound" and termination_context.provider_call_id:
            try:
                from app.core.container import get_container

                try:
                    redis_client = getattr(get_container(), "redis", None)
                except Exception:
                    redis_client = None
                await finalize_proven_inbound_termination(
                    db_client.pool,
                    provider_call_id=termination_context.provider_call_id,
                    durable_call_id=call_id,
                    tenant_id=str(call["tenant_id"]),
                    provider=str(call["provider"] or "asterisk"),
                    terminal_status="ended",
                    reason="admin_operator_hangup",
                    redis_client=redis_client,
                    campaign_id=(str(call["campaign_id"]) if call.get("campaign_id") else None),
                )
            except Exception as exc:  # noqa: BLE001 - durable watchdog retries
                settlement_deferred = True
                logger.error(
                    "admin confirmed termination settlement deferred call=%s: %s",
                    call_id,
                    exc,
                )

        # A terminal database row alone cannot prove that every PBX leg is
        # absent. It is idempotent only after proof plus shared settlement.
        if current_status in terminal_statuses:
            return {
                "status": "already_terminal",
                "detail": "Call was already terminal and PBX absence was confirmed",
                "call_id": call_id,
                "previous_status": current_status,
                "new_status": current_status,
                "call_status": current_status,
                "termination_status": "confirmed",
                "provider_hangup_requested": proof.requested,
                "provider_hangup_confirmed": True,
                "provider_hangup_error": None,
                "settlement_deferred": settlement_deferred,
            }

        if settlement_deferred:
            try:
                await audit_logger.log(
                    event_type=AuditEvent.CONFIG_CHANGED,
                    actor_id=admin_user.id,
                    actor_type="user",
                    tenant_id=call["tenant_id"],
                    resource_type="call",
                    resource_id=call["id"],
                    action="admin_call_termination_settlement_deferred",
                    description=("PBX absence was confirmed; logical settlement is retrying"),
                    metadata={
                        "previous_status": current_status,
                        "provider_hangup_confirmed": True,
                    },
                )
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("admin deferred-settlement audit failed: %s", exc)
            return {
                "status": "confirmed",
                "detail": "Provider confirmed; call settlement is retrying",
                "call_id": call_id,
                "previous_status": current_status,
                "new_status": "termination_pending",
                "call_status": "termination_pending",
                "termination_status": "confirmed",
                "provider_hangup_requested": proof.requested,
                "provider_hangup_confirmed": True,
                "provider_hangup_error": None,
                "settlement_deferred": True,
            }

        latest_status = None
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
            async with acquire_with_tenant(db_client.pool, None) as conn:
                latest_status = await conn.fetchval(
                    "SELECT status FROM calls WHERE id = $1::uuid",
                    call_id,
                )
            if str(latest_status or "") not in terminal_statuses:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "termination_state_conflict", "call_id": call_id},
                )

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
                    "provider_hangup_requested": proof.requested,
                    "provider_hangup_confirmed": True,
                    "settlement_deferred": settlement_deferred,
                },
            )
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("admin call termination audit failed: %s", exc)

        return {
            "status": "confirmed",
            "detail": "Provider/PBX termination confirmed and call closed",
            "call_id": call_id,
            "previous_status": current_status,
            "new_status": str(updated["status"] if updated else latest_status),
            "call_status": str(updated["status"] if updated else latest_status),
            "termination_status": "confirmed",
            "provider_hangup_requested": proof.requested,
            "provider_hangup_confirmed": True,
            "provider_hangup_error": None,
            "settlement_deferred": settlement_deferred,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("admin call termination failed call=%s", call_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to terminate call: {str(e)}",
        ) from e
