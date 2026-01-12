"""
Billing API Endpoints
Handles Stripe subscription management and payment operations
"""
import os
import logging
from fastapi import APIRouter, HTTPException, Depends, Request, status
from pydantic import BaseModel
from typing import Optional
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser
from app.domain.services.billing_service import BillingService

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

def get_billing_service(supabase: Client = Depends(get_supabase)) -> BillingService:
    """Dependency to get billing service instance"""
    return BillingService(supabase)


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
    billing: BillingService = Depends(get_billing_service)
):
    """
    Create a Stripe Checkout Session for subscribing to a plan.
    
    Returns a URL to redirect the user to Stripe's hosted checkout page.
    After successful payment, user is redirected to success_url.
    
    In mock mode (no Stripe key configured), returns a mock session.
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
    supabase: Client = Depends(get_supabase)
):
    """
    Handle Stripe webhook events.
    
    This endpoint should be configured in Stripe Dashboard:
    https://dashboard.stripe.com/webhooks
    
    Events handled:
    - checkout.session.completed
    - customer.subscription.created
    - customer.subscription.updated
    - customer.subscription.deleted
    - invoice.paid
    - invoice.payment_failed
    """
    billing = BillingService(supabase)
    
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
    billing: BillingService = Depends(get_billing_service)
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
        
        if not subscription:
            return SubscriptionResponse(
                status="inactive",
                minutes_allocated=current_user.minutes_remaining,
                minutes_used=0,
                minutes_remaining=current_user.minutes_remaining
            )
        
        # Get plan info
        plan = subscription.get("plans") or subscription.get("plan") or {}
        
        return SubscriptionResponse(
            status=subscription.get("status", "unknown"),
            plan_id=subscription.get("plan_id"),
            plan_name=plan.get("name") if plan else None,
            current_period_start=str(subscription.get("current_period_start")) if subscription.get("current_period_start") else None,
            current_period_end=str(subscription.get("current_period_end")) if subscription.get("current_period_end") else None,
            cancel_at_period_end=bool(subscription.get("cancel_at")),
            minutes_allocated=plan.get("minutes", 0) if plan else 0,
            minutes_used=current_user.minutes_remaining,  # This would need proper calculation
            minutes_remaining=current_user.minutes_remaining
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
    billing: BillingService = Depends(get_billing_service)
):
    """
    Create a Stripe Customer Portal session.
    
    The Customer Portal allows users to:
    - View and download invoices
    - Update payment methods
    - Cancel or modify subscription
    - View billing history
    """
    try:
        default_urls = get_default_urls(request)
        return_url = body.return_url or f"{default_urls['success_url'].rsplit('/', 1)[0]}"
        
        result = await billing.create_portal_session(
            tenant_id=current_user.tenant_id,
            return_url=return_url
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
    current_user: CurrentUser = Depends(get_current_user),
    billing: BillingService = Depends(get_billing_service)
):
    """
    Cancel the current subscription.
    
    By default, cancels at the end of the current billing period.
    The subscription remains active until the period ends.
    """
    try:
        result = await billing.cancel_subscription(
            tenant_id=current_user.tenant_id,
            cancel_at_period_end=True
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
    supabase: Client = Depends(get_supabase)
):
    """
    List invoices for the current tenant.
    """
    try:
        result = supabase.table("invoices").select(
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
