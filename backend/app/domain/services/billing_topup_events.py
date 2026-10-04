"""Verify current provider objects before transactional top-up ledger changes."""

from decimal import Decimal
from uuid import UUID

from app.domain.services.billing_catalog import _EXPONENTS
from app.domain.services.topup_service import BillingTopupReviewRequired, TopupService


class BillingTopupBusy(RuntimeError):
    """Another transaction owns this payment; provider redelivery is safe."""


def _id(value):
    return value.get("id") if hasattr(value, "get") else value


def _review(reason, tenant_id=None):
    raise BillingTopupReviewRequired(reason, tenant_id)


def _result(status, tenant_id=None, *, reason=None, notifications=None):
    return {
        "status": status,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "notifications": notifications or [],
        **({"reason": reason} if reason else {}),
    }


def _metadata_matches(obj, order):
    metadata = obj.get("metadata") or {}
    return (
        metadata.get("purpose") == "minute_topup"
        and metadata.get("tenant_id") == str(order["tenant_id"])
        and metadata.get("order_id") == str(order["id"])
    )


def _amount_matches(obj, field, expected):
    return type(obj.get(field)) is int and obj[field] == expected


async def _retrieve(billing, resource, identity):
    try:
        return await billing._stripe_call(resource, "retrieve", identity)
    except Exception as exc:
        if getattr(exc, "http_status", getattr(exc, "status_code", None)) == 404:
            _review("provider_object_not_found")
        raise


async def _current_reversal(billing, event_type, identity):
    dispute = None
    charge_id = identity
    if event_type == "charge.dispute.created":
        dispute = await _retrieve(billing, "Dispute", identity)
        if dispute.get("id") != identity:
            _review("dispute_identity_mismatch")
        charge_id = _id(dispute.get("charge"))
    if not charge_id:
        _review("charge_binding_missing")
    charge = await _retrieve(billing, "Charge", charge_id)
    if charge.get("id") != charge_id:
        _review("charge_identity_mismatch")
    return charge, dispute


def _receipt(order, session):
    currency = order["currency"].lower()
    exponent = _EXPONENTS[currency]
    amount = Decimal(order["price_cents"]) / (10**exponent)
    recipient = (session.get("customer_details") or {}).get("email")
    if (
        not isinstance(recipient, str)
        or "@" not in recipient
        or any(c in recipient for c in "\r\n")
    ):
        recipient = None
    return {
        "delivery_key": f"topup:{order['id']}:paid",
        "kind": "topup_paid",
        "tenant_id": str(order["tenant_id"]),
        "recipient": recipient,
        "subject": f"Payment for {order['minutes']} minutes recorded",
        "body": (
            f"Your purchase of {order['minutes']} call minutes was recorded. "
            f"Purchase amount: {currency.upper()} {amount:.{exponent}f}. "
            "Your current balance is available on the Billing page."
        ),
    }


