"""PayPal top-ups: start a PayPal order, capture it, credit once, reverse.

Sits beside the Stripe path and reuses ``TopupService`` for the order row, the
once-only credit and the reversal. Nothing here changes those.

WHAT IS TRUSTED
---------------
Only our own order row. The browser sends our order id and nothing else; the
minutes credited are the stored ``topup_orders.minutes``, never a value from
PayPal or the client. PayPal's answer is used to prove that the money really
arrived for THIS order (ids, one purchase unit, one capture, amount, currency)
and anything that does not match is raised as ``review`` and not credited.

WHAT MAKES CREDIT ONCE-ONLY
---------------------------
``TopupService.credit_paid_order`` (row lock, "already credited" check and the
unique ``billing_ledger.provider_event_id``). The event id used here is
``paypal:capture:<capture id>``, so a browser return and a webhook for the same
capture collide in the database and credit once.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.paypal_client import (
    SUPPORTED_CURRENCIES,
    PayPalError,
    approval_url,
    get_paypal_client,
    minor_units,
    money_value,
)
from app.domain.services.paypal_mode import paypal_env, paypal_provider_mode
from app.domain.services.topup_service import TopupError, TopupService

logger = logging.getLogger(__name__)

PROVIDER = "paypal"
_BRAND = "Talk-Lee"
_DONE_STATES = frozenset({"paid", "refunded", "disputed"})

_ORDER_COLUMNS = (
    "id, tenant_id, minutes, price_cents, currency, status, provider, provider_session_id"
)


class PayPalTopupError(RuntimeError):
    """``code``: not_found | unsupported_currency | provider_failed | review."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class StartedCheckout:
    order_id: str
    paypal_order_id: str
    checkout_url: str
    minutes: int
    price_cents: int
    currency: str


@dataclass(frozen=True)
class CaptureOutcome:
    order_id: str
    status: Literal["paid", "pending", "not_approved", "failed"]
    minutes: int | None


def _short(value: Any) -> str:
    return str(value)[:8]


def _review(order_id: Any, paypal_order_id: Any, rule: str, **ids: Any) -> PayPalTopupError:
    extra = " ".join(f"{k}={str(v)[:64]}" for k, v in ids.items() if v)
    logger.error(
        "paypal_topup_review order=%s paypal_order=%s rule=%s %s",
        _short(order_id), str(paypal_order_id)[:64], rule, extra,
    )
    return PayPalTopupError("review", rule)


def _capture_of(paypal_order: dict, order: dict, paypal_order_id: str) -> tuple[dict, str]:
    """The one capture of a PayPal order that proves our order was paid.

    Raises ``review`` (and logs the failed rule) unless every rule holds.
    """
    oid = str(order["id"])
    if paypal_order.get("id") != order["provider_session_id"]:
        raise _review(oid, paypal_order_id, "order_id_mismatch", debug=paypal_order.get("id"))
    units = paypal_order.get("purchase_units")
    if not isinstance(units, list) or len(units) != 1 or not isinstance(units[0], dict):
        raise _review(oid, paypal_order_id, "purchase_units_not_one")
    unit = units[0]
    for field in ("custom_id", "reference_id"):
        value = unit.get(field)
        if value is not None and value != oid:
            raise _review(oid, paypal_order_id, f"unit_{field}_mismatch")
    payments = unit.get("payments")
    captures = payments.get("captures") if isinstance(payments, dict) else None
    if not isinstance(captures, list) or len(captures) != 1 or not isinstance(captures[0], dict):
        raise _review(oid, paypal_order_id, "captures_not_one")
    capture = captures[0]
    capture_id = capture.get("id")
    if not isinstance(capture_id, str) or not capture_id:
        raise _review(oid, paypal_order_id, "capture_id_missing")
    custom = capture.get("custom_id")
    if custom is not None and custom != oid:
        raise _review(oid, paypal_order_id, "capture_custom_id_mismatch", capture=capture_id)
    amount = capture.get("amount")
    currency = str(order["currency"]).upper()
    if not isinstance(amount, dict) or amount.get("currency_code") != currency:
        raise _review(oid, paypal_order_id, "currency_mismatch", capture=capture_id)
    try:
        paid_minor = minor_units(amount.get("value"), currency)
    except ValueError:
        raise _review(oid, paypal_order_id, "amount_unreadable", capture=capture_id) from None
    if paid_minor != int(order["price_cents"]):
        raise _review(oid, paypal_order_id, "amount_mismatch", capture=capture_id)
    return capture, capture_id


