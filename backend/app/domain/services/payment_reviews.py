"""Payments that need a person: list them and close them, for platform staff.

The list holds exactly the rows the payments alert counts (scripts/healthwatch.py):
Stripe and PayPal receipts in needs_review, plus receipts that have been stuck
(failed/pending/received) for longer than the alert's own limit.

Closing a review changes NO money, minutes or plan. It marks one receipt
``dismissed`` and records who closed it and why. Nothing here calls a function
that grants minutes or a plan, refunds, or touches the ledger.

The caller passes a platform connection (``acquire_with_tenant(pool, None)``).
No statement has a tenant predicate on purpose: this is a platform-staff read and
write across accounts, on platform-only tables, authorised by the endpoint
(require_platform_admin + session check + require_platform_admin_mutation for
writes). Each join is keyed by one exact id. The list never returns a payload,
a provider customer id or an email.
"""
from __future__ import annotations

import re
from uuid import UUID

from app.domain.services.billing_catalog import _EXPONENTS
from app.domain.services.billing_webhook_reconciliation import dismiss_receipt
from app.domain.services.billing_webhooks import BillingWebhookRetryable, lock_event

_Q_STRIPE = (
    "SELECT event_id, event_type, state, attempt_count, last_error_code, received_at, "
    "FLOOR(EXTRACT(EPOCH FROM (NOW() - received_at)) / 3600)::int AS waiting_hours, "
    "received_at < NOW() - INTERVAL '4 days' AS past_redelivery, "
    "event_payload->'data'->'object'->>'id' AS object_id, "
    "event_payload->'data'->'object'->'metadata'->>'tenant_id' AS metadata_tenant_id, "
    "event_payload->'data'->'object'->'metadata'->>'purpose' AS purpose, "
    "event_payload->'data'->'object'->>'mode' AS checkout_mode, "
    "event_payload->'data'->'object'->>'customer' AS customer_id, "
    "COALESCE(event_payload->'data'->'object'->>'amount_total', "
    "event_payload->'data'->'object'->>'amount_paid', "
    "event_payload->'data'->'object'->>'amount') AS amount_text, "
    "event_payload->'data'->'object'->>'currency' AS currency "
    "FROM processed_webhook_events "
    "WHERE state = 'needs_review' OR (state IN ('failed', 'pending') AND received_at < NOW() - INTERVAL '6 hours') "
    "ORDER BY received_at, event_id LIMIT $1"
)
_Q_PAYPAL = (
    "SELECT e.event_id, e.event_type, e.state, e.detail, e.attempt_count, e.received_at, e.resource_id, "
    "FLOOR(EXTRACT(EPOCH FROM (NOW() - e.received_at)) / 3600)::int AS waiting_hours, "
    "e.received_at < NOW() - INTERVAL '4 days' AS past_redelivery, "
    "o.tenant_id AS order_tenant_id, o.price_cents, o.currency AS order_currency, "
    "s.tenant_id AS plan_tenant_id, s.snapshot->'option'->>'amount_minor' AS plan_amount_text, "
    "s.snapshot->'option'->>'currency' AS plan_currency "
    "FROM paypal_webhook_events e "
    "LEFT JOIN LATERAL (SELECT t.tenant_id, t.price_cents, t.currency FROM topup_orders t "
    "WHERE t.provider = 'paypal' AND (t.id = e.order_id OR (e.order_id IS NULL AND "
    "(t.provider_session_id = e.resource_id OR t.provider_payment_id = e.resource_id))) "
    "ORDER BY t.created_at LIMIT 1) o ON TRUE "
    "LEFT JOIN paypal_subscriptions s ON s.paypal_subscription_id = e.resource_id "
    "WHERE e.state = 'needs_review' OR (e.state IN ('failed', 'received') AND e.received_at < NOW() - INTERVAL '1 hour') "
    "ORDER BY e.received_at, e.event_id LIMIT $1"
)
_Q_NAMES = (
    "SELECT id, business_name, stripe_customer_id FROM tenants "
    "WHERE id = ANY($1::uuid[]) OR stripe_customer_id = ANY($2::text[])"
)
_Q_PP_LOCK = (
    "SELECT state, detail, attempt_count, received_at < NOW() - INTERVAL '4 days' AS past_redelivery "
    "FROM paypal_webhook_events WHERE event_id = $1 FOR UPDATE"
)
_Q_PP_LOG = (
    "INSERT INTO paypal_webhook_review_log (event_id, operator, operator_id, decision, reason, attempt_count_seen) "
    "VALUES ($1, $2, $3::uuid, 'dismiss', $4, $5)"
)
_Q_PP_CLOSE = (
    "UPDATE paypal_webhook_events SET state = 'dismissed' "
    "WHERE event_id = $1 AND state = $2 AND attempt_count = $3 RETURNING event_id"
)
_Q_STRIPE_LOCK = (
    "SELECT state, attempt_count, received_at < NOW() - INTERVAL '4 days' AS past_redelivery "
    "FROM processed_webhook_events WHERE event_id = $1 FOR UPDATE"
)

