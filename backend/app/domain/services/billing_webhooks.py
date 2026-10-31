"""Durable Stripe receipts. A claim is never evidence that billing completed.

Receipt insertion commits first; business writes and completion share a second
transaction. Transaction-scoped locks expire with the connection, so a crashed
worker cannot resume with an expired lease and overwrite another worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)


class BillingWebhookRetryable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BillingWebhookReviewRequired(BillingWebhookRetryable):
    pass


async def lock_event(conn, event_id: str) -> bool:
    key = int.from_bytes(
        hashlib.sha256(f"billing:event:{event_id}".encode()).digest()[:8], "big", signed=True
    )
    return bool(await conn.fetchval("SELECT pg_try_advisory_xact_lock($1::bigint)", key))


def event_identity(event: dict) -> tuple[str, str, str, str]:
    event_id, event_type = event.get("id"), event.get("type")
    if (
        not isinstance(event_id, str)
        or not event_id.startswith("evt_")
        or len(event_id) > 255
        or not isinstance(event_type, str)
        or not event_type
        or len(event_type) > 255
        or not isinstance(event.get("livemode"), bool)
        or not isinstance(event.get("data"), dict)
        or not isinstance(event["data"].get("object"), dict)
    ):
        raise ValueError("Invalid Stripe event envelope")
    # pending_webhooks changes when Stripe retrieves an otherwise identical
    # event. Hash immutable event content, not transport/delivery counters.
    stable = {
        k: event.get(k)
        for k in ("id", "type", "livemode", "account", "api_version", "created", "data")
    }
    digest = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return event_id, event_type, "live" if event["livemode"] else "test", digest


class BillingWebhookProcessor:
    def __init__(self, pool):
        self.pool = pool

    async def process(self, event: dict, handler) -> dict:
        event_id, event_type, mode, digest = event_identity(event)
        try:
            async with asyncio.timeout(55):
                async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
                    await conn.execute("SET LOCAL statement_timeout = '10000ms'")
                    await conn.execute(
                        """INSERT INTO processed_webhook_events
                           (event_id,event_type,state,event_payload,payload_hash,provider_mode,legacy_claim)
                           VALUES($1,$2,'pending',$3::jsonb,$4,$5,FALSE)
                           ON CONFLICT(event_id) DO NOTHING""",
                        event_id,
                        event_type,
                        json.dumps(event),
                        digest,
                        mode,
                    )
                async with acquire_with_tenant(self.pool, None, timeout=5) as conn:
                    await conn.execute("SET LOCAL statement_timeout = '10000ms'")
                    await conn.execute("SET LOCAL lock_timeout = '3000ms'")
                    if not await lock_event(conn, event_id):
                        raise BillingWebhookRetryable("event_busy")
                    receipt = await conn.fetchrow(
                        "SELECT * FROM processed_webhook_events WHERE event_id=$1 FOR UPDATE",
                        event_id,
                    )
                    if receipt is None:
                        raise BillingWebhookRetryable("receipt_missing")
                    if receipt["state"] in {"legacy_unverified", "needs_review"}:
                        raise BillingWebhookReviewRequired(
                            receipt["last_error_code"] or receipt["state"]
                        )
                    if (
                        receipt["payload_hash"] != digest
                        or receipt["provider_mode"] != mode
                        or receipt["event_type"] != event_type
                    ):
                        raise BillingWebhookReviewRequired("event_identity_conflict")
                    if receipt["state"] == "completed":
                        return {
                            "status": "duplicate",
                            "event_id": event_id,
                            "event_type": event_type,
                        }
                    result = await handler(conn, event)
                    if result.get("status") == "needs_reconciliation":
                        raise BillingWebhookReviewRequired(
                            result.get("reason") or "business_reconciliation_required"
                        )
                    if result.get("status") == "deferred":
                        raise BillingWebhookRetryable(
                            result.get("reason") or "business_state_pending"
                        )
                    if result.get("status") not in {"handled", "ignored"}:
                        raise BillingWebhookRetryable("invalid_handler_result")
                    from app.domain.services.billing_webhook_notifications import (
                        store_notifications,
                    )

                    await store_notifications(
                        conn, event_id, event_type, result.get("notifications") or [],
                        legacy_claim=receipt["legacy_claim"],
                    )
                    await conn.execute(
                        """UPDATE processed_webhook_events SET state='completed',completed_at=now(),
                           attempt_count=attempt_count+1,last_error_code=NULL,tenant_id=$2::uuid
                           WHERE event_id=$1""",
                        event_id,
                        result.get("tenant_id"),
                    )
                return {"status": result["status"], "event_id": event_id, "event_type": event_type}
        except BillingWebhookRetryable as exc:
            if exc.code != "event_busy":
                await self._record_failure(
                    event_id, exc.code, isinstance(exc, BillingWebhookReviewRequired)
                )
            raise
        except Exception as exc:
            # Only fixed codes/class names, never provider payloads, addresses,
            # card details, SQL parameters or SDK exception messages in logs.
            logger.warning(
                "Billing event failed event_id=%s error_type=%s", event_id, type(exc).__name__
            )
            await self._record_failure(event_id, "processing_failed", False)
            raise BillingWebhookRetryable("processing_failed") from exc

    async def _record_failure(self, event_id: str, code: str, needs_review: bool) -> None:
        try:
            async with asyncio.timeout(8):
                async with acquire_with_tenant(self.pool, None, timeout=3) as conn:
                    await conn.execute("SET LOCAL statement_timeout = '3000ms'")
                    if not await lock_event(conn, event_id):
                        return
                    await conn.execute(
                        """UPDATE processed_webhook_events SET
                           state=CASE WHEN state='legacy_unverified' THEN state ELSE $2 END,
                           attempt_count=attempt_count+1,last_error_code=$3
                           WHERE event_id=$1 AND state<>'completed'
                           AND (state<>'needs_review' OR $2='needs_review')""",
                        event_id,
                        "needs_review" if needs_review else "failed",
                        code,
                    )
        except Exception as exc:
            logger.error(
                "Billing receipt failure could not be recorded event_id=%s error_type=%s",
                event_id,
                type(exc).__name__,
            )
