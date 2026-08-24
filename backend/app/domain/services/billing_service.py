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

        if self.mock_mode:
            # In mock mode, stripe_price_id may be NULL — we still return a
            # fake checkout URL so the frontend flow is fully testable
            # before real Stripe products are configured.
            # Return mock checkout session
            session_id = f"cs_mock_{tenant_id[:8]}_{plan_id}"
            return {
                "session_id": session_id,
                "checkout_url": f"{success_url}?session_id={session_id}&mock=true",
                "mock_mode": True,
                "message": "Mock checkout session created. Configure STRIPE_SECRET_KEY for real payments."
            }

        if not stripe_price_id:
            raise ValueError(
                f"Plan {plan_id} has no stripe_price_id configured. "
                "Create the product/price in Stripe Dashboard and update "
                "plans.stripe_price_id for this row."
            )

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
    # Minute Top-Ups (one-time payments, goals.md §9)
    # =========================================================================

    async def create_topup_checkout_session(
        self,
        *,
        tenant_id: str,
        email: str,
        order_id: str,
        minutes: int,
        price_cents: int,
        currency: str,
        product_name: str,
        success_url: str,
        cancel_url: str,
        business_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One-time checkout for a minute bundle.

        Three things here differ from the subscription flow and each one
        matters:

        ``mode="payment"``   A top-up is bought once. Opening it in
                             subscription mode would silently enrol the
                             customer in a recurring charge.

        ``metadata.purpose`` The webhook receives ONE ``checkout.session.completed``
                             stream for both flows. Without this marker the
                             subscription handler would run on a top-up and
                             overwrite ``plan_id`` and ``stripe_subscription_id``
                             with the nulls a one-time session carries —
                             a top-up purchase would break the customer's plan.

        inline ``price_data`` The amount comes from the package row we already
                             snapshotted onto the order, so no Stripe Price
                             object has to exist first and the customer is
                             charged exactly what the order says.
        """
        customer_result = await self.create_or_get_customer(
            tenant_id, email, business_name
        )
        customer_id = customer_result["customer_id"]

        # Shared by the session and the resulting PaymentIntent. The charge and
        # refund events carry no checkout session, so the payment intent has to
        # carry enough to identify the order on its own.
        meta = {
            "purpose": "minute_topup",
            "tenant_id": tenant_id,
            "order_id": str(order_id),
            "minutes": str(minutes),
        }

        if self.mock_mode:
            session_id = f"cs_mock_topup_{str(order_id)[:8]}"
            return {
                "session_id": session_id,
                "checkout_url": (
                    f"{success_url}?session_id={session_id}&mock=true"
                ),
                "mock_mode": True,
                "message": (
                    "Mock checkout session created. Configure STRIPE_SECRET_KEY "
                    "for real payments."
                ),
            }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": currency.lower(),
                    "product_data": {"name": product_name},
                    "unit_amount": price_cents,
                },
                "quantity": 1,
            }],
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            client_reference_id=str(order_id),
            metadata=meta,
            payment_intent_data={"metadata": meta},
        )

        logger.info(
            "topup_checkout_created session=%s order=%s tenant=%s minutes=%d",
            session.id, str(order_id)[:8], str(tenant_id)[:8], minutes,
        )
        return {
            "session_id": session.id,
            "checkout_url": session.url,
            "mock_mode": False,
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
        event_id = event.get("id")

        # Idempotency: Stripe redelivers events (up to ~3x). Without dedup,
        # checkout.completed/invoice.paid would re-apply minute resets and
        # re-send confirmation emails on each redelivery. Claim the event id;
        # if it was already processed, ack 200 without re-running the handler.
        if event_id and not await self._claim_webhook_event(event_id, event_type):
            logger.info("Duplicate Stripe webhook ignored event_id=%s type=%s", event_id, event_type)
            return {"status": "duplicate", "event_id": event_id, "event_type": event_type}

        logger.info(f"Processing webhook event: {event_type} (id={event_id})")

        # ── minute top-ups branch off FIRST ─────────────────────────────────
        # Stripe delivers subscription checkouts and top-up checkouts down the
        # same `checkout.session.completed` stream. Routing on the purpose we
        # stamped at creation time is what keeps a top-up from reaching
        # _handle_checkout_completed, which would null out the tenant's
        # plan_id and stripe_subscription_id from a one-time session's empty
        # fields — breaking the plan of a customer who just gave us money.
        if await self._is_topup_event(event_type, data):
            try:
                return await self._handle_topup_event(event_type, data, event_id)
            except Exception:
                # The claim was taken before the handler ran, so a redelivery
                # would be discarded as a duplicate and the minutes would never
                # be credited. Release it and let Stripe retry.
                if event_id:
                    await self._release_webhook_claim(event_id)
                raise

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
    
    async def _claim_webhook_event(self, event_id: str, event_type: str) -> bool:
        """Atomically claim a Stripe event id for processing.

        Returns True if THIS call claimed it (first time → process), False if it
        was already processed (duplicate → skip). Fail-OPEN on any error: a
        missing table or DB hiccup must not drop a real billing event, so we
        process it (the previous always-process behavior).
        """
        try:
            async with self.db_client.pool.acquire() as conn:
                await conn.execute("SET app.bypass_rls = 'on'")
                await conn.execute(
                    "SET app.current_tenant_id = '00000000-0000-0000-0000-000000000000'"
                )
                status = await conn.execute(
                    """
                    INSERT INTO processed_webhook_events (event_id, event_type)
                    VALUES ($1, $2)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event_id, event_type,
                )
            # asyncpg tag: "INSERT 0 1" = inserted (claimed); "INSERT 0 0" = conflict
            return status.strip().endswith(" 1")
        except Exception as e:  # noqa: BLE001
            logger.warning("webhook idempotency claim failed (processing anyway): %s", e)
            return True

    async def _release_webhook_claim(self, event_id: str) -> None:
        """Undo a claim whose handler then failed.

        Without this, a transient database error while crediting a top-up is
        permanent: the claim is committed before the handler runs, so Stripe's
        redelivery is discarded as a duplicate and the customer never receives
        the minutes they paid for. Releasing turns that into a retry.
        """
        try:
            async with self.db_client.pool.acquire() as conn:
                await conn.execute("SET app.bypass_rls = 'on'")
                await conn.execute(
                    "SET app.current_tenant_id = '00000000-0000-0000-0000-000000000000'"
                )
                await conn.execute(
                    "DELETE FROM processed_webhook_events WHERE event_id = $1",
                    event_id,
                )
            logger.warning(
                "webhook claim released after handler failure event_id=%s — "
                "Stripe's redelivery will retry", event_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "could not release webhook claim %s: %s — this event will NOT be "
                "retried, reconcile it by hand", event_id, e,
            )

    # -- top-up routing -------------------------------------------------------

    _TOPUP_SESSION_EVENTS = {
        "checkout.session.completed",
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
    }
    # Reversals arrive on the charge, which carries no checkout session. Neither
    # of these has a handler in the subscription table, so claiming them for the
    # top-up path costs nothing when the charge turns out to be a subscription:
    # the order lookup finds nothing and the handler no-ops.
    _TOPUP_CHARGE_EVENTS = {"charge.refunded", "charge.dispute.created"}

    async def _is_topup_event(self, event_type: str, data: Dict) -> bool:
        if event_type in self._TOPUP_SESSION_EVENTS:
            return (data.get("metadata") or {}).get("purpose") == "minute_topup"
        return event_type in self._TOPUP_CHARGE_EVENTS

    async def _handle_topup_event(
        self, event_type: str, data: Dict, event_id: Optional[str]
    ) -> Dict[str, Any]:
        from app.domain.services.topup_service import TopupService

        topups = TopupService(self.db_client.pool)
        # A missing event id would defeat the ledger's uniqueness guard, so fall
        # back to something equally unique per payment rather than NULL (which
        # a partial unique index does not dedupe).
        eid = event_id or f"no_event_id:{event_type}:{data.get('id')}"

        if event_type == "checkout.session.completed":
            # payment_status is the field that actually says money arrived.
            # A session can complete with payment still processing, and
            # crediting there hands out minutes for a payment that may fail.
            if data.get("payment_status") != "paid":
                logger.info(
                    "topup_checkout_completed_unpaid session=%s payment_status=%s "
                    "— waiting for the payment to settle",
                    str(data.get("id"))[:24], data.get("payment_status"),
                )
                return {"status": "deferred", "event_type": event_type}
            credited = await topups.credit_paid_order(
                session_id=str(data.get("id")),
                event_id=eid,
                payment_id=data.get("payment_intent"),
            )
            if credited:
                # Only on a real credit, so a redelivery does not send a second
                # receipt for one payment.
                await self._send_topup_receipt(data)
            return {
                "status": "handled" if credited else "duplicate",
                "event_type": event_type,
            }

        if event_type in ("checkout.session.expired",
                          "checkout.session.async_payment_failed"):
            await topups.mark_failed(
                session_id=str(data.get("id")),
                status="cancelled" if event_type.endswith("expired") else "failed",
            )
            return {"status": "handled", "event_type": event_type}

        if event_type == "charge.refunded":
            # A PARTIAL refund must not claw back the whole bundle. Only a fully
            # refunded charge reverses the minutes; anything else is flagged for
            # a human because splitting a bundle is a judgement call.
            if not data.get("refunded"):
                logger.warning(
                    "topup_partial_refund charge=%s refunded=%s of %s — minutes "
                    "left in place, reconcile by hand",
                    str(data.get("id"))[:24], data.get("amount_refunded"),
                    data.get("amount"),
                )
                return {"status": "ignored", "event_type": event_type}
            await topups.reverse(
                event_id=eid, kind="refund",
                payment_id=data.get("payment_intent"),
            )
            return {"status": "handled", "event_type": event_type}

        if event_type == "charge.dispute.created":
            await topups.reverse(
                event_id=eid, kind="dispute",
                payment_id=data.get("payment_intent"),
            )
            return {"status": "handled", "event_type": event_type}

        return {"status": "ignored", "event_type": event_type}

    async def _send_topup_receipt(self, session: Dict) -> None:
        """Confirm the purchase to the customer (goals.md §9).

        NEVER RAISES. The minutes are already credited and committed by the
        time this runs. Letting a mail-provider outage propagate would fail the
        webhook, release the claim, and have Stripe retry an event whose credit
        has already happened — a loop of 500s over an email that could simply
        be sent later. A failed receipt is logged and dropped.
        """
        try:
            tenant_id = (session.get("metadata") or {}).get("tenant_id")
            minutes = (session.get("metadata") or {}).get("minutes")
            if not tenant_id:
                return

            to_email = (session.get("customer_details") or {}).get("email")
            if not to_email:
                # Fall back to the account owner. A receipt with nowhere to go
                # is not worth failing over, but it is worth trying twice.
                users = self.db_client.table("user_profiles").select("email").eq(
                    "tenant_id", tenant_id
                ).eq("role", "owner").limit(1).execute()
                if users.data:
                    to_email = users.data[0].get("email", "")
            if not to_email:
                logger.warning(
                    "topup_receipt_no_recipient tenant=%s — minutes credited, "
                    "no address to confirm to", str(tenant_id)[:8],
                )
                return

            amount = (session.get("amount_total") or 0) / 100
            currency = (session.get("currency") or "gbp").upper()
            notification_service = get_notification_service()
            await notification_service.send_email(
                to_email=to_email,
                subject=f"{minutes} minutes added to your Talky.ai account",
                html_body=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; color: #333;">
                        <h1 style="color: #34C759;">Minutes added</h1>
                        <p><strong>{minutes}</strong> call minutes have been added
                           to your account and are ready to use.</p>
                        <p><strong>Amount charged:</strong> {amount:.2f} {currency}</p>
                        <p>You can see this purchase and your remaining balance on
                           the Billing page.</p>
                    </body>
                </html>
                """,
                text_body=(
                    f"{minutes} call minutes have been added to your Talky.ai "
                    f"account. Amount charged: {amount:.2f} {currency}."
                ),
            )
            logger.info("topup_receipt_sent tenant=%s minutes=%s",
                        str(tenant_id)[:8], minutes)

            if self.audit_logger:
                await self.audit_logger.log(
                    event_type=AuditEvent.BILLING_UPDATED,
                    tenant_id=tenant_id,
                    action="topup_credited",
                    description=f"{minutes} minutes credited via top-up",
                    metadata={
                        "minutes": minutes,
                        "amount_total": session.get("amount_total"),
                        "currency": currency,
                        "session_id": session.get("id"),
                    },
                    actor_type="system",
                )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "topup receipt failed (minutes ARE credited, this is cosmetic): %s", e,
            )

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
        """Get usage summary for the current billing period.

        `GET /billing/usage` used to report zero for every tenant, always.
        It summed `usage_records`, whose only writer is `record_usage()` —
        a method with no callers anywhere in the codebase — so the table is
        empty. It then compared that against `tenants.minutes_used`, a column
        that is likewise zero for every tenant in production.

        Minutes now come from the same live computation as the quota gate,
        the dashboard and the auth/profile paths, so a tenant cannot be
        blocked for exhausting an allowance that this endpoint says they have
        not touched. Non-minute usage types keep the `usage_records` path;
        it is unwired rather than wrong, and metered add-ons will populate it.
        """
        # Get tenant allocation (`minutes_used` deliberately not selected —
        # it is never written; see `tenant_minutes`).
        tenant = self.db_client.table("tenants").select(
            "minutes_allocated"
        ).eq("id", tenant_id).single().execute()

        allocated = (tenant.data.get("minutes_allocated", 0) if tenant.data else 0) or 0

        if usage_type == "minutes":
            from app.core.db import get_pool
            from app.services.scripts.tenant_minutes import (
                compute_tenant_minutes_used,
            )
            try:
                total_usage = await compute_tenant_minutes_used(
                    get_pool(), tenant_id
                )
            except Exception as exc:  # noqa: BLE001
                # Matches the fail-soft contract of every other minutes
                # reader: a metering hiccup must not 500 the billing page.
                logger.warning(
                    "usage summary: live minutes lookup failed for tenant %s: %s",
                    str(tenant_id)[:8], exc,
                )
                total_usage = 0
        else:
            usage = self.db_client.table("usage_records").select(
                "quantity"
            ).eq("tenant_id", tenant_id).eq("usage_type", usage_type).execute()
            total_usage = (
                sum(record["quantity"] for record in usage.data)
                if usage.data else 0
            )

        return {
            "usage_type": usage_type,
            "total_used": total_usage,
            "allocated": allocated,
            "remaining": max(0, allocated - total_usage),
            "overage": max(0, total_usage - allocated)
        }