_CUSTOMER = re.compile(r"^cus_[A-Za-z0-9]+$")
_DIGITS = re.compile(r"^[0-9]{1,15}$")
_BLOCKED = (
    "PayPal may still send this message again (it keeps trying for up to 3 days). "
    "It can be closed 4 days after it arrived."
)
_BLOCKED_STRIPE = (
    "Stripe may still send this message again (it keeps trying for up to 3 days). "
    "It can be closed 4 days after it arrived."
)
# These two plan rules are written again by the 5-minute sweep on every tick while the
# condition lasts, so their attempt count moves without any new information: a close
# prepared over a normal read must not be refused for it.
_RECURRING_RULES = frozenset({"binding", "not_confirmed_with_paypal"})
_OPERATOR_REASON = "An identified operator and substantive reconciliation reason are required"

_CHANGED_PAIR = (
    "The provider sent the same message twice with different contents. Nothing was processed."
)
_PP_ORDER_TEXT = (
    "PayPal shows a payment for this order that does not match it (amount, currency or order). "
    "The minutes were not given. Check it at PayPal and refund it or settle it with the customer."
)
_PP_REVERSED_TEXT = "PayPal took money back for an order that was never credited. Nothing was changed."
_PP_TEXTS = {
    "payload_changed": _CHANGED_PAIR,
    "service_review": _PP_ORDER_TEXT,
    "refund_review": "PayPal shows a refund we could not match in full. The minutes were not taken back.",
    "reversal_review": _PP_REVERSED_TEXT,
    "dispute_created": _PP_REVERSED_TEXT,
    "dispute_escalated": _PP_REVERSED_TEXT,
    "dispute_inquiry": (
        "The customer opened a PayPal dispute. The money is still ours and nothing was changed. "
        "Answer it at PayPal."
    ),
    "dispute_update": "A PayPal dispute on this payment changed. Nothing was changed here. Check it at PayPal.",
    "plan_review": "PayPal's subscription does not match our record. Nothing was given or changed.",
    "binding": "PayPal's subscription does not match our record. Nothing was given or changed.",
    "superseded": (
        "A second plan purchase was stopped at PayPal. If PayPal took a payment for it, refund it."
    ),
    "still_running_at_paypal": (
        "This plan has ended here but PayPal still shows it running and may charge again. "
        "Check it at PayPal."
    ),
    "active_without_payment": (
        "PayPal shows the plan active but no payment of the price was seen. The plan was not given "
        "and was stopped at PayPal. Refund any payment."
    ),
    "charged_after_cancel": (
        "PayPal took a payment after the customer cancelled. The plan was not extended. Refund it at PayPal."
    ),
    "dispute": "A plan payment was taken back. The plan was ended and the account blocked.",
    "reversal": "A plan payment was taken back. The plan was ended and the account blocked.",
    "not_confirmed_with_paypal": (
        "This paying plan could not be confirmed with PayPal for 3 days. It is still active. Check it at PayPal."
    ),
}
_PP_ORDER_CODES = frozenset(
    {
        "order_id_mismatch", "purchase_units_not_one", "captures_not_one", "capture_id_missing",
        "capture_custom_id_mismatch", "currency_mismatch", "amount_unreadable", "amount_mismatch",
    }
)
_FALLBACK = "This payment did not match our records. Nothing was given or taken back automatically."


