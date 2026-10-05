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

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.domain.services.audit_logger import AuditLogger
from app.domain.services.subscription_status import (
    CANCELLED as SUBSCRIPTION_CANCELLED,
)
from app.domain.services.subscription_status import (
    INACTIVE as SUBSCRIPTION_INACTIVE,
)
from app.domain.services.subscription_status import (
    canonical as canonical_subscription_status,
)

logger = logging.getLogger(__name__)

# Try to import stripe, but make it optional for development
try:
    import stripe

    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    logger.warning("Stripe SDK not installed. Provider billing is unavailable.")


class BillingService:
    """
    Service for handling Stripe billing operations.

    Mock mode requires an explicit STRIPE_MOCK_MODE setting. Missing provider
    configuration and disabled billing never simulate a financial operation.
    """

    def __init__(self, db_client: Client, audit_logger: Optional[AuditLogger] = None):
        self.db_client = db_client
        self.audit_logger = audit_logger
        from app.domain.services.billing_mode import get_billing_mode
        self.billing_mode = get_billing_mode(sdk_available=STRIPE_AVAILABLE)
        self._stripe_api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
        self.mock_mode = self._should_use_mock_mode()

        self.webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
        if self.billing_mode in {"live", "test"}:
            stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

        logger.info(f"BillingService initialized (mock_mode={self.mock_mode})")

    def _should_use_mock_mode(self) -> bool:
        return self.billing_mode == "mock"

    def _require_billing_enabled(self):
        if self.billing_mode not in {"live", "test", "mock"}:
            raise ValueError("Billing is unavailable in the current mode")

    async def _stripe_call(self, resource: str, method: str, *args, **kwargs):
        """Keep synchronous SDK I/O off the request event loop.

        A timed-out thread may still be accepted by Stripe; checkout attempts
        retain their identity and uncertainty instead of replaying a new write.
        """
        if self.billing_mode not in {"live", "test"}:
            raise ValueError("Paid billing is not available in the current mode")
        paths = {"Price": ("prices",), "Customer": ("customers",),
                 "Subscription": ("subscriptions",), "Invoice": ("invoices",),
                 "Charge": ("charges",), "PaymentIntent": ("payment_intents",),
                 "Dispute": ("disputes",), "Event": ("events",),
                 "InvoiceLineItem": ("invoices", "line_items"),
                 "InvoicePayment": ("invoice_payments",), "CreditNote": ("credit_notes",),
                 "Refund": ("refunds",),
                 "checkout.Session": ("checkout", "sessions"),
                 "billing_portal.Session": ("billing_portal", "sessions")}
        options = {}
        if "idempotency_key" in kwargs:
            options["idempotency_key"] = kwargs.pop("idempotency_key")

        def call():
            transport = stripe.RequestsClient(timeout=5)
            try:
                client = stripe.StripeClient(self._stripe_api_key, http_client=transport, max_network_retries=0)
                target = client.v1
                for part in paths[resource]:
                    target = getattr(target, part)
                return getattr(target, method)(*args, params=kwargs, options=options)
            finally:
                transport.close()

        return await asyncio.wait_for(asyncio.to_thread(call), 20.0)

    # =========================================================================
    # Customer Management
    # =========================================================================

    async def create_or_get_customer(
        self, tenant_id: str, email: str, business_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get existing Stripe customer or create a new one.

        Returns:
            Dict with customer_id and whether it was newly created
        """
        self._require_billing_enabled()
        # Check if tenant already has a Stripe customer
        tenant = (
            self.db_client.table("tenants")
            .select("stripe_customer_id")
            .eq("id", tenant_id)
            .single()
            .execute()
        )

        existing_customer_id = tenant.data.get("stripe_customer_id") if tenant.data else None

        if existing_customer_id:
            return {"customer_id": existing_customer_id, "created": False}

        # Create new customer
        if self.mock_mode:
            customer_id = f"cus_mock_{tenant_id[:8]}"
        else:
            customer = stripe.Customer.create(
                email=email, name=business_name, metadata={"tenant_id": tenant_id}
            )
            customer_id = customer.id

        # Update tenant with customer ID
        self.db_client.table("tenants").update({"stripe_customer_id": customer_id}).eq(
            "id", tenant_id
        ).execute()

        logger.info(f"Created Stripe customer {customer_id} for tenant {tenant_id}")

        return {"customer_id": customer_id, "created": True}

    # =========================================================================
    # Checkout Session
    # =========================================================================

    async def create_checkout_session(
        self, *, tenant_id: str, email: str, request_id: str,
        price_option_id: str, business_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.domain.services.billing_checkout import CheckoutAttempts
        return await CheckoutAttempts(self.db_client.pool, self).create(
            tenant_id=tenant_id, email=email, request_id=request_id,
            price_option_id=price_option_id, business_name=business_name,
        )

    async def get_checkout_attempt(self, *, tenant_id: str, request_id: str):
        from app.domain.services.billing_checkout import CheckoutAttempts
        return await CheckoutAttempts(self.db_client.pool, self).get(
            tenant_id=tenant_id, request_id=request_id,
        )

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
        self._require_billing_enabled()
        customer_result = await self.create_or_get_customer(tenant_id, email, business_name)
        customer_id = customer_result["customer_id"]
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        def return_url(url, **values):
            parts = urlsplit(url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query.update(order_id=str(order_id), **values)
            return urlunsplit(parts._replace(query=urlencode(query, safe="{}")))

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
                "checkout_url": return_url(success_url, session_id=session_id, mock="true"),
                "mock_mode": True,
                "message": (
                    "Mock checkout session created. Configure STRIPE_SECRET_KEY "
                    "for real payments."
                ),
            }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            # IMMEDIATE methods only. Left unset, Stripe offers every method
            # enabled on the account — on a GBP account that includes Bacs, and
            # Klarna/SEPA elsewhere, all of which settle days after checkout.
            # Nothing in this product handles that gap: the order shows
            # 'pending' with no reaper and the customer has no pending-payment
            # UI. Widen this only alongside a reaper and a pending state.
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "product_data": {"name": product_name},
                        "unit_amount": price_cents,
                    },
                    "quantity": 1,
                }
            ],
            success_url=return_url(success_url, session_id="{CHECKOUT_SESSION_ID}"),
            cancel_url=return_url(cancel_url),
            client_reference_id=str(order_id),
            metadata=meta,
            payment_intent_data={"metadata": meta},
        )

        logger.info(
            "topup_checkout_created session=%s order=%s tenant=%s minutes=%d",
            session.id,
            str(order_id)[:8],
            str(tenant_id)[:8],
            minutes,
        )
        return {
            "session_id": session.id,
            "checkout_url": session.url,
            "mock_mode": False,
        }

    # =========================================================================
    # Customer Portal
    # =========================================================================

    async def create_portal_session(self, tenant_id: str, return_url: str) -> Dict[str, Any]:
        """
        Create a Stripe Customer Portal session for managing subscription.
        """
        # Get customer ID
        tenant = (
            self.db_client.table("tenants")
            .select("stripe_customer_id")
            .eq("id", tenant_id)
            .single()
            .execute()
        )

        customer_id = tenant.data.get("stripe_customer_id") if tenant.data else None

        if not customer_id:
            raise ValueError("No Stripe customer found for this tenant")

        if not await self._portal_customer_verified(tenant_id, customer_id):
            raise ValueError("Billing portal customer ownership could not be verified")
        session = await self._stripe_call("billing_portal.Session", "create", customer=customer_id, return_url=return_url)

        return {"portal_url": session.url, "mock_mode": False}

    async def _portal_customer_verified(self, tenant_id, customer_id):
        if self.billing_mode not in {"live", "test"} or not customer_id:
            return False
        try:
            customer = await self._stripe_call("Customer", "retrieve", customer_id)
            return bool(customer.get("id") == customer_id and not customer.get("deleted")
                        and customer.get("livemode") is (self.billing_mode == "live")
                        and (customer.get("metadata") or {}).get("tenant_id") == str(tenant_id))
        except Exception:
            return False

    async def _purchase_projection(self, tenant_id, subscription):
        from app.domain.services.billing_checkout import _object, public_option
        metadata = _object(subscription.get("metadata"))
        purchase = metadata.get("purchase")
        if not purchase:
            async with acquire_with_tenant(self.db_client.pool, tenant_id) as conn:
                row = await conn.fetchrow(
                    """SELECT a.snapshot FROM billing_checkout_attempts a JOIN tenants t ON t.id=a.tenant_id
                       WHERE a.tenant_id=$1::uuid AND a.status='completed'
                         AND a.snapshot->'option'->>'kind'='free'
                         AND a.snapshot->'option'->>'plan_id'=t.plan_id
                         AND t.stripe_subscription_id IS NULL
                       ORDER BY a.created_at DESC LIMIT 1""", str(tenant_id),
                )
            if row:
                purchase = _object(row["snapshot"])["option"]
        subscription["purchased_price_option"] = public_option(purchase) if purchase else None
        customer_id = subscription.get("stripe_customer_id")
        if not customer_id:
            tenant = self.db_client.table("tenants").select("stripe_customer_id").eq("id", tenant_id).single().execute()
            customer_id = (tenant.data or {}).get("stripe_customer_id")
        subscription["billing_portal_available"] = await self._portal_customer_verified(tenant_id, customer_id)
        return subscription

    # =========================================================================
    # Subscription Management
    # =========================================================================

    async def get_subscription(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Project current tenant access, never an unrelated historical subscription."""
        tenant = self.db_client.table("tenants").select(
            "subscription_status,stripe_subscription_id,stripe_customer_id,plan_id,plans(name,price,minutes,agents)"
        ).eq("id", tenant_id).single().execute()
        if not tenant.data:
            return None
        data = tenant.data
        subscription_id = data.get("stripe_subscription_id")
        if not subscription_id and data.get("subscription_status") == SUBSCRIPTION_INACTIVE:
            return None
        result = {
            "status": data.get("subscription_status", SUBSCRIPTION_INACTIVE),
            "plan_id": data.get("plan_id"), "plan": data.get("plans"),
            "stripe_subscription_id": subscription_id,
            "stripe_customer_id": data.get("stripe_customer_id"),
        }
        if subscription_id:
            stored = self.db_client.table("subscriptions").select("*,plans(name,price,minutes,agents)").eq(
                "tenant_id", tenant_id
            ).eq("stripe_subscription_id", subscription_id).limit(1).execute()
            if stored.data:
                result = {**stored.data[0], "status": result["status"]}
        return await self._purchase_projection(tenant_id, result)

    async def cancel_subscription(
        self, tenant_id: str, cancel_at_period_end: bool = True
    ) -> Dict[str, Any]:
        """
        Cancel a subscription (at period end by default).
        """
        self._require_billing_enabled()
        tenant = (
            self.db_client.table("tenants")
            .select("stripe_subscription_id")
            .eq("id", tenant_id)
            .single()
            .execute()
        )

        subscription_id = tenant.data.get("stripe_subscription_id") if tenant.data else None

        if not subscription_id:
            raise ValueError("No active subscription found")

        if self.mock_mode:
            # Update local state in mock mode
            self.db_client.table("tenants").update(
                {"subscription_status": SUBSCRIPTION_CANCELLED}
            ).eq("id", tenant_id).execute()

            return {
                "status": SUBSCRIPTION_CANCELLED,
                "mock_mode": True,
                "message": "Subscription canceled (mock mode)",
            }

        subscription = stripe.Subscription.modify(
            subscription_id, cancel_at_period_end=cancel_at_period_end
        )

        # Update local state. Stripe spells cancellation "canceled"; the call
        # guard blocks on the canonical value, so normalise before storing.
        self.db_client.table("tenants").update(
            {"subscription_status": canonical_subscription_status(subscription.status)}
        ).eq("id", tenant_id).execute()

        self.db_client.table("subscriptions").update(
            {
                "status": subscription.status,
                "cancel_at": (
                    datetime.fromtimestamp(subscription.cancel_at)
                    if subscription.cancel_at
                    else None
                ),
                "canceled_at": datetime.now(),
            }
        ).eq("stripe_subscription_id", subscription_id).execute()

        return {
            "status": subscription.status,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "mock_mode": False,
        }

    # =========================================================================
    # Webhook Handlers
    # =========================================================================

    async def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """Verify raw Stripe bytes before persisting or applying any event."""
        from app.domain.services.billing_webhooks import BillingWebhookProcessor, BillingWebhookRetryable
        if self.mock_mode:
            return {"status": "ignored", "reason": "mock_mode"}
        if self.billing_mode not in {"test", "live"} or not self.webhook_secret:
            raise BillingWebhookRetryable("webhook_unavailable")
        if not signature:
            raise ValueError("Missing Stripe signature")
        try:
            event = stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
        except (stripe.error.SignatureVerificationError, ValueError) as exc:
            raise ValueError("Invalid Stripe webhook") from exc
        event = json.loads(json.dumps(event))
        return await BillingWebhookProcessor(self.db_client.pool).process(event, self._apply_webhook_event)

    _TOPUP_SESSION_EVENTS = {
        "checkout.session.completed", "checkout.session.expired",
        "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed",
    }
    _TOPUP_CHARGE_EVENTS = {"charge.refunded", "charge.dispute.created"}

    async def _is_topup_event(self, event_type: str, data: Dict) -> bool:
        if event_type in self._TOPUP_SESSION_EVENTS:
            return (data.get("metadata") or {}).get("purpose") == "minute_topup"
        return event_type in self._TOPUP_CHARGE_EVENTS

    async def _apply_webhook_event(self, conn, event: Dict) -> Dict[str, Any]:
        from app.domain.services.billing_webhooks import BillingWebhookReviewRequired
        from app.domain.services.billing_state_events import apply_billing_event, BillingStateReviewRequired
        from app.domain.services.billing_topup_events import apply_topup_event, BillingTopupReviewRequired
        mode = "live" if event["livemode"] else "test"
        if mode != self.billing_mode or event.get("account"):
            raise BillingWebhookReviewRequired("provider_account_or_mode_mismatch")
        event_type, data = event["type"], event["data"]["object"]
        try:
            if event_type in {"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"}:
                from app.domain.services.billing_refund_projection import handle_topup_refund_observation
                result = await handle_topup_refund_observation(conn, self, event_type, data, event["id"])
                if result["status"] != "ignored":
                    return result
            if await self._is_topup_event(event_type, data):
                result = await apply_topup_event(conn, self, event_type, data, event["id"])
                if result["status"] != "ignored" or event_type != "charge.refunded":
                    return result
            return await apply_billing_event(conn, self, event_type, data, source_reference=event["id"])
        except (BillingStateReviewRequired, BillingTopupReviewRequired) as exc:
            raise BillingWebhookReviewRequired(exc.code) from exc

    # =========================================================================
    # Usage Tracking (for metered billing)
    # =========================================================================

    async def record_usage(
        self, tenant_id: str, quantity: int, usage_type: str = "minutes"
    ) -> Dict[str, Any]:
        """
        Record usage for metered billing.

        This stores usage locally and optionally reports to Stripe.
        """
        # Store usage record
        result = (
            self.db_client.table("usage_records")
            .insert(
                {
                    "tenant_id": tenant_id,
                    "quantity": quantity,
                    "usage_type": usage_type,
                    "reported_to_stripe": False,
                }
            )
            .execute()
        )

        return {"recorded": True, "usage_id": result.data[0]["id"] if result.data else None}

    async def get_usage_summary(
        self, tenant_id: str, usage_type: str = "minutes"
    ) -> Dict[str, Any]:
        """Current calendar-month meter, not an invoice-period estimate.

        Reuse the quota's settled-call definition and purchased allowance. A
        failed/missing read is unavailable, never evidence of zero usage.
        """
        if usage_type != "minutes":
            raise ValueError("Unsupported usage_type; only minutes are metered")
        from uuid import UUID
        from app.core.db_utils import acquire_with_tenant
        from app.domain.services.minutes_quota import compute_minutes_status

        tenant_uuid = UUID(str(tenant_id))
        async with acquire_with_tenant(self.db_client.pool, str(tenant_uuid), timeout=5) as conn:
            exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)", tenant_uuid)
            if not exists:
                raise RuntimeError("Usage tenant unavailable")
            quota = (await compute_minutes_status(conn, tenant_uuid)).require_available()
        return {
            "usage_type": usage_type,
            "total_used": quota.used_minutes,
            "allocated": quota.allocated,
            "remaining": quota.remaining_minutes,
            "overage": 0 if quota.unlimited else max(0, quota.used_minutes - quota.allocated),
            "unlimited": quota.unlimited,
            "metering_period": "calendar_month",
        }
