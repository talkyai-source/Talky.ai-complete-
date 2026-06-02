"""
Billing API Endpoints
Handles Stripe subscription management and payment operations
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends, Request, status
from pydantic import BaseModel
from typing import List, Optional
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser, get_audit_logger, get_db_pool
from app.domain.services.billing_service import BillingService
from app.domain.services.audit_logger import AuditEvent, AuditLogger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ============================================
# Request/Response Models
# ============================================

class CreateCheckoutRequest(BaseModel):
    """Request to create a checkout session"""
    plan_id: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CreateCheckoutResponse(BaseModel):
    """Checkout session response"""
    session_id: str
    checkout_url: str
    mock_mode: bool = False
    message: Optional[str] = None


class PortalRequest(BaseModel):
    """Request to create customer portal session"""
    return_url: Optional[str] = None


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


# ============================================
# Helper Functions
# ============================================

def get_billing_service(db_client: Client = Depends(get_db_client)) -> BillingService:
    """Dependency to get billing service instance"""
    return BillingService(db_client)


def get_default_urls(request: Request):
    """Get default success/cancel URLs based on request origin"""
    origin = request.headers.get("origin", "http://localhost:3000")
    return {
        "success_url": f"{origin}/dashboard/billing/success",
        "cancel_url": f"{origin}/dashboard/billing/canceled"
    }


# ============================================
# Endpoints
# ============================================

@router.post("/create-checkout-session", response_model=CreateCheckoutResponse)
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
        # Get default URLs if not provided
        default_urls = get_default_urls(request)
        success_url = body.success_url or default_urls["success_url"]
        cancel_url = body.cancel_url or default_urls["cancel_url"]
        
        # Validate plan exists
        result = await billing.create_checkout_session(
            tenant_id=current_user.tenant_id,
            email=current_user.email,
            plan_id=body.plan_id,
            success_url=success_url,
            cancel_url=cancel_url,
            business_name=current_user.business_name
        )

        # Log event (Day 8)
        await audit_logger.log(
            event_type=AuditEvent.BILLING_UPDATED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=current_user.tenant_id,
            action="checkout_session_created",
            description=f"User initiated checkout for plan: {body.plan_id}",
            metadata={"plan_id": body.plan_id, "mock_mode": result.get("mock_mode", False)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        
        return CreateCheckoutResponse(**result)
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to create checkout session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create checkout session: {str(e)}"
        )


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
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    
    if not signature and not billing.mock_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature"
        )
    
    try:
        result = await billing.handle_webhook(payload, signature)
        return result
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Webhook handling failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook handling failed"
        )


@router.get("/subscription", response_model=SubscriptionResponse)
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

        # Live `minutes_used` from the calls table; matches the dashboard
        # endpoint and the auth/profile path.
        from app.services.scripts.tenant_minutes import compute_tenant_minutes_used
        minutes_used = await compute_tenant_minutes_used(
            db_pool,
            tenant_id=current_user.tenant_id,
        )

        if not subscription:
            allocated = (
                current_user.minutes_remaining + minutes_used
                if current_user.minutes_remaining is not None
                else 0
            )
            return SubscriptionResponse(
                status="inactive",
                minutes_allocated=allocated,
                minutes_used=minutes_used,
                minutes_remaining=current_user.minutes_remaining,
            )

        # Get plan info
        plan = subscription.get("plans") or subscription.get("plan") or {}
        allocated = int(plan.get("minutes", 0) or 0) if plan else 0
        minutes_remaining = max(0, allocated - minutes_used)

        return SubscriptionResponse(
            status=subscription.get("status", "unknown"),
            plan_id=subscription.get("plan_id"),
            plan_name=plan.get("name") if plan else None,
            current_period_start=str(subscription.get("current_period_start")) if subscription.get("current_period_start") else None,
            current_period_end=str(subscription.get("current_period_end")) if subscription.get("current_period_end") else None,
            cancel_at_period_end=bool(subscription.get("cancel_at")),
            minutes_allocated=allocated,
            minutes_used=minutes_used,
            minutes_remaining=minutes_remaining,
        )
    
    except Exception as e:
        logger.error(f"Failed to get subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get subscription: {str(e)}"
        )


@router.post("/portal", response_model=PortalResponse)
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
        default_urls = get_default_urls(request)
        return_url = body.return_url or f"{default_urls['success_url'].rsplit('/', 1)[0]}"
        
        result = await billing.create_portal_session(
            tenant_id=current_user.tenant_id,
            return_url=return_url
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to create portal session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create portal session: {str(e)}"
        )


@router.post("/cancel", response_model=CancelResponse)
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
            tenant_id=current_user.tenant_id,
            cancel_at_period_end=True
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to cancel subscription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel subscription: {str(e)}"
        )


@router.get("/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    usage_type: str = "minutes",
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service)
):
    """
    Get usage summary for the current billing period.
    
    Returns:
    - Total minutes/units used
    - Allocated amount (from plan)
    - Remaining amount
    - Overage (if any)
    """
    try:
        result = await billing.get_usage_summary(
            tenant_id=current_user.tenant_id,
            usage_type=usage_type
        )
        
        return UsageSummaryResponse(**result)
    
    except Exception as e:
        logger.error(f"Failed to get usage summary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get usage summary: {str(e)}"
        )


@router.get("/invoices")
async def list_invoices(
    limit: int = 10,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    List invoices for the current tenant.
    """
    try:
        result = db_client.table("invoices").select(
            "*"
        ).eq("tenant_id", current_user.tenant_id).order(
            "created_at", desc=True
        ).limit(limit).execute()
        
        return {
            "invoices": result.data if result.data else [],
            "count": len(result.data) if result.data else 0
        }
    
    except Exception as e:
        logger.error(f"Failed to list invoices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list invoices: {str(e)}"
        )