def reason_text(provider: str, state: str, code: str | None) -> str:
    """Plain words for why a payment is on the list."""
    name = "Stripe" if provider == "stripe" else "PayPal"
    code = code or ""
    if state == "stuck":
        wait = "6 hours" if provider == "stripe" else "an hour"
        return (
            f"We could not finish this {name} message for more than {wait}. The customer may have paid "
            f"without getting minutes or a plan. Check the payment at {name}."
        )
    if provider == "stripe":
        if code in ("event_identity_conflict", "payload_changed"):
            return _CHANGED_PAIR
        if code == "provider_account_or_mode_mismatch":
            return (
                "The message came from another Stripe account or the wrong mode (test or live). "
                "Nothing was changed."
            )
        if code:
            return (
                "Stripe's message did not match our records, so nothing was given or taken back. "
                "Check the payment in Stripe."
            )
        return _FALLBACK
    if code in _PP_TEXTS:
        return _PP_TEXTS[code]
    if (
        code in _PP_ORDER_CODES
        or code.startswith("capture_status_")
        or (code.startswith("unit_") and code.endswith("_mismatch"))
    ):
        return _PP_ORDER_TEXT
    if code.startswith("refund_"):
        return _PP_TEXTS["refund_review"]
    if code.startswith("reverse_on_"):
        return _PP_REVERSED_TEXT
    return _FALLBACK


def _uuid(value) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _digits(value) -> int | None:
    return int(value) if isinstance(value, str) and _DIGITS.match(value) else None


def _stripe_kind(row) -> str:
    event_type = row["event_type"] or ""
    if row["purpose"] == "minute_topup":
        return "minutes"
    if (
        event_type.startswith("customer.subscription.")
        or event_type.startswith("invoice.")
        or row["checkout_mode"] == "subscription"
    ):
        return "plan"
    return "unknown"


def _paypal_kind(row) -> str:
    event_type = row["event_type"] or ""
    if (
        row["order_tenant_id"] is not None
        or event_type == "LOCAL.CAPTURE.REVIEW"
        or event_type.startswith(("CHECKOUT.ORDER.", "PAYMENT.CAPTURE."))
    ):
        return "minutes"
    if (
        row["plan_tenant_id"] is not None
        or event_type == "LOCAL.PLAN.REVIEW"
        or event_type.startswith(("BILLING.SUBSCRIPTION.", "PAYMENT.SALE."))
    ):
        return "plan"
    return "unknown"


def _item(provider, row, *, state, kind, tenant_id, amount, currency, code, reference, can_close, blocked):
    currency = currency.lower() if isinstance(currency, str) and currency else None
    exponent = _EXPONENTS.get(currency) if currency else None
    if exponent is None:
        amount = None
    return {
        "provider": provider,
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "state": state,
        "received_at": row["received_at"],
        "waiting_hours": int(row["waiting_hours"] or 0),
        "kind": kind,
        "tenant_id": tenant_id,
        "business_name": None,
        "amount_minor": amount,
        "currency": currency,
        "currency_exponent": exponent,
        "reason_code": code,
        "reason_text": reason_text(provider, state, code),
        "provider_reference": reference,
        "attempt_count": int(row["attempt_count"] or 0),
        "can_close": can_close,
        "close_blocked_reason": blocked,
    }