async def apply_topup_event(conn, billing, event_type, data, event_id):
    """Caller owns the transaction and durable signed-event receipt.

    Read failures propagate for retry; conflicting evidence raises review before
    any ledger mutation. No email or external financial write occurs here.
    """
    if not conn.is_in_transaction():
        raise RuntimeError("Top-up event requires a transaction")
    if not event_id:
        _review("event_identity_missing")
    if billing.billing_mode not in {"test", "live"}:
        _review("billing_mode_unavailable")
    session_event = event_type in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
    reversal = event_type in {"charge.refunded", "charge.dispute.created"}
    if not session_event and not reversal:
        return _result("ignored")
    payment_id = _id(data.get("payment_intent"))
    aggregate = (
        "billing:topup-payment:" + str(payment_id)
        if payment_id
        else "billing:topup-object:" + str(data.get("id"))
    )
    locked = await conn.fetchval(
        "SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))", aggregate
    )
    if not locked:
        raise BillingTopupBusy("Top-up payment is already being processed")

    session = charge = dispute = None
    if session_event:
        session = await _retrieve(billing, "checkout.Session", data.get("id"))
        if session.get("id") != data.get("id") or session.get("livemode") is not (
            billing.billing_mode == "live"
        ):
            _review("session_identity_or_mode_mismatch")
        current_payment = _id(session.get("payment_intent"))
        if payment_id is None and current_payment:
            # An unpaid event can precede creation of its PaymentIntent. Discover
            # the binding, lock it, then reread so no money uses an unlocked view.
            if not await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))",
                "billing:topup-payment:" + current_payment,
            ):
                raise BillingTopupBusy("Top-up payment is already being processed")
            payment_id = current_payment
            session = await _retrieve(billing, "checkout.Session", data.get("id"))
            if session.get("id") != data.get("id") or session.get("livemode") is not (
                billing.billing_mode == "live"
            ):
                _review("session_identity_or_mode_mismatch")
        if _id(session.get("payment_intent")) != payment_id:
            _review("payment_binding_changed")
        metadata = session.get("metadata") or {}
    else:
        charge, dispute = await _current_reversal(billing, event_type, data.get("id"))
        if not payment_id:
            # Dispute.payment_intent is nullable. Its current Charge supplies
            # the payment binding; reread both objects after acquiring that lock.
            payment_id = _id(charge.get("payment_intent"))
            if not payment_id:
                _review("payment_binding_missing")
            if not await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))",
                "billing:topup-payment:" + payment_id,
            ):
                raise BillingTopupBusy("Top-up payment is already being processed")
            charge, dispute = await _current_reversal(billing, event_type, data.get("id"))
        if _id(charge.get("payment_intent")) != payment_id:
            _review("charge_payment_mismatch")
        if dispute is not None and _id(dispute.get("payment_intent")) not in {None, payment_id}:
            _review("dispute_identity_mismatch")
        metadata = None

    payment = None
    if payment_id:
        payment = await _retrieve(billing, "PaymentIntent", payment_id)
        if payment.get("id") != payment_id:
            _review("payment_identity_mismatch")
        if metadata is None:
            metadata = payment.get("metadata") or {}
    if not metadata or metadata.get("purpose") != "minute_topup":
        # A charge on this account can belong to a subscription. However a
        # locally bound top-up payment must not disappear through metadata drift.
        known = await conn.fetchval(
            "SELECT id FROM topup_orders WHERE provider_payment_id=$1", payment_id
        )
        if known or session_event:
            _review("topup_metadata_missing")
        return _result("ignored")
    try:
        order_id = UUID(str(metadata.get("order_id")))
    except (ValueError, TypeError):
        _review("order_identity_invalid")
    order_row = await conn.fetchrow("SELECT * FROM topup_orders WHERE id=$1 FOR UPDATE", order_id)
    if not order_row:
        _review("order_not_recorded")
    order = dict(order_row)
    tenant_id = order["tenant_id"]
    if order["provider"] != "stripe" or not order.get("provider_session_id"):
        _review("order_provider_binding_missing", tenant_id)
    if session is None:
        session = await _retrieve(billing, "checkout.Session", order["provider_session_id"])
    tenant = await conn.fetchrow(
        "SELECT stripe_customer_id,minutes_allocated FROM tenants WHERE id=$1 FOR UPDATE", tenant_id
    )
    expected_live = billing.billing_mode == "live"
    currency = str(order["currency"]).lower()
    if (
        not tenant
        or not tenant["stripe_customer_id"]
        or currency not in _EXPONENTS
        or type(order["price_cents"]) is not int
        or order["price_cents"] <= 0
        or type(order["minutes"]) is not int
        or order["minutes"] <= 0
    ):
        _review("order_terms_or_customer_unavailable", tenant_id)
    customer = tenant["stripe_customer_id"]
    if (
        session.get("id") != order["provider_session_id"]
        or session.get("mode") != "payment"
        or session.get("livemode") is not expected_live
        or _id(session.get("customer")) != customer
        or session.get("client_reference_id") != str(order["id"])
        or not _metadata_matches(session, order)
        or _id(session.get("payment_intent")) != payment_id
        or not _amount_matches(session, "amount_total", order["price_cents"])
        or session.get("currency") != currency
        or order.get("provider_payment_id") not in {None, payment_id}
    ):
        _review("session_order_terms_mismatch", tenant_id)
    topups = TopupService(None)
    if session.get("status") == "expired":
        await topups.mark_failed(session_id=session["id"], status="cancelled", conn=conn)
        return _result("handled", tenant_id)
    if session.get("status") != "complete" or session.get("payment_status") != "paid":
        if (
            event_type == "checkout.session.async_payment_failed"
            and payment
            and payment.get("status") in {"requires_payment_method", "canceled"}
            and _metadata_matches(payment, order)
            and payment.get("livemode") is expected_live
            and _id(payment.get("customer")) == customer
            and _amount_matches(payment, "amount", order["price_cents"])
            and payment.get("currency") == currency
        ):
            await topups.mark_failed(session_id=session["id"], status="failed", conn=conn)
            return _result("handled", tenant_id)
        return _result("deferred", tenant_id, reason="payment_not_settled")
    if (
        not payment
        or not _metadata_matches(payment, order)
        or payment.get("livemode") is not expected_live
        or _id(payment.get("customer")) != customer
        or not _amount_matches(payment, "amount", order["price_cents"])
        or not _amount_matches(payment, "amount_received", order["price_cents"])
        or payment.get("currency") != currency
        or payment.get("status") != "succeeded"
    ):
        _review("payment_order_terms_mismatch", tenant_id)
    latest_charge = _id(payment.get("latest_charge"))
    if not latest_charge:
        _review("payment_charge_missing", tenant_id)
    if charge is None:
        charge = await _retrieve(billing, "Charge", latest_charge)
    if (
        charge.get("id") != latest_charge
        or _id(charge.get("payment_intent")) != payment_id
        or charge.get("livemode") is not expected_live
        or _id(charge.get("customer")) != customer
        or charge.get("paid") is not True
        or charge.get("status") != "succeeded"
        or not _amount_matches(charge, "amount", order["price_cents"])
        or not _amount_matches(charge, "amount_captured", order["price_cents"])
        or charge.get("currency") != currency
    ):
        _review("charge_order_terms_mismatch", tenant_id)
    refunded = charge.get("amount_refunded")
    if type(refunded) is not int or not 0 <= refunded <= order["price_cents"]:
        _review("refund_amount_unavailable", tenant_id)
    if (charge.get("refunded") is True) != (refunded == order["price_cents"]):
        _review("refund_evidence_conflict", tenant_id)
    credit = await conn.fetchrow(
        """SELECT COUNT(*) AS count,COUNT(*) FILTER (WHERE lower(currency)<>$2) AS currency_mismatches,
        COALESCE(SUM(minutes_delta),0) AS minutes,
        COALESCE(SUM(amount_cents),0) AS amount FROM billing_ledger WHERE order_id=$1 AND kind='topup'""",
        order["id"],
        currency,
    )
    has_credit = (
        credit["count"] == 1
        and credit["minutes"] == order["minutes"]
        and credit["amount"] == order["price_cents"]
        and not credit["currency_mismatches"]
    )
    if credit["count"] and not has_credit:
        _review("recorded_credit_conflict", tenant_id)
    debits = await conn.fetch(
        """SELECT kind,minutes_delta,amount_cents,currency FROM billing_ledger
        WHERE order_id=$1 AND kind IN ('refund','dispute')""",
        order["id"],
    )
    if debits:
        if (
            not has_credit
            or len(debits) != 1
            or debits[0]["minutes_delta"] != -order["minutes"]
            or debits[0]["amount_cents"] != -order["price_cents"]
            or str(debits[0]["currency"]).lower() != currency
            or order["status"] not in {"refunded", "disputed"}
        ):
            _review("recorded_reversal_conflict", tenant_id)
        return _result("handled", tenant_id)
    if order["status"] in {"refunded", "disputed"}:
        _review("order_reversal_not_recorded", tenant_id)
    if has_credit and order["status"] != "paid":
        _review("order_ledger_state_conflict", tenant_id)
    if 0 < refunded < order["price_cents"]:
        _review("partial_refund_requires_allocation_policy", tenant_id)
    if reversal:
        if not has_credit:
            _review("reversal_before_recorded_credit", tenant_id)
        if order["status"] != "paid":
            _review("order_ledger_state_conflict", tenant_id)
        if dispute is not None:
            if (
                dispute.get("livemode") is not expected_live
                or dispute.get("currency") != currency
                or not _amount_matches(dispute, "amount", order["price_cents"])
                or charge.get("disputed") is not True
                or dispute.get("status") not in {"needs_response", "under_review", "lost"}
            ):
                _review("dispute_requires_reconciliation", tenant_id)
        elif refunded != order["price_cents"]:
            _review("full_refund_not_confirmed", tenant_id)
        reversed_payment = await topups.reverse(
            event_id=event_id,
            kind="dispute" if dispute else "refund",
            payment_id=payment_id,
            session_id=session["id"],
            conn=conn,
        )
        if not reversed_payment:
            _review("reversal_not_confirmed", tenant_id)
        return _result("handled", tenant_id)
    if refunded or charge.get("disputed") is True:
        _review("payment_reversed_before_credit", tenant_id)
    if tenant["minutes_allocated"] is None:
        _review("topup_allocation_missing", tenant_id)
    if order["status"] in {"refunded", "disputed"} or (
        order["status"] == "paid" and not has_credit
    ):
        _review("order_ledger_state_conflict", tenant_id)
    credited = await topups.credit_paid_order(
        session_id=session["id"], payment_id=payment_id, event_id=event_id, conn=conn
    )
    if not credited and not has_credit:
        _review("credit_not_confirmed", tenant_id)
    # New credit and intent commit together. A historical credit with no intent
    # has unknown prior delivery; insert a reconciliation fence, never resend it.
    receipt = _receipt(order, session)
    receipt["prior_delivery_unknown"] = has_credit
    return _result("handled", tenant_id, notifications=[receipt])
