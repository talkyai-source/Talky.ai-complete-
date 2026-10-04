"""Bounded operator recovery; no blanket deletion, replay or success backfill."""

from __future__ import annotations

import json
import asyncio
from uuid import UUID, uuid4

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_webhooks import (
    BillingWebhookProcessor,
    BillingWebhookRetryable,
    event_identity,
    lock_event,
)

RECOVERABLE_TYPES = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.expired",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.finalized", "invoice.updated", "invoice.voided", "invoice.marked_uncollectible",
        "credit_note.created", "credit_note.updated", "credit_note.voided",
        "refund.created", "refund.updated", "refund.failed", "charge.refund.updated",
        "charge.refunded",
        "charge.dispute.created",
    }
)


class BillingReconciliation:
    def __init__(self, pool, billing):
        self.pool, self.billing = pool, billing

    async def list_unresolved(self, limit=25):
        async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
            rows = await conn.fetch(
                """SELECT event_id,event_type,state,provider_mode,attempt_count,last_error_code,received_at
                   FROM processed_webhook_events WHERE state<>'completed'
                   ORDER BY received_at,event_id LIMIT $1""",
                max(1, min(limit, 100)),
            )
            deliveries = await conn.fetch(
                """SELECT id,event_id,kind,status,attempt_count,last_error_code,created_at
                   FROM billing_webhook_notifications WHERE status NOT IN ('accepted','superseded')
                   ORDER BY created_at,id LIMIT $1""",
                max(1, min(limit, 100)),
            )
        return {
            "events": [dict(row) for row in rows],
            "notifications": [dict(row) for row in deliveries],
        }

    async def _read(self, event_id):
        async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM processed_webhook_events WHERE event_id=$1", event_id
            )
        if row is None:
            raise ValueError("Saved event does not exist")
        return dict(row)

    async def inspect(self, event_id):
        """Read current provider evidence and ledger history without changing state."""
        receipt = await self._read(event_id)
        event = await self.billing._stripe_call("Event", "retrieve", event_id)
        event = json.loads(json.dumps(event))
        identity, event_type, mode, digest = event_identity(event)
        if (
            identity != event_id
            or receipt["event_type"] != event_type
            or mode != self.billing.billing_mode
            or event.get("account")
        ):
            raise ValueError("Provider event identity, account or mode does not match")
        if receipt["payload_hash"] is not None and receipt["payload_hash"] != digest:
            raise ValueError("Provider event differs from the stored verified event")
        data = event["data"]["object"]
        resource = (
            "checkout.Session"
            if event_type.startswith("checkout.session.")
            else (
                "Subscription"
                if event_type.startswith("customer.subscription.")
                else (
                    "Invoice"
                    if event_type.startswith("invoice.")
                    else (
                        "Dispute"
                        if event_type == "charge.dispute.created"
                        else "Charge" if event_type == "charge.refunded"
                        else "Refund" if event_type.startswith("refund.") or event_type == "charge.refund.updated"
                        else "CreditNote" if event_type.startswith("credit_note.") else None
                    )
                )
            )
        )
        current = None
        if resource and data.get("id"):
            current = await self.billing._stripe_call(resource, "retrieve", data["id"])
            if current.get("id") != data["id"] or (
                resource != "Refund" and current.get("livemode") is not (mode == "live")
            ):
                raise ValueError("Current provider object identity does not match")
        async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
            ledger = await conn.fetch(
                """SELECT l.id,l.order_id,l.tenant_id,l.kind,l.minutes_delta,l.amount_cents,l.currency,
                          l.provider_event_id FROM billing_ledger l WHERE l.provider_event_id=$1
                     OR l.order_id IN (SELECT id FROM topup_orders WHERE provider_session_id=$2
                       OR provider_payment_id=$3) ORDER BY l.created_at,l.id""",
                event_id,
                data.get("id") if resource == "checkout.Session" else None,
                _id(data.get("payment_intent")),
            )
            notifications = await conn.fetch(
                "SELECT id,kind,status,attempt_count,last_error_code FROM billing_webhook_notifications WHERE event_id=$1",
                event_id,
            )
            from app.domain.services.billing_state_events import invoice_subscription_id

            subscription_id = (
                data.get("id")
                if resource == "Subscription"
                else (
                    invoice_subscription_id(current or data)
                    if resource == "Invoice"
                    else _id((current or data).get("subscription"))
                )
            )
            subscriptions = (
                await conn.fetch(
                    """SELECT s.tenant_id,s.stripe_subscription_id,s.status,t.subscription_status AS tenant_status,
                          t.stripe_subscription_id AS tenant_current_subscription,t.minutes_allocated
                   FROM subscriptions s JOIN tenants t ON t.id=s.tenant_id
                   WHERE s.stripe_subscription_id=$1""",
                    subscription_id,
                )
                if subscription_id
                else []
            )
            invoices = (
                await conn.fetch(
                    """SELECT stripe_invoice_id,tenant_id,stripe_subscription_id,status,amount_due,amount_paid,currency
                   FROM invoices WHERE stripe_invoice_id=$1""",
                    data.get("id"),
                )
                if resource == "Invoice"
                else []
            )
            request_id = ((current or {}).get("metadata") or {}).get("request_id")
            try:
                request_id = str(UUID(request_id)) if request_id else None
            except (ValueError, TypeError, AttributeError):
                request_id = None
            attempts = (
                await conn.fetch(
                    """SELECT id,tenant_id,status,price_option_id,stripe_customer_id,stripe_session_id
                   FROM billing_checkout_attempts WHERE id=$1::uuid AND stripe_customer_id=$2""",
                    request_id,
                    _id((current or {}).get("customer")),
                )
                if request_id
                else []
            )
        # Return a restricted report: no raw signed customer payload or email.
        report = {
            "event_id": event_id,
            "event_type": event_type,
            "state": receipt["state"],
            "provider_mode": mode,
            "last_error_code": receipt["last_error_code"],
            "current_provider": (
                {
                    k: current.get(k)
                    for k in (
                        "id",
                        "status",
                        "payment_status",
                        "amount",
                        "amount_refunded",
                        "refunded",
                        "disputed",
                    )
                }
                if current is not None
                else None
            ),
            "ledger": [dict(row) for row in ledger],
            "notifications": [dict(row) for row in notifications],
            "subscriptions": [dict(row) for row in subscriptions],
            "invoices": [dict(row) for row in invoices],
            "checkout_attempts": [dict(row) for row in attempts],
        }
        return report, event

    async def authorize_retry(self, event_id, *, operator, reason):
        if not operator.strip() or len(operator) > 200 or not 12 <= len(reason.strip()) <= 2000:
            raise ValueError(
                "An identified operator and substantive reconciliation reason are required"
            )
        report, event = await self.inspect(event_id)
        _, event_type, mode, digest = event_identity(event)
        if event_type not in RECOVERABLE_TYPES:
            raise ValueError("This event type has no reviewed idempotent recovery handler")
        async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
            if not await lock_event(conn, event_id):
                raise BillingWebhookRetryable("event_busy")
            receipt = await conn.fetchrow(
                "SELECT * FROM processed_webhook_events WHERE event_id=$1 FOR UPDATE", event_id
            )
            if receipt["state"] not in {"legacy_unverified", "needs_review"}:
                raise ValueError(
                    "Only unresolved legacy or review receipts need replay authorization"
                )
            if receipt["payload_hash"] is not None and receipt["payload_hash"] != digest:
                raise ValueError("Saved event changed during review")
            await conn.execute(
                """INSERT INTO billing_webhook_review_log(event_id,operator,decision,reason)
                   VALUES($1,$2,'authorize_idempotent_retry',$3)""",
                event_id,
                operator.strip(),
                json.dumps(
                    {"reason": reason.strip(), "observed": report}, default=str, sort_keys=True
                ),
            )
            await conn.execute(
                """UPDATE processed_webhook_events SET state='pending',event_payload=$2::jsonb,
                   payload_hash=$3,provider_mode=$4,last_error_code=NULL WHERE event_id=$1""",
                event_id,
                json.dumps(event),
                digest,
                mode,
            )
        return {
            "event_id": event_id,
            "state": "pending",
            "financial_effects_applied": False,
            "reviewed": report,
        }

    async def refresh_details(self, event_id, *, operator, reason):
        """Append current provider observations; never replay financial handlers."""
        from app.domain.services.billing_refund_projection import handle_topup_refund_observation
        from app.domain.services.billing_state_events import (
            INVOICE_EVENTS, CREDIT_NOTE_EVENTS, REFUND_EVENTS, refresh_invoice_details,
        )

        if not operator.strip() or len(operator) > 200 or not 12 <= len(reason.strip()) <= 2000:
            raise ValueError("An identified operator and substantive reconciliation reason are required")
        async with asyncio.timeout(50):
            report, event = await self.inspect(event_id)
            event_type = event["type"]
            if event_type not in INVOICE_EVENTS | CREDIT_NOTE_EVENTS | REFUND_EVENTS:
                raise ValueError("This event does not identify invoice or refund detail")
            reference = "observation:" + str(uuid4())
            async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
                await conn.execute("SET LOCAL statement_timeout='10000ms'")
                if not await lock_event(conn, event_id):
                    raise BillingWebhookRetryable("event_busy")
                result = await handle_topup_refund_observation(
                    conn, self.billing, event_type, event["data"]["object"], reference
                )
                if result.get("status") == "ignored":
                    result = await refresh_invoice_details(
                        conn, self.billing, event_type, event["data"]["object"], reference
                    )
                await conn.execute(
                    """INSERT INTO billing_webhook_review_log(event_id,operator,decision,reason)
                    VALUES($1,$2,'refresh_provider_details',$3)""",
                    event_id, operator.strip(), json.dumps({
                        "reason": reason.strip(), "observation": reference, "result": result,
                        "prior_receipt_state": report["state"],
                    }, default=str, sort_keys=True),
                )
        return {
            "event_id": event_id, "observation": reference, "result": result,
            "financial_effects_applied": False, "notifications_sent": False,
        }

    async def retry(self, event_id):
        receipt = await self._read(event_id)
        if receipt["state"] not in {"pending", "failed", "completed"}:
            raise ValueError("This event requires recorded reconciliation before retry")
        payload = receipt["event_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict) or receipt["event_type"] not in RECOVERABLE_TYPES:
            raise ValueError("No verified recoverable payload is available")
        # Calls only current-state reads and transaction-scoped idempotent DB
        # handlers. It never invokes email delivery or creates a Stripe charge.
        return await BillingWebhookProcessor(self.pool).process(
            payload, self.billing._apply_webhook_event
        )

    async def retry_unsent_notification(self, notification_id, *, operator, reason):
        """Only a proved pre-send failure is eligible. Unknown means stop."""
        if not operator.strip() or len(operator) > 200 or not 12 <= len(reason.strip()) <= 2000:
            raise ValueError(
                "An identified operator and substantive reconciliation reason are required"
            )
        async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
            row = await conn.fetchrow(
                "SELECT event_id,status FROM billing_webhook_notifications WHERE id=$1::uuid FOR UPDATE",
                notification_id,
            )
            if row is None or row["status"] != "failed_before_send":
                raise ValueError(
                    "Only a known pre-send failure can be retried; unknown or sending outcomes require provider investigation"
                )
            await conn.execute(
                """INSERT INTO billing_webhook_review_log(event_id,operator,decision,reason)
                   VALUES($1,$2,'retry_unsent_notification',$3)""",
                row["event_id"],
                operator.strip(),
                f"Notification {notification_id}: {reason.strip()}",
            )
            await conn.execute(
                """UPDATE billing_webhook_notifications SET status='pending',updated_at=now(),
                   last_error_code=NULL WHERE id=$1::uuid""",
                notification_id,
            )
        return {"notification_id": str(notification_id), "status": "pending", "sent": False}


def _id(value):
    return value.get("id") if isinstance(value, dict) else value