async def list_payment_reviews(conn, *, limit: int = 100) -> dict:
    limit = max(1, min(int(limit), 100))
    stripe_rows = await conn.fetch(_Q_STRIPE, limit + 1)
    paypal_rows = await conn.fetch(_Q_PAYPAL, limit + 1)
    truncated = len(stripe_rows) > limit or len(paypal_rows) > limit
    stripe_rows, paypal_rows = list(stripe_rows)[:limit], list(paypal_rows)[:limit]

    stripe_items = []
    for row in stripe_rows:
        state = "needs_review" if row["state"] == "needs_review" else "stuck"
        amount = _digits(row["amount_text"])
        can_close = state == "needs_review" or bool(row["past_redelivery"])
        item = _item(
            "stripe", row, state=state, kind=_stripe_kind(row),
            tenant_id=_uuid(row["metadata_tenant_id"]), amount=amount, currency=row["currency"],
            code=row["last_error_code"], reference=row["object_id"],
            can_close=can_close, blocked=None if can_close else _BLOCKED_STRIPE,
        )
        item["_customer"] = row["customer_id"] if _CUSTOMER.match(row["customer_id"] or "") else None
        stripe_items.append(item)

    paypal_items = []
    for row in paypal_rows:
        state = "needs_review" if row["state"] == "needs_review" else "stuck"
        kind = _paypal_kind(row)
        if row["order_tenant_id"] is not None:
            tenant_id = _uuid(row["order_tenant_id"])
            amount, currency = row["price_cents"], row["order_currency"]
        elif row["plan_tenant_id"] is not None:
            tenant_id = _uuid(row["plan_tenant_id"])
            amount, currency = _digits(row["plan_amount_text"]), row["plan_currency"]
        else:
            tenant_id, amount, currency = None, None, None
        can_close = state == "needs_review" or bool(row["past_redelivery"])
        paypal_items.append(_item(
            "paypal", row, state=state, kind=kind, tenant_id=tenant_id, amount=amount,
            currency=currency, code=row["detail"], reference=row["resource_id"],
            can_close=can_close, blocked=None if can_close else _BLOCKED,
        ))

    items = stripe_items + paypal_items
    ids = sorted({i["tenant_id"] for i in items if i["tenant_id"]})
    customers = sorted({i["_customer"] for i in stripe_items if i["tenant_id"] is None and i["_customer"]})
    names, by_customer = {}, {}
    if ids or customers:
        for tenant in await conn.fetch(_Q_NAMES, ids, customers):
            names[str(tenant["id"])] = tenant["business_name"]
            if tenant["stripe_customer_id"]:
                by_customer.setdefault(tenant["stripe_customer_id"], []).append(str(tenant["id"]))
    for item in items:
        customer = item.pop("_customer", None)
        if item["tenant_id"] is None and customer and len(by_customer.get(customer, [])) == 1:
            item["tenant_id"] = by_customer[customer][0]
        item["business_name"] = names.get(item["tenant_id"]) if item["tenant_id"] else None
    items.sort(key=lambda i: (i["received_at"], i["event_id"]))
    return {"items": items, "truncated": truncated, "limit": limit}


def _closed(provider, event_id, prior_state, changed):
    return {
        "provider": provider, "event_id": event_id, "state": "dismissed", "changed": changed,
        "prior_state": prior_state, "financial_effects_applied": False,
    }


async def close_payment_review(
    conn, *, provider, event_id, seen_attempt_count, operator, operator_id, reason,
) -> dict:
    """Mark one receipt handled. Never moves money, minutes or a plan."""
    if (
        not isinstance(operator, str) or not operator.strip() or len(operator) > 200
        or not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 2000
    ):
        raise ValueError(_OPERATOR_REASON)
    changed_text = "This payment changed after the list was read. Read the list again."
    if provider == "paypal":
        row = await conn.fetchrow(_Q_PP_LOCK, event_id)
        if row is None:
            raise ValueError("No such payment message.")
        if row["state"] == "dismissed":
            return _closed(provider, event_id, "dismissed", False)
        if row["attempt_count"] != seen_attempt_count and row["detail"] not in _RECURRING_RULES:
            raise ValueError(changed_text)
        if row["state"] == "needs_review":
            pass
        elif row["state"] in ("failed", "received"):
            if not row["past_redelivery"]:
                raise ValueError(_BLOCKED)
        else:
            raise ValueError("Only a payment that needs review can be closed.")
        await conn.execute(
            _Q_PP_LOG, event_id, operator.strip(), operator_id, reason.strip(), row["attempt_count"],
        )
        updated = await conn.fetch(_Q_PP_CLOSE, event_id, row["state"], row["attempt_count"])
        if len(updated) != 1:
            raise ValueError("Exactly one receipt must change; nothing was recorded")
        return _closed(provider, event_id, row["state"], True)
    if provider == "stripe":
        if not await lock_event(conn, event_id):
            raise BillingWebhookRetryable("event_busy")
        row = await conn.fetchrow(_Q_STRIPE_LOCK, event_id)
        if row is None:
            raise ValueError("No such payment message.")
        if row["state"] != "dismissed" and row["attempt_count"] != seen_attempt_count:
            raise ValueError(changed_text)
        if row["state"] in ("failed", "pending") and not row["past_redelivery"]:
            raise ValueError(_BLOCKED_STRIPE)
        result = await dismiss_receipt(conn, event_id, operator=operator, reason=reason)
        return _closed(provider, event_id, row["state"], bool(result["changed"]))
    raise ValueError("Unknown payment provider.")
