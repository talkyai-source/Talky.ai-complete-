"""
Billing Service
Handles Stripe subscription operations and billing logic
"""
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from supabase import Client

logger = logging.getLogger(__name__)

# Try to import stripe, but make it optional for development
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    logger.warning("Stripe SDK not installed. Billing features will use mock mode.")


class BillingService:
    """
    Service for handling Stripe billing operations.
    
    Supports mock mode when:
    - Stripe SDK is not installed
    - STRIPE_SECRET_KEY is not configured
    - STRIPE_MOCK_MODE environment variable is set to 'true'
    """
    
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.mock_mode = self._should_use_mock_mode()
        
        if not self.mock_mode and STRIPE_AVAILABLE:
            stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
            self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        
        logger.info(f"BillingService initialized (mock_mode={self.mock_mode})")
    
    def _should_use_mock_mode(self) -> bool:
        """Determine if we should use mock mode"""
        if not STRIPE_AVAILABLE:
            return True
        if os.getenv("STRIPE_MOCK_MODE", "false").lower() == "true":
            return True
        if not os.getenv("STRIPE_SECRET_KEY"):
            return True
        return False
    
    # =========================================================================
    # Customer Management
    # =========================================================================
    
    async def create_or_get_customer(
        self, 
        tenant_id: str, 
        email: str,
        business_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get existing Stripe customer or create a new one.
        
        Returns:
            Dict with customer_id and whether it was newly created
        """
        # Check if tenant already has a Stripe customer
        tenant = self.supabase.table("tenants").select(
            "stripe_customer_id"
        ).eq("id", tenant_id).single().execute()
        
        existing_customer_id = tenant.data.get("stripe_customer_id") if tenant.data else None
        
        if existing_customer_id:
            return {
                "customer_id": existing_customer_id,
                "created": False
            }
        
        # Create new customer
        if self.mock_mode:
            customer_id = f"cus_mock_{tenant_id[:8]}"
        else:
            customer = stripe.Customer.create(
                email=email,
                name=business_name,
                metadata={
                    "tenant_id": tenant_id
                }
            )
            customer_id = customer.id
        
        # Update tenant with customer ID
        self.supabase.table("tenants").update({
            "stripe_customer_id": customer_id
        }).eq("id", tenant_id).execute()
        
        logger.info(f"Created Stripe customer {customer_id} for tenant {tenant_id}")
        
        return {
            "customer_id": customer_id,
            "created": True
        }
    
    # =========================================================================
    # Checkout Session
    # =========================================================================
    
    async def create_checkout_session(
        self,
        tenant_id: str,
        email: str,
        plan_id: str,
        success_url: str,
        cancel_url: str,
        business_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a Stripe Checkout Session for subscription.
        
        Returns:
            Dict with checkout_url and session_id
        """
        # Get or create customer
        customer_result = await self.create_or_get_customer(
            tenant_id, email, business_name
        )
        customer_id = customer_result["customer_id"]
        
        # Get plan's stripe_price_id
        plan = self.supabase.table("plans").select(
            "stripe_price_id, name"
        ).eq("id", plan_id).single().execute()
        
        if not plan.data:
            raise ValueError(f"Plan not found: {plan_id}")
        
        stripe_price_id = plan.data.get("stripe_price_id")
        
        if not stripe_price_id:
            raise ValueError(f"Plan {plan_id} has no stripe_price_id configured")
        
        if self.mock_mode:
            # Return mock checkout session
            session_id = f"cs_mock_{tenant_id[:8]}_{plan_id}"
            return {
                "session_id": session_id,
                "checkout_url": f"{success_url}?session_id={session_id}&mock=true",
                "mock_mode": True,
                "message": "Mock checkout session created. Configure STRIPE_SECRET_KEY for real payments."
            }
        
        # Create real Stripe Checkout Session
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{
                "price": stripe_price_id,
                "quantity": 1
            }],
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            metadata={
                "tenant_id": tenant_id,
                "plan_id": plan_id
            },
            subscription_data={
                "metadata": {
                    "tenant_id": tenant_id,
                    "plan_id": plan_id
                }
            }
        )
        
        logger.info(f"Created checkout session {session.id} for tenant {tenant_id}")
        
        return {
            "session_id": session.id,
            "checkout_url": session.url,
            "mock_mode": False
        }
    
    # =========================================================================
    # Customer Portal
    # =========================================================================
    
    async def create_portal_session(
        self,
        tenant_id: str,
        return_url: str
    ) -> Dict[str, Any]:
        """
        Create a Stripe Customer Portal session for managing subscription.
        """
        # Get customer ID
        tenant = self.supabase.table("tenants").select(
            "stripe_customer_id"
        ).eq("id", tenant_id).single().execute()
        
        customer_id = tenant.data.get("stripe_customer_id") if tenant.data else None
        
        if not customer_id:
            raise ValueError("No Stripe customer found for this tenant")
        
        if self.mock_mode:
            return {
                "portal_url": f"{return_url}?mock_portal=true",
                "mock_mode": True,
                "message": "Mock portal session. Configure STRIPE_SECRET_KEY for real portal."
            }
        
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )
        
        return {
            "portal_url": session.url,
            "mock_mode": False
        }
    
    # =========================================================================
    # Subscription Management
    # =========================================================================
    
    async def get_subscription(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """
        Get current subscription for a tenant.
        """
        subscription = self.supabase.table("subscriptions").select(
            "*, plans(name, price, minutes, agents)"
        ).eq("tenant_id", tenant_id).order(
            "created_at", desc=True
        ).limit(1).execute()
        
        if not subscription.data:
            # Check tenants table for basic subscription info
            tenant = self.supabase.table("tenants").select(
                "subscription_status, stripe_subscription_id, plan_id, plans(name, price, minutes)"
            ).eq("id", tenant_id).single().execute()
            
            if tenant.data and tenant.data.get("subscription_status") != "inactive":
                return {
                    "status": tenant.data.get("subscription_status", "inactive"),
                    "plan": tenant.data.get("plans"),
                    "stripe_subscription_id": tenant.data.get("stripe_subscription_id")
                }
            return None
        
        return subscription.data[0]
    
    async def cancel_subscription(
        self, 
        tenant_id: str, 
        cancel_at_period_end: bool = True
    ) -> Dict[str, Any]:
        """
        Cancel a subscription (at period end by default).
        """
        tenant = self.supabase.table("tenants").select(
            "stripe_subscription_id"
        ).eq("id", tenant_id).single().execute()
        
        subscription_id = tenant.data.get("stripe_subscription_id") if tenant.data else None
        
        if not subscription_id:
            raise ValueError("No active subscription found")
        
        if self.mock_mode:
            # Update local state in mock mode
            self.supabase.table("tenants").update({
                "subscription_status": "canceled"
            }).eq("id", tenant_id).execute()
            
            return {
                "status": "canceled",
                "mock_mode": True,
                "message": "Subscription canceled (mock mode)"
            }
        
        subscription = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=cancel_at_period_end
        )
        
        # Update local state
        self.supabase.table("tenants").update({
            "subscription_status": subscription.status
        }).eq("id", tenant_id).execute()
        
        self.supabase.table("subscriptions").update({
            "status": subscription.status,
            "cancel_at": datetime.fromtimestamp(subscription.cancel_at) if subscription.cancel_at else None,
            "canceled_at": datetime.now()
        }).eq("stripe_subscription_id", subscription_id).execute()
        
        return {
            "status": subscription.status,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "mock_mode": False
        }
    
    # =========================================================================
    # Webhook Handlers
    # =========================================================================
    
    async def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """
        Verify and handle Stripe webhook events.
        """
        if self.mock_mode:
            return {"status": "ignored", "reason": "mock_mode"}
        
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Webhook signature verification failed: {e}")
            raise ValueError("Invalid webhook signature")
        
        event_type = event["type"]
        data = event["data"]["object"]
        
        logger.info(f"Processing webhook event: {event_type}")
        
        handlers = {
            "checkout.session.completed": self._handle_checkout_completed,
            "customer.subscription.created": self._handle_subscription_created,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "invoice.paid": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_invoice_payment_failed,
        }
        
        handler = handlers.get(event_type)
        if handler:
            await handler(data)
            return {"status": "handled", "event_type": event_type}
        
        return {"status": "ignored", "event_type": event_type}
    
    async def _handle_checkout_completed(self, session: Dict):
        """Handle checkout.session.completed event"""
        tenant_id = session.get("metadata", {}).get("tenant_id")
        plan_id = session.get("metadata", {}).get("plan_id")
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")
        
        if not tenant_id:
            logger.warning("Checkout completed but no tenant_id in metadata")
            return
        
        # Update tenant
        self.supabase.table("tenants").update({
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "subscription_status": "active",
            "plan_id": plan_id
        }).eq("id", tenant_id).execute()
        
        # Get plan details to update minutes
        if plan_id:
            plan = self.supabase.table("plans").select("minutes").eq("id", plan_id).single().execute()
            if plan.data:
                self.supabase.table("tenants").update({
                    "minutes_allocated": plan.data.get("minutes", 0),
                    "minutes_used": 0
                }).eq("id", tenant_id).execute()
        
        logger.info(f"Activated subscription for tenant {tenant_id}")
    
    async def _handle_subscription_created(self, subscription: Dict):
        """Handle customer.subscription.created event"""
        await self._sync_subscription(subscription)
    
    async def _handle_subscription_updated(self, subscription: Dict):
        """Handle customer.subscription.updated event"""
        await self._sync_subscription(subscription)
    
    async def _handle_subscription_deleted(self, subscription: Dict):
        """Handle customer.subscription.deleted event"""
        tenant_id = subscription.get("metadata", {}).get("tenant_id")
        
        if tenant_id:
            self.supabase.table("tenants").update({
                "subscription_status": "canceled",
                "stripe_subscription_id": None
            }).eq("id", tenant_id).execute()
        
        # Update subscription record
        self.supabase.table("subscriptions").update({
            "status": "canceled",
            "canceled_at": datetime.now()
        }).eq("stripe_subscription_id", subscription["id"]).execute()
    
    async def _handle_invoice_paid(self, invoice: Dict):
        """Handle invoice.paid event"""
        # Store invoice record
        self.supabase.table("invoices").upsert({
            "stripe_invoice_id": invoice["id"],
            "stripe_subscription_id": invoice.get("subscription"),
            "tenant_id": invoice.get("metadata", {}).get("tenant_id"),
            "amount_due": invoice.get("amount_due", 0),
            "amount_paid": invoice.get("amount_paid", 0),
            "currency": invoice.get("currency", "usd"),
            "status": "paid",
            "invoice_pdf": invoice.get("invoice_pdf"),
            "hosted_invoice_url": invoice.get("hosted_invoice_url"),
            "paid_at": datetime.now()
        }, on_conflict="stripe_invoice_id").execute()
    
    async def _handle_invoice_payment_failed(self, invoice: Dict):
        """Handle invoice.payment_failed event"""
        subscription_id = invoice.get("subscription")
        
        if subscription_id:
            self.supabase.table("subscriptions").update({
                "status": "past_due"
            }).eq("stripe_subscription_id", subscription_id).execute()
            
            # Update tenant status
            self.supabase.table("tenants").update({
                "subscription_status": "past_due"
            }).eq("stripe_subscription_id", subscription_id).execute()
    
    async def _sync_subscription(self, subscription: Dict):
        """Sync subscription data from Stripe to database"""
        tenant_id = subscription.get("metadata", {}).get("tenant_id")
        plan_id = subscription.get("metadata", {}).get("plan_id")
        
        subscription_data = {
            "stripe_subscription_id": subscription["id"],
            "stripe_customer_id": subscription["customer"],
            "status": subscription["status"],
            "current_period_start": datetime.fromtimestamp(subscription["current_period_start"]),
            "current_period_end": datetime.fromtimestamp(subscription["current_period_end"]),
        }
        
        if tenant_id:
            subscription_data["tenant_id"] = tenant_id
        if plan_id:
            subscription_data["plan_id"] = plan_id
        
        # Upsert subscription record
        self.supabase.table("subscriptions").upsert(
            subscription_data,
            on_conflict="stripe_subscription_id"
        ).execute()
        
        # Update tenant
        if tenant_id:
            self.supabase.table("tenants").update({
                "subscription_status": subscription["status"],
                "stripe_subscription_id": subscription["id"]
            }).eq("id", tenant_id).execute()
    
    # =========================================================================
    # Usage Tracking (for metered billing)
    # =========================================================================
    
    async def record_usage(
        self, 
        tenant_id: str, 
        quantity: int,
        usage_type: str = "minutes"
    ) -> Dict[str, Any]:
        """
        Record usage for metered billing.
        
        This stores usage locally and optionally reports to Stripe.
        """
        # Store usage record
        result = self.supabase.table("usage_records").insert({
            "tenant_id": tenant_id,
            "quantity": quantity,
            "usage_type": usage_type,
            "reported_to_stripe": False
        }).execute()
        
        return {
            "recorded": True,
            "usage_id": result.data[0]["id"] if result.data else None
        }
    
    async def get_usage_summary(
        self, 
        tenant_id: str,
        usage_type: str = "minutes"
    ) -> Dict[str, Any]:
        """Get usage summary for the current billing period"""
        # Get current subscription period
        subscription = await self.get_subscription(tenant_id)
        
        # Get total usage
        usage = self.supabase.table("usage_records").select(
            "quantity"
        ).eq("tenant_id", tenant_id).eq("usage_type", usage_type).execute()
        
        total_usage = sum(record["quantity"] for record in usage.data) if usage.data else 0
        
        # Get tenant allocation
        tenant = self.supabase.table("tenants").select(
            "minutes_allocated, minutes_used"
        ).eq("id", tenant_id).single().execute()
        
        allocated = tenant.data.get("minutes_allocated", 0) if tenant.data else 0
        
        return {
            "usage_type": usage_type,
            "total_used": total_usage,
            "allocated": allocated,
            "remaining": max(0, allocated - total_usage),
            "overage": max(0, total_usage - allocated)
        }
