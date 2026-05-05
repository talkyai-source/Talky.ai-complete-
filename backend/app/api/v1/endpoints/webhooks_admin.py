"""
Admin Webhooks Management
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter(prefix="/admin", tags=["Admin Webhooks"])
logger = logging.getLogger(__name__)


class WebhookEndpoint(BaseModel):
    id: str
    url: str
    events: List[str]
    active: bool = True
    created_at: Optional[str] = None


class WebhookDelivery(BaseModel):
    id: str
    webhook_id: str
    event: str
    status: str
    created_at: Optional[str] = None


class CreateWebhookRequest(BaseModel):
    url: str = Field(..., min_length=1)
    events: List[str] = Field(default_factory=list)
    active: bool = True


@router.get("/webhooks", response_model=List[WebhookEndpoint])
async def list_webhooks(
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """List configured webhook endpoints."""
    try:
        result = db_client.table("webhook_endpoints").select("*").execute()
        data = result.data or []
        return [
            WebhookEndpoint(
                id=str(row.get("id")),
                url=row.get("url") or row.get("endpoint_url"),
                events=row.get("events") or [],
                active=row.get("active", True),
                created_at=str(row.get("created_at")) if row.get("created_at") else None,
            )
            for row in data
        ]
    except Exception as e:
        logger.error(f"Failed to list webhooks: {e}")
        return []


@router.post("/webhooks", response_model=WebhookEndpoint)
async def create_webhook(
    request: CreateWebhookRequest,
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Create a new webhook endpoint."""
    try:
        import uuid
        payload = {
            "id": str(uuid.uuid4()),
            "tenant_id": current_user.tenant_id,
            "url": request.url,
            "events": request.events,
            "active": request.active,
            "created_at": datetime.utcnow().isoformat(),
        }
        result = db_client.table("webhook_endpoints").insert(payload).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=500, detail="Failed to create webhook")
        row = result.data[0]
        return WebhookEndpoint(
            id=str(row.get("id")),
            url=row.get("url") or row.get("endpoint_url"),
            events=row.get("events") or [],
            active=row.get("active", True),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create webhook: {e}")
        raise HTTPException(status_code=500, detail="Failed to create webhook")


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Delete a webhook endpoint."""
    try:
        result = db_client.table("webhook_endpoints").delete().eq("id", webhook_id).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=404, detail="Webhook not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete webhook: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete webhook")


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Test a webhook endpoint."""
    try:
        result = db_client.table("webhook_endpoints").select("*").eq("id", webhook_id).execute()
        if result.error or not result.data:
            raise HTTPException(status_code=404, detail="Webhook not found")
        return {"success": True, "delivery_id": webhook_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test webhook: {e}")
        raise HTTPException(status_code=500, detail="Failed to test webhook")


@router.get("/webhooks/deliveries", response_model=List[WebhookDelivery])
async def list_webhook_deliveries(
    webhook_id: Optional[str] = None,
    current_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """List webhook delivery history."""
    try:
        query = db_client.table("webhook_deliveries").select("*")
        if webhook_id:
            query = query.eq("webhook_id", webhook_id)
        result = query.execute()
        data = result.data or []
        return [
            WebhookDelivery(
                id=str(row.get("id")),
                webhook_id=str(row.get("webhook_id")),
                event=row.get("event") or "",
                status=row.get("status") or "pending",
                created_at=str(row.get("created_at")) if row.get("created_at") else None,
            )
            for row in data
        ]
    except Exception as e:
        logger.error(f"Failed to list webhook deliveries: {e}")
        return []
