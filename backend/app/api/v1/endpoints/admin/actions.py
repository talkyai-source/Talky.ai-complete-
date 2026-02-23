"""
Admin Actions Endpoints
Assistant action log: list, detail, retry, cancel
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter()


# Safe actions that can be retried
RETRYABLE_ACTION_TYPES = {"send_email", "send_sms", "set_reminder"}


# =============================================================================
# Response Models
# =============================================================================

class ActionItem(BaseModel):
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


class ActionDetail(BaseModel):
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
    Admin-only endpoint that can view all tenants.
    """
    try:
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
        if tenant_id:
            query = query.eq("tenant_id", tenant_id)
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
        # Fetch action with all relations
        response = db_client.table("assistant_actions").select(
            "*, tenants!inner(business_name), leads(first_name, last_name, phone_number), "
            "campaigns(name), connectors(name)"
        ).eq("id", action_id).single().execute()
        
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
        is_cancellable = action["status"] in ("pending", "scheduled")
        
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
    """
    Retry a failed action. Only allowed for safe/idempotent action types.
    Creates a new action with the same parameters.
    """
    try:
        # Fetch original action
        response = db_client.table("assistant_actions").select("*").eq("id", action_id).single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Action not found")
        
        original = response.data
        
        # Validate action can be retried
        if original["status"] != "failed":
            raise HTTPException(
                status_code=400,
                detail=f"Only failed actions can be retried. Current status: {original['status']}"
            )
        
        if original["type"] not in RETRYABLE_ACTION_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Action type '{original['type']}' is not safe to retry. Only {list(RETRYABLE_ACTION_TYPES)} can be retried."
            )
        
        # Create new action with same parameters
        from uuid import uuid4
        now = datetime.utcnow().isoformat()
        
        new_action = {
            "id": str(uuid4()),
            "tenant_id": original["tenant_id"],
            "conversation_id": original.get("conversation_id"),
            "user_id": original.get("user_id"),
            "call_id": original.get("call_id"),
            "lead_id": original.get("lead_id"),
            "campaign_id": original.get("campaign_id"),
            "connector_id": original.get("connector_id"),
            "type": original["type"],
            "status": "pending",
            "input_data": original.get("input_data"),
            "triggered_by": "admin_retry",
            "created_at": now
        }
        
        insert_response = db_client.table("assistant_actions").insert(new_action).execute()
        
        if not insert_response.data:
            raise HTTPException(status_code=500, detail="Failed to create retry action")
        
        return {
            "detail": "Action queued for retry",
            "original_action_id": action_id,
            "new_action_id": new_action["id"],
            "status": "pending"
        }
    
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
    """
    Cancel a pending action.
    """
    try:
        # Fetch action
        response = db_client.table("assistant_actions").select("status").eq("id", action_id).single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Action not found")
        
        current_status = response.data["status"]
        
        # Validate action can be cancelled
        if current_status not in ("pending", "scheduled"):
            raise HTTPException(
                status_code=400,
                detail=f"Only pending or scheduled actions can be cancelled. Current status: {current_status}"
            )
        
        # Update status to cancelled
        now = datetime.utcnow().isoformat()
        update_response = db_client.table("assistant_actions").update({
            "status": "cancelled",
            "outcome_status": "cancelled_by_admin",
            "completed_at": now
        }).eq("id", action_id).execute()
        
        if not update_response.data:
            raise HTTPException(status_code=500, detail="Failed to cancel action")
        
        return {
            "detail": "Action cancelled successfully",
            "action_id": action_id,
            "previous_status": current_status,
            "new_status": "cancelled"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel action: {str(e)}"
        )
