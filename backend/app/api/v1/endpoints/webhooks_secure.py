"""
Secured Webhook Endpoints (Day 6)

Webhook endpoints with HMAC-SHA256 signature verification.
Follows Stripe's webhook verification pattern.

OWASP API Security Top 10 2023:
- API8: Security Misconfiguration (webhook verification)
- API10: Unsafe Consumption of APIs
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends, Header, status

from app.core.security.webhook_verification import (
    verify_webhook_request,
    create_webhook_signature_headers,
    generate_webhook_secret,
    WebhookSecretManager,
)
from app.core.security.idempotency import (
    idempotency_dependency,
    store_idempotent_response,
    release_idempotency_lock,
)
from app.core.security.api_security import rate_limit_dependency
from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/secure", tags=["webhooks-secure"])

# Webhook secret manager for database lookups
_webhook_secret_manager: Optional[WebhookSecretManager] = None


def get_webhook_secret_manager() -> WebhookSecretManager:
    """Get or create webhook secret manager singleton."""
    global _webhook_secret_manager
    if _webhook_secret_manager is None:
        _webhook_secret_manager = WebhookSecretManager()
    return _webhook_secret_manager


async def get_webhook_secret_from_db(
    tenant_id: str,
    webhook_name: str
) -> Optional[str]:
    """Lookup webhook secret from database."""
    from app.core.container import get_container

    container = get_container()
    if not container.is_initialized:
        return None

    manager = get_webhook_secret_manager()
    return await manager.get_secret(tenant_id, webhook_name, container.db_pool)


# =============================================================================
# Example: Secured Webhook Endpoints
# =============================================================================

@router.post(
    "/call/goal-achieved",
    summary="Mark call goal achieved (secured)",
    description="Secured webhook with signature verification",
)
async def mark_goal_achieved_secured(
    request: Request,
    x_webhook_signature: str = Header(..., alias="X-Webhook-Signature"),
    x_webhook_timestamp: Optional[str] = Header(None, alias="X-Webhook-Timestamp"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    db_client: Client = Depends(get_db_client),
    _: None = Depends(rate_limit_dependency),
):
    """
    Mark a call as having achieved its goal - with webhook signature verification.

    Headers required:
    - X-Webhook-Signature: HMAC-SHA256 signature of payload
    - X-Webhook-Timestamp: Unix timestamp (optional, for replay protection)
    - X-Tenant-ID: Tenant identifier for secret lookup
    """
    # Get webhook secret for this tenant
    secret = await get_webhook_secret_from_db(x_tenant_id, "call_goal_achieved")

    if not secret:
        logger.warning(f"No webhook secret configured for tenant {x_tenant_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook not configured"
        )

    # Verify signature
    try:
        body = await verify_webhook_request(
            request=request,
            secret=secret,
            signature_header="X-Webhook-Signature",
            timestamp_header="X-Webhook-Timestamp",
        )
    except HTTPException:
        # Log security event
        logger.warning(
            f"Webhook signature verification failed for tenant {x_tenant_id}",
            extra={
                "tenant_id": x_tenant_id,
                "endpoint": "/webhooks/secure/call/goal-achieved",
                "signature": x_webhook_signature[:20] + "..." if x_webhook_signature else None,
            }
        )
        raise

    # Process webhook
    import json
    try:
        data = json.loads(body)
        call_id = data.get("call_id")

        if not call_id:
            raise HTTPException(status_code=400, detail="call_id required")

        from app.core.container import get_container
        call_service = get_container().call_service
        result = await call_service.mark_goal_achieved(call_id)
        return result

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking goal achieved: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark goal achieved")


@router.post(
    "/call/mark-spam",
    summary="Mark call as spam (secured)",
    description="Secured webhook with signature verification",
)
async def mark_as_spam_secured(
    request: Request,
    x_webhook_signature: str = Header(..., alias="X-Webhook-Signature"),
    x_webhook_timestamp: Optional[str] = Header(None, alias="X-Webhook-Timestamp"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    db_client: Client = Depends(get_db_client),
    _: None = Depends(rate_limit_dependency),
):
    """
    Mark a call/lead as spam - with webhook signature verification.
    """
    # Get webhook secret
    secret = await get_webhook_secret_from_db(x_tenant_id, "call_mark_spam")

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook not configured"
        )

    # Verify signature
    await verify_webhook_request(
        request=request,
        secret=secret,
        signature_header="X-Webhook-Signature",
        timestamp_header="X-Webhook-Timestamp",
    )

    # Process webhook
    import json
    try:
        data = await request.json()
        call_id = data.get("call_id")
        lead_id = data.get("lead_id")
        reason = data.get("reason", "spam")

        from app.core.container import get_container
        call_service = get_container().call_service
        result = await call_service.mark_as_spam(
            call_id=call_id,
            lead_id=lead_id,
            reason=reason
        )
        return result

    except Exception as e:
        logger.error(f"Error marking as spam: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark as spam")


@router.post(
    "/idempotent-example",
    summary="Idempotent webhook example",
    description="Webhook with idempotency key support",
)
async def idempotent_webhook_example(
    request: Request,
    x_webhook_signature: str = Header(..., alias="X-Webhook-Signature"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    idempotency_key: Optional[str] = Depends(idempotency_dependency),
    db_client: Client = Depends(get_db_client),
):
    """
    Example webhook with both signature verification and idempotency.

    Headers:
    - X-Webhook-Signature: HMAC-SHA256 signature
    - X-Tenant-ID: Tenant identifier
    - Idempotency-Key: Unique key for idempotent processing
    """
    # Get webhook secret
    secret = await get_webhook_secret_from_db(x_tenant_id, "idempotent_example")

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook not configured"
        )

    # Verify signature
    try:
        await verify_webhook_request(request, secret)
    except HTTPException:
        await release_idempotency_lock(request)
        raise

    # Process webhook (would be idempotent)
    try:
        data = await request.json()

        # ... business logic ...
        result = {"status": "processed", "data": data}

        # Store response for idempotency
        import json
        await store_idempotent_response(
            request,
            status.HTTP_200_OK,
            json.dumps(result)
        )

        return result

    except Exception as e:
        await release_idempotency_lock(request)
        raise


# =============================================================================
# Webhook Configuration Management (Admin)
# =============================================================================

@router.post(
    "/admin/configure",
    summary="Configure webhook for tenant",
    description="Admin endpoint to set up webhook HMAC secret",
    include_in_schema=False,
)
async def configure_webhook(
    request: Request,
    webhook_name: str,
    tenant_id: str,
    db_client: Client = Depends(get_db_client),
):
    """
    Configure a new webhook for a tenant.
    Generates and returns a secure secret.
    """
    # In production, this should require admin authentication
    secret = generate_webhook_secret()

    try:
        await db_client.execute(
            """
            INSERT INTO webhook_configs (tenant_id, webhook_name, secret_key)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant_id, webhook_name)
            DO UPDATE SET secret_key = $3, is_active = TRUE, updated_at = NOW()
            """,
            tenant_id, webhook_name, secret
        )

        return {
            "webhook_name": webhook_name,
            "tenant_id": tenant_id,
            "secret": secret,  # Return once - not stored in plaintext
            "note": "Store this secret securely - it will not be shown again"
        }

    except Exception as e:
        logger.error(f"Failed to configure webhook: {e}")
        raise HTTPException(status_code=500, detail="Failed to configure webhook")


# =============================================================================
# Utility Endpoints
# =============================================================================

@router.get(
    "/verify-test",
    summary="Test webhook signature verification",
    description="Endpoint to test webhook signature generation/verification",
)
async def verify_test(request: Request):
    """
    Test endpoint for webhook signature verification.

    Returns instructions for testing signature verification.
    """
    import json

    test_secret = "whsec_test_secret_for_development_only"
    test_payload = b'{"test": "data", "timestamp": 1234567890}'

    # Generate test signature
    headers = create_webhook_signature_headers(test_payload, test_secret)

    return {
        "message": "Webhook signature test",
        "test_secret": test_secret,
        "test_payload": test_payload.decode(),
        "generated_headers": headers,
        "instructions": {
            "1": "Send POST request to this endpoint with the test payload",
            "2": f"Include header X-Webhook-Signature: {headers['X-Webhook-Signature']}",
            "3": f"Include header X-Webhook-Timestamp: {headers['X-Webhook-Timestamp']}",
            "4": "The endpoint will verify the signature and return success",
        }
    }