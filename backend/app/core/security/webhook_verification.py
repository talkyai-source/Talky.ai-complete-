"""
Webhook Signature Verification (Day 6)

OWASP API Security: Verify webhook authenticity using HMAC-SHA256.
Standard implementation following Stripe's webhook verification pattern.

References:
- https://stripe.com/docs/webhooks/signatures
- OWASP API Security Top 10 2023 - API8: Security Misconfiguration
"""

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional, Tuple

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# Constants
SIGNATURE_VERSION = "v1"
MAX_TIMESTAMP_AGE_SECONDS = 300  # 5 minutes - replay protection


class WebhookVerificationError(Exception):
    """Raised when webhook signature verification fails."""
    pass


def generate_webhook_secret() -> str:
    """Generate a secure random webhook secret."""
    return secrets.token_urlsafe(32)


def compute_signature(payload: bytes, secret: str) -> str:
    """
    Compute HMAC-SHA256 signature for webhook payload.

    Args:
        payload: Raw request body bytes
        secret: Webhook signing secret

    Returns:
        Hex-encoded HMAC-SHA256 signature
    """
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()


def compute_signature_with_timestamp(
    payload: bytes,
    secret: str,
    timestamp: int
) -> str:
    """
    Compute signature including timestamp for replay protection.

    Format: timestamp.payload (same as Stripe)
    """
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    return compute_signature(signed_payload, secret)


def verify_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    timestamp_header: Optional[str] = None,
    max_age_seconds: int = MAX_TIMESTAMP_AGE_SECONDS
) -> Tuple[bool, Optional[str]]:
    """
    Verify webhook signature.

    Supports two schemes:
    1. Simple: signature = hmac-sha256(payload, secret)
    2. Timestamped: signature = hmac-sha256(timestamp.payload, secret)

    Args:
        payload: Raw request body
        signature_header: Value from X-Webhook-Signature header
        secret: Webhook signing secret
        timestamp_header: Optional timestamp for replay protection
        max_age_seconds: Maximum acceptable age for timestamp

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not signature_header:
        return False, "Missing signature header"

    if not secret:
        return False, "Webhook secret not configured"

    # Check timestamp for replay protection
    if timestamp_header:
        try:
            timestamp = int(timestamp_header)
            now = int(time.time())
            age = now - timestamp

            if age > max_age_seconds:
                return False, f"Webhook timestamp too old ({age}s)"

            if age < 0:
                return False, "Webhook timestamp in future"

            # Compute expected signature with timestamp
            expected_sig = compute_signature_with_timestamp(payload, secret, timestamp)
        except ValueError:
            return False, "Invalid timestamp format"
    else:
        # Simple signature without timestamp
        expected_sig = compute_signature(payload, secret)

    # Use constant-time comparison to prevent timing attacks
    is_valid = secrets.compare_digest(
        signature_header.lower(),
        expected_sig.lower()
    )

    if not is_valid:
        return False, "Invalid signature"

    return True, None


async def verify_webhook_request(
    request: Request,
    secret: str,
    signature_header: str = "X-Webhook-Signature",
    timestamp_header: str = "X-Webhook-Timestamp",
    max_age_seconds: int = MAX_TIMESTAMP_AGE_SECONDS
) -> bytes:
    """
    FastAPI helper to verify incoming webhook request.

    Args:
        request: FastAPI Request object
        secret: Webhook signing secret
        signature_header: Header name containing signature
        timestamp_header: Header name containing timestamp
        max_age_seconds: Maximum acceptable timestamp age

    Returns:
        Request body bytes if verification succeeds

    Raises:
        HTTPException: If verification fails
    """
    # Read body
    body = await request.body()

    # Get headers
    signature = request.headers.get(signature_header)
    timestamp = request.headers.get(timestamp_header)

    # Verify
    is_valid, error = verify_signature(
        payload=body,
        signature_header=signature or "",
        secret=secret,
        timestamp_header=timestamp,
        max_age_seconds=max_age_seconds
    )

    if not is_valid:
        logger.warning(f"Webhook verification failed: {error}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Webhook verification failed: {error}"
        )

    return body


def create_webhook_signature_headers(
    payload: bytes,
    secret: str,
    include_timestamp: bool = True
) -> dict:
    """
    Create webhook signature headers for outgoing webhooks.

    Use this when sending webhooks to external systems.

    Args:
        payload: Webhook payload bytes
        secret: Signing secret
        include_timestamp: Whether to include timestamp header

    Returns:
        Dictionary of headers to include in webhook request
    """
    headers = {}

    if include_timestamp:
        timestamp = int(time.time())
        signature = compute_signature_with_timestamp(payload, secret, timestamp)
        headers["X-Webhook-Timestamp"] = str(timestamp)
    else:
        signature = compute_signature(payload, secret)

    headers["X-Webhook-Signature"] = signature
    headers["X-Webhook-Version"] = SIGNATURE_VERSION

    return headers


# FastAPI Dependency Factory
def require_webhook_signature(secret: str):
    """
    Create a FastAPI dependency that requires valid webhook signature.

    Usage:
        @router.post("/webhook/provider")
        async def webhook_endpoint(
            request: Request,
            _=Depends(require_webhook_signature("my-secret"))
        ):
            body = await request.body()
            ...
    """
    async def verify(request: Request):
        await verify_webhook_request(request, secret)
    return verify


class WebhookSecretManager:
    """
    Manages webhook secrets from database configuration.

    Looks up secrets by tenant_id and webhook_name.
    """

    def __init__(self):
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes

    async def get_secret(
        self,
        tenant_id: str,
        webhook_name: str,
        db_pool=None
    ) -> Optional[str]:
        """
        Get webhook secret for tenant/webhook combination.

        Args:
            tenant_id: Tenant identifier
            webhook_name: Webhook endpoint name
            db_pool: Optional database pool for lookup

        Returns:
            Webhook secret or None if not found
        """
        cache_key = f"{tenant_id}:{webhook_name}"
        cached = self._cache.get(cache_key)

        if cached:
            secret, expiry = cached
            if time.time() < expiry:
                return secret

        if not db_pool:
            return None

        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT secret_key FROM webhook_configs
                    WHERE tenant_id = $1 AND webhook_name = $2 AND is_active = TRUE
                    """,
                    tenant_id, webhook_name
                )

                if row:
                    secret = row["secret_key"]
                    self._cache[cache_key] = (secret, time.time() + self._cache_ttl)
                    return secret
        except Exception as e:
            logger.error(f"Failed to lookup webhook secret: {e}")

        return None

    def invalidate_cache(self, tenant_id: str, webhook_name: str) -> None:
        """Invalidate cached secret for tenant/webhook."""
        cache_key = f"{tenant_id}:{webhook_name}"
        self._cache.pop(cache_key, None)
