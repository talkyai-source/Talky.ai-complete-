"""PayPal minute top-ups, beside the Stripe ones.

    POST /billing/topups/paypal/checkout   create the order, get the PayPal approval URL
    POST /billing/topups/paypal/capture    the customer is back from PayPal: finish the payment

The browser names a package code (checkout) or OUR order id (capture). It never
sends a price, a minute count or a PayPal id.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.dependencies import CurrentUser, get_current_user
from app.api.v1.endpoints.billing_topups import (
    _live_topups_enabled,
    _purchase_audience,
    _tenant,
)
from app.core.container import get_container
from app.core.error_handlers import safe_exception_message
from app.core.security.rbac import Permission, require_permission
from app.domain.services.billing_checkout import CheckoutError, billing_return_urls
from app.domain.services.paypal_client import PayPalError, PayPalNotConfigured
from app.domain.services.paypal_mode import paypal_topup_availability
from app.domain.services.paypal_topups import PayPalTopupError, PayPalTopupService
from app.domain.services.topup_service import TopupError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing/topups/paypal", tags=["billing"])

_NOT_STARTED = "Could not start the PayPal payment. You have not been charged."
_NOT_CONFIRMED = "We could not confirm the payment yet."


class PayPalCheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_code: str = Field(..., max_length=64)


class PayPalCheckoutOut(BaseModel):
    order_id: str
    paypal_order_id: str
    checkout_url: str
    minutes: int
    price_cents: int
    currency: str


class PayPalCaptureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID


class PayPalCaptureOut(BaseModel):
    order_id: str
    status: Literal["paid", "pending", "not_approved", "failed"]
    minutes: Optional[int] = None


def _service() -> PayPalTopupService:
    c = get_container()
    if not c.is_initialized:
        raise HTTPException(status_code=503, detail="Backend not ready")
    return PayPalTopupService(c.db_pool)


@router.post(
    "/checkout",
    response_model=PayPalCheckoutOut,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def create_paypal_topup_checkout(
    body: PayPalCheckoutIn,
    current_user: CurrentUser = Depends(get_current_user),
):
    tenant_id = _tenant(current_user)

    if not _live_topups_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Minute top-ups are not available yet",
        )
    from app.api.v1.endpoints.billing import _is_platform_admin

    open_, reason = paypal_topup_availability(
        is_platform_admin=_is_platform_admin(current_user),
        email=getattr(current_user, "email", None),
    )
    if not open_:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=reason)

    svc = _service()
    audience = await _purchase_audience(tenant_id)
    try:
        billing_base = billing_return_urls()
    except CheckoutError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=safe_exception_message(e, "The request could not be completed."),
        )

    try:
        started = await svc.start_checkout(
            tenant_id=tenant_id,
            user_id=getattr(current_user, "id", None),
            package_code=body.package_code,
            audience=audience,
            billing_base=billing_base,
        )
    except TopupError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_exception_message(e, "The request could not be completed."),
        )
    except PayPalTopupError as e:
        if e.code == "unsupported_currency":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PayPal cannot take this currency. Please pay by card.",
            )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_NOT_STARTED)
    except (PayPalError, PayPalNotConfigured):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_NOT_STARTED)

    return PayPalCheckoutOut(
        order_id=started.order_id,
        paypal_order_id=started.paypal_order_id,
        checkout_url=started.checkout_url,
        minutes=started.minutes,
        price_cents=started.price_cents,
        currency=started.currency,
    )


@router.post(
    "/capture",
    response_model=PayPalCaptureOut,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def capture_paypal_topup(
    body: PayPalCaptureIn,
    current_user: CurrentUser = Depends(get_current_user),
):
    # No availability check: a customer who already approved must be able to finish.
    tenant_id = _tenant(current_user)
    svc = _service()
    try:
        outcome = await svc.capture_for_tenant(tenant_id=tenant_id, order_id=str(body.order_id))
    except PayPalNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="PayPal is not available yet."
        )
    except PayPalTopupError as e:
        if e.code == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        if e.code == "review":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payment needs a manual check. Nothing was added to your minutes yet.",
            )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_NOT_CONFIRMED)
    except PayPalError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_NOT_CONFIRMED)
    return PayPalCaptureOut(order_id=outcome.order_id, status=outcome.status, minutes=outcome.minutes)
