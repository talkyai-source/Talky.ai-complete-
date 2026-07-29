"""
Admin Rate Limits Management
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, require_admin_tenant, CurrentUser

# TENANT-SCOPED router. `tenant_telephony_threshold_policies.tenant_id` is NOT
# NULL and every row belongs to exactly one customer, so a tenant_admin
# managing their OWN telephony thresholds is a legitimate customer feature —
# this stays at admin level rather than platform_admin. The isolation comes
# from filtering every query by the caller's tenant (RLS is BYPASSRLS in prod,
# so this app-level filter is the only defence).
router = APIRouter(prefix="/admin", tags=["Admin Rate Limits"])
logger = logging.getLogger(__name__)


class RateLimitRule(BaseModel):
    id: str
    name: str
    calls_per_minute: int
    calls_per_hour: int
    calls_per_day: int
    active: bool = True
    created_at: Optional[str] = None


class CreateRateLimitRequest(BaseModel):
    name: str = Field(..., min_length=1)
    calls_per_minute: int = Field(default=60, ge=1)
    calls_per_hour: int = Field(default=1000, ge=1)
    calls_per_day: int = Field(default=10000, ge=1)
    active: bool = True


class UpdateRateLimitRequest(BaseModel):
    active: bool


@router.get("/rate-limits", response_model=List[RateLimitRule])
async def list_rate_limits(
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """List rate limit rules for the caller's own tenant."""
    try:
        result = (
            db_client.table("tenant_telephony_threshold_policies")
            .select("*")
            .eq("tenant_id", current_user.tenant_id)
            .execute()
        )
        data = result.data or []
        return [
            RateLimitRule(
                id=str(row.get("id")),
                name=row.get("name") or row.get("policy_name") or "Unnamed",
                calls_per_minute=row.get("calls_per_minute", 60),
                calls_per_hour=row.get("calls_per_hour", 1000),
                calls_per_day=row.get("calls_per_day", 10000),
                active=row.get("active", True),
                created_at=str(row.get("created_at")) if row.get("created_at") else None,
            )
            for row in data
        ]
    except Exception as e:
        logger.error(f"Failed to list rate limits: {e}")
        return [
            RateLimitRule(id="default-1", name="Default Policy", calls_per_minute=60, calls_per_hour=1000, calls_per_day=10000, active=True),
        ]


@router.post("/rate-limits", response_model=RateLimitRule)
async def create_rate_limit(
    request: CreateRateLimitRequest,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """Create a rate limit rule owned by the caller's tenant."""
    try:
        import uuid
        payload = {
            "id": str(uuid.uuid4()),
            "tenant_id": current_user.tenant_id,
            "name": request.name,
            "calls_per_minute": request.calls_per_minute,
            "calls_per_hour": request.calls_per_hour,
            "calls_per_day": request.calls_per_day,
            "active": request.active,
            "created_at": datetime.utcnow().isoformat(),
        }
        result = db_client.table("tenant_telephony_threshold_policies").insert(payload).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=500, detail="Failed to create rate limit")
        row = result.data[0]
        return RateLimitRule(
            id=str(row.get("id")),
            name=row.get("name") or row.get("policy_name") or "Unnamed",
            calls_per_minute=row.get("calls_per_minute", 60),
            calls_per_hour=row.get("calls_per_hour", 1000),
            calls_per_day=row.get("calls_per_day", 10000),
            active=row.get("active", True),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create rate limit: {e}")
        raise HTTPException(status_code=500, detail="Failed to create rate limit")


@router.patch("/rate-limits/{rule_id}", response_model=RateLimitRule)
async def update_rate_limit(
    rule_id: str,
    request: UpdateRateLimitRequest,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """Update rate limit rule status (only within the caller's tenant)."""
    try:
        # tenant_id is part of the WHERE clause, so another tenant's rule_id
        # matches zero rows and surfaces as 404 — never a cross-tenant write.
        result = db_client.table("tenant_telephony_threshold_policies").update({
            "active": request.active,
        }).eq("id", rule_id).eq("tenant_id", current_user.tenant_id).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=404, detail="Rate limit rule not found")
        row = result.data[0]
        return RateLimitRule(
            id=str(row.get("id")),
            name=row.get("name") or row.get("policy_name") or "Unnamed",
            calls_per_minute=row.get("calls_per_minute", 60),
            calls_per_hour=row.get("calls_per_hour", 1000),
            calls_per_day=row.get("calls_per_day", 10000),
            active=row.get("active", True),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update rate limit: {e}")
        raise HTTPException(status_code=500, detail="Failed to update rate limit")
