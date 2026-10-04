"""Durable, tenant-bound subscription checkout; no financial event replay."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg

from app.core.config import get_settings
from app.core.db_utils import acquire_with_tenant


class CheckoutError(ValueError):
    def __init__(self, code, message, status_code=409, *, request_not_started=False, existing_attempt=None):
        super().__init__(message)
        self.code, self.status_code = code, status_code
        self.request_not_started = request_not_started
        self.existing_attempt = existing_attempt


def _object(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _id(value):
    return value.get("id") if isinstance(value, dict) else value


def public_option(option):
    return {key: option.get(key) for key in (
        "id", "plan_id", "plan_name", "kind", "interval", "interval_count",
        "amount_minor", "currency", "currency_exponent",
    )}


def billing_return_urls(request_id=None):
    """One configured destination; neither request Origin nor body can redirect."""
    raw = get_settings().frontend_url
    parsed = urlsplit(raw)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise CheckoutError("billing_return_unconfigured", "Billing return destination is unavailable.", 503)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in get_settings().allowed_origins:
        raise CheckoutError("billing_return_unconfigured", "Billing return destination is unavailable.", 503)
    base = f"{origin}/billing"
    if request_id is None:
        return base
    identity = str(UUID(str(request_id)))
    return (
        f"{base}?checkout=success&request_id={identity}&session_id={{CHECKOUT_SESSION_ID}}",
        f"{base}?checkout=cancelled&request_id={identity}",
    )


async def apply_plan_allocation(conn, tenant_id, minutes):
    """Preserve the existing signed top-up ledger and unlimited sentinel."""
    result = await conn.execute(
        """UPDATE tenants SET minutes_allocated=CASE WHEN $2::int<=0 THEN 0
             ELSE $2::int + GREATEST(0,COALESCE((SELECT SUM(minutes_delta)
               FROM billing_ledger WHERE tenant_id=$1::uuid),0)) END,
             minutes_used=0 WHERE id=$1::uuid""", str(tenant_id), int(minutes),
    )
    if result != "UPDATE 1":
        raise CheckoutError("tenant_not_found", "Billing account is unavailable.", 404)


class CheckoutAttempts:
    def __init__(self, pool, billing):
        self.pool, self.billing = pool, billing

    async def _get(self, tenant_id, request_id):
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM billing_checkout_attempts WHERE id=$1::uuid AND tenant_id=$2::uuid",
                str(request_id), str(tenant_id),
            )
        if row is None:
            return None
        result = dict(row)
        result["snapshot"] = _object(result["snapshot"])
        return result

    @staticmethod
    def response(row):
        state = {
            "ready": "open", "completed": "activated", "expired": "expired", "failed": "failed",
        }.get(row["status"], "pending")
        return {
            "request_id": str(row["id"]), "state": state,
            "session_id": row.get("stripe_session_id"),
            "checkout_url": row.get("checkout_url") if state == "open" else None,
            "price_option": public_option(row["snapshot"]["option"]), "mock_mode": False,
            "message": "Receipt is unconfirmed. Retry this saved request." if state == "pending" else None,
        }

    async def _claim(self, tenant_id, request_id, option, email, business_name):
        success_url, cancel_url = billing_return_urls(request_id)
        snapshot = {
            "option": option, "email": email, "business_name": business_name,
            "success_url": success_url, "cancel_url": cancel_url,
            # Frozen for provider idempotency, never recomputed on retry.
            "expires_at": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        }
        request_hash = hashlib.sha256(str(option["id"]).encode()).hexdigest()
        try:
            async with acquire_with_tenant(self.pool, tenant_id) as conn:
                tenant = await conn.fetchrow(
                    "SELECT id,status,stripe_customer_id,stripe_subscription_id FROM tenants WHERE id=$1::uuid FOR UPDATE",
                    str(tenant_id),
                )
                # The tenant lock serializes different request IDs; same-ID replays
                # are returned before the subscription/outstanding guards.
                existing = await conn.fetchrow(
                    "SELECT id FROM billing_checkout_attempts WHERE id=$1::uuid AND tenant_id=$2::uuid",
                    str(request_id), str(tenant_id),
                )
                if not existing:
                    if not tenant or tenant["status"] != "active":
                        raise CheckoutError("tenant_unavailable", "Billing account is unavailable.", 403, request_not_started=True)
                    active = await conn.fetchval(
                        """SELECT EXISTS(SELECT 1 FROM subscriptions WHERE tenant_id=$1::uuid
                           AND status NOT IN ('canceled','cancelled','incomplete_expired'))""", str(tenant_id),
                    )
                    if tenant["stripe_subscription_id"] or active:
                        raise CheckoutError("subscription_exists", "Manage your existing subscription in the billing portal, or contact support if the portal is unavailable.", request_not_started=True)
                    outstanding = await conn.fetchrow(
                        """SELECT * FROM billing_checkout_attempts WHERE tenant_id=$1::uuid
                           AND status IN ('creating','ready','unknown') LIMIT 1""", str(tenant_id),
                    )
                    if outstanding:
                        outstanding = dict(outstanding)
                        outstanding["snapshot"] = _object(outstanding["snapshot"])
                        raise CheckoutError(
                            "checkout_outstanding", "Resume or reconcile your existing checkout before starting another.",
                            request_not_started=True, existing_attempt=self.response(outstanding),
                        )
                    await conn.execute(
                        """INSERT INTO billing_checkout_attempts
                           (id,tenant_id,price_option_id,request_hash,snapshot,status,stripe_customer_id)
                           VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5::jsonb,'creating',$6)""",
                        str(request_id), str(tenant_id), str(option["id"]), request_hash,
                        json.dumps(snapshot, default=str), tenant["stripe_customer_id"],
                    )
        except asyncpg.UniqueViolationError as exc:
            raise CheckoutError("checkout_conflict", "This checkout identity or account already has another request.") from exc
        return await self._get(tenant_id, request_id)

    async def _existing_or_reject_offer(self, tenant_id, request_id, message):
        """A catalog rejection must not unlock an identity concurrently saved.

        Use the same tenant lock as insertion. A failed lookup or ambiguous
        database outcome never produces the definitive pre-insert flag.
        """
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            await conn.fetchrow("SELECT id FROM tenants WHERE id=$1::uuid FOR UPDATE", tenant_id)
            existing = await conn.fetchrow(
                "SELECT * FROM billing_checkout_attempts WHERE id=$1::uuid AND tenant_id=$2::uuid",
                request_id, tenant_id,
            )
            if existing is None:
                raise CheckoutError("billing_offer_unavailable", message, 400, request_not_started=True)
            result = dict(existing)
            result["snapshot"] = _object(result["snapshot"])
            return result

    async def _state(self, tenant_id, request_id, state):
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            await conn.execute(
                """UPDATE billing_checkout_attempts SET status=$3,updated_at=NOW()
                   WHERE id=$1::uuid AND tenant_id=$2::uuid AND status NOT IN ('completed','expired','failed')""",
                str(request_id), str(tenant_id), state,
            )

    async def _save_customer(self, row, customer):
        tenant_id, request_id = str(row["tenant_id"]), str(row["id"])
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            tenant = await conn.fetchrow(
                "SELECT stripe_customer_id FROM tenants WHERE id=$1::uuid FOR UPDATE", tenant_id,
            )
            if not tenant or tenant["stripe_customer_id"] not in {None, customer["id"]}:
                raise CheckoutError("customer_binding_changed", "Billing customer changed; review this checkout.")
            await conn.execute("UPDATE tenants SET stripe_customer_id=$2 WHERE id=$1::uuid", tenant_id, customer["id"])
            await conn.execute(
                "UPDATE billing_checkout_attempts SET stripe_customer_id=$3,updated_at=NOW() WHERE id=$1::uuid AND tenant_id=$2::uuid",
                request_id, tenant_id, customer["id"],
            )

    @staticmethod
    def _check_customer(customer, row):
        option = row["snapshot"]["option"]
        if (not customer.get("id") or customer.get("deleted")
                or customer.get("livemode") is not (option["provider_mode"] == "live")
                or _object(customer.get("metadata")).get("tenant_id") != str(row["tenant_id"])):
            raise CheckoutError("customer_mismatch", "Billing customer ownership could not be verified.")

    @staticmethod
    def check_session(session, row):
        option = row["snapshot"]["option"]
        meta = _object(session.get("metadata"))
        if (not session.get("id") or session.get("mode") != "subscription"
                or row.get("stripe_session_id") not in {None, session.get("id")}
                or _id(session.get("customer")) != row["stripe_customer_id"]
                or session.get("livemode") is not (option["provider_mode"] == "live")
                or session.get("client_reference_id") != str(row["id"])
                or meta.get("request_id") != str(row["id"])
                or meta.get("tenant_id") != str(row["tenant_id"])
                or meta.get("price_option_id") != str(option["id"])):
            raise CheckoutError("session_mismatch", "Payment session ownership could not be verified.")
        if session.get("currency") not in {None, option["currency"]}:
            raise CheckoutError("session_price_mismatch", "Payment session currency changed.")
        if session.get("amount_subtotal") not in {None, option["amount_minor"]}:
            raise CheckoutError("session_price_mismatch", "Payment session amount changed.")

    async def _save_session(self, row, session):
        self.check_session(session, row)
        status = session.get("status")
        if status not in {"open", "complete", "expired"}:
            raise CheckoutError("session_unconfirmed", "Payment session status is unconfirmed.")
        url = session.get("url")
        if status == "open":
            parsed = urlsplit(url or "")
            if parsed.scheme != "https" or parsed.netloc != "checkout.stripe.com" or not parsed.path:
                raise CheckoutError("session_unconfirmed", "Payment session destination is unconfirmed.")
        expires = session.get("expires_at")
        if not isinstance(expires, int) or isinstance(expires, bool):
            raise CheckoutError("session_unconfirmed", "Payment session expiry is unconfirmed.")
        state = {"open": "ready", "complete": "unknown", "expired": "expired"}[status]
        async with acquire_with_tenant(self.pool, str(row["tenant_id"])) as conn:
            await conn.execute(
                """UPDATE billing_checkout_attempts SET stripe_session_id=$3,checkout_url=$4,
                   provider_expires_at=$5,status=$6,updated_at=NOW()
                   WHERE id=$1::uuid AND tenant_id=$2::uuid AND status NOT IN ('completed','expired','failed')""",
                str(row["id"]), str(row["tenant_id"]), session["id"], url,
                datetime.fromtimestamp(expires, UTC), state,
            )

    async def _activate_free(self, row):
        tenant_id, request_id = str(row["tenant_id"]), str(row["id"])
        option = row["snapshot"]["option"]
        async with acquire_with_tenant(self.pool, tenant_id) as conn:
            tenant = await conn.fetchrow("SELECT stripe_subscription_id,plan_id,subscription_status FROM tenants WHERE id=$1::uuid FOR UPDATE", tenant_id)
            attempt = await conn.fetchrow("SELECT status FROM billing_checkout_attempts WHERE id=$1::uuid AND tenant_id=$2::uuid FOR UPDATE", request_id, tenant_id)
            if attempt["status"] == "completed":
                return
            if not tenant or tenant["stripe_subscription_id"]:
                raise CheckoutError("subscription_exists", "Manage your existing subscription before changing plan.")
            if tenant["plan_id"] != option["plan_id"] or tenant["subscription_status"] != "active":
                await apply_plan_allocation(conn, tenant_id, option["minutes"])
                await conn.execute("UPDATE tenants SET plan_id=$2,subscription_status='active' WHERE id=$1::uuid", tenant_id, option["plan_id"])
            await conn.execute("UPDATE billing_checkout_attempts SET status='completed',updated_at=NOW() WHERE id=$1::uuid AND tenant_id=$2::uuid", request_id, tenant_id)

    async def create(self, *, tenant_id, request_id, price_option_id, email, business_name=None):
        from app.domain.services.billing_catalog import (
            BillingCatalogUnavailable,
            resolve_checkout_option,
            validate_provider_price,
        )
        tenant_id, request_id, price_option_id = map(str, (tenant_id, request_id, price_option_id))
        row = await self._get(tenant_id, request_id)
        if row is None:
            try:
                option = await resolve_checkout_option(self.pool, price_option_id)
            except BillingCatalogUnavailable as exc:
                row = await self._existing_or_reject_offer(tenant_id, request_id, str(exc))
            else:
                row = await self._claim(tenant_id, request_id, option, email, business_name)
        if str(row["price_option_id"]) != price_option_id:
            raise CheckoutError("checkout_payload_conflict", "This checkout reference belongs to a different offer.")
        if row["status"] in {"completed", "failed", "expired"}:
            return self.response(row)
        if row.get("stripe_session_id"):
            return await self.get(tenant_id=tenant_id, request_id=request_id)
        option, snapshot = row["snapshot"]["option"], row["snapshot"]
        if option["kind"] == "free":
            await self._activate_free(row)
            return self.response(await self._get(tenant_id, request_id))
        if self.billing.billing_mode not in {"test", "live"} or option["provider_mode"] != self.billing.billing_mode:
            raise CheckoutError("billing_unavailable", "Paid checkout is unavailable in the current billing mode.", 503)
        # An old uncertain attempt is fenced forever until reconciled. Stripe's
        # idempotency cache is not a permanent financial receipt.
        if datetime.now(UTC) - row["created_at"] >= timedelta(hours=23):
            await self._state(tenant_id, request_id, "unknown")
            return self.response(await self._get(tenant_id, request_id))
        try:
            price = await self.billing._stripe_call("Price", "retrieve", option["stripe_price_id"])
            validate_provider_price(option, price)
            if row.get("stripe_customer_id"):
                customer = await self.billing._stripe_call("Customer", "retrieve", row["stripe_customer_id"])
            else:
                customer = await self.billing._stripe_call(
                    "Customer", "create", email=snapshot["email"], name=snapshot["business_name"],
                    metadata={"tenant_id": tenant_id}, idempotency_key=f"subscription-customer:{request_id}",
                )
            self._check_customer(customer, row)
            await self._save_customer(row, customer)
            row["stripe_customer_id"] = customer["id"]
            meta = {"tenant_id": tenant_id, "plan_id": option["plan_id"], "price_option_id": price_option_id, "request_id": request_id}
            session = await self.billing._stripe_call(
                "checkout.Session", "create", customer=customer["id"], mode="subscription",
                # Delayed payment-method completion/recovery belongs to CP03.
                payment_method_types=["card"],
                line_items=[{"price": option["stripe_price_id"], "quantity": 1}],
                success_url=snapshot["success_url"], cancel_url=snapshot["cancel_url"],
                expires_at=snapshot["expires_at"], adaptive_pricing={"enabled": False},
                client_reference_id=request_id, metadata=meta, subscription_data={"metadata": meta},
                idempotency_key=f"subscription-checkout:{request_id}",
            )
            await self._save_session(row, session)
        except asyncio.CancelledError:
            await asyncio.shield(self._state(tenant_id, request_id, "unknown"))
            raise
        except Exception:
            # Even a transport error may follow provider acceptance. Never issue
            # a new identity or claim that no financial effect occurred.
            await self._state(tenant_id, request_id, "unknown")
            raise
        return self.response(await self._get(tenant_id, request_id))

    async def get(self, *, tenant_id, request_id):
        row = await self._get(str(tenant_id), str(request_id))
        if row is None:
            raise CheckoutError("checkout_not_found", "Checkout request was not found.", 404)
        option = row["snapshot"]["option"]
        if row.get("stripe_session_id") and row["status"] not in {"completed", "failed", "expired"}:
            if self.billing.billing_mode != option["provider_mode"]:
                raise CheckoutError("billing_mode_changed", "Checkout needs reconciliation in its original billing mode.", 503)
            session = await self.billing._stripe_call("checkout.Session", "retrieve", row["stripe_session_id"])
            await self._save_session(row, session)
            row = await self._get(str(tenant_id), str(request_id))
        return self.response(row)

    async def sync_subscription(self, subscription, *, checkout_session=None):
        """Bind new purchase events to their durable attempt and paid session.

        CP03 still owns event claims/recovery/order. No event claim is deleted or
        replayed here. A subscription-created event alone cannot grant allowance.
        """
        from app.domain.services.billing_catalog import validate_provider_price
        from app.domain.services.subscription_status import ACTIVE, INACTIVE, canonical
        meta = _object(subscription.get("metadata"))
        tenant_id, request_id = meta.get("tenant_id"), meta.get("request_id")
        if not tenant_id or not request_id:
            raise CheckoutError("purchase_binding_missing", "Subscription purchase binding is unavailable.")
        row = await self._get(str(tenant_id), str(UUID(request_id)))
        if row is None:
            raise CheckoutError("purchase_binding_missing", "Subscription purchase was not recorded.")
        option = row["snapshot"]["option"]
        if (option["kind"] != "stripe" or self.billing.billing_mode != option["provider_mode"]
                or subscription.get("livemode") is not (option["provider_mode"] == "live")
                or _id(subscription.get("customer")) != row["stripe_customer_id"]
                or meta.get("price_option_id") != str(option["id"])):
            raise CheckoutError("subscription_mismatch", "Subscription ownership could not be verified.")
        items = _object(subscription.get("items")).get("data") or []
        if len(items) != 1 or items[0].get("quantity") != 1:
            raise CheckoutError("subscription_price_mismatch", "Subscription items do not match the selected offer.")
        item = items[0]
        price = item.get("price")
        if not isinstance(price, dict):
            price = await self.billing._stripe_call("Price", "retrieve", price)
        validate_provider_price(option, price, require_active=False)
        start, end = subscription_period(subscription)
        eligible = canonical(subscription.get("status")) == ACTIVE
        if checkout_session is not None:
            self.check_session(checkout_session, row)
            if (checkout_session.get("status") != "complete"
                    or checkout_session.get("payment_status") != "paid"
                    or _id(checkout_session.get("subscription")) != subscription.get("id")):
                raise CheckoutError("payment_unconfirmed", "Subscription payment is not confirmed.")
        async with acquire_with_tenant(self.pool, str(tenant_id)) as conn:
            tenant = await conn.fetchrow("SELECT stripe_customer_id,stripe_subscription_id FROM tenants WHERE id=$1::uuid FOR UPDATE", str(tenant_id))
            saved = await conn.fetchrow("SELECT status,stripe_session_id FROM billing_checkout_attempts WHERE id=$1::uuid AND tenant_id=$2::uuid FOR UPDATE", str(request_id), str(tenant_id))
            if (not tenant or not saved or tenant["stripe_customer_id"] != row["stripe_customer_id"]
                    or tenant["stripe_subscription_id"] not in {None, subscription["id"]}
                    or (checkout_session is not None and saved["stripe_session_id"] not in {None, checkout_session["id"]})):
                raise CheckoutError("purchase_binding_changed", "Billing account changed; reconciliation is required.")
            if saved["status"] in {"expired", "failed"}:
                raise CheckoutError("purchase_terminal", "A terminal checkout needs reconciliation before activation.")
            purchase = public_option(option)
            purchase["stripe_price_id"] = option["stripe_price_id"]
            was_completed = saved["status"] == "completed"
            activate = checkout_session is not None and eligible
            if not was_completed and not activate:
                # Do not replace existing free access, plan or quota while the
                # first payment is still unconfirmed. The attempt stays pending.
                return
            stored = await conn.execute(
                """INSERT INTO subscriptions(tenant_id,stripe_subscription_id,stripe_customer_id,plan_id,status,
                   current_period_start,current_period_end,cancel_at,metadata)
                   VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,jsonb_build_object('purchase',$9::jsonb))
                   ON CONFLICT(stripe_subscription_id) DO UPDATE SET status=EXCLUDED.status,
                   current_period_start=EXCLUDED.current_period_start,current_period_end=EXCLUDED.current_period_end,
                   cancel_at=EXCLUDED.cancel_at,metadata=COALESCE(subscriptions.metadata,'{}'::jsonb)||EXCLUDED.metadata,updated_at=NOW()
                   WHERE subscriptions.tenant_id=EXCLUDED.tenant_id AND subscriptions.stripe_customer_id=EXCLUDED.stripe_customer_id""",
                str(tenant_id), subscription["id"], row["stripe_customer_id"], option["plan_id"],
                subscription["status"], start, end,
                datetime.fromtimestamp(subscription["cancel_at"], UTC) if subscription.get("cancel_at") else None,
                json.dumps(purchase),
            )
            if stored != "INSERT 0 1":
                raise CheckoutError("subscription_binding_conflict", "Subscription belongs to another billing account.")
            if activate and not was_completed:
                await apply_plan_allocation(conn, str(tenant_id), option["minutes"])
                await conn.execute(
                    """UPDATE billing_checkout_attempts SET status='completed',stripe_session_id=$3,
                       updated_at=NOW() WHERE id=$1::uuid AND tenant_id=$2::uuid""",
                    str(request_id), str(tenant_id), checkout_session["id"],
                )
            # Preserve the raw subscription status for the display, but do not
            # grant tenant activation on an early created/updated event alone.
            tenant_status = canonical(subscription["status"]) if (was_completed or activate or not eligible) else INACTIVE
            await conn.execute(
                "UPDATE tenants SET stripe_subscription_id=$2,subscription_status=$3,plan_id=$4 WHERE id=$1::uuid",
                str(tenant_id), subscription["id"], tenant_status, option["plan_id"],
            )


def subscription_period(subscription):
    """Support both retained legacy events and the current single-item SDK shape."""
    items = _object(subscription.get("items")).get("data") or []
    source = items[0] if len(items) == 1 and items[0].get("current_period_start") is not None else subscription
    start, end = source.get("current_period_start"), source.get("current_period_end")
    if (not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int)
            or isinstance(end, bool) or end <= start):
        raise CheckoutError("subscription_period_unavailable", "Subscription period is unavailable.")
    return datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end, UTC)
