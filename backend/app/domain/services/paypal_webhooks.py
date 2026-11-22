"""PayPal webhook handler: verify with PayPal, record a receipt, dispatch.

The event only tells us WHERE to look. Every money decision is made by
``PayPalTopupService`` (prepaid minutes) or ``PayPalSubscriptionService``
(plans), which re-read PayPal. Nothing is written to the
database before PayPal has confirmed the signature. The receipt row keeps ids
and a SHA-256 of the body, never the body (it carries the payer's name, email
and address). Nothing here logs a header, a signature or a body.

Answers: ``completed`` | ``ignored`` | ``needs_review`` | ``duplicate``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.paypal_client import PayPalError, PayPalNotConfigured, get_paypal_client
from app.domain.services.paypal_mode import paypal_provider_mode, paypal_webhook_id
from app.domain.services.paypal_topups import PayPalTopupError, PayPalTopupService

logger = logging.getLogger(__name__)

_CAPTURE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SETTLE_EVENTS = frozenset(
    {"PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DECLINED", "PAYMENT.CAPTURE.DENIED"}
)
_PLAN_EVENTS = frozenset(
    {
        "BILLING.SUBSCRIPTION.ACTIVATED",
        "BILLING.SUBSCRIPTION.UPDATED",
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "BILLING.SUBSCRIPTION.EXPIRED",
        "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
    }
)
_FINISHED = frozenset({"completed", "ignored", "needs_review", "dismissed"})
_MAX_VERIFICATIONS_IN_FLIGHT = 4
_verifications_in_flight = 0
_RANK = {"ignored": 0, "completed": 1, "needs_review": 2}


class PayPalWebhookRejected(Exception):
    """Not ours or not genuine: answer 400."""


class PayPalWebhookRetry(Exception):
    """Cannot decide now: answer 503 so PayPal sends it again."""


def _clip(value: Any, limit: int = 255) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


_LOG_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")


def _log(value: Any) -> str:
    """Text for one log line. The route is public, so an unverified caller
    chooses these values: no line breaks, no spaces, at most 64 characters."""
    return _LOG_UNSAFE.sub("?", value)[:64] if isinstance(value, str) and value else "-"


def _parse(raw_body: bytes) -> tuple[dict, str, str, dict]:
    try:
        event = json.loads(raw_body)
    except (ValueError, TypeError):
        raise PayPalWebhookRejected("body is not JSON") from None
    if not isinstance(event, dict):
        raise PayPalWebhookRejected("body is not an object")
    event_id = event.get("id")
    event_type = event.get("event_type")
    resource = event.get("resource")
    if (
        not isinstance(event_id, str) or not event_id or len(event_id) > 255
        or not isinstance(event_type, str)
        or not isinstance(resource, dict)
    ):
        raise PayPalWebhookRejected("invalid event envelope")
    return event, event_id, event_type, resource


def _up_link_capture_id(resource: dict) -> str | None:
    links = resource.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") == "up" and isinstance(link.get("href"), str):
            segment = link["href"].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            return segment if _CAPTURE_ID.match(segment) else None
    return None


def _from_reverse(answer: str) -> str:
    if answer in ("reversed", "already"):
        return "completed"
    if answer == "review":
        return "needs_review"
    return "ignored"


def _worst(results: list[str]) -> str:
    return max(results, key=_RANK.__getitem__) if results else "ignored"


def _seller_transactions(resource: dict) -> list[str]:
    items = resource.get("disputed_transactions")
    if not isinstance(items, list):
        return []
    return [
        t["seller_transaction_id"]
        for t in items
        if isinstance(t, dict) and isinstance(t.get("seller_transaction_id"), str)
        and t["seller_transaction_id"]
    ]


async def _update(event_id: str, state: str, detail: str | None, order_id: str | None, pool) -> None:
    async with acquire_with_tenant(pool, None) as conn:
        await conn.execute(
            "UPDATE paypal_webhook_events SET state = $2, detail = $3, "
            "order_id = COALESCE($4::uuid, order_id), completed_at = NOW() WHERE event_id = $1",
            event_id, state, detail, order_id,
        )


async def _plan_sync(
    plans, subscription_id: Any, *, wait_for_payment: bool
) -> tuple[str, str | None, str | None]:
    if not isinstance(subscription_id, str) or not _CAPTURE_ID.match(subscription_id):
        return "ignored", "no_subscription_id", None
    outcome = await plans.sync(subscription_id)
    if outcome is None:
        return "ignored", "not_our_subscription", None
    if outcome.review:
        return "needs_review", "plan_review", outcome.subscription_id
    if wait_for_payment and not outcome.activated and outcome.status == "approval_pending":
        # PayPal does not show the payment yet: keep the receipt open.
        raise PayPalWebhookRetry("payment not visible at PayPal yet")
    return "completed", f"plan_{outcome.status}"[:64], outcome.subscription_id


_ESCALATED_STAGES = frozenset({"CHARGEBACK", "PRE_ARBITRATION", "ARBITRATION"})


def _escalated(resource: dict) -> bool:
    """The money is taken back only once the dispute is past the inquiry stage.
    Field and values: PayPal's public webhook documentation (not observed here)."""
    if resource.get("status") == "RESOLVED":
        return False  # already decided (fields: PayPal's documentation, not observed here)
    outcome = resource.get("dispute_outcome")
    if isinstance(outcome, dict) and outcome.get("outcome_code") == "RESOLVED_SELLER_FAVOUR":
        return False  # the seller won: the money is ours
    return resource.get("dispute_life_cycle_stage") in _ESCALATED_STAGES


