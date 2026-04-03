"""
Billing Service - Complete implementation with Stripe & notifications
Handles subscription management, payments, webhooks, and billing notifications.

Day 8: Fully integrated billing with:
- Stripe Checkout & subscriptions
- Webhook event handling
- Email/Slack notifications
- Usage tracking & metering
- Invoice management
"""
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from app.core.postgres_adapter import Client

from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.notification_service import (
    get_notification_service,
    NotificationChannel,
)

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
    
    def __init__(self, db_client: Client, audit_logger: Optional[AuditLogger] = None):
        self.db_client = db_client
        self.audit_logger = audit_logger
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
        tenant = self.db_client.table("tenants").select(
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
        self.db_client.table("tenants").update({
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
        plan = self.db_client.table("plans").select(
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
        tenant = self.db_client.table("tenants").select(
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
        subscription = self.db_client.table("subscriptions").select(
            "*, plans(name, price, minutes, agents)"
        ).eq("tenant_id", tenant_id).order(
            "created_at", desc=True
        ).limit(1).execute()
        
        if not subscription.data:
            # Check tenants table for basic subscription info
            tenant = self.db_client.table("tenants").select(
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
        tenant = self.db_client.table("tenants").select(
            "stripe_subscription_id"
        ).eq("id", tenant_id).single().execute()
        
        subscription_id = tenant.data.get("stripe_subscription_id") if tenant.data else None
        
        if not subscription_id:
            raise ValueError("No active subscription found")
        
        if self.mock_mode:
            # Update local state in mock mode
            self.db_client.table("tenants").update({
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
        self.db_client.table("tenants").update({
            "subscription_status": subscription.status
        }).eq("id", tenant_id).execute()
        
        self.db_client.table("subscriptions").update({
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
        self.db_client.table("tenants").update({
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "subscription_status": "active",
            "plan_id": plan_id
        }).eq("id", tenant_id).execute()
        
        # Get plan details to update minutes
        if plan_id:
            plan = self.db_client.table("plans").select("minutes").eq("id", plan_id).single().execute()
            if plan.data:
                self.db_client.table("tenants").update({
                    "minutes_allocated": plan.data.get("minutes", 0),
                    "minutes_used": 0
                }).eq("id", tenant_id).execute()
        
        logger.info(f"Activated subscription for tenant {tenant_id}")

        # Day 8: Audit log
        if self.audit_logger:
            await self.audit_logger.log(
                event_type=AuditEvent.BILLING_UPDATED,
                tenant_id=tenant_id,
                action="subscription_activated",
                description=f"Subscription activated via Stripe checkout: {plan_id}",
                metadata={"subscription_id": subscription_id, "plan_id": plan_id},
                actor_type="system"
            )
    
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
            self.db_client.table("tenants").update({
                "subscription_status": "canceled",
                "stripe_subscription_id": None
            }).eq("id", tenant_id).execute()
        
        # Update subscription record
        self.db_client.table("subscriptions").update({
            "status": "canceled",
            "canceled_at": datetime.now()
        }).eq("stripe_subscription_id", subscription["id"]).execute()

        # Day 8: Audit log
        if self.audit_logger and tenant_id:
            await self.audit_logger.log(
                event_type=AuditEvent.BILLING_UPDATED,
                tenant_id=tenant_id,
                action="subscription_deleted",
                description="Subscription deleted/canceled via Stripe",
                metadata={"subscription_id": subscription["id"]},
                actor_type="system"
            )
    
    async def _handle_invoice_paid(self, invoice: Dict):
        """Handle invoice.paid event"""
        tenant_id = invoice.get("metadata", {}).get("tenant_id")

        # Store invoice record
        self.db_client.table("invoices").upsert({
            "stripe_invoice_id": invoice["id"],
            "stripe_subscription_id": invoice.get("subscription"),
            "tenant_id": tenant_id,
            "amount_due": invoice.get("amount_due", 0),
            "amount_paid": invoice.get("amount_paid", 0),
            "currency": invoice.get("currency", "usd"),
            "status": "paid",
            "invoice_pdf": invoice.get("invoice_pdf"),
            "hosted_invoice_url": invoice.get("hosted_invoice_url"),
            "paid_at": datetime.now()
        }, on_conflict="stripe_invoice_id").execute()

        # Send payment success notification
        if tenant_id:
            # Get user email from tenant
            tenant_data = self.db_client.table("tenants").select(
                "business_name"
            ).eq("id", tenant_id).single().execute()

            user_email = ""
            if tenant_data.data:
                # Try to get admin user email
                users = self.db_client.table("user_profiles").select(
                    "email"
                ).eq("tenant_id", tenant_id).eq("role", "owner").limit(1).execute()
                if users.data:
                    user_email = users.data[0].get("email", "")

            if user_email:
                notification_service = get_notification_service()
                await notification_service.send_email(
                    to_email=user_email,
                    subject="Payment Received",
                    html_body=f"""
                    <html>
                        <body style="font-family: Arial, sans-serif; color: #333;">
                            <h1 style="color: #34C759;">Payment Successful</h1>
                            <p>Your payment of ${invoice.get('amount_paid', 0)/100:.2f} {invoice.get('currency', 'USD').upper()} has been received.</p>
                            <p><strong>Invoice ID:</strong> {invoice['id']}</p>
                            <p><a href="{invoice.get('hosted_invoice_url', 'https://talky.ai/invoices')}" style="color: #007AFF;">View Invoice</a></p>
                        </body>
                    </html>
                    """,
                )

            # Audit log
            if self.audit_logger:
                await self.audit_logger.log(
                    event_type=AuditEvent.BILLING_UPDATED,
                    tenant_id=tenant_id,
                    action="payment_received",
                    description=f"Payment received: ${invoice.get('amount_paid', 0)/100:.2f}",
                    metadata={
                        "invoice_id": invoice["id"],
                        "amount": invoice.get("amount_paid", 0)
                    },
                    actor_type="system"
                )

    async def _handle_invoice_payment_failed(self, invoice: Dict):
        """Handle invoice.payment_failed event"""
        subscription_id = invoice.get("subscription")
        tenant_id = invoice.get("metadata", {}).get("tenant_id")

        if subscription_id:
            self.db_client.table("subscriptions").update({
                "status": "past_due"
            }).eq("stripe_subscription_id", subscription_id).execute()

            # Update tenant status
            if tenant_id:
                self.db_client.table("tenants").update({
                    "subscription_status": "past_due"
                }).eq("id", tenant_id).execute()

        # Send payment failure notification
        if tenant_id:
            # Get user email
            users = self.db_client.table("user_profiles").select(
                "email"
            ).eq("tenant_id", tenant_id).eq("role", "owner").limit(1).execute()

            if users.data:
                user_email = users.data[0].get("email", "")
                if user_email:
                    notification_service = get_notification_service()
                    await notification_service.notify_billing_failure(
                        user_email=user_email,
                        amount=invoice.get("amount_due", 0) / 100,
                        error_message=invoice.get("attempt_count", 1) > 1 and "Multiple payment attempts failed" or "Payment declined",
                        channels=NotificationChannel.BOTH,
                    )

            # Audit log
            if self.audit_logger:
                await self.audit_logger.log_security_event(
                    event_type="billing_payment_failed",
                    severity="HIGH",
                    description=f"Payment failed for tenant {tenant_id}: {invoice['id']}",
                    metadata={
                        "invoice_id": invoice["id"],
                        "amount": invoice.get("amount_due", 0),
                        "attempt_count": invoice.get("attempt_count", 1)
                    },
                )
    
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
        self.db_client.table("subscriptions").upsert(
            subscription_data,
            on_conflict="stripe_subscription_id"
        ).execute()
        
        # Update tenant
        if tenant_id:
            self.db_client.table("tenants").update({
                "subscription_status": subscription["status"],
                "stripe_subscription_id": subscription["id"]
            }).eq("id", tenant_id).execute()

            # Day 8: Audit log
            if self.audit_logger:
                await self.audit_logger.log(
                    event_type=AuditEvent.BILLING_UPDATED,
                    tenant_id=tenant_id,
                    action="subscription_synced",
                    description=f"Subscription state synced: {subscription['status']}",
                    metadata={
                        "subscription_id": subscription["id"],
                        "status": subscription["status"],
                        "plan_id": plan_id
                    },
                    actor_type="system"
                )
    
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
        result = self.db_client.table("usage_records").insert({
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
        usage = self.db_client.table("usage_records").select(
            "quantity"
        ).eq("tenant_id", tenant_id).eq("usage_type", usage_type).execute()
        
        total_usage = sum(record["quantity"] for record in usage.data) if usage.data else 0
        
        # Get tenant allocation
        tenant = self.db_client.table("tenants").select(
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
