"""PayPal's webhook: ``POST /billing/paypal/webhooks`` (public; PayPal verifies the signature).

No login and no CSRF: PayPal calls it. The body is read raw (the signature covers
the exact bytes) with the same 512 KiB cap as the Stripe route.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.container import get_container
from app.domain.services.paypal_webhooks import (
    PayPalWebhookRejected,
    PayPalWebhookRetry,
    handle_paypal_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing/paypal", tags=["billing"])

_MAX_BODY = 512 * 1024


class PayPalWebhookOut(BaseModel):
    received: bool
    status: str


@router.post("/webhooks", response_model=PayPalWebhookOut)
async def paypal_webhook(request: Request) -> PayPalWebhookOut:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > _MAX_BODY:
            raise HTTPException(413, detail="Webhook payload is too large")
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(503, detail="PayPal webhook cannot be verified right now")
    try:
        result = await handle_paypal_webhook(
            container.db_pool, headers=request.headers, raw_body=bytes(payload)
        )
    except PayPalWebhookRejected:
        raise HTTPException(400, detail="Invalid PayPal webhook") from None
    except PayPalWebhookRetry:
        raise HTTPException(
            503,
            detail="PayPal webhook cannot be verified right now",
            headers={"Retry-After": "30"},
        ) from None
    return PayPalWebhookOut(received=True, status=result["status"])
