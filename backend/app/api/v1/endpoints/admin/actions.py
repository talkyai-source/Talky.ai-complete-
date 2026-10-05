"""
Admin Actions Endpoints
Assistant action log: list, detail, retry, cancel
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import json
from uuid import UUID
from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from app.core.security.rbac import UserRole, normalize_role
from app.services.voice_callback_service import callback_job_id

from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser
from ._serialization import AdminResponseModel

router = APIRouter()


# These historical audit types have no generic replay consumer. A new pending
# row is not queue admission, and an uncertain effect must never be replayed.
RETRYABLE_ACTION_TYPES = frozenset()


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _admin_tenant(user):
    """Broad Admin roles do not grant platform-wide receipt access."""
    if normalize_role(getattr(user, "role", None)) == UserRole.PLATFORM_ADMIN:
        return None
    tenant = getattr(user, "tenant_id", None)
    if not tenant:
        raise HTTPException(status_code=403, detail="Tenant context required for action receipts")
    try:
        return str(UUID(str(tenant)))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid tenant context") from exc


def _is_callback(row):
    return row.get("type") == "schedule_callback" and row.get("triggered_by") == "voice"


def _cancellation_owned(row):
    """Only an existing claim/outbox can prove that cancellation stops work."""
    if row.get("status") == "pending":
        # Legacy email/SMS pending rows are audits, not execution claims: a
        # provider request may already be in progress while they remain pending.
        return bool(row.get("idempotency_key") and _object(row.get("input_data")).get("request_hash"))
    return (row.get("status") == "scheduled" and _is_callback(row)
            and not _object(row.get("output_data")).get("dialer_job_id"))


async def _callback_reserved(conn, row):
    if not _is_callback(row):
        return False
    return bool(await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM dialer_jobs WHERE tenant_id=$1::uuid AND id=$2::uuid)",
        str(row["tenant_id"]), callback_job_id(row["id"]),
    ))


# =============================================================================
# Response Models
# =============================================================================

class ActionItem(AdminResponseModel):
    """Action list item for table display"""
    id: str
    tenant_id: str
    tenant_name: str
    type: str
    status: str
    outcome_status: Optional[str] = None
    triggered_by: Optional[str] = None
    lead_name: Optional[str] = None
    lead_phone: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None


class ActionListResponse(BaseModel):
    """Paginated action list response"""
    items: List[ActionItem]
    total: int
    page: int
    page_size: int


class ActionDetail(AdminResponseModel):
    """Full action detail with payload"""
    id: str
    tenant_id: str
    tenant_name: str
    type: str
    status: str
    outcome_status: Optional[str] = None
    triggered_by: Optional[str] = None
    
    # Related entities
    conversation_id: Optional[str] = None
    call_id: Optional[str] = None
    lead_id: Optional[str] = None
    lead_name: Optional[str] = None
    lead_phone: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    connector_id: Optional[str] = None
    connector_name: Optional[str] = None
    
    # Payload
    input_data: Optional[dict] = None
    output_data: Optional[dict] = None
    error: Optional[str] = None
    
    # Audit
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    
    # Timing
    scheduled_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: str
    
    # Flags
    is_retryable: bool = False
    is_cancellable: bool = False


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/actions", response_model=ActionListResponse)
async def get_admin_actions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    type: Optional[str] = Query(None, description="Filter by action type"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search by lead phone"),
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    List all assistant actions with pagination and filters.
    Platform admins may view all tenants; tenant and partner admins stay scoped.
    """
    try:
        scope = _admin_tenant(admin_user)
        if scope and tenant_id and str(tenant_id) != scope:
            raise HTTPException(status_code=403, detail="Cannot access another tenant's action receipts")
        offset = (page - 1) * page_size
        
        # Build query with joins
        query = db_client.table("assistant_actions").select(
            "*, tenants!inner(business_name), leads(first_name, last_name, phone_number)",
            count="exact"
        ).order("created_at", desc=True)
        
        # Apply filters
        if status:
            query = query.eq("status", status)
        if type:
            query = query.eq("type", type)
        if scope or tenant_id:
            query = query.eq("tenant_id", scope or tenant_id)
        if from_date:
            query = query.gte("created_at", f"{from_date}T00:00:00")
        if to_date:
            query = query.lte("created_at", f"{to_date}T23:59:59")
        if search:
            # Search by lead phone - need to filter on lead relation
            query = query.ilike("leads.phone_number", f"%{search}%")
        
        # Pagination
        query = query.range(offset, offset + page_size - 1)
        
        response = query.execute()
        
        items = []
        for action in response.data or []:
            tenant = action.get("tenants", {})
            lead = action.get("leads") or {}
            
            lead_name = None
            if lead.get("first_name") or lead.get("last_name"):
                lead_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            
            items.append(ActionItem(
                id=action["id"],
                tenant_id=action["tenant_id"],
                tenant_name=tenant.get("business_name", "Unknown"),
                type=action["type"],
                status=action["status"],
                outcome_status=action.get("outcome_status"),
                triggered_by=action.get("triggered_by"),
                lead_name=lead_name,
                lead_phone=lead.get("phone_number"),
                error=action.get("error"),
                created_at=action["created_at"],
                started_at=action.get("started_at"),
                completed_at=action.get("completed_at"),
                duration_ms=action.get("duration_ms")
            ))
        
        return ActionListResponse(
            items=items,
            total=response.count or 0,
            page=page,
            page_size=page_size
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch actions: {str(e)}"
        )


