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

from fastapi import APIRouter, Request, HTTPException, Depends, Header, status

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client
from app.core.security.webhook_verification import verify_webhook_request
from app.api.v1.endpoints.webhooks_secure import get_webhook_secret_from_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _verify_signed_body(
    request: Request, tenant_id: str, secret_name: str
) -> bytes:
    """Look up the tenant's webhook secret and verify the request signature.

    Returns the raw request body on success. Raises 401 when no secret is
    configured for the tenant or the signature is missing/invalid — closing
    the forgery hole (P0-5). Mirrors webhooks_secure.py.
    """
    secret = await get_webhook_secret_from_db(tenant_id, secret_name)
    if not secret:
        logger.warning("No webhook secret configured for tenant %s (%s)", tenant_id, secret_name)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook not configured",
        )
    return await verify_webhook_request(
        request=request,
        secret=secret,
        signature_header="X-Webhook-Signature",
        timestamp_header="X-Webhook-Timestamp",
    )


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
    body = await _verify_signed_body(request, x_tenant_id, "call_goal_achieved")
    try:
        data = json.loads(body)
        call_id = data.get("call_id")
        if not call_id:
            raise HTTPException(status_code=400, detail="call_id required")

        from app.core.container import get_container
        result = await get_container().call_service.mark_goal_achieved(call_id)
        return result

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
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
    body = await _verify_signed_body(request, x_tenant_id, "call_mark_spam")
    try:
        data = json.loads(body)
        call_id = data.get("call_id")
        lead_id = data.get("lead_id")
        reason = data.get("reason", "spam")

        from app.core.container import get_container
        result = await get_container().call_service.mark_as_spam(
            call_id=call_id,
            lead_id=lead_id,
            reason=reason,
        )
        return result

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking as spam: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark as spam")
