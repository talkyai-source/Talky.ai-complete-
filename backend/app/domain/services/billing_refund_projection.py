"""Read-only provider refund facts. These never issue refunds or move minutes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from app.domain.services.billing_catalog import _EXPONENTS
from app.domain.services.billing_webhooks import (
    BillingWebhookReviewRequired,
    BillingWebhookRetryable,
)

REFUND_EVENTS = {"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"}
REFUND_STATUSES = {"pending", "requires_action", "succeeded", "failed", "canceled"}


def _id(value):
    return value.get("id") if hasattr(value, "get") else value


def _timestamp(value):
    if type(value) is not int:
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def public_refund_details(row):
    projection = row.get("refund_projection")
    if isinstance(projection, str):
        projection = json.loads(projection)
    captured_at = row.get("refund_captured_at")
    return {
        "detail_status": (
            projection.get("detail_status", "unavailable") if projection else "unavailable"
        ),
        "refunds": projection.get("refunds") if projection else None,
        "captured_at": captured_at.isoformat() if captured_at else None,
        "source_event_id": row.get("refund_source_event_id"),
    }


def normalize_refunds(charge, refund_list):
    """An absent/truncated list cannot prove there were no refunds."""
    if not hasattr(refund_list, "get") or not isinstance(refund_list.get("data"), list):
        return {"detail_status": "unavailable", "refunds": None}
    result, seen = [], set()
    complete = refund_list.get("has_more") is False
    for refund in refund_list["data"]:
        if (
            not hasattr(refund, "get")
            or not isinstance(refund.get("id"), str)
            or not refund["id"].startswith("re_")
            or refund["id"] in seen
            or _id(refund.get("charge")) != charge["id"]
            or _id(refund.get("payment_intent")) not in {None, _id(charge.get("payment_intent"))}
            or refund.get("currency") != charge.get("currency")
        ):
            complete = False
            continue
        seen.add(refund["id"])
        amount = refund.get("amount")
        amount = amount if type(amount) is int and 0 <= amount <= 2**53 - 1 else None
        refund_status = refund.get("status") if refund.get("status") in REFUND_STATUSES else None
        currency = refund.get("currency")
        exponent = _EXPONENTS.get(currency)
        created_at = _timestamp(refund.get("created"))
        complete = complete and all(
            value is not None for value in (amount, refund_status, exponent, created_at)
        )
        result.append(
            {
                "id": refund["id"],
                "status": refund_status,
                "amount": amount,
                "currency": currency,
                "currency_exponent": exponent,
                "created_at": created_at,
            }
        )
    # Failed/pending attempts may make the list total higher than the charge's
    # refunded amount. A lower known total is still incomplete evidence; this
    # check neither assigns minute reversals nor infers bank settlement.
    refunded_amount = charge.get("amount_refunded")
    if refunded_amount is not None:
        if type(refunded_amount) is not int or not 0 <= refunded_amount <= 2**53 - 1:
            complete = False
        elif sum(row["amount"] for row in result if row["amount"] is not None) < refunded_amount:
            complete = False
    return {"detail_status": "complete" if complete else "partial", "refunds": result}


async def capture_topup_refunds(conn, order, charge, event_id, *, refund_list=None):
    """Caller has verified the current charge and its local order/customer binding."""
    if not conn.is_in_transaction():
        raise RuntimeError("Refund observation requires a transaction")
    projection = normalize_refunds(
        charge, refund_list if refund_list is not None else charge.get("refunds")
    )
    # Existing payments with no expanded refund object make no factual claim;
    # the reader already reports unavailable. Avoid writing empty observations.
    if projection["detail_status"] == "unavailable":
        return projection
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    await conn.execute(
        """INSERT INTO billing_refund_snapshots
        (order_id,tenant_id,source_event_id,content_hash,projection)
        VALUES($1,$2,$3,$4,$5::jsonb)
        ON CONFLICT(order_id,source_event_id,content_hash) DO NOTHING""",
        order["id"],
        order["tenant_id"],
        event_id,
        digest,
        encoded,
    )
    return projection


async def handle_topup_refund_observation(conn, billing, event_type, data, event_id):
    """Refresh a known purchase's refund status without altering its ledger.

    Signed refund status events can arrive before/after a financial reversal.
    Partial refunds still require the existing allocation-policy review.
    """
    if event_type not in REFUND_EVENTS | {"charge.refunded"}:
        return {"status": "ignored"}
    if event_type == "charge.refunded":
        charge_id = data.get("id")
    else:
        refund_id = data.get("id")
        refund = await billing._stripe_call("Refund", "retrieve", refund_id)
        if refund.get("id") != refund_id or not _id(refund.get("charge")):
            raise BillingWebhookReviewRequired("refund_identity_mismatch")
        charge_id = _id(refund["charge"])
    charge = await billing._stripe_call("Charge", "retrieve", charge_id)
    payment_id = _id(charge.get("payment_intent"))
    if charge.get("id") != charge_id or not payment_id:
        raise BillingWebhookReviewRequired("refund_charge_mismatch")
    if not await conn.fetchval(
        "SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))",
        "billing:topup-payment:" + payment_id,
    ):
        raise BillingWebhookRetryable("refund_payment_busy")
    # Re-read after the shared payment lock, exactly as financial handlers do.
    charge = await billing._stripe_call("Charge", "retrieve", charge["id"])
    orders = await conn.fetch(
        """SELECT o.*,t.stripe_customer_id FROM topup_orders o
        JOIN tenants t ON t.id=o.tenant_id WHERE o.provider_payment_id=$1
        ORDER BY o.id LIMIT 2 FOR UPDATE OF o""",
        payment_id,
    )
    if not orders:
        return {"status": "ignored"}
    if len(orders) != 1:
        raise BillingWebhookReviewRequired("refund_order_binding_ambiguous")
    order = orders[0]
    payment = await billing._stripe_call("PaymentIntent", "retrieve", payment_id)
    metadata = payment.get("metadata") or {}
    expected_live = billing.billing_mode == "live"
    currency = str(order["currency"]).lower()
    customer = order["stripe_customer_id"]
    if (
        billing.billing_mode not in {"test", "live"}
        or order["provider"] != "stripe"
        or not customer
        or charge.get("id") != charge_id
        or _id(charge.get("payment_intent")) != payment_id
        or payment.get("id") != payment_id
        or metadata.get("purpose") != "minute_topup"
        or metadata.get("order_id") != str(order["id"])
        or metadata.get("tenant_id") != str(order["tenant_id"])
        or _id(payment.get("latest_charge")) != charge["id"]
        or charge.get("livemode") is not expected_live
        or payment.get("livemode") is not expected_live
        or _id(charge.get("customer")) != customer
        or _id(payment.get("customer")) != customer
        or charge.get("currency") != currency
        or payment.get("currency") != currency
        or type(charge.get("amount")) is not int
        or charge["amount"] != order["price_cents"]
        or type(payment.get("amount")) is not int
        or payment["amount"] != order["price_cents"]
    ):
        raise BillingWebhookReviewRequired("refund_order_binding_mismatch")
    refund_list = {"data": [], "has_more": True}
    cursor = None
    # Bound reads; truncated or interrupted detail stays partial, never empty-complete.
    try:
        async with asyncio.timeout(12):
            for _ in range(3):
                params = {"charge": charge["id"], "limit": 100}
                if cursor:
                    params["starting_after"] = cursor
                page = await billing._stripe_call("Refund", "list", **params)
                page_rows = page.get("data")
                if not isinstance(page_rows, list):
                    break
                refund_list["data"].extend(page_rows)
                if page.get("has_more") is False:
                    refund_list["has_more"] = False
                    break
                next_cursor = page_rows[-1].get("id") if page_rows else None
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
    except Exception:
        # No external side effects occurred. Preserve known facts and allow a
        # later status event/operator replay to improve the observation.
        pass
    projection = await capture_topup_refunds(conn, order, charge, event_id, refund_list=refund_list)
    return {
        "status": "handled",
        "tenant_id": str(order["tenant_id"]),
        "detail_status": projection["detail_status"],
    }