async def _reverse_dispute(
    service, plans, event_id: str, resource: dict
) -> tuple[str, str | None, str | None]:
    dispute = _clip(resource.get("dispute_id"), 64) or event_id
    results: list[str] = []
    for capture_id in _seller_transactions(resource):
        if not _CAPTURE_ID.match(capture_id):
            continue
        answer = await service.reverse_capture(
            capture_id=capture_id, kind="dispute",
            event_id=f"paypal:dispute:{dispute}:{capture_id}",
        )
        if answer != "unknown":
            results.append(_from_reverse(answer))
            continue
        plan_answer = await plans.end_for_reversal(
            transaction_id=capture_id, kind="dispute",
            event_id=f"paypal:plan-dispute:{dispute}:{capture_id}",
        )
        results.append("ignored" if plan_answer == "not_ours" else "completed")
    return _worst(results), "dispute_created" if results else "no_transactions", None


async def _dispatch(
    pool, service, plans, event_id: str, event_type: str, event: dict, resource: dict
) -> tuple[str, str | None, str | None]:
    """(state, detail, our order id). Raises whatever the service raises."""
    if event_type in _PLAN_EVENTS:
        return await _plan_sync(plans, resource.get("id"), wait_for_payment=False)

    if event_type == "PAYMENT.SALE.COMPLETED":
        return await _plan_sync(plans, resource.get("billing_agreement_id"), wait_for_payment=True)

    if event_type == "PAYMENT.SALE.REFUNDED":
        sale_id = resource.get("sale_id")
        if not isinstance(sale_id, str) or not _CAPTURE_ID.match(sale_id):
            return "ignored", "no_sale_id", None
        async with acquire_with_tenant(pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT s.paypal_subscription_id FROM paypal_subscription_payments p "
                "JOIN paypal_subscriptions s ON s.id = p.subscription_id AND s.tenant_id = p.tenant_id "
                "WHERE p.paypal_transaction_id = $1 LIMIT 1",
                sale_id,
            )
        if row is None:
            return "ignored", "not_our_payment", None
        state, detail, our_id = await _plan_sync(
            plans, row["paypal_subscription_id"], wait_for_payment=False
        )
        if state == "completed":
            return "completed", "plan_refund_recorded", our_id
        return state, detail, our_id

    if event_type == "PAYMENT.SALE.REVERSED":
        sale_id = resource.get("id")
        if not isinstance(sale_id, str) or not _CAPTURE_ID.match(sale_id):
            return "ignored", "no_sale_id", None
        answer = await plans.end_for_reversal(
            transaction_id=sale_id, kind="reversal", event_id=f"paypal:plan-reversal:{event_id}"
        )
        if answer == "not_ours":
            agreement = resource.get("billing_agreement_id")
            if isinstance(agreement, str) and _CAPTURE_ID.match(agreement):
                answer = await plans.end_subscription_for_reversal(
                    paypal_subscription_id=agreement, kind="reversal",
                    event_id=f"paypal:plan-reversal:{event_id}",
                )
        if answer == "not_ours":
            return "ignored", "not_our_payment", None
        return "completed", f"plan_reversal_{answer}", None

    if event_type in _SETTLE_EVENTS:
        supplementary = resource.get("supplementary_data")
        ids = supplementary.get("related_ids") if isinstance(supplementary, dict) else None
        paypal_order_id = ids.get("order_id") if isinstance(ids, dict) else None
        if not isinstance(paypal_order_id, str) or not paypal_order_id:
            return "ignored", "no_order_id", None
        outcome = await service.settle_paypal_order(paypal_order_id=paypal_order_id)
        if outcome is None:
            return "ignored", "not_our_order", None
        if event_type == "PAYMENT.CAPTURE.COMPLETED" and outcome.status == "not_approved":
            # PayPal's order does not show as completed yet: keep the receipt open.
            raise PayPalWebhookRetry("order not completed at PayPal yet")
        return "completed", f"capture_{outcome.status}", str(outcome.order_id)

    if event_type == "PAYMENT.CAPTURE.REFUNDED":
        capture_id = _up_link_capture_id(resource)
        if capture_id is None:
            return "ignored", "no_capture_id", None
        ref = _clip(resource.get("id"), 64) or event_id
        answer = await service.reverse_capture(
            capture_id=capture_id, kind="refund", event_id=f"paypal:refund:{ref}"
        )
        return _from_reverse(answer), f"refund_{answer}", None

    if event_type == "PAYMENT.CAPTURE.REVERSED":
        if event.get("resource_type") == "capture" and isinstance(resource.get("id"), str):
            capture_id = resource["id"] if _CAPTURE_ID.match(resource["id"]) else None
        else:
            capture_id = _up_link_capture_id(resource)
        if capture_id is None:
            return "ignored", "no_capture_id", None
        answer = await service.reverse_capture(
            capture_id=capture_id, kind="dispute", event_id=f"paypal:reversal:{event_id}"
        )
        return _from_reverse(answer), f"reversal_{answer}", None

    if event_type == "CHECKOUT.ORDER.APPROVED":
        paypal_order_id = resource.get("id")
        if not isinstance(paypal_order_id, str) or not _CAPTURE_ID.match(paypal_order_id):
            return "ignored", "no_order_id", None
        outcome = await service.capture_approved_order(paypal_order_id=paypal_order_id)
        if outcome is None:
            return "ignored", "not_our_order", None
        if outcome.status == "not_approved":
            # PayPal refused the capture (declined card, expired order) and will
            # answer the same on every redelivery: final. The browser return and
            # PAYMENT.CAPTURE.COMPLETED still credit a later successful capture.
            # A transient PayPal error is raised by the service and still retries.
            return "completed", "approved_not_captured", str(outcome.order_id)
        return "completed", f"approved_{outcome.status}", str(outcome.order_id)

    if event_type == "CUSTOMER.DISPUTE.CREATED" and _escalated(resource):
        return await _reverse_dispute(service, plans, event_id, resource)

    if event_type == "CUSTOMER.DISPUTE.UPDATED" and _escalated(resource):
        state, detail, our_id = await _reverse_dispute(service, plans, event_id, resource)
        return state, "dispute_escalated" if detail == "dispute_created" else detail, our_id

    if event_type in (
        "CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"
    ):
        # An inquiry (money still ours) or a later update: minutes never change
        # automatically here; a person decides.
        for capture_id in _seller_transactions(resource):
            async with acquire_with_tenant(pool, None) as conn:
                row = await conn.fetchrow(
                    "SELECT id FROM topup_orders "
                    "WHERE provider_payment_id = $1 AND provider = 'paypal' LIMIT 1",
                    capture_id,
                )
            if row is not None:
                detail = "dispute_inquiry" if event_type == "CUSTOMER.DISPUTE.CREATED" else "dispute_update"
                return "needs_review", detail, str(row["id"])
            if await plans.owns_transaction(capture_id):
                # A plan payment: the plan is ended only past the inquiry stage.
                detail = "dispute_inquiry" if event_type == "CUSTOMER.DISPUTE.CREATED" else "dispute_update"
                return "needs_review", detail, None
        return "ignored", "not_our_order", None

    return "ignored", "event_not_handled", None


