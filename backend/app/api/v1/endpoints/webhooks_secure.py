"""
Secured Webhook Endpoints (Day 6)

Webhook endpoints with HMAC-SHA256 signature verification.
Follows Stripe's webhook verification pattern.

OWASP API Security Top 10 2023:
- API8: Security Misconfiguration (webhook verification)
- API10: Unsafe Consumption of APIs
"""

import json
import logging
import os
import secrets
from typing import Optional, Tuple

from fastapi import APIRouter, Request, HTTPException, Depends, Header, status

from app.core.security.webhook_verification import (
    verify_webhook_request,
    create_webhook_signature_headers,
    generate_webhook_secret,
    WebhookSecretManager,
)
from app.domain.services.call_service import WebhookTargetMismatch
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
# Shared verified-tenant helpers (single source of truth for ALL four
# call-mutation webhook routes — webhooks.py ×2 and webhooks_secure.py ×2)
# =============================================================================

async def verify_webhook_tenant(
    request: Request, tenant_id: str, secret_name: str
) -> Tuple[str, bytes]:
    """Authenticate a webhook to a tenant via its per-tenant HMAC secret.

    Looks up the tenant's secret and verifies the request signature. On
    success the ``X-Tenant-ID`` is proven to belong to the caller (they hold
    that tenant's secret), so the returned tenant id is SAFE to use as the
    object-level authorization scope for the ensuing mutation. Raises 401
    when no secret is configured or the signature is missing/invalid.

    Returns ``(verified_tenant_id, raw_body)``.
    """
    secret = await get_webhook_secret_from_db(tenant_id, secret_name)
    if not secret:
        logger.warning("No webhook secret configured for tenant %s (%s)", tenant_id, secret_name)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook not configured",
        )
    body = await verify_webhook_request(
        request=request,
        secret=secret,
        signature_header="X-Webhook-Signature",
        timestamp_header="X-Webhook-Timestamp",
    )
    return tenant_id, body


async def dispatch_goal_achieved(tenant_id: str, call_id: Optional[str]) -> dict:
    """Single service dispatch for BOTH goal-achieved routes.

    Threads the VERIFIED tenant id into the tenant-scoped service method.
    A ``None`` result (call not found OR belongs to another tenant) becomes
    an identical 404, so a foreign id is indistinguishable from a missing
    one and a 200 always means a real write occurred.
    """
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id required")

    from app.core.container import get_container
    result = await get_container().call_service.mark_goal_achieved(
        tenant_id=tenant_id, call_id=call_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return result


async def dispatch_mark_spam(
    tenant_id: str,
    call_id: Optional[str],
    lead_id: Optional[str],
    reason: str,
) -> dict:
    """Single service dispatch for BOTH mark-spam routes.

    404 (not found / cross-tenant, indistinguishable) on a ``None`` result;
    400 when the body ``lead_id`` does not belong to the scoped call.
    """
    from app.core.container import get_container
    try:
        result = await get_container().call_service.mark_as_spam(
            tenant_id=tenant_id,
            call_id=call_id,
            lead_id=lead_id,
            reason=reason,
        )
    except WebhookTargetMismatch:
        raise HTTPException(
            status_code=400, detail="lead_id does not belong to the specified call"
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return result


_INTERNAL_TOKEN_HEADER = "x-internal-service-token"


def require_internal_service(request: Request) -> None:
    """Gate an admin/provisioning route to a valid internal-service token.

    Mirrors ``app.core.security.internal_auth`` (constant-time compare,
    fail-safe on an unset/empty ``INTERNAL_SERVICE_TOKEN`` — no token is
    ever accepted, so the guarded route stays non-functional rather than
    open). Raises 401 otherwise. This closes the unauthenticated
    secret-write hole on ``/admin/configure``.
    """
    configured = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
    presented = request.headers.get(_INTERNAL_TOKEN_HEADER, "")
    if configured and presented and secrets.compare_digest(presented, configured):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required",
    )


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
    # Verify HMAC signature and obtain the VERIFIED tenant id (shared helper).
    try:
        tenant_id, body = await verify_webhook_tenant(
            request, x_tenant_id, "call_goal_achieved"
        )
    except HTTPException:
        logger.warning(
            f"Webhook signature verification failed for tenant {x_tenant_id}",
            extra={
                "tenant_id": x_tenant_id,
                "endpoint": "/webhooks/secure/call/goal-achieved",
                "signature": x_webhook_signature[:20] + "..." if x_webhook_signature else None,
            }
        )
        raise

    # Process webhook via the single shared, tenant-scoped dispatch path.
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
    # Verify HMAC signature and obtain the VERIFIED tenant id (shared helper).
    tenant_id, body = await verify_webhook_tenant(request, x_tenant_id, "call_mark_spam")

    # Process webhook via the single shared, tenant-scoped dispatch path.
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
    _admin: None = Depends(require_internal_service),
):
    """
    Configure a new webhook for a tenant.
    Generates and returns a secure secret.

    SECURITY (P0): this writes a tenant's webhook HMAC secret (a
    secret-takeover primitive) and MUST NOT be reachable unauthenticated.
    It is now gated by ``require_internal_service`` — a valid internal
    service token is required. Fail-safe: with INTERNAL_SERVICE_TOKEN
    unset the guard 401s every request, leaving the route non-functional
    rather than open. TODO: expose an admin-JWT (platform_admin) variant
    via webhooks_admin.py if a UI-driven provisioning path is needed.
    """
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