class PayPalTopupService:
    def __init__(self, pool, *, client=None, topups: TopupService | None = None) -> None:
        self._pool = pool
        self._client_override = client
        self._topups = topups or TopupService(pool)

    def _client(self):
        return self._client_override if self._client_override is not None else get_paypal_client()

    # -- start ---------------------------------------------------------------

    async def start_checkout(
        self, *, tenant_id: str, user_id: str | None, package_code: str,
        audience: str, billing_base: str,
    ) -> StartedCheckout:
        client = self._client()  # PayPalNotConfigured: refused before any order exists
        for pkg in await self._topups.packages(audience):
            if pkg.get("code") == package_code:
                if str(pkg.get("currency") or "").upper() not in SUPPORTED_CURRENCIES:
                    raise PayPalTopupError("unsupported_currency")
                break

        order = await self._topups.create_order(
            tenant_id=tenant_id, user_id=user_id,
            package_code=package_code, audience=audience,
        )
        order_id = str(order["id"])
        currency = str(order["currency"]).upper()

        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            tag = await conn.execute(
                "UPDATE topup_orders SET provider = 'paypal', updated_at = NOW() "
                " WHERE id = $1::uuid AND tenant_id = $2::uuid "
                "   AND status = 'pending' AND provider_session_id IS NULL",
                order_id, str(tenant_id),
            )
        if _rows(tag) != 1:
            # Nothing was sent to PayPal yet; do not leave a pending order behind.
            await self._fail_unattached(tenant_id, order_id)
            raise PayPalTopupError("provider_failed", "order could not be marked as PayPal's")

        try:
            answer = await client.create_order(
                request_id=f"topup-{order_id}",
                reference_id=order_id,
                custom_id=order_id,
                description=f"{_BRAND}: {order['name']}"[:127],
                currency=currency,
                value=money_value(int(order["price_cents"]), currency),
                return_url=f"{billing_base}?topup=success&provider=paypal&order_id={order_id}",
                cancel_url=f"{billing_base}?topup=cancelled&provider=paypal&order_id={order_id}",
                brand_name=_BRAND,
            )
            paypal_id = answer.get("id") if isinstance(answer, dict) else None
            url = approval_url(answer, env=paypal_env()) if isinstance(paypal_id, str) else None
            if not isinstance(paypal_id, str) or not paypal_id or not url:
                raise PayPalError(status=None, name="BAD_CREATE_ANSWER")
        except (PayPalError, ValueError) as exc:
            logger.error(
                "paypal_topup_create_failed order=%s status=%s name=%s issue=%s debug_id=%s",
                _short(order_id), getattr(exc, "status", None), getattr(exc, "name", None),
                getattr(exc, "issue", None), getattr(exc, "debug_id", None),
            )
            await self._fail_unattached(tenant_id, order_id)
            raise PayPalTopupError("provider_failed") from None

        try:
            await self._topups.attach_session(order_id, paypal_id, tenant_id=tenant_id)
        except TopupError as exc:
            # The customer was never sent to PayPal, so nothing can be paid on it.
            logger.error(
                "paypal_topup_session_orphaned order=%s paypal_order=%s: %s",
                _short(order_id), paypal_id, exc,
            )
            await self._fail_unattached(tenant_id, order_id)
            raise PayPalTopupError("provider_failed") from None
        logger.info("paypal_topup_started order=%s paypal_order=%s", _short(order_id), paypal_id)
        return StartedCheckout(
            order_id=order_id, paypal_order_id=paypal_id, checkout_url=url,
            minutes=int(order["minutes"]), price_cents=int(order["price_cents"]),
            currency=currency,
        )

    async def _fail_unattached(self, tenant_id: str, order_id: str) -> None:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            await conn.execute(
                "UPDATE topup_orders SET status = 'failed', updated_at = NOW() "
                " WHERE id = $1::uuid AND tenant_id = $2::uuid AND status = 'pending'",
                order_id, str(tenant_id),
            )

    # -- capture (browser return) ------------------------------------------------

    async def capture_for_tenant(self, *, tenant_id: str, order_id: str) -> CaptureOutcome:
        try:
            oid = str(UUID(str(order_id)))
        except ValueError:
            raise PayPalTopupError("not_found") from None
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                f"SELECT {_ORDER_COLUMNS} FROM topup_orders "
                " WHERE id = $1::uuid AND tenant_id = $2::uuid",
                oid, str(tenant_id),
            )
        if row is None or row["provider"] != PROVIDER:
            raise PayPalTopupError("not_found")
        order = dict(row)
        minutes = int(order["minutes"])
        paypal_order_id = order["provider_session_id"]
        if not paypal_order_id:
            return CaptureOutcome(oid, "not_approved", None)
        status = order["status"]
        if status in _DONE_STATES:
            return CaptureOutcome(oid, "paid", minutes)
        if status != "pending":  # failed, cancelled
            return CaptureOutcome(oid, "failed", None)

        client = self._client()
        try:
            answer = await client.capture_order(paypal_order_id, request_id=f"capture-{oid}")
        except PayPalError as exc:
            if exc.retryable:
                raise
            self._log_capture_error(oid, exc)
            current = await client.get_order(paypal_order_id)
            state = current.get("status") if isinstance(current, dict) else None
            if state == "COMPLETED":
                return await self._verify_or_record(order, current)
            if state == "VOIDED":
                await self._topups.mark_failed(session_id=paypal_order_id, status="failed")
                return CaptureOutcome(oid, "failed", None)
            return CaptureOutcome(oid, "not_approved", None)
        return await self._verify_or_record(order, answer)

    async def _verify_or_record(self, order: dict, paypal_order: dict) -> CaptureOutcome:
        """Browser-return path: a ``review`` also leaves one row for the alert."""
        try:
            return await self._verify_and_credit(order, paypal_order)
        except PayPalTopupError as exc:
            if exc.code == "review":
                await self._record_review(order, str(exc))
            raise

    async def _record_review(self, order: dict, rule: str) -> None:
        """Best effort: one platform-level row so a payment that needs a person is counted."""
        event_id = f"local:review:{order['id']}:{rule}"[:255]
        try:
            async with acquire_with_tenant(self._pool, None) as conn:
                await conn.execute(
                    "INSERT INTO paypal_webhook_events "
                    "(event_id, event_type, resource_type, resource_id, order_id, "
                    "provider_mode, state, detail, payload_hash) "
                    "VALUES ($1, 'LOCAL.CAPTURE.REVIEW', 'order', $2, $3::uuid, $4, "
                    "'needs_review', $5, $6) "
                    "ON CONFLICT (event_id) DO UPDATE SET "
                    "attempt_count = paypal_webhook_events.attempt_count + 1, "
                    "state = 'needs_review'",
                    event_id, str(order["provider_session_id"])[:255], str(order["id"]),
                    paypal_provider_mode() or "live", rule[:255],
                    hashlib.sha256(event_id.encode()).hexdigest(),
                )
        except Exception:
            logger.error("paypal_topup_review_record_failed order=%s", _short(order["id"]))

    @staticmethod
    def _log_capture_error(oid: str, exc: PayPalError) -> None:
        logger.warning(
            "paypal_topup_capture_refused order=%s status=%s name=%s issue=%s debug_id=%s",
            _short(oid), exc.status, exc.name, exc.issue, exc.debug_id,
        )

    # -- webhook ---------------------------------------------------------------

    async def capture_approved_order(self, *, paypal_order_id: str) -> CaptureOutcome | None:
        """CHECKOUT.ORDER.APPROVED: capture our pending order without the browser.

        The order is found by its PayPal id, then the SAME function the browser
        return uses runs for that order's own tenant (which re-reads the row
        with ``tenant_id = $2``), with the same request id and checks.
        """
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT id, tenant_id FROM topup_orders "
                " WHERE provider_session_id = $1 AND provider = 'paypal'",
                paypal_order_id,
            )
        if row is None:
            return None
        return await self.capture_for_tenant(
            tenant_id=str(row["tenant_id"]), order_id=str(row["id"])
        )

    async def settle_paypal_order(self, *, paypal_order_id: str) -> CaptureOutcome | None:
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                f"SELECT {_ORDER_COLUMNS} FROM topup_orders "
                " WHERE provider_session_id = $1 AND provider = 'paypal'",
                paypal_order_id,
            )
        if row is None:
            return None
        order = dict(row)
        oid = str(order["id"])
        if order["status"] in _DONE_STATES:
            return CaptureOutcome(oid, "paid", int(order["minutes"]))
        current = await self._client().get_order(paypal_order_id)
        if not isinstance(current, dict) or current.get("status") != "COMPLETED":
            return CaptureOutcome(oid, "not_approved", None)
        return await self._verify_and_credit(order, current)

    # -- shared ----------------------------------------------------------------

    async def _verify_and_credit(self, order: dict, paypal_order: dict) -> CaptureOutcome:
        oid = str(order["id"])
        paypal_order_id = order["provider_session_id"]
        capture, capture_id = _capture_of(paypal_order, order, paypal_order_id)
        state = capture.get("status")
        if state == "COMPLETED":
            # The minutes come from the stored order row inside credit_paid_order.
            await self._topups.credit_paid_order(
                session_id=paypal_order_id,
                payment_id=capture_id,
                event_id=f"paypal:capture:{capture_id}",
            )
            return CaptureOutcome(oid, "paid", int(order["minutes"]))
        if state == "PENDING":
            return CaptureOutcome(oid, "pending", None)
        if state in ("DECLINED", "FAILED"):
            await self._topups.mark_failed(session_id=paypal_order_id, status="failed")
            return CaptureOutcome(oid, "failed", None)
        raise _review(oid, paypal_order_id, f"capture_status_{state}", capture=capture_id)

    # -- refund / dispute ------------------------------------------------------

    async def reverse_capture(
        self, *, capture_id: str, kind: Literal["refund", "dispute"], event_id: str,
    ) -> Literal["reversed", "already", "unknown", "review"]:
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT id, tenant_id, status, provider_session_id FROM topup_orders "
                " WHERE provider_payment_id = $1 AND provider = 'paypal'",
                capture_id,
            )
        if row is None:
            return "unknown"
        oid = str(row["id"])
        if kind == "refund":
            capture = await self._client().get_capture(capture_id)
            custom = capture.get("custom_id") if isinstance(capture, dict) else None
            if custom is not None and custom != oid:
                _review(oid, row["provider_session_id"], "refund_custom_id_mismatch",
                        capture=capture_id)
                return "review"
            state = capture.get("status") if isinstance(capture, dict) else None
            if state != "REFUNDED":
                # PARTIALLY_REFUNDED or anything else: a person decides.
                _review(oid, row["provider_session_id"], f"refund_status_{state}",
                        capture=capture_id)
                return "review"
        moved = await self._topups.reverse(event_id=event_id, kind=kind, payment_id=capture_id)
        if moved:
            return "reversed"
        # False: already reversed, or the order was never paid. Only the first is done.
        async with acquire_with_tenant(self._pool, None) as conn:
            current = await conn.fetchrow(
                "SELECT status FROM topup_orders WHERE id = $1::uuid AND provider = 'paypal'",
                oid,
            )
        status = current["status"] if current is not None else None
        if status in ("refunded", "disputed"):
            return "already"
        _review(oid, row["provider_session_id"], f"reverse_on_{status}", capture=capture_id)
        return "review"


def _rows(tag: Any) -> int:
    try:
        return int(str(tag).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return -1
