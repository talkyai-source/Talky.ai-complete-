"""
Billing API Endpoints
Handles Stripe subscription management and payment operations
"""

import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Any, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
    get_db_pool,
)
from app.core.postgres_adapter import Client
from app.core.security.rbac import Permission, require_permission
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.billing_service import BillingService
from app.domain.services.call_outcomes import (
    ANSWERED_OUTCOME_LIST,
    FAILED_OUTCOME_LIST,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ============================================
# Request/Response Models
# ============================================


class CreateCheckoutRequest(BaseModel):
    """Select an approved server price; preserve identity across uncertainty."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    price_option_id: UUID


class CreateCheckoutResponse(BaseModel):
    """Checkout session response"""

    request_id: UUID
    state: Literal["open", "activated", "pending", "expired", "failed"]
    session_id: Optional[str] = None
    checkout_url: Optional[str] = None
    price_option: dict[str, Any]
    mock_mode: bool = False
    message: Optional[str] = None


class PortalRequest(BaseModel):
    """Request to create customer portal session"""

    model_config = ConfigDict(extra="forbid")


class PortalResponse(BaseModel):
    """Portal session response"""

    portal_url: str
    mock_mode: bool = False
    message: Optional[str] = None


class SubscriptionResponse(BaseModel):
    """Subscription status response"""

    status: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    cancel_at_period_end: bool = False
    minutes_allocated: int = 0
    minutes_used: int = 0
    minutes_remaining: int = 0
    minutes_state: Literal["known", "unlimited", "unavailable"] = "unavailable"
    purchased_price_option: Optional[dict[str, Any]] = None
    billing_portal_available: bool = False


class CancelResponse(BaseModel):
    """Cancellation response"""

    status: str
    cancel_at_period_end: bool = False
    mock_mode: bool = False
    message: Optional[str] = None


class UsageSummaryResponse(BaseModel):
    """Usage summary response"""

    usage_type: str
    total_used: int
    allocated: int
    remaining: int
    overage: int
    unlimited: bool
    metering_period: Literal["calendar_month"]


# ============================================
# Helper Functions
# ============================================


def get_billing_service(db_client: Client = Depends(get_db_client)) -> BillingService:
    """Dependency to get billing service instance"""
    return BillingService(db_client)


# ============================================
# Endpoints
# ============================================


@router.post(
    "/create-checkout-session",
    response_model=CreateCheckoutResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def create_checkout_session(
    body: CreateCheckoutRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Create a Stripe Checkout Session for subscribing to a plan.
    """
    try:
        result = await billing.create_checkout_session(
            tenant_id=current_user.tenant_id,
            email=current_user.email,
            request_id=str(body.request_id),
            price_option_id=str(body.price_option_id),
            business_name=current_user.business_name,
        )

        # Log event (Day 8)
        await audit_logger.log(
            event_type=AuditEvent.BILLING_UPDATED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=current_user.tenant_id,
            action="checkout_session_created",
            description="User requested a saved subscription checkout",
            metadata={
                "request_id": str(body.request_id),
                "price_option_id": str(body.price_option_id),
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return CreateCheckoutResponse(**result)

    except ValueError as e:
        from app.domain.services.billing_checkout import CheckoutError

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
        logger.error("Checkout outcome unconfirmed: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "checkout_unconfirmed",
                "message": "Checkout is unconfirmed. Retry the same saved request.",
            },
        )


@router.get(
    "/checkout-attempts/{request_id}",
    response_model=CreateCheckoutResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def get_checkout_attempt(
    request_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
):
    from app.domain.services.billing_checkout import CheckoutError

    try:
        return await billing.get_checkout_attempt(
            tenant_id=current_user.tenant_id, request_id=str(request_id)
        )
    except CheckoutError as exc:
        raise HTTPException(
            exc.status_code, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "checkout_unconfirmed",
                "message": "Checkout status is unavailable. Keep your saved request.",
            },
        ) from exc


@router.post("/webhooks")
async def stripe_webhook(
    request: Request,
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Handle Stripe webhook events.
    """
    billing = BillingService(db_client, audit_logger=audit_logger)

    # Get raw body and signature
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > 512 * 1024:
            raise HTTPException(413, detail="Webhook payload is too large")
    signature = request.headers.get("stripe-signature", "")

    if not signature and not billing.mock_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Stripe signature"
        )

    from app.domain.services.billing_webhooks import BillingWebhookRetryable

    try:
        result = await billing.handle_webhook(bytes(payload), signature)
        return result
    except BillingWebhookRetryable as exc:
        raise HTTPException(
            503,
            detail={
                "code": exc.code,
                "message": "Billing event is not completed. Retry or reconcile the saved receipt.",
            },
            headers={"Retry-After": "30"},
        ) from exc
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Webhook handling failed error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook handling failed"
        )


@router.get(
    "/subscription",
    response_model=SubscriptionResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_READ))],
)
async def get_subscription(
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
    db_pool=Depends(get_db_pool),
):
    """
    Get the current user's subscription status.

    Returns subscription details including:
    - Current plan
    - Billing period dates
    - Minutes usage
    """
    try:
        subscription = await billing.get_subscription(current_user.tenant_id)

        from app.core.db_utils import acquire_with_tenant
        from app.domain.services.minutes_quota import compute_minutes_status

        async with acquire_with_tenant(db_pool, current_user.tenant_id) as conn:
            quota = (await compute_minutes_status(conn, current_user.tenant_id)).require_available()
        minutes_used = quota.used_minutes

        if not subscription:
            return SubscriptionResponse(
                status="inactive",
                minutes_allocated=quota.allocated,
                minutes_used=minutes_used,
                minutes_remaining=quota.remaining_minutes,
                minutes_state=quota.state,
            )

        # Get plan info
        plan = subscription.get("plans") or subscription.get("plan") or {}
        allocated = quota.allocated
        minutes_remaining = quota.remaining_minutes

        return SubscriptionResponse(
            status=subscription.get("status", "unknown"),
            plan_id=subscription.get("plan_id"),
            plan_name=plan.get("name") if plan else None,
            current_period_start=(
                str(subscription.get("current_period_start"))
                if subscription.get("current_period_start")
                else None
            ),
            current_period_end=(
                str(subscription.get("current_period_end"))
                if subscription.get("current_period_end")
                else None
            ),
            cancel_at_period_end=bool(subscription.get("cancel_at")),
            minutes_allocated=allocated,
            minutes_used=minutes_used,
            minutes_remaining=minutes_remaining,
            minutes_state=quota.state,
            purchased_price_option=subscription.get("purchased_price_option"),
            billing_portal_available=bool(subscription.get("billing_portal_available")),
        )

    except Exception as e:
        from app.domain.services.minutes_quota import MeteringUnavailable
        if isinstance(e, MeteringUnavailable):
            raise HTTPException(status_code=503, detail={"code": "usage_unavailable", "message": "Minute allowance is temporarily unavailable."}) from e
        logger.error(f"Failed to get subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get subscription: {str(e)}",
        )


@router.post(
    "/portal",
    response_model=PortalResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_UPDATE))],
)
async def create_portal_session(
    body: PortalRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Create a Stripe Customer Portal session.
    """
    try:
        from app.domain.services.billing_checkout import billing_return_urls

        return_url = billing_return_urls()

        result = await billing.create_portal_session(
            tenant_id=current_user.tenant_id, return_url=return_url
        )

        # Log event (Day 8)
        await audit_logger.log(
            event_type=AuditEvent.BILLING_UPDATED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=current_user.tenant_id,
            action="portal_session_created",
            description="User accessed billing portal",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return PortalResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create portal session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create portal session: {str(e)}",
        )


@router.post(
    "/cancel",
    response_model=CancelResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_ADMIN))],
)
async def cancel_subscription(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Cancel the current subscription.
    """
    try:
        result = await billing.cancel_subscription(
            tenant_id=current_user.tenant_id, cancel_at_period_end=True
        )

        # Log event (Day 8)
        await audit_logger.log(
            event_type=AuditEvent.BILLING_UPDATED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=current_user.tenant_id,
            action="subscription_cancelled",
            description="User cancelled their subscription",
            metadata={"tenant_id": current_user.tenant_id},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return CancelResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to cancel subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel subscription: {str(e)}",
        )


@router.get(
    "/usage",
    response_model=UsageSummaryResponse,
    dependencies=[Depends(require_permission(Permission.BILLING_READ))],
)
async def get_usage_summary(
    usage_type: Literal["minutes"] = "minutes",
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
):
    """
    Current calendar-month settled usage and actual tenant allowance, including
    recorded top-ups. This is not an invoice-period or monetary projection.
    """
    try:
        result = await billing.get_usage_summary(
            tenant_id=current_user.tenant_id, usage_type=usage_type
        )

        return UsageSummaryResponse(**result)

    except Exception as e:
        logger.error("Usage summary unavailable error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "usage_unavailable", "message": "Usage is temporarily unavailable."},
        )


@router.get("/invoices", dependencies=[Depends(require_permission(Permission.BILLING_READ))])
async def list_invoices(
    limit: int = Query(10, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
):
    """
    List invoices for the current tenant.
    """
    try:
        rows = await _invoice_rows(db_pool, current_user.tenant_id, limit=limit)
        return {"invoices": [_invoice_public(row) for row in rows], "count": len(rows)}

    except Exception as e:
        logger.error("Invoice list unavailable error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "invoices_unavailable",
                "message": "Invoices are temporarily unavailable.",
            },
        )


@router.get("/usage/daily", dependencies=[Depends(require_permission(Permission.BILLING_READ))])
async def get_daily_usage(
    days: int = 30,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
):
    """
    Daily minutes-used breakdown for the last `days` days (default 30).

    Aggregates settled parent and finalized transfer seconds by the parent's UTC day.
    Days with no calls return 0 so the response is a continuous time series
    ready for a sparkline.

    Minutes are summed with NO disposition filter, matching the dashboard and
    `minutes_quota.compute_minutes_status` (the gate that actually blocks
    calls). Legacy outbound rows remain usage; inbound parent time becomes
    usage only after its immutable finalize transaction commits. Connected
    and failed counts key on `outcome`, never `status` — see `call_outcomes`.
    The previous version filtered
    `status IN ('answered','completed','in_progress')`, which dropped every
    `ended` call, and then derived `failed` from `status NOT IN (...)` — a
    count the outer WHERE made structurally impossible to be anything but 0.
    """
    days = max(1, min(int(days), 90))
    try:
        tenant_uuid = UUID(str(current_user.tenant_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid tenant id")

    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    try:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(db_pool, str(tenant_uuid), timeout=5) as conn:
            rows = await conn.fetch(
                """
                    WITH parent_usage AS (
                    SELECT date_trunc('day', created_at AT TIME ZONE 'UTC')::date AS day,
                           COALESCE(
                               SUM(duration_seconds) FILTER (
                                   WHERE direction IS DISTINCT FROM 'inbound'
                                      OR billing_status='finalized'
                               ),
                               0
                           ) AS total_seconds,
                           COUNT(*) AS total_calls,
                           COUNT(*) FILTER (WHERE outcome = ANY($2::text[])) AS successful,
                           COUNT(*) FILTER (WHERE outcome = ANY($3::text[])) AS failed
                    FROM calls
                    WHERE tenant_id = $1
                      AND created_at >= $4
                      AND NOT is_test          -- test sessions are not billable
                    GROUP BY day
                    ), transfer_usage AS (
                      SELECT date_trunc('day', parent.created_at AT TIME ZONE 'UTC')::date AS day,
                             COALESCE(SUM(COALESCE(leg.duration_seconds,0)),0) AS total_seconds
                        FROM call_legs leg JOIN calls parent ON parent.id=leg.call_id
                       WHERE parent.tenant_id=$1 AND parent.created_at >= $4
                         AND NOT parent.is_test AND leg.leg_type='transfer'
                         AND leg.billing_status='finalized'
                       GROUP BY day
                    )
                    SELECT p.day,p.total_seconds+COALESCE(t.total_seconds,0) AS total_seconds,
                           p.total_calls,p.successful,p.failed
                      FROM parent_usage p LEFT JOIN transfer_usage t ON t.day=p.day
                    """,
                tenant_uuid,
                ANSWERED_OUTCOME_LIST,
                FAILED_OUTCOME_LIST,
                start,
            )
    except Exception as e:
        logger.error("Daily usage unavailable error_type=%s", type(e).__name__)
        raise HTTPException(
            503,
            detail={
                "code": "usage_unavailable",
                "message": "Daily usage is temporarily unavailable.",
            },
        ) from e

    by_day = {r["day"].isoformat(): r for r in rows}
    out: List[dict] = []
    for i in range(days):
        d = (start + timedelta(days=i)).date().isoformat()
        r = by_day.get(d)
        total_seconds = int(r["total_seconds"]) if r else 0
        total_calls = int(r["total_calls"]) if r else 0
        successful = int(r["successful"]) if r else 0
        failed = int(r["failed"]) if r else 0
        out.append(
            {
                "date": d,
                "minutesUsed": total_seconds // 60,
                # Daily whole minutes are floored separately. Retain seconds so
                # clients never imply sum(daily floors) equals the monthly meter.
                "secondsUsed": total_seconds,
                "totalCalls": total_calls,
                "successfulCalls": successful,
                "failedCalls": failed,
            }
        )
    return out


async def _invoice_rows(pool, tenant_id, *, invoice_id=None, limit=10):
    """Latest recorded provider capture, with two explicit tenant predicates."""
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(pool, str(tenant_id), timeout=5) as conn:
        return await conn.fetch(
            """SELECT i.*,snapshot.projection,snapshot.captured_at
                 FROM invoices i
                 LEFT JOIN LATERAL (
                   SELECT projection,captured_at FROM invoice_snapshots
                    WHERE invoice_id=i.id AND tenant_id=i.tenant_id
                    ORDER BY id DESC LIMIT 1
                 ) snapshot ON TRUE
                WHERE i.tenant_id=$1 AND ($2::uuid IS NULL OR i.id=$2)
                ORDER BY i.created_at DESC,i.id DESC LIMIT $3""",
            UUID(str(tenant_id)),
            invoice_id,
            limit,
        )


def _invoice_public(row):
    """Exact recorded minor units. Null means unrecorded, not a zero charge."""
    from app.domain.services.billing_catalog import _EXPONENTS
    from app.domain.services.billing_invoice_projection import provider_document_url

    record = dict(row)

    def date(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def amount(value):
        return value if type(value) is int and abs(value) <= 2**53 - 1 else None

    currency = record.get("currency")
    currency = currency.lower() if isinstance(currency, str) and currency else None
    output = {
        "id": str(record["id"]),
        "tenant_id": str(record["tenant_id"]),
        "stripe_invoice_id": record["stripe_invoice_id"],
        "status": record["status"],
        "invoice_pdf": record.get("invoice_pdf"),
        "hosted_invoice_url": record.get("hosted_invoice_url"),
        "paid_at": date(record.get("paid_at")),
        "due_date": date(record.get("due_date")),
        "created_at": date(record.get("created_at")),
        "detail_status": "unavailable",
        "detail_source": "stored_summary",
        "captured_at": None,
        "source": "stripe",
        "source_reference": None,
        "provider_mode": None,
        "invoice_number": None,
        "currency": currency,
        "currency_exponent": _EXPONENTS.get(currency),
        "amount_due": amount(record.get("amount_due")),
        "amount_paid": amount(record.get("amount_paid")),
        "amount_remaining": None,
        "subtotal": None,
        "total": None,
        "discounts": None,
        "taxes": None,
        "credits": None,
        "refunds": None,
        "line_items": None,
        "period_start": date(record.get("period_start")),
        "period_end": date(record.get("period_end")),
        "errors": [],
    }
    projection = record.get("projection")
    if isinstance(projection, str):
        try:
            projection = json.loads(projection)
        except (TypeError, ValueError):
            projection = None
    if isinstance(projection, dict):
        fields = (
            "detail_status",
            "source",
            "source_reference",
            "provider_mode",
            "invoice_number",
            "currency",
            "currency_exponent",
            "amount_due",
            "amount_paid",
            "amount_remaining",
            "subtotal",
            "total",
            "discounts",
            "taxes",
            "credits",
            "refunds",
            "line_items",
            "period_start",
            "period_end",
            "invoice_pdf",
            "hosted_invoice_url",
            "status",
            "paid_at",
            "due_date",
            "errors",
        )
        output.update({key: projection[key] for key in fields if key in projection})
        output.update(
            detail_source="provider_snapshot", captured_at=date(record.get("captured_at"))
        )
    for key in ("invoice_pdf", "hosted_invoice_url"):
        output[key] = provider_document_url(output[key])
    for key in ("amount_due", "amount_paid", "amount_remaining", "subtotal", "total"):
        output[key] = amount(output[key])
    return output


@router.get(
    "/invoices/{invoice_id}", dependencies=[Depends(require_permission(Permission.BILLING_READ))]
)
async def get_invoice(
    invoice_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
):
    """
    Single invoice detail, tenant-scoped.
    """
    try:
        inv_uuid = UUID(invoice_id)
        tenant_uuid = UUID(str(current_user.tenant_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")

    try:
        rows = await _invoice_rows(db_pool, tenant_uuid, invoice_id=inv_uuid, limit=1)
    except Exception as e:
        logger.error("Invoice unavailable error_type=%s", type(e).__name__)
        raise HTTPException(
            503,
            detail={
                "code": "invoice_unavailable",
                "message": "Invoice is temporarily unavailable.",
            },
        ) from e

    if not rows:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return _invoice_public(rows[0])


@router.get("/overage-alerts", dependencies=[Depends(require_permission(Permission.BILLING_READ))])
async def get_overage_alerts(
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
    db_pool=Depends(get_db_pool),
):
    """
    Current settled usage above the tenant's actual allowance. Without an
    approved monetary overage policy, this reports usage only, never a charge.
    """
    try:
        usage = await billing.get_usage_summary(current_user.tenant_id)
    except Exception as e:
        logger.error("Overage status unavailable error_type=%s", type(e).__name__)
        raise HTTPException(
            503,
            detail={"code": "usage_unavailable", "message": "Usage is temporarily unavailable."},
        ) from e
    if usage["unlimited"]:
        return []
    allocated, minutes_used = usage["allocated"], usage["total_used"]
    alerts: List[dict] = []
    if minutes_used > allocated:
        exceeded = minutes_used - allocated
        alerts.append(
            {
                "type": "minutes",
                "currentUsage": minutes_used,
                "limit": allocated,
                "exceededBy": exceeded,
                # No approved overage pricing policy is stored. Crossing a usage
                # allowance is not evidence of an additional monetary charge.
                "estimatedCharge": None,
                "currency": None,
                "currency_exponent": None,
                "severity": "critical",
            }
        )
    return alerts


@router.get("/adjustments", dependencies=[Depends(require_permission(Permission.BILLING_READ))])
async def get_adjustments(
    current_user: CurrentUser = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
):
    """
    Existing signed top-up movements, with no invented invoice relationship.
    """
    from app.domain.services.topup_service import TopupService, public_ledger_entry

    try:
        rows = await TopupService(db_pool).ledger(current_user.tenant_id, limit=100)
        return [public_ledger_entry(row) for row in rows]
    except Exception as exc:
        logger.error("Billing adjustments unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(
            503,
            detail={
                "code": "adjustments_unavailable",
                "message": "Billing movements are temporarily unavailable.",
            },
        ) from exc


@router.get("/plans")
async def list_billing_plans(
    db_client: Client = Depends(get_db_client),
):
    """
    Convenience pass-through to the plans catalog so the frontend can
    fetch /billing/plans from a single billing module.
    """
    try:
        from app.domain.services.billing_catalog import list_plan_catalog

        return await list_plan_catalog(db_client.pool)
    except Exception as e:
        logger.error(f"Failed to list plans: {e}")
        raise HTTPException(status_code=500, detail="Failed to list plans")


@router.get("/config")
async def get_billing_config():
    """
    Get billing configuration status.

    Useful for frontend to determine if billing is in mock mode.
    """
    from app.domain.services.billing_mode import get_billing_mode

    mode = get_billing_mode()
    stripe_configured = mode in {"live", "test"}

    return {
        "stripe_configured": stripe_configured,
        "mock_mode": mode == "mock",
        "billing_mode": mode,
        "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY") if stripe_configured else None,
    }
