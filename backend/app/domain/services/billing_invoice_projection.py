"""Sanitized, exact provider invoice observations; never reconstructed charges.

An incomplete display capture does not erase a proved payment. Conversely its
partial lines must never become proof authorizing a first subscription payment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.domain.services.billing_catalog import _EXPONENTS

MAX_PAGES = 10
MAX_CAPTURE_SECONDS = 20
_DOCUMENT_HOSTS = {"invoice.stripe.com", "pay.stripe.com", "files.stripe.com"}


def _id(value):
    return value.get("id") if isinstance(value, dict) else value if isinstance(value, str) else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _text(value, maximum=2000):
    return value[:maximum] if isinstance(value, str) else None


def _amount(value):
    # Keep values exact across Python, JSON and the existing JS client.
    return value if type(value) is int and abs(value) <= 2**53 - 1 else None


def _currency(value):
    # Never turn a malformed value such as 'usd-other' into a supported code.
    return value if isinstance(value, str) and value in _EXPONENTS else None


def _date(value):
    if type(value) is not int:
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def provider_document_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname in _DOCUMENT_HOSTS
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
        ):
            return value
    except ValueError:
        pass
    return None


def snapshot_digest(projection):
    return hashlib.sha256(
        json.dumps(projection, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _taxes(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return [
        {
            "amount": _amount(item.get("amount")),
            "inclusive": (
                item["inclusive"]
                if type(item.get("inclusive")) is bool
                else {"inclusive": True, "exclusive": False}.get(item.get("tax_behavior"))
            ),
            "tax_rate_id": _id(item.get("tax_rate"))
            or _id(_object(item.get("tax_rate_details")).get("tax_rate")),
        }
        for item in value
    ]


def _discounts(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return [
        {"amount": _amount(item.get("amount")), "discount_id": _id(item.get("discount"))}
        for item in value
    ]


def _line(value):
    period = _object(value.get("period"))
    return {
        "id": value["id"],
        "description": _text(value.get("description")),
        "quantity": _amount(value.get("quantity")),
        "amount": _amount(value.get("amount")),
        "currency": _currency(value.get("currency")),
        "period_start": _date(period.get("start")),
        "period_end": _date(period.get("end")),
    }


async def _pages(billing, resource, *args, initial=None, validate=None, retained=None, **params):
    """Bounded explicit cursors; no hidden SDK iterator beyond our timeout."""
    rows = retained if retained is not None else []
    seen = set()
    page = initial
    for _ in range(MAX_PAGES):
        if page is None:
            page = await billing._stripe_call(resource, "list", *args, limit=100, **params)
        if (
            not isinstance(page, dict)
            or type(page.get("has_more")) is not bool
            or not isinstance(page.get("data"), list)
        ):
            raise ValueError("provider_page_invalid")
        for item in page["data"]:
            identity = _id(item)
            if (
                not isinstance(item, dict)
                or not identity
                or identity in seen
                or (validate and not validate(item))
            ):
                raise ValueError("provider_page_binding_invalid")
            seen.add(identity)
            rows.append(item)
        if not page["has_more"]:
            return rows
        if not page["data"]:
            raise ValueError("provider_page_cursor_missing")
        params["starting_after"] = _id(page["data"][-1])
        page = None
    raise ValueError("provider_page_limit")


def _credit(note):
    refs = note.get("refunds")
    # New Stripe shape has an array of {refund, amount_refunded}. A legacy
    # singular refund is a reference only; its amount/status is not inferred.
    if isinstance(refs, list):
        refunds = (
            [
                {"id": _id(item.get("refund")), "amount": _amount(item.get("amount_refunded"))}
                for item in refs
            ]
            if all(isinstance(item, dict) and _id(item.get("refund")) for item in refs)
            else None
        )
    elif "refund" in note:
        refunds = [{"id": _id(note["refund"]), "amount": None}] if _id(note["refund"]) else []
    else:
        refunds = None
    return {
        "id": note["id"],
        "amount": _amount(note.get("amount")),
        "currency": _currency(note.get("currency")),
        "status": _text(note.get("status"), 32),
        "type": _text(note.get("type"), 32),
        "pre_payment_amount": _amount(note.get("pre_payment_amount")),
        "post_payment_amount": _amount(note.get("post_payment_amount")),
        "pdf": provider_document_url(note.get("pdf")),
        "refunds": refunds,
    }


async def _invoice_refunds(billing, invoice):
    """A credit note is not a refund. Follow only invoice-bound payments."""
    expected_live = billing.billing_mode == "live"
    payment_ids = set()
    legacy = _id(invoice.get("payment_intent"))
    if legacy:
        payment_ids.add(legacy)
    else:
        payments = await _pages(
            billing,
            "InvoicePayment",
            invoice=invoice["id"],
            validate=lambda item: (
                _id(item.get("invoice")) == invoice["id"]
                and item.get("livemode") is expected_live
                and item.get("currency") == invoice.get("currency")
            ),
        )
        for item in payments:
            payment = _object(item.get("payment"))
            if payment.get("type") != "payment_intent" or not _id(payment.get("payment_intent")):
                raise ValueError("invoice_payment_type_unavailable")
            payment_ids.add(_id(payment["payment_intent"]))
    if len(payment_ids) > 10:
        raise ValueError("invoice_payment_limit")
    refunds = {}
    for identity in sorted(payment_ids):
        payment = await billing._stripe_call("PaymentIntent", "retrieve", identity)
        if (
            payment.get("id") != identity
            or payment.get("livemode") is not expected_live
            or _id(payment.get("customer")) != _id(invoice.get("customer"))
            or payment.get("currency") != invoice.get("currency")
        ):
            raise ValueError("invoice_payment_binding_invalid")
        rows = await _pages(
            billing,
            "Refund",
            payment_intent=identity,
            validate=lambda item, identity=identity: (
                _id(item.get("payment_intent")) == identity
                and item.get("currency") == invoice.get("currency")
            ),
        )
        if rows:
            # A PaymentIntent can pay several invoices. Never attribute its
            # whole refund to each invoice or to an unallocated payment balance.
            allocations = await _pages(
                billing,
                "InvoicePayment",
                payment={"type": "payment_intent", "payment_intent": identity},
                validate=lambda item, identity=identity: (
                    item.get("livemode") is expected_live
                    and _object(item.get("payment")).get("type") == "payment_intent"
                    and _id(_object(item.get("payment")).get("payment_intent")) == identity
                    and item.get("currency") == invoice.get("currency")
                    and item.get("status") == "paid"
                    and _amount(item.get("amount_paid")) is not None
                ),
            )
            if (
                {_id(item.get("invoice")) for item in allocations} != {invoice["id"]}
                or _amount(payment.get("amount_received")) is None
                or sum(item["amount_paid"] for item in allocations) != payment["amount_received"]
            ):
                raise ValueError("invoice_refund_allocation_unavailable")
        charges = {}
        for refund in rows:
            charge_id = _id(refund.get("charge"))
            if not charge_id:
                raise ValueError("invoice_refund_charge_missing")
            if charge_id not in charges:
                charges[charge_id] = await billing._stripe_call("Charge", "retrieve", charge_id)
            charge = charges[charge_id]
            if (
                charge.get("id") != charge_id
                or charge.get("livemode") is not expected_live
                or _id(charge.get("customer")) != _id(invoice.get("customer"))
                or _id(charge.get("payment_intent")) != identity
                or charge.get("currency") != invoice.get("currency")
            ):
                raise ValueError("invoice_refund_binding_invalid")
            refunds[refund["id"]] = {
                "id": refund["id"],
                "amount": _amount(refund.get("amount")),
                "currency": _currency(refund.get("currency")),
                "status": _text(refund.get("status"), 32),
                "charge_id": charge_id,
                "payment_intent_id": identity,
                "created_at": _date(refund.get("created")),
            }
    return list(refunds.values())


async def capture_invoice(billing, invoice, source_reference):
    """Return (public projection, invoice with explicitly complete/partial lines)."""
    hydrated = deepcopy(invoice)
    currency = _currency(invoice.get("currency"))
    result = {
        "version": 1,
        "source": "stripe",
        "source_reference": source_reference,
        "provider_mode": billing.billing_mode,
        "invoice_number": _text(invoice.get("number"), 255),
        "status": _text(invoice.get("status"), 32),
        "currency": currency,
        "currency_exponent": _EXPONENTS.get(currency),
        **{
            key: _amount(invoice.get(key))
            for key in ("subtotal", "total", "amount_due", "amount_paid", "amount_remaining")
        },
        "period_start": _date(invoice.get("period_start")),
        "period_end": _date(invoice.get("period_end")),
        "paid_at": _date(_object(invoice.get("status_transitions")).get("paid_at")),
        "due_date": _date(invoice.get("due_date")),
        "finalized_at": _date(_object(invoice.get("status_transitions")).get("finalized_at")),
        "taxes": _taxes(invoice.get("total_taxes", invoice.get("total_tax_amounts"))),
        "discounts": _discounts(invoice.get("total_discount_amounts")),
        "credits": None,
        "refunds": None,
        "line_items": None,
        "errors": [],
        "invoice_pdf": provider_document_url(invoice.get("invoice_pdf")),
        "hosted_invoice_url": provider_document_url(invoice.get("hosted_invoice_url")),
    }
    retained = []
    complete_lines = False
    try:
        async with asyncio.timeout(MAX_CAPTURE_SECONDS):
            try:
                if not isinstance(invoice.get("lines"), dict):
                    raise ValueError("invoice_lines_missing")
                await _pages(
                    billing,
                    "InvoiceLineItem",
                    invoice["id"],
                    initial=invoice["lines"],
                    retained=retained,
                    validate=lambda line: (
                        line.get("invoice") in {None, invoice["id"]}
                        and (
                            line.get("livemode") is None
                            or line.get("livemode") is (billing.billing_mode == "live")
                        )
                        and line.get("currency") == currency
                    ),
                )
                complete_lines = True
            except Exception:
                result["errors"].append("invoice_lines_unavailable")
            try:
                notes = await _pages(
                    billing,
                    "CreditNote",
                    invoice=invoice["id"],
                    validate=lambda note: (
                        _id(note.get("invoice")) == invoice["id"]
                        and _id(note.get("customer")) == _id(invoice.get("customer"))
                        and note.get("livemode") is (billing.billing_mode == "live")
                        and note.get("currency") == currency
                    ),
                )
                result["credits"] = [_credit(note) for note in notes]
            except Exception:
                result["errors"].append("invoice_credits_unavailable")
            try:
                result["refunds"] = await _invoice_refunds(billing, invoice)
            except Exception:
                result["errors"].append("invoice_refunds_unavailable")
    except TimeoutError:
        result["errors"].append("invoice_capture_timeout")
    # Cancellation from the outer financial transaction is never swallowed.
    hydrated["lines"] = {"data": retained, "has_more": not complete_lines}
    if retained or complete_lines:
        result["line_items"] = [_line(line) for line in retained]
    missing = (
        not complete_lines
        or result["currency_exponent"] is None
        or any(
            result[key] is None
            for key in (
                "subtotal",
                "total",
                "amount_due",
                "amount_paid",
                "amount_remaining",
                "taxes",
                "discounts",
                "credits",
                "refunds",
            )
        )
        or any(
            line["amount"] is None or line["currency"] is None
            for line in result["line_items"] or []
        )
        or any(
            item["amount"] is None for key in ("taxes", "discounts") for item in result[key] or []
        )
        or any(
            any(
                item[field] is None
                for field in (
                    "amount",
                    "currency",
                    "pre_payment_amount",
                    "post_payment_amount",
                    "refunds",
                )
            )
            or item["status"] not in {"issued", "void"}
            or item["type"] not in {"pre_payment", "post_payment", "mixed"}
            or any(ref["amount"] is None for ref in item["refunds"] or [])
            for item in result["credits"] or []
        )
        or any(
            item["amount"] is None
            or item["currency"] is None
            or item["status"]
            not in {"pending", "requires_action", "succeeded", "failed", "canceled"}
            for item in result["refunds"] or []
        )
    )
    result["detail_status"] = (
        "unavailable" if result["line_items"] is None else "partial" if missing else "complete"
    )
    return result, hydrated


async def store_snapshot(conn, invoice_id, tenant_id, projection):
    await conn.execute(
        """INSERT INTO invoice_snapshots(invoice_id,tenant_id,content_hash,projection)
           VALUES($1::uuid,$2::uuid,$3,$4::jsonb) ON CONFLICT(invoice_id,content_hash) DO NOTHING""",
        str(invoice_id),
        str(tenant_id),
        snapshot_digest(projection),
        json.dumps(projection, allow_nan=False),
    )
