"""
Admin Tenants Endpoints
Tenant management: list, detail, quota, suspend, resume
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class TenantListItem(BaseModel):
    """Enhanced tenant list item with counts and status"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_used: int
    minutes_allocated: int
    status: str  # subscription_status: active, suspended, inactive
    user_count: int
    campaign_count: int
    max_concurrent_calls: int
    created_at: Optional[str] = None


class TenantDetailResponse(BaseModel):
    """Full tenant details response"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_used: int
    minutes_allocated: int
    status: str
    user_count: int
    campaign_count: int
    max_concurrent_calls: int
    calling_rules: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class QuotaUpdateRequest(BaseModel):
    """Request model for updating tenant quota"""
    minutes_allocated: Optional[int] = None
    max_concurrent_calls: Optional[int] = None


class UserResponse(BaseModel):
    """User response model"""
    id: str
    email: str
    role: str
    tenant_id: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/tenants", response_model=List[TenantListItem])
async def list_tenants(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    search: Optional[str] = None,
    status: Optional[str] = None
):
    """
    List all tenants with enhanced data (admin only).
    
    Query params:
        - search: Filter by business name
        - status: Filter by status (active, suspended, inactive)
    """
    try:
        # Fetch tenants with plan info
        query = db_client.table("tenants").select(
            "id, business_name, plan_id, minutes_used, minutes_allocated, "
            "subscription_status, calling_rules, created_at, "
            "plans(name)"
        ).order("business_name")
        
        if search:
            query = query.ilike("business_name", f"%{search}%")
        
        if status:
            query = query.eq("subscription_status", status)
        
        response = query.execute()
        
        tenants = []
        for tenant in response.data or []:
            tenant_id = tenant["id"]
            
            # Get user count
            user_count_resp = db_client.table("user_profiles").select(
                "id", count="exact"
            ).eq("tenant_id", tenant_id).execute()
            user_count = user_count_resp.count or 0
            
            # Get campaign count
            campaign_count_resp = db_client.table("campaigns").select(
                "id", count="exact"
            ).eq("tenant_id", tenant_id).execute()
            campaign_count = campaign_count_resp.count or 0
            
            # Extract max_concurrent_calls from calling_rules
            calling_rules = tenant.get("calling_rules") or {}
            max_concurrent = calling_rules.get("max_concurrent_calls", 10)
            
            # Get plan name
            plan_data = tenant.get("plans") or {}
            plan_name = plan_data.get("name") if plan_data else None
            
            tenants.append(TenantListItem(
                id=tenant_id,
                business_name=tenant["business_name"],
                plan_id=tenant.get("plan_id"),
                plan_name=plan_name,
                minutes_used=tenant.get("minutes_used", 0),
                minutes_allocated=tenant.get("minutes_allocated", 0),
                status=tenant.get("subscription_status", "active"),
                user_count=user_count,
                campaign_count=campaign_count,
                max_concurrent_calls=max_concurrent,
                created_at=tenant.get("created_at")
            ))
        
        return tenants
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tenants: {str(e)}"
        )


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    List all users (admin only).
    
    Used by: /dashboard/admin page (when user.role === 'admin').
    """
    try:
        response = db_client.table("user_profiles").select(
            "id, email, role, tenant_id"
        ).order("email").execute()
        
        users = []
        for user in response.data or []:
            users.append(UserResponse(
                id=user["id"],
                email=user["email"],
                role=user.get("role", "user"),
                tenant_id=user.get("tenant_id")
            ))
        
        return users
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch users: {str(e)}"
        )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
async def get_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get a single tenant by ID with full details (admin only).
    """
    try:
        # Fetch tenant with plan info
        response = db_client.table("tenants").select(
            "*, plans(name)"
        ).eq("id", tenant_id).single().execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        tenant = response.data
        
        # Get user count
        user_count_resp = db_client.table("user_profiles").select(
            "id", count="exact"
        ).eq("tenant_id", tenant_id).execute()
        user_count = user_count_resp.count or 0
        
        # Get campaign count
        campaign_count_resp = db_client.table("campaigns").select(
            "id", count="exact"
        ).eq("tenant_id", tenant_id).execute()
        campaign_count = campaign_count_resp.count or 0
        
        # Extract max_concurrent_calls from calling_rules
        calling_rules = tenant.get("calling_rules") or {}
        max_concurrent = calling_rules.get("max_concurrent_calls", 10)
        
        # Get plan name
        plan_data = tenant.get("plans") or {}
        plan_name = plan_data.get("name") if plan_data else None
        
        return TenantDetailResponse(
            id=tenant["id"],
            business_name=tenant["business_name"],
            plan_id=tenant.get("plan_id"),
            plan_name=plan_name,
            minutes_used=tenant.get("minutes_used", 0),
            minutes_allocated=tenant.get("minutes_allocated", 0),
            status=tenant.get("subscription_status", "active"),
            user_count=user_count,
            campaign_count=campaign_count,
            max_concurrent_calls=max_concurrent,
            calling_rules=calling_rules,
            created_at=tenant.get("created_at"),
            updated_at=tenant.get("updated_at")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tenant: {str(e)}"
        )


@router.patch("/tenants/{tenant_id}/quota")
async def update_tenant_quota(
    tenant_id: str,
    quota: QuotaUpdateRequest,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Update tenant's quota (minutes allocated and/or max concurrent calls).
    """
    try:
        # First get current tenant data
        current = db_client.table("tenants").select(
            "calling_rules"
        ).eq("id", tenant_id).single().execute()
        
        if not current.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        update_data = {}
        
        if quota.minutes_allocated is not None:
            update_data["minutes_allocated"] = quota.minutes_allocated
        
        if quota.max_concurrent_calls is not None:
            # Update calling_rules JSONB
            calling_rules = current.data.get("calling_rules") or {}
            calling_rules["max_concurrent_calls"] = quota.max_concurrent_calls
            update_data["calling_rules"] = calling_rules
        
        if not update_data:
            return {"detail": "No changes provided"}
        
        response = db_client.table("tenants").update(
            update_data
        ).eq("id", tenant_id).execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        return {
            "detail": "Quota updated",
            "minutes_allocated": quota.minutes_allocated,
            "max_concurrent_calls": quota.max_concurrent_calls
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update quota: {str(e)}"
        )


@router.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Suspend a tenant (sets subscription_status to 'suspended').
    Suspended tenants cannot make calls or use the platform.
    """
    try:
        # Check if tenant exists
        check = db_client.table("tenants").select("id, subscription_status").eq("id", tenant_id).single().execute()
        
        if not check.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        if check.data.get("subscription_status") == "suspended":
            return {"detail": "Tenant is already suspended", "status": "suspended"}
        
        response = db_client.table("tenants").update({
            "subscription_status": "suspended"
        }).eq("id", tenant_id).execute()
        
        return {"detail": "Tenant suspended", "status": "suspended"}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to suspend tenant: {str(e)}"
        )


@router.post("/tenants/{tenant_id}/resume")
async def resume_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Resume a suspended tenant (sets subscription_status to 'active').
    """
    try:
        # Check if tenant exists
        check = db_client.table("tenants").select("id, subscription_status").eq("id", tenant_id).single().execute()
        
        if not check.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        if check.data.get("subscription_status") == "active":
            return {"detail": "Tenant is already active", "status": "active"}
        
        response = db_client.table("tenants").update({
            "subscription_status": "active"
        }).eq("id", tenant_id).execute()
        
        return {"detail": "Tenant resumed", "status": "active"}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume tenant: {str(e)}"
        )