async def handle_paypal_webhook(
    pool,
    *,
    headers: Mapping[str, str],
    raw_body: bytes,
    client=None,
    service=None,
    plans=None,
) -> dict[str, str]:
    event, event_id, event_type, resource = _parse(raw_body)

    webhook_id = paypal_webhook_id()
    mode = paypal_provider_mode()
    if not webhook_id or mode is None:
        raise PayPalWebhookRetry("PayPal webhook is not set up")
    if client is None:
        try:
            client = get_paypal_client()
        except PayPalNotConfigured:
            raise PayPalWebhookRetry("PayPal is not configured") from None

    global _verifications_in_flight
    if _verifications_in_flight >= _MAX_VERIFICATIONS_IN_FLIGHT:
        raise PayPalWebhookRetry("too many verifications in flight")
    _verifications_in_flight += 1
    try:
        genuine = await client.verify_webhook_signature(
            webhook_id=webhook_id, headers=headers, raw_body=raw_body
        )
    except PayPalError:
        logger.warning("paypal_webhook_verify_unreachable event=%s type=%s", _log(event_id), _log(event_type))
        raise PayPalWebhookRetry("PayPal could not verify the event") from None
    finally:
        _verifications_in_flight -= 1
    if not genuine:
        logger.warning("paypal_webhook_rejected event=%s type=%s", _log(event_id), _log(event_type))
        raise PayPalWebhookRejected("signature not verified")

    resource_id = _clip(resource.get("id"))
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    resource_type = _clip(event.get("resource_type"))

    async with acquire_with_tenant(pool, None) as conn:
        inserted = await conn.fetchval(
            "INSERT INTO paypal_webhook_events "
            "(event_id, event_type, resource_type, resource_id, provider_mode, state, payload_hash) "
            "VALUES ($1, $2, $3, $4, $5, 'received', $6) "
            "ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
            event_id, event_type[:255], resource_type, resource_id, mode, payload_hash,
        )
        existing = None
        if inserted is None:
            existing = await conn.fetchrow(
                "SELECT state, payload_hash FROM paypal_webhook_events WHERE event_id = $1",
                event_id,
            )
    if inserted is None:
        if existing is None:
            raise PayPalWebhookRetry("receipt could not be read")
        if existing["payload_hash"] != payload_hash:
            await _update(event_id, "needs_review", "payload_changed", None, pool)
            logger.error("paypal_webhook_payload_changed event=%s type=%s", _log(event_id), _log(event_type))
            return {"status": "needs_review"}
        if existing["state"] in _FINISHED:
            return {"status": "duplicate"}
        async with acquire_with_tenant(pool, None) as conn:
            await conn.execute(
                "UPDATE paypal_webhook_events SET attempt_count = attempt_count + 1 "
                "WHERE event_id = $1",
                event_id,
            )

    if service is None:
        service = PayPalTopupService(pool)
    if plans is None:
        from app.domain.services.paypal_subscriptions import PayPalSubscriptionService

        plans = PayPalSubscriptionService(pool)

    try:
        state, detail, order_id = await _dispatch(pool, service, plans, event_id, event_type, event, resource)
    except PayPalWebhookRetry:
        raise  # the receipt stays 'received' so PayPal's next delivery is processed
    except CheckoutError as exc:
        await _fail(pool, event_id, f"plan_{exc.code}"[:64])
        raise
    except PayPalTopupError as exc:
        if exc.code != "review":
            await _fail(pool, event_id, f"topup_{exc.code}"[:64])
            raise
        state, detail, order_id = "needs_review", "service_review", None
    except PayPalError as exc:
        await _fail(pool, event_id, f"paypal_error_{exc.status}"[:64])
        logger.warning(
            "paypal_webhook_retry event=%s type=%s resource=%s",
            _log(event_id), _log(event_type), _log(resource_id),
        )
        raise PayPalWebhookRetry("PayPal could not be reached") from None
    except Exception as exc:
        await _fail(pool, event_id, f"error_{type(exc).__name__}"[:64])
        raise

    await _update(event_id, state, detail, order_id, pool)
    logger.info(
        "paypal_webhook event=%s type=%s resource=%s order=%s state=%s",
        _log(event_id), _log(event_type), _log(resource_id), (order_id or "-")[:8], state,
    )
    return {"status": state}


async def _fail(pool, event_id: str, detail: str) -> None:
    """Best effort: the original error is what the caller needs to see."""
    try:
        async with acquire_with_tenant(pool, None) as conn:
            await conn.execute(
                "UPDATE paypal_webhook_events SET state = 'failed', detail = $2 WHERE event_id = $1",
                event_id, detail,
            )
    except Exception:
        logger.error("paypal_webhook_fail_mark_failed event=%s", _log(event_id))
