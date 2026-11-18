"""Plans bought with a PayPal subscription, beside the Stripe plan checkout.

    POST /billing/paypal/subscriptions/checkout      start (or resume) the purchase, get the PayPal approval URL
    GET  /billing/paypal/subscriptions/{request_id}  read the state of that purchase

The browser names a price option id and its own request id. It never sends a
price, a plan, a minute count, a tenant or a PayPal id: the server decides all
of them. The answer has the same shape as the card checkout.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
)
from app.api.v1.endpoints.billing import (
    CreateCheckoutResponse,
    _is_billing_team,
    _is_platform_admin,
    _require_checkout_permitted_in_test_mode,
)
from app.core.container import get_container
from app.core.error_handlers import safe_exception_message
from app.core.security.rbac import Permission, require_permission
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.paypal_mode import PAYPAL_UNAVAILABLE, paypal_plan_availability
from app.domain.services.paypal_subscriptions import PayPalSubscriptionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing/paypal/subscriptions", tags=["billing"])


class PayPalPlanCheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    price_option_id: UUID


def _service() -> PayPalSubscriptionService:
    c = get_container()
    if not c.is_initialized:
        raise HTTPException(status_code=503, detail="Backend not ready")
    return PayPalSubscriptionService(c.db_pool)


@router.post(
    "/checkout",
    response_model=CreateCheckoutResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def create_paypal_plan_checkout(
    body: PayPalPlanCheckoutIn,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    _require_checkout_permitted_in_test_mode(current_user)

    open_, reason = paypal_plan_availability(
        is_platform_admin=_is_platform_admin(current_user),
        email=getattr(current_user, "email", None),
    )
    if not open_:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "paypal_unavailable",
                "message": reason or PAYPAL_UNAVAILABLE,
                "request_not_started": True,
            },
        )

    svc = _service()
    try:
        result = await svc.start_checkout(
            tenant_id=current_user.tenant_id,
            request_id=str(body.request_id),
            price_option_id=str(body.price_option_id),
            allow_team_only=_is_billing_team(current_user),
        )

        await audit_logger.log(
            event_type=AuditEvent.BILLING_UPDATED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=current_user.tenant_id,
            action="paypal_plan_checkout_started",
            description="User started a plan purchase with PayPal",
            metadata={
                "request_id": str(body.request_id),
                "price_option_id": str(body.price_option_id),
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return CreateCheckoutResponse(**result)

    except HTTPException:
        raise
    except ValueError as e:
        detail = {
            "code": getattr(e, "code", "checkout_unconfirmed"),
            "message": (
                str(e)
                if isinstance(e, CheckoutError)
                else "Checkout is unconfirmed. Retry the same saved request."
            ),
        }
        if isinstance(e, CheckoutError) and e.request_not_started:
            detail["request_not_started"] = True
            if e.existing_attempt is not None:
                detail["existing_attempt"] = e.existing_attempt
        raise HTTPException(
            status_code=e.status_code if isinstance(e, CheckoutError) else 503,
            detail=detail,
        )
    except Exception as e:
        logger.error("PayPal plan checkout outcome unconfirmed: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "checkout_unconfirmed",
                "message": "Checkout is unconfirmed. Retry the same saved request.",
            },
        )


@router.get(
    "/{request_id}",
    response_model=CreateCheckoutResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def get_paypal_plan_attempt(
    request_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    # No availability check: a customer who already approved must be able to see the result.
    svc = _service()
    try:
        return await svc.get_attempt(tenant_id=current_user.tenant_id, request_id=str(request_id))
    except CheckoutError as exc:
        raise HTTPException(
            exc.status_code,
            detail={
                "code": exc.code,
                "message": safe_exception_message(exc, "The request could not be completed."),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "checkout_unconfirmed",
                "message": "Checkout status is unavailable. Keep your saved request.",
            },
        ) from exc
