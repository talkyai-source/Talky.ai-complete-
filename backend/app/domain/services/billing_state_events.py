"""Current provider state, verified ownership, and one caller-owned transaction.

Only provider reads occur here. Financial writes and notification intents are
returned to the webhook receipt transaction; no email or audit send is issued.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.services.billing_checkout import (
    CheckoutAttempts,
    CheckoutError,
    _id,
    _object,
    subscription_period,
)
from app.domain.services.subscription_status import (
    CANCELLED,
    INCOMPLETE_EXPIRED,
    KNOWN_STATUSES,
    canonical,
)


class BillingStateBusy(RuntimeError):
    """Another transaction is reading/applying this subscription; retry later."""


class BillingStateReviewRequired(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def invoice_subscription_id(invoice):
    parent = _object(invoice.get("parent"))
    modern = None
    if parent.get("type") == "subscription_details":
        modern = _id(_object(parent.get("subscription_details")).get("subscription"))
    legacy = _id(invoice.get("subscription"))
    if modern and legacy and modern != legacy:
        raise BillingStateReviewRequired("invoice_subscription_conflict")
    return modern or legacy


def validate_paid_invoice_lines(invoice, option):
    """The first payment cannot grant a frozen offer using unrelated line items."""
    lines = _object(invoice.get("lines"))
    if lines.get("has_more") is not False or not isinstance(lines.get("data"), list):
        raise BillingStateReviewRequired("invoice_lines_unconfirmed")
    subscription_lines = []
    for line in lines["data"]:
        parent = _object(line.get("parent"))
        if line.get("type") == "subscription" or parent.get("type") == "subscription_item_details":
            subscription_lines.append(line)
    if not subscription_lines:
        raise BillingStateReviewRequired("invoice_subscription_lines_missing")
    for line in subscription_lines:
        pricing = _object(line.get("pricing"))
        modern_price = _id(_object(pricing.get("price_details")).get("price"))
        legacy_price = _id(line.get("price"))
        if modern_price and legacy_price and modern_price != legacy_price:
            raise BillingStateReviewRequired("invoice_line_price_conflict")
        if ((modern_price or legacy_price) != option["stripe_price_id"]
                or line.get("quantity") != 1 or isinstance(line.get("quantity"), bool)
                or line.get("currency") != option["currency"]):
            raise BillingStateReviewRequired("invoice_line_price_mismatch")


def _result(status, tenant_id=None, *, reason=None, notifications=None):
    return {"status": status, "tenant_id": str(tenant_id) if tenant_id else None,
            "reason": reason, "notifications": notifications or []}


async def _lock(conn, billing, kind, identity):
    if not identity or not isinstance(identity, str):
        raise BillingStateReviewRequired("provider_identity_missing")
    acquired = await conn.fetchval(
        "SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))",
        f"billing:{billing.billing_mode}:{kind}:{identity}",
    )
    if not acquired:
        raise BillingStateBusy("billing_object_busy")


async def _current(billing, resource, identity):
    current = await billing._stripe_call(resource, "retrieve", identity)
    if (current.get("id") != identity
            or billing.billing_mode not in {"live", "test"}
            or current.get("livemode") is not (billing.billing_mode == "live")):
        raise BillingStateReviewRequired("provider_identity_or_mode_mismatch")
    if resource == "Subscription" and canonical(current.get("status")) not in KNOWN_STATUSES:
        raise BillingStateReviewRequired("subscription_status_unknown")
    return current


async def _binding(conn, subscription):
    """Metadata locates new attempts; persisted identities authorize ownership."""
    subscription_id, customer_id = subscription["id"], _id(subscription.get("customer"))
    if not customer_id:
        raise BillingStateReviewRequired("subscription_customer_missing")
    metadata = _object(subscription.get("metadata"))
    attempt = None
    recorded = await conn.fetchrow(
        "SELECT * FROM subscriptions WHERE stripe_subscription_id=$1", subscription_id,
    )
    if metadata.get("request_id"):
        try:
            request_id = str(UUID(metadata["request_id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise BillingStateReviewRequired("purchase_identity_invalid") from exc
        attempt = await conn.fetchrow("SELECT * FROM billing_checkout_attempts WHERE id=$1::uuid", request_id)
        if not attempt:
            raise BillingStateReviewRequired("purchase_binding_missing")
        attempt = dict(attempt)
        attempt["snapshot"] = _object(attempt["snapshot"])
        tenant_id = str(attempt["tenant_id"])
        if (attempt["stripe_customer_id"] != customer_id or metadata.get("tenant_id") != tenant_id
                or metadata.get("price_option_id") != str(attempt["price_option_id"])):
            raise BillingStateReviewRequired("purchase_binding_mismatch")
    elif recorded:
        tenant_id = str(recorded["tenant_id"])
    else:
        # Preserve already-bound legacy accounts without manufacturing a new
        # purchase from provider metadata. Ambiguous legacy mappings need review.
        rows = await conn.fetch(
            "SELECT id FROM tenants WHERE stripe_subscription_id=$1 AND stripe_customer_id=$2 LIMIT 2",
            subscription_id, customer_id,
        )
        if len(rows) != 1:
            raise BillingStateReviewRequired("legacy_subscription_unbound")
        tenant_id = str(rows[0]["id"])
    if (recorded and (str(recorded["tenant_id"]) != tenant_id or recorded["stripe_customer_id"] != customer_id)):
        raise BillingStateReviewRequired("stored_subscription_binding_mismatch")
    if metadata.get("tenant_id") not in {None, tenant_id}:
        raise BillingStateReviewRequired("subscription_metadata_mismatch")
    tenant = await conn.fetchrow(
        "SELECT id,plan_id,subscription_status,stripe_customer_id,stripe_subscription_id FROM tenants WHERE id=$1::uuid FOR UPDATE",
        tenant_id,
    )
    if not tenant or tenant["stripe_customer_id"] != customer_id:
        raise BillingStateReviewRequired("tenant_customer_mismatch")
    current = tenant["stripe_subscription_id"] == subscription_id
    if attempt and attempt["status"] != "completed" and tenant["stripe_subscription_id"] is None:
        current = True
    return {"tenant_id": tenant_id, "customer_id": customer_id, "tenant": tenant,
            "recorded": recorded, "attempt": attempt, "current": current}


async def _apply_subscription(conn, billing, subscription, binding, *, session=None, invoice=None):
    tenant_id, customer_id = binding["tenant_id"], binding["customer_id"]
    subscription_id = subscription["id"]
    if not binding["current"]:
        # An old subscription may change its own history, never the replacement
        # currently providing this tenant's access.
        await conn.execute(
            "UPDATE subscriptions SET status=$4,updated_at=NOW() WHERE tenant_id=$1::uuid AND stripe_subscription_id=$2 AND stripe_customer_id=$3",
            tenant_id, subscription_id, customer_id, subscription["status"],
        )
        return _result("ignored", tenant_id, reason="subscription_replaced")
    if binding["attempt"]:
        if (binding["attempt"]["status"] != "completed"
                and canonical(subscription["status"]) in {CANCELLED, INCOMPLETE_EXPIRED}):
            await conn.execute(
                """UPDATE billing_checkout_attempts SET status='failed',updated_at=NOW()
                   WHERE id=$1::uuid AND tenant_id=$2::uuid AND status IN ('creating','ready','unknown')""",
                str(binding["attempt"]["id"]), tenant_id,
            )
            return _result("handled", tenant_id, reason="initial_subscription_terminal")
        try:
            await CheckoutAttempts(None, billing).sync_subscription(
                subscription, checkout_session=session, paid_invoice=invoice, conn=conn,
            )
        except CheckoutError as exc:
            raise BillingStateReviewRequired(exc.code) from exc
        if (binding["attempt"]["status"] != "completed"
                and not ((session is not None or invoice is not None) and subscription["status"] == "active")):
            return _result("deferred", tenant_id, reason="initial_payment_unconfirmed")
    else:
        start, end = subscription_period(subscription)
        recorded = binding["recorded"] or {}
        plan_id = recorded.get("plan_id") or binding["tenant"]["plan_id"]
        written = await conn.execute(
            """INSERT INTO subscriptions(tenant_id,stripe_subscription_id,stripe_customer_id,plan_id,status,
               current_period_start,current_period_end,cancel_at,canceled_at)
               VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT(stripe_subscription_id) DO UPDATE SET status=EXCLUDED.status,
               current_period_start=EXCLUDED.current_period_start,current_period_end=EXCLUDED.current_period_end,
               cancel_at=EXCLUDED.cancel_at,canceled_at=EXCLUDED.canceled_at,updated_at=NOW()
               WHERE subscriptions.tenant_id=EXCLUDED.tenant_id AND subscriptions.stripe_customer_id=EXCLUDED.stripe_customer_id""",
            tenant_id, subscription_id, customer_id, plan_id, subscription["status"], start, end,
            _timestamp(subscription.get("cancel_at")), _timestamp(subscription.get("canceled_at")),
        )
        if written != "INSERT 0 1":
            raise BillingStateReviewRequired("subscription_binding_conflict")
        await conn.execute(
            """UPDATE tenants SET subscription_status=$4 WHERE id=$1::uuid
               AND stripe_subscription_id=$2 AND stripe_customer_id=$3""",
            tenant_id, subscription_id, customer_id, canonical(subscription["status"]),
        )
    if canonical(subscription["status"]) == CANCELLED:
        await conn.execute(
            """UPDATE tenants SET stripe_subscription_id=NULL,subscription_status=$4
               WHERE id=$1::uuid AND stripe_subscription_id=$2 AND stripe_customer_id=$3""",
            tenant_id, subscription_id, customer_id, CANCELLED,
        )
    return _result("handled", tenant_id)


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise BillingStateReviewRequired("provider_timestamp_invalid")
    return datetime.fromtimestamp(value, UTC)


async def _apply_invoice(conn, billing, event_type, data, *, source_reference=None, details_only=False):
    identity = data.get("id")
    subscription_id = invoice_subscription_id(data)
    if subscription_id:
        await _lock(conn, billing, "subscription", subscription_id)
    else:
        # Discovery alone never authorizes a write. Re-read after the discovered
        # subscription lock so concurrent events cannot apply stale snapshots.
        discovered = await _current(billing, "Invoice", identity)
        subscription_id = invoice_subscription_id(discovered)
        if not subscription_id:
            return _result("ignored", reason="not_subscription_invoice")
        await _lock(conn, billing, "subscription", subscription_id)
    invoice = await _current(billing, "Invoice", identity)
    if invoice_subscription_id(invoice) != subscription_id:
        raise BillingStateReviewRequired("invoice_subscription_changed")
    subscription = await _current(billing, "Subscription", subscription_id)
    if _id(invoice.get("customer")) != _id(subscription.get("customer")):
        raise BillingStateReviewRequired("invoice_customer_mismatch")
    binding = await _binding(conn, subscription)
    tenant_id = binding["tenant_id"]
    if _object(invoice.get("metadata")).get("tenant_id") not in {None, tenant_id}:
        raise BillingStateReviewRequired("invoice_metadata_mismatch")
    if invoice.get("status") not in {"draft", "open", "paid", "uncollectible", "void"}:
        raise BillingStateReviewRequired("invoice_status_unknown")
    if any(not isinstance(invoice.get(key), int) or isinstance(invoice.get(key), bool)
           for key in ("amount_due", "amount_paid")):
        raise BillingStateReviewRequired("invoice_amount_invalid")
    prior_invoice = await conn.fetchrow(
        "SELECT tenant_id,stripe_subscription_id,status,notification_history_known FROM invoices WHERE stripe_invoice_id=$1 FOR UPDATE", identity,
    )
    if prior_invoice and (str(prior_invoice["tenant_id"]) != tenant_id
                          or prior_invoice["stripe_subscription_id"] != subscription_id):
        raise BillingStateReviewRequired("invoice_binding_conflict")
    from app.domain.services.billing_invoice_projection import capture_invoice, store_snapshot
    projection, hydrated = await capture_invoice(
        billing, invoice, source_reference or f"observation:{uuid4()}",
    )
    paid = invoice["status"] == "paid"
    financially_relevant = not details_only and event_type in {"invoice.paid", "invoice.payment_failed"}
    if (financially_relevant and paid and binding["attempt"] and binding["attempt"]["status"] != "completed"
            and hydrated["lines"]["has_more"]):
        # Retry provider reads. Partial display rows cannot authorize access.
        raise BillingStateBusy("invoice_lines_pending")
    first_payment = paid and binding["attempt"] and binding["attempt"]["status"] != "completed"
    result = (await _apply_subscription(conn, billing, subscription, binding, invoice=hydrated if first_payment else None)
              if financially_relevant else _result("handled", tenant_id))
    if details_only:
        stored_invoice_id = await conn.fetchval(
            "SELECT id FROM invoices WHERE stripe_invoice_id=$1 AND tenant_id=$2::uuid", identity, tenant_id,
        )
        if not stored_invoice_id:
            raise BillingStateReviewRequired("invoice_not_recorded")
        await store_snapshot(conn, stored_invoice_id, tenant_id, projection)
        return {**result, "detail_status": projection["detail_status"]}
    stored = await conn.execute(
        """INSERT INTO invoices(stripe_invoice_id,stripe_subscription_id,tenant_id,amount_due,amount_paid,
           currency,status,invoice_pdf,hosted_invoice_url,period_start,period_end,due_date,paid_at,notification_history_known)
           VALUES($1,$2,$3::uuid,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,TRUE)
           ON CONFLICT(stripe_invoice_id) DO UPDATE SET amount_due=EXCLUDED.amount_due,amount_paid=EXCLUDED.amount_paid,
           currency=EXCLUDED.currency,status=EXCLUDED.status,invoice_pdf=EXCLUDED.invoice_pdf,
           hosted_invoice_url=EXCLUDED.hosted_invoice_url,period_start=EXCLUDED.period_start,
           period_end=EXCLUDED.period_end,due_date=EXCLUDED.due_date,paid_at=EXCLUDED.paid_at
           WHERE invoices.tenant_id=EXCLUDED.tenant_id AND invoices.stripe_subscription_id=EXCLUDED.stripe_subscription_id""",
        identity, subscription_id, tenant_id, invoice["amount_due"], invoice["amount_paid"], invoice.get("currency"),
        invoice["status"], invoice.get("invoice_pdf"), invoice.get("hosted_invoice_url"),
        _timestamp(invoice.get("period_start")), _timestamp(invoice.get("period_end")),
        _timestamp(invoice.get("due_date")), _timestamp(_object(invoice.get("status_transitions")).get("paid_at")),
    )
    if stored != "INSERT 0 1":
        raise BillingStateReviewRequired("invoice_binding_conflict")
    stored_invoice_id = await conn.fetchval(
        "SELECT id FROM invoices WHERE stripe_invoice_id=$1 AND tenant_id=$2::uuid", identity, tenant_id,
    )
    await store_snapshot(conn, stored_invoice_id, tenant_id, projection)
    if not financially_relevant:
        return result
    if paid:
        await conn.execute(
            """UPDATE billing_webhook_notifications SET status='superseded',updated_at=NOW()
               WHERE delivery_key=$1 AND tenant_id=$2::uuid
                 AND status IN ('pending','failed_before_send','recipient_missing')""",
            f"invoice:{identity}:payment_failed", tenant_id,
        )
        kind = "invoice_paid"
    elif event_type == "invoice.payment_failed" and invoice["status"] == "open" and subscription["status"] in {"past_due", "incomplete", "unpaid"}:
        kind = "invoice_payment_failed"
    else:
        kind = None
    if kind:
        # Retain the existing owner-only recipient policy. CP06 owns replacing
        # its known gap; no guessed admin/global recipient is introduced here.
        recipients = await conn.fetch(
            "SELECT email FROM user_profiles WHERE tenant_id=$1::uuid AND role='owner' LIMIT 2", tenant_id,
        )
        recipient = recipients[0]["email"] if len(recipients) == 1 else None
        suffix = "paid" if paid else "payment_failed"
        subject = "Payment Received" if paid else "Payment Failed"
        body = ("Your invoice payment has been received. You can review the invoice in Billing."
                if paid else "An invoice payment could not be completed. Please review your payment details in Billing.")
        result["notifications"] = [{"delivery_key": f"invoice:{identity}:{suffix}", "kind": kind,
                                    "tenant_id": tenant_id, "recipient": recipient,
                                    "subject": subject, "body": body,
                                    # A legacy invoice projection proves money
                                    # state, never that its email was unsent.
                                    "prior_delivery_unknown": bool(prior_invoice and not prior_invoice["notification_history_known"]
                                                                   and prior_invoice["status"] == invoice["status"])}]
    return result


async def _apply_checkout(conn, billing, event_type, data):
    identity = data.get("id")
    subscription_id = _id(data.get("subscription"))
    await _lock(conn, billing, "subscription" if subscription_id else "checkout", subscription_id or identity)
    session = await _current(billing, "checkout.Session", identity)
    current_subscription = _id(session.get("subscription"))
    if subscription_id and current_subscription != subscription_id:
        raise BillingStateReviewRequired("checkout_subscription_changed")
    if not subscription_id and current_subscription:
        subscription_id = current_subscription
        await _lock(conn, billing, "subscription", subscription_id)
        session = await _current(billing, "checkout.Session", identity)
        if _id(session.get("subscription")) != subscription_id:
            raise BillingStateReviewRequired("checkout_subscription_changed")
    if session.get("mode") != "subscription":
        return _result("ignored", reason="not_subscription_checkout")
    metadata = _object(session.get("metadata"))
    try:
        request_id = str(UUID(metadata.get("request_id")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BillingStateReviewRequired("legacy_checkout_unreconciled") from exc
    # Match CheckoutAttempts' lock order: tenant first, then attempt. Taking an
    # attempt lock here could deadlock a concurrent saved-customer retry.
    row = await conn.fetchrow("SELECT * FROM billing_checkout_attempts WHERE id=$1::uuid", request_id)
    if not row:
        raise BillingStateReviewRequired("purchase_binding_missing")
    row = dict(row)
    row["snapshot"] = _object(row["snapshot"])
    try:
        CheckoutAttempts.check_session(session, row)
    except CheckoutError as exc:
        raise BillingStateReviewRequired(exc.code) from exc
    tenant_id = str(row["tenant_id"])
    if session.get("status") == "expired":
        await conn.execute(
            """UPDATE billing_checkout_attempts SET status='expired',stripe_session_id=$3,updated_at=NOW()
               WHERE id=$1::uuid AND tenant_id=$2::uuid AND status IN ('creating','ready','unknown')""",
            request_id, tenant_id, identity,
        )
        return _result("handled", tenant_id)
    if not subscription_id:
        return _result("deferred", tenant_id, reason="subscription_unconfirmed")
    subscription = await _current(billing, "Subscription", subscription_id)
    binding = await _binding(conn, subscription)
    if binding["tenant_id"] != tenant_id:
        raise BillingStateReviewRequired("checkout_subscription_tenant_mismatch")
    if session.get("status") == "complete" and session.get("payment_status") == "paid":
        return await _apply_subscription(conn, billing, subscription, binding, session=session)
    # An async-failure notification alone is not a permanent provider terminal
    # receipt. Keep uncertainty until the current subscription is terminal.
    if canonical(subscription.get("status")) in {CANCELLED, INCOMPLETE_EXPIRED}:
        await conn.execute(
            """UPDATE billing_checkout_attempts SET status='failed',updated_at=NOW()
               WHERE id=$1::uuid AND tenant_id=$2::uuid AND status IN ('creating','ready','unknown')""",
            request_id, tenant_id,
        )
        return _result("handled", tenant_id)
    return _result("deferred", tenant_id, reason="payment_unconfirmed")


INVOICE_EVENTS = {"invoice.paid", "invoice.payment_failed", "invoice.finalized", "invoice.updated",
                  "invoice.voided", "invoice.marked_uncollectible"}
CREDIT_NOTE_EVENTS = {"credit_note.created", "credit_note.updated", "credit_note.voided"}
REFUND_EVENTS = {"charge.refunded", "refund.created", "refund.updated", "refund.failed", "charge.refund.updated"}


async def _invoice_for_adjustment(conn, billing, event_type, data):
    """Discover an invoice by provider relationships, never by customer search."""
    if event_type in CREDIT_NOTE_EVENTS:
        note = await _current(billing, "CreditNote", data.get("id"))
        identity = _id(note.get("invoice"))
        adjustment_customer = _id(note.get("customer"))
    else:
        if event_type == "charge.refunded":
            charge = await _current(billing, "Charge", data.get("id"))
        else:
            refund = await billing._stripe_call("Refund", "retrieve", data.get("id"))
            if refund.get("id") != data.get("id") or not _id(refund.get("charge")):
                raise BillingStateReviewRequired("refund_identity_mismatch")
            charge = await _current(billing, "Charge", _id(refund["charge"]))
            if (_id(refund.get("payment_intent")) != _id(charge.get("payment_intent"))
                    or refund.get("currency") != charge.get("currency")):
                raise BillingStateReviewRequired("refund_payment_mismatch")
        identity = _id(charge.get("invoice"))
        adjustment_customer = _id(charge.get("customer"))
        if not identity:
            from app.domain.services.billing_invoice_projection import _pages
            payment_id = _id(charge.get("payment_intent"))
            if not payment_id:
                return None
            payments = await _pages(
                billing, "InvoicePayment", payment={"type": "payment_intent", "payment_intent": payment_id},
                validate=lambda item: (item.get("livemode") is (billing.billing_mode == "live")
                                       and _id(_object(item.get("payment")).get("payment_intent")) == payment_id),
            )
            identities = {_id(item.get("invoice")) for item in payments}
            if not identities:
                return None
            if len(identities) != 1 or None in identities:
                raise BillingStateReviewRequired("refund_invoice_binding_ambiguous")
            identity = identities.pop()
    if not identity or not await conn.fetchval("SELECT id FROM invoices WHERE stripe_invoice_id=$1", identity):
        return None
    current_invoice = await _current(billing, "Invoice", identity)
    if not adjustment_customer or _id(current_invoice.get("customer")) != adjustment_customer:
        raise BillingStateReviewRequired("invoice_adjustment_customer_mismatch")
    return identity


async def refresh_invoice_details(conn, billing, event_type, data, source_reference):
    """Append current verified details only. Never grant access or send notices."""
    if event_type not in INVOICE_EVENTS | CREDIT_NOTE_EVENTS | REFUND_EVENTS:
        return _result("ignored", reason="unsupported_event")
    identity = data.get("id") if event_type in INVOICE_EVENTS else await _invoice_for_adjustment(conn, billing, event_type, data)
    if not identity:
        return _result("ignored", reason="invoice_not_recorded")
    return await _apply_invoice(conn, billing, event_type, {"id": identity},
                                source_reference=source_reference, details_only=True)


async def apply_billing_event(conn, billing, event_type, data, *, source_reference=None):
    """Apply one event under the caller's transaction and durable receipt lock."""
    if event_type in INVOICE_EVENTS:
        return await _apply_invoice(conn, billing, event_type, data, source_reference=source_reference)
    if event_type in CREDIT_NOTE_EVENTS | REFUND_EVENTS:
        return await refresh_invoice_details(conn, billing, event_type, data, source_reference or f"observation:{uuid4()}")
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded",
                      "checkout.session.expired", "checkout.session.async_payment_failed"}:
        return await _apply_checkout(conn, billing, event_type, data)
    if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        identity = data.get("id")
        await _lock(conn, billing, "subscription", identity)
        subscription = await _current(billing, "Subscription", identity)
        binding = await _binding(conn, subscription)
        return await _apply_subscription(conn, billing, subscription, binding)
    return _result("ignored", reason="unsupported_event")
