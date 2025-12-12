"""
Admin Endpoints
Administrative operations for managing tenants and users
Requires admin role
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client

from app.api.v1.dependencies import get_supabase, require_admin, CurrentUser

router = APIRouter(prefix="/admin", tags=["admin"])


class TenantResponse(BaseModel):
    """Tenant response model"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    minutes_used: int
    minutes_allocated: int


class UserResponse(BaseModel):
    """User response model"""
    id: str
    email: str
    role: str
    tenant_id: Optional[str] = None


@router.get("/tenants", response_model=List[TenantResponse])
async def list_tenants(
    admin_user: CurrentUser = Depends(require_admin),
    supabase: Client = Depends(get_supabase)
):
    """
    List all tenants (admin only).
    
    Used by: /dashboard/admin page (when user.role === 'admin').
    """
    try:
        response = supabase.table("tenants").select(
            "id, business_name, plan_id, minutes_used, minutes_allocated"
        ).order("business_name").execute()
        
        tenants = []
        for tenant in response.data or []:
            tenants.append(TenantResponse(
                id=tenant["id"],
                business_name=tenant["business_name"],
                plan_id=tenant.get("plan_id"),
                minutes_used=tenant.get("minutes_used", 0),
                minutes_allocated=tenant.get("minutes_allocated", 0)
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
    supabase: Client = Depends(get_supabase)
):
    """
    List all users (admin only).
    
    Used by: /dashboard/admin page (when user.role === 'admin').
    """
    try:
        response = supabase.table("user_profiles").select(
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


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    supabase: Client = Depends(get_supabase)
):
    """
    Get a single tenant by ID (admin only).
    """
    try:
        response = supabase.table("tenants").select("*").eq("id", tenant_id).single().execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        tenant = response.data
        
        return TenantResponse(
            id=tenant["id"],
            business_name=tenant["business_name"],
            plan_id=tenant.get("plan_id"),
            minutes_used=tenant.get("minutes_used", 0),
            minutes_allocated=tenant.get("minutes_allocated", 0)
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tenant: {str(e)}"
        )


@router.patch("/tenants/{tenant_id}/minutes")
async def update_tenant_minutes(
    tenant_id: str,
    minutes_allocated: int,
    admin_user: CurrentUser = Depends(require_admin),
    supabase: Client = Depends(get_supabase)
):
    """
    Update tenant's allocated minutes (admin only).
    """
    try:
        response = supabase.table("tenants").update({
            "minutes_allocated": minutes_allocated
        }).eq("id", tenant_id).execute()
        
        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )
        
        return {"detail": "Minutes updated", "minutes_allocated": minutes_allocated}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update tenant: {str(e)}"
        )
