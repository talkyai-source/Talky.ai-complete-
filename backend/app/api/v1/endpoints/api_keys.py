"""
Admin API Keys Management
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, require_admin_tenant, CurrentUser

# TENANT-SCOPED router. `tenant_ai_credentials.tenant_id` is NOT NULL — these
# are the customer's own bring-your-own AI provider credentials, so managing
# them is a legitimate tenant_admin feature and must NOT be raised to
# platform_admin. It is also the highest-impact leak of the set: an unfiltered
# list returned every other customer's credential rows, and an unfiltered
# revoke let one customer disable another customer's live credentials.
router = APIRouter(prefix="/admin", tags=["Admin API Keys"])
logger = logging.getLogger(__name__)


class ApiKeyResponse(BaseModel):
    id: str
    name: Optional[str] = None
    provider: Optional[str] = None
    tenant_id: Optional[str] = None
    created_at: Optional[str] = None
    revoked_at: Optional[str] = None


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1)
    provider: Optional[str] = "openai"
    key_value: Optional[str] = None


@router.get("/api-keys", response_model=List[ApiKeyResponse])
async def list_api_keys(
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """List the caller tenant's own API keys (via tenant_ai_credentials)."""
    try:
        result = (
            db_client.table("tenant_ai_credentials")
            .select("*")
            .eq("tenant_id", current_user.tenant_id)
            .execute()
        )
        data = result.data or []
        return [
            ApiKeyResponse(
                id=str(row.get("id")),
                name=row.get("name") or row.get("credential_name"),
                provider=row.get("provider"),
                tenant_id=str(row.get("tenant_id")) if row.get("tenant_id") else None,
                created_at=str(row.get("created_at")) if row.get("created_at") else None,
                revoked_at=str(row.get("revoked_at")) if row.get("revoked_at") else None,
            )
            for row in data
        ]
    except Exception as e:
        logger.error(f"Failed to list API keys: {e}")
        return []


@router.post("/api-keys", response_model=ApiKeyResponse)
async def create_api_key(
    request: CreateApiKeyRequest,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """Create a new API key record owned by the caller's tenant."""
    try:
        import uuid
        payload = {
            "id": str(uuid.uuid4()),
            "tenant_id": current_user.tenant_id,
            "name": request.name,
            "provider": request.provider,
            "credential_data": {"key": request.key_value} if request.key_value else {},
            "created_at": datetime.utcnow().isoformat(),
        }
        result = db_client.table("tenant_ai_credentials").insert(payload).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=500, detail="Failed to create API key")
        row = result.data[0]
        return ApiKeyResponse(
            id=str(row.get("id")),
            name=row.get("name") or row.get("credential_name"),
            provider=row.get("provider"),
            tenant_id=str(row.get("tenant_id")) if row.get("tenant_id") else None,
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create API key: {e}")
        raise HTTPException(status_code=500, detail="Failed to create API key")


@router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    current_user: CurrentUser = Depends(require_admin_tenant),
    db_client: Client = Depends(get_db_client),
):
    """Revoke an API key belonging to the caller's tenant."""
    try:
        # Without the tenant predicate this was a cross-tenant denial-of-service:
        # any admin could revoke any other customer's provider credentials.
        result = db_client.table("tenant_ai_credentials").update({
            "revoked_at": datetime.utcnow().isoformat(),
            "status": "revoked"
        }).eq("id", key_id).eq("tenant_id", current_user.tenant_id).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=404, detail="API key not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to revoke API key: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke API key")