@router.get("/usage/daily")
async def get_daily_usage(
    days: int = 30,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
):
    """
    Daily minutes-used breakdown for the last `days` days (default 30).

    Aggregates `duration_seconds` from the `calls` table grouped by day,
    same call-status predicate as compute_tenant_minutes_used. Days with
    no calls return 0 so the response is a continuous time series ready
    for a sparkline.
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
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                rows = await conn.fetch(
                    """
                    SELECT date_trunc('day', created_at)::date AS day,
                           COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                           COUNT(*) AS total_calls,
                           COUNT(*) FILTER (WHERE status IN ('answered','completed')) AS successful,
                           COUNT(*) FILTER (WHERE status NOT IN ('answered','completed','in_progress')) AS failed
                    FROM calls
                    WHERE tenant_id = $1
                      AND status = ANY($2::text[])
                      AND created_at >= $3
                    GROUP BY day
                    """,
                    tenant_uuid,
                    ["answered", "completed", "in_progress"],
                    start,
                )
    except Exception as e:
        logger.error(f"Daily usage query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load daily usage")

    by_day = {r["day"].isoformat(): r for r in rows}
    out: List[dict] = []
    for i in range(days):
        d = (start + timedelta(days=i)).date().isoformat()
        r = by_day.get(d)
        total_seconds = int(r["total_seconds"]) if r else 0
        total_calls = int(r["total_calls"]) if r else 0
        successful = int(r["successful"]) if r else 0
        failed = int(r["failed"]) if r else 0
        out.append({
            "date": d,
            "minutesUsed": total_seconds // 60,
            "totalCalls": total_calls,
            "successfulCalls": successful,
            "failedCalls": failed,
        })
    return out


@router.get("/invoices/{invoice_id}")
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
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                row = await conn.fetchrow(
                    """
                    SELECT i.*, p.name AS plan_name, p.minutes AS plan_minutes,
                           p.concurrent_calls AS plan_concurrent_calls
                    FROM invoices i
                    LEFT JOIN subscriptions s ON s.stripe_subscription_id = i.stripe_subscription_id
                    LEFT JOIN plans p ON p.id = s.plan_id
                    WHERE i.id = $1 AND i.tenant_id = $2
                    """,
                    inv_uuid,
                    tenant_uuid,
                )
    except Exception as e:
        logger.error(f"Invoice fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to load invoice")

    if not row:
        raise HTTPException(status_code=404, detail="Invoice not found")

    amount_due = (row["amount_due"] or 0) / 100.0
    amount_paid = (row["amount_paid"] or 0) / 100.0
    plan_minutes = int(row["plan_minutes"] or 0)
    plan_concurrent = int(row["plan_concurrent_calls"] or 0)

    return {
        "id": str(row["id"]),
        "tenantId": str(row["tenant_id"]),
        "billingPeriodStart": row["period_start"].isoformat() if row["period_start"] else None,
        "billingPeriodEnd": row["period_end"].isoformat() if row["period_end"] else None,
        "planName": row["plan_name"] or "—",
        "planFee": amount_due,
        "includedMinutes": plan_minutes,
        "usedMinutes": plan_minutes,
        "overageMinutes": 0,
        "overageCharges": 0,
        "includedConcurrentCalls": plan_concurrent,
        "peakConcurrentCalls": 0,
        "adjustments": [],
        "subtotal": amount_due,
        "tax": 0,
        "totalAmount": amount_due,
        "status": row["status"],
        "paidAt": row["paid_at"].isoformat() if row["paid_at"] else None,
        "dueDate": row["due_date"].isoformat() if row["due_date"] else None,
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "currency": row["currency"] or "usd",
        "amountPaid": amount_paid,
        "invoicePdf": row["invoice_pdf"],
        "hostedInvoiceUrl": row["hosted_invoice_url"],
        "lineItems": [
            {
                "description": f"{row['plan_name'] or 'Plan'} subscription",
                "quantity": 1,
                "unitPrice": amount_due,
                "total": amount_due,
            }
        ],
    }


@router.get("/overage-alerts")
async def get_overage_alerts(
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service),
    db_pool=Depends(get_db_pool),
):
    """
    Derived overage alerts: emits an alert if current usage exceeds the
    plan's included minutes. No dedicated table; computed on the fly so
    the UI always reflects the live state from `calls` + `plans`.
    """
    try:
        subscription = await billing.get_subscription(current_user.tenant_id)
        from app.services.scripts.tenant_minutes import compute_tenant_minutes_used
        minutes_used = await compute_tenant_minutes_used(
            db_pool, tenant_id=current_user.tenant_id,
        )
    except Exception as e:
        logger.error(f"Overage alerts query failed: {e}")
        return []

    plan = (subscription or {}).get("plans") or (subscription or {}).get("plan") or {}
    allocated = int(plan.get("minutes", 0) or 0)
    if allocated <= 0:
        return []

    alerts: List[dict] = []
    if minutes_used > allocated:
        exceeded = minutes_used - allocated
        # Standard $0.10/min overage in mock; real rate would live on plans table
        rate = float(plan.get("overage_per_minute", 0.10) or 0.10)
        alerts.append({
            "type": "minutes",
            "currentUsage": minutes_used,
            "limit": allocated,
            "exceededBy": exceeded,
            "estimatedCharge": round(exceeded * rate, 2),
            "severity": "critical",
        })
    return alerts


@router.get("/adjustments")
async def get_adjustments(
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Billing adjustments / credits ledger. No backing table yet — returns
    an empty list so the UI renders an honest empty state instead of
    pulling from a mock constant.
    """
    return []


@router.get("/plans")
async def list_billing_plans(
    db_client: Client = Depends(get_db_client),
):
    """
    Convenience pass-through to the plans catalog so the frontend can
    fetch /billing/plans from a single billing module.
    """
    try:
        response = db_client.table("plans").select("*").order("price").execute()
        return response.data or []
    except Exception as e:
        logger.error(f"Failed to list plans: {e}")
        raise HTTPException(status_code=500, detail="Failed to list plans")


@router.get("/config")
async def get_billing_config():
    """
    Get billing configuration status.
    
    Useful for frontend to determine if billing is in mock mode.
    """
    stripe_configured = bool(os.getenv("STRIPE_SECRET_KEY"))
    mock_mode = os.getenv("STRIPE_MOCK_MODE", "false").lower() == "true" or not stripe_configured
    
    return {
        "stripe_configured": stripe_configured,
        "mock_mode": mock_mode,
        "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY") if stripe_configured else None
    }
