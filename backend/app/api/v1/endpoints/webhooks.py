"""
Webhooks API Endpoints

Call-lifecycle webhooks that MUTATE call/lead state (goal-achieved, mark-spam)
from an EXTERNAL caller.

SECURITY (P0-5): these previously took a raw ``call_id``/``lead_id`` with NO
verification, so anyone who could reach the API could forge call outcomes or
block arbitrary leads cross-tenant. They are now HMAC-SHA256 signature-verified
exactly like ``webhooks_secure.py`` (per-tenant secret + ``X-Webhook-Signature``
+ ``X-Tenant-ID``). The in-pipeline path calls ``call_service`` DIRECTLY and does
not use these HTTP routes, so hardening them breaks no internal caller.
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends, Header

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client
# Single source of truth for verified-tenant auth + tenant-scoped dispatch,
# shared with the /webhooks/secure/* routes so the object-level authorization
# scoping can never be forgotten by one route but not another (P0).
from app.api.v1.endpoints.webhooks_secure import (
    verify_webhook_tenant,
    dispatch_goal_achieved,
    dispatch_mark_spam,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/call/goal-achieved")
async def mark_goal_achieved(
    request: Request,
    x_webhook_signature: str = Header(..., alias="X-Webhook-Signature"),
    x_webhook_timestamp: Optional[str] = Header(None, alias="X-Webhook-Timestamp"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    db_client: Client = Depends(get_db_client),
):
    """Mark a call as having achieved its goal (prevents future retries).

    HMAC-signature-verified; called by external integrations. The voice
    pipeline marks goals in-process via call_service, not this route.
    """
    tenant_id, body = await verify_webhook_tenant(request, x_tenant_id, "call_goal_achieved")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        return await dispatch_goal_achieved(tenant_id, data.get("call_id"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking goal achieved: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark goal achieved")


@router.post("/call/mark-spam")
async def mark_as_spam(
    request: Request,
    x_webhook_signature: str = Header(..., alias="X-Webhook-Signature"),
    x_webhook_timestamp: Optional[str] = Header(None, alias="X-Webhook-Timestamp"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    db_client: Client = Depends(get_db_client),
):
    """Mark a call/lead as spam (prevents future calls). HMAC-signature-verified."""
    tenant_id, body = await verify_webhook_tenant(request, x_tenant_id, "call_mark_spam")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        return await dispatch_mark_spam(
            tenant_id,
            data.get("call_id"),
            data.get("lead_id"),
            data.get("reason", "spam"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking as spam: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark as spam")