@router.get("/actions/{action_id}", response_model=ActionDetail)
async def get_admin_action_detail(
    action_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get full action detail including input/output payloads.
    """
    try:
        scope = _admin_tenant(admin_user)
        # Fetch action with all relations
        query = db_client.table("assistant_actions").select(
            "*, tenants!inner(business_name), leads(first_name, last_name, phone_number), "
            "campaigns(name), connectors(name)"
        ).eq("id", action_id)
        if scope:
            query = query.eq("tenant_id", scope)
        response = query.single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Action not found")
        
        action = response.data
        tenant = action.get("tenants", {})
        lead = action.get("leads") or {}
        campaign = action.get("campaigns") or {}
        connector = action.get("connectors") or {}
        
        lead_name = None
        if lead.get("first_name") or lead.get("last_name"):
            lead_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
        
        # Determine if action is retryable/cancellable
        is_retryable = (
            action["status"] == "failed" and 
            action["type"] in RETRYABLE_ACTION_TYPES
        )
        is_cancellable = _cancellation_owned(action)
        if is_cancellable and _is_callback(action):
            async with acquire_with_tenant(db_client.pool, str(action["tenant_id"])) as conn:
                is_cancellable = not await _callback_reserved(conn, action)
        
        return ActionDetail(
            id=action["id"],
            tenant_id=action["tenant_id"],
            tenant_name=tenant.get("business_name", "Unknown"),
            type=action["type"],
            status=action["status"],
            outcome_status=action.get("outcome_status"),
            triggered_by=action.get("triggered_by"),
            conversation_id=action.get("conversation_id"),
            call_id=action.get("call_id"),
            lead_id=action.get("lead_id"),
            lead_name=lead_name,
            lead_phone=lead.get("phone_number"),
            campaign_id=action.get("campaign_id"),
            campaign_name=campaign.get("name"),
            connector_id=action.get("connector_id"),
            connector_name=connector.get("name"),
            input_data=action.get("input_data"),
            output_data=action.get("output_data"),
            error=action.get("error"),
            ip_address=str(action["ip_address"]) if action.get("ip_address") else None,
            user_agent=action.get("user_agent"),
            request_id=action.get("request_id"),
            idempotency_key=action.get("idempotency_key"),
            scheduled_at=action.get("scheduled_at"),
            started_at=action.get("started_at"),
            completed_at=action.get("completed_at"),
            duration_ms=action.get("duration_ms"),
            created_at=action["created_at"],
            is_retryable=is_retryable,
            is_cancellable=is_cancellable
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch action detail: {str(e)}"
        )


@router.post("/actions/{action_id}/retry")
async def retry_action(
    action_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """Retain the receipt; no generic worker supports replaying audit rows."""
    try:
        scope = _admin_tenant(admin_user)
        # Fetch original action
        query = db_client.table("assistant_actions").select("*").eq("id", action_id)
        if scope:
            query = query.eq("tenant_id", scope)
        response = query.single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Action not found")
        
        original = response.data
        
        uncertain = original["status"] in {"pending", "scheduled", "running", "unknown"}
        raise HTTPException(status_code=501, detail={
            "code": "action_retry_unavailable", "action_id": str(original["id"]),
            "status": original["status"], "retryable": False,
            "message": "No action was queued. This receipt has no supported automatic replay route.",
            "next_step": ("Keep this action held. Review the original account and provider receipt; do not resend an uncertain effect."
                          if uncertain else
                          "Review the original receipt first. Only after non-execution is established may an authorized user create a fresh preview and confirm it through the existing assistant."),
        })
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retry action: {str(e)}"
        )


@router.post("/actions/{action_id}/cancel")
async def cancel_action(
    action_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """Cancel only before the owned executor/outbox has reserved dispatch."""
    try:
        scope = _admin_tenant(admin_user)
        action_id = str(UUID(action_id))
        # Only platform admins use the existing bypass; all other accepted
        # Admin roles stay tenant scoped. This lock is shared with claim and
        # callback preparation; updates also pin the row's authoritative tenant.
        async with asyncio.timeout(5):
            async with acquire_with_tenant(db_client.pool, scope) as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM assistant_actions WHERE id=$1::uuid "
                    "AND ($2::uuid IS NULL OR tenant_id=$2::uuid) FOR UPDATE", action_id, scope,
                )
                if row is None:
                    raise HTTPException(status_code=404, detail="Action not found")
                if not _cancellation_owned(row) or await _callback_reserved(conn, row):
                    raise HTTPException(status_code=409, detail={
                        "code": "action_not_cancellable", "action_id": action_id,
                        "status": row["status"], "retryable": False,
                        "message": "Cancellation is not confirmed: execution is already reserved or this audit row does not own a cancellable action. Review the saved receipt; do not resend.",
                    })
                result = await conn.fetchval(
                    """UPDATE assistant_actions SET status='cancelled',outcome_status='cancelled_by_admin',
                        completed_at=NOW(),output_data=COALESCE(output_data,'{}'::jsonb) || $4::jsonb
                        WHERE id=$1::uuid AND tenant_id=$2::uuid AND status=$3 RETURNING id""",
                    action_id, str(row["tenant_id"]), row["status"], json.dumps({
                        "success": False, "status": "cancelled", "confirmation_allowed": False,
                        "message": "Cancelled before dispatch was reserved.",
                        "cancelled_by_user_id": str(admin_user.id),
                    }),
                )
                if result is None:
                    raise RuntimeError("Cancellation was not persisted")
        return {"detail": "Cancelled before dispatch was reserved", "action_id": action_id,
                "previous_status": row["status"], "new_status": "cancelled"}
    
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid action ID") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Cancellation could not be confirmed. Reload the saved receipt before another action.") from exc
