"""
Admin Blocked Entities Management
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, require_admin_tenant, CurrentUser

# TENANT-SCOPED router over `dnc_entries`. A customer managing their own
# do-not-call/blocked list is a core self-service (and compliance) feature, so
# this stays at admin level and is filtered to the caller's tenant.
#
# Note on GLOBAL entries: dnc_entries.tenant_id is NULLABLE, where NULL means a
# platform-wide entry. Those are deliberately NOT returned or deletable here —
# they are managed by the platform operator (see /admin/dnc in call_limits.py,
# now platform_admin-only). Scoping strictly to `tenant_id = caller` means a
# tenant can never delete a global suppression, which would be a compliance
# breach, and the query builder has no OR support to safely widen the read.
router = APIRouter(prefix="/admin", tags=["Admin Blocked Entities"])
logger = logging.getLogger(__name__)


class BlockedEntity(BaseModel):
    id: str
    entity_type: str
    value: str
    reason: Optional[str] = None
    created_at: Optional[str] = None


class CreateBlockedEntityRequest(BaseModel):
    entity_type: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    reason: Optional[str] = None


@router.get("/blocked-entities", response_model=List[BlockedEntity])
async def list_blocked_entities(
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """List the caller tenant's own blocked entities (via DNC entries)."""
    try:
        result = (
            db_client.table("dnc_entries")
            .select("*")
            .eq("tenant_id", current_user.tenant_id)
            .limit(200)
            .execute()
        )
        data = result.data or []
        return [
            BlockedEntity(
                id=str(row.get("id")),
                entity_type="phone_number",
                value=row.get("normalized_number") or row.get("phone_number"),
                reason=row.get("reason"),
                created_at=str(row.get("created_at")) if row.get("created_at") else None,
            )
            for row in data
        ]
    except Exception as e:
        logger.error(f"Failed to list blocked entities: {e}")
        return []


@router.post("/blocked-entities", response_model=BlockedEntity)
async def block_entity(
    request: CreateBlockedEntityRequest,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """Block a new entity for the caller's tenant."""
    try:
        import uuid
        payload = {
            "id": str(uuid.uuid4()),
            "tenant_id": current_user.tenant_id,
            "phone_number": request.value,
            "normalized_number": request.value,
            "reason": request.reason,
            "source": "manual",
            "created_at": datetime.utcnow().isoformat(),
        }
        result = db_client.table("dnc_entries").insert(payload).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=500, detail="Failed to block entity")
        row = result.data[0]
        return BlockedEntity(
            id=str(row.get("id")),
            entity_type=request.entity_type,
            value=row.get("normalized_number") or row.get("phone_number"),
            reason=row.get("reason"),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to block entity: {e}")
        raise HTTPException(status_code=500, detail="Failed to block entity")


@router.delete("/blocked-entities/{entity_id}")
async def unblock_entity(
    entity_id: str,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """Unblock an entity belonging to the caller's tenant."""
    try:
        # Tenant-scoped delete: another tenant's (or a global, tenant_id NULL)
        # entry matches zero rows and returns 404 instead of being removed.
        result = (
            db_client.table("dnc_entries")
            .delete()
            .eq("id", entity_id)
            .eq("tenant_id", current_user.tenant_id)
            .execute()
        )
        if result.error or not result.data:
            raise HTTPException(status_code=404, detail="Blocked entity not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to unblock entity: {e}")
        raise HTTPException(status_code=500, detail="Failed to unblock entity")
