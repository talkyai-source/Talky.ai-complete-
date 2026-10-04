"""Real provider shapes, exact amounts and incomplete detail boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.billing_invoice_projection import capture_invoice, snapshot_digest


def provider_invoice():
    return {
        "id": "in_synthetic",
        "object": "invoice",
        "livemode": False,
        "customer": "cus_synthetic",
        "currency": "usd",
        "number": "TEST-1",
        "status": "paid",
        "subtotal": 10000,
        "total": 10800,
        "amount_due": 8800,
        "amount_paid": 8800,
        "amount_remaining": 0,
        "period_start": 1700000000,
        "period_end": 1702592000,
        "status_transitions": {"finalized_at": 1700000000},
        "total_taxes": [
            {
                "amount": 1800,
                "tax_behavior": "exclusive",
                "tax_rate_details": {"tax_rate": "txr_synthetic"},
            }
        ],
        "total_discount_amounts": [{"amount": 1000, "discount": "di_synthetic"}],
        "pre_payment_credit_notes_amount": 0,
        "post_payment_credit_notes_amount": 0,
        "lines": {
            "has_more": False,
            "data": [
                {
                    "id": "il_synthetic",
                    "invoice": "in_synthetic",
                    "livemode": False,
                    "description": "Original annual plan",
                    "amount": 10000,
                    "quantity": 1,
                    "currency": "usd",
                    "period": {"start": 1700000000, "end": 1731622400},
                }
            ],
        },
    }


def billing(read=None):
    return SimpleNamespace(
        billing_mode="test",
        _stripe_call=read or AsyncMock(return_value={"data": [], "has_more": False}),
    )


@pytest.mark.asyncio
async def test_totals_and_line_period_are_provider_facts_not_amount_due_or_current_plan():
    value = provider_invoice()
    projection, hydrated = await capture_invoice(billing(), value, "evt_synthetic")
    assert projection["detail_status"] == "complete"
    assert projection["total"] == 10800 and projection["amount_due"] == 8800
    assert projection["taxes"][0]["amount"] == 1800
    assert projection["discounts"][0]["amount"] == 1000
    assert projection["line_items"][0]["description"] == "Original annual plan"
    assert projection["line_items"][0]["period_end"] != projection["period_end"]
    assert projection["credits"] == [] and hydrated["lines"]["has_more"] is False
    assert "usedMinutes" not in projection and "planName" not in projection


@pytest.mark.asyncio
async def test_unrecorded_components_are_null_not_zero_or_empty():
    value = provider_invoice()
    for key in ("subtotal", "total_taxes", "total_discount_amounts", "lines"):
        value.pop(key)
    projection, hydrated = await capture_invoice(billing(), value, "evt_synthetic")
    assert projection["detail_status"] == "unavailable"
    assert projection["subtotal"] is None and projection["taxes"] is None
    assert projection["discounts"] is None and projection["line_items"] is None
    assert hydrated["lines"]["has_more"] is True


@pytest.mark.asyncio
async def test_pagination_preserves_full_line_proof_and_detects_partial_failure():
    value = provider_invoice()
    value["lines"]["has_more"] = True
    second = {**value["lines"]["data"][0], "id": "il_second"}
    read = AsyncMock(
        side_effect=[{"data": [second], "has_more": False}, {"data": [], "has_more": False}]
    )
    projection, hydrated = await capture_invoice(billing(read), value, "evt_pages")
    assert len(projection["line_items"]) == 2 and hydrated["lines"]["has_more"] is False
    assert read.await_args_list[0].args == ("InvoiceLineItem", "list", "in_synthetic")
    assert read.await_args_list[0].kwargs == {"limit": 100, "starting_after": "il_synthetic"}
    read = AsyncMock(side_effect=TimeoutError())
    partial, incomplete = await capture_invoice(billing(read), value, "evt_failure")
    assert partial["detail_status"] == "partial"
    assert len(partial["line_items"]) == 1 and incomplete["lines"]["has_more"] is True
    assert "invoice_lines_unavailable" in partial["errors"]


@pytest.mark.asyncio
async def test_repeated_cursor_or_wrong_invoice_cannot_make_complete_lines():
    for line in (
        provider_invoice()["lines"]["data"][0],
        {**provider_invoice()["lines"]["data"][0], "id": "il_other", "invoice": "in_other"},
    ):
        value = provider_invoice()
        value["lines"]["has_more"] = True
        read = AsyncMock(return_value={"data": [line], "has_more": True})
        projection, hydrated = await capture_invoice(billing(read), value, "evt_bad")
        assert projection["detail_status"] == "partial" and hydrated["lines"]["has_more"] is True
        assert sum(call.args[0] == "InvoiceLineItem" for call in read.await_args_list) == 1
        assert read.await_count <= 4


@pytest.mark.asyncio
async def test_credit_note_refund_reference_is_not_cash_settlement():
    value = provider_invoice()
    note = {
        "id": "cn_synthetic",
        "invoice": value["id"],
        "customer": value["customer"],
        "livemode": False,
        "currency": "usd",
        "amount": 2000,
        "status": "issued",
        "type": "post_payment",
        "post_payment_amount": 2000,
        "pre_payment_amount": 0,
        "refunds": [{"refund": "re_synthetic", "amount_refunded": 500}],
        "pdf": "https://pay.stripe.com/credit_notes/synthetic/pdf",
    }
    read = AsyncMock(
        side_effect=[{"data": [note], "has_more": False}, {"data": [], "has_more": False}]
    )
    result, _ = await capture_invoice(billing(read), value, "evt_credit")
    assert result["credits"][0]["post_payment_amount"] == 2000
    assert result["credits"][0]["refunds"] == [{"id": "re_synthetic", "amount": 500}]
    assert "status" not in result["credits"][0]["refunds"][0]
    assert result["refunds"] == []


@pytest.mark.asyncio
async def test_refund_status_requires_exact_invoice_payment_charge_customer_chain():
    value = provider_invoice()
    value["payment_intent"] = "pi_synthetic"

    async def read(resource, method, *args, **kwargs):
        if resource == "CreditNote":
            return {"data": [], "has_more": False}
        if resource == "PaymentIntent":
            return {
                "id": "pi_synthetic",
                "customer": value["customer"],
                "livemode": False,
                "currency": "usd",
                "amount_received": 8800,
            }
        if resource == "InvoicePayment":
            return {
                "data": [
                    {
                        "id": "inpay_synthetic",
                        "invoice": value["id"],
                        "livemode": False,
                        "currency": "usd",
                        "status": "paid",
                        "amount_paid": 8800,
                        "payment": {"type": "payment_intent", "payment_intent": "pi_synthetic"},
                    }
                ],
                "has_more": False,
            }
        if resource == "Refund":
            assert kwargs["payment_intent"] == "pi_synthetic"
            return {
                "data": [
                    {
                        "id": "re_synthetic",
                        "payment_intent": "pi_synthetic",
                        "charge": "ch_synthetic",
                        "amount": 500,
                        "currency": "usd",
                        "status": "pending",
                        "created": 1700000000,
                    }
                ],
                "has_more": False,
            }
        return {
            "id": "ch_synthetic",
            "payment_intent": "pi_synthetic",
            "customer": value["customer"],
            "currency": "usd",
            "livemode": False,
        }

    result, _ = await capture_invoice(billing(read), value, "evt_refund")
    assert result["refunds"][0]["status"] == "pending" and result["refunds"][0]["amount"] == 500

    async def wrong(resource, method, *args, **kwargs):
        result = await read(resource, method, *args, **kwargs)
        if resource == "Charge":
            result["customer"] = "cus_other_tenant"
        return result

    result, _ = await capture_invoice(billing(wrong), value, "evt_wrong")
    assert result["refunds"] is None and result["detail_status"] == "partial"

    async def shared_payment(resource, method, *args, **kwargs):
        result = await read(resource, method, *args, **kwargs)
        if resource == "InvoicePayment":
            result["data"].append({**result["data"][0], "id": "inpay_other", "invoice": "in_other"})
        return result

    result, _ = await capture_invoice(billing(shared_payment), value, "evt_shared_payment")
    assert result["refunds"] is None and result["detail_status"] == "partial"

    for field in ("amount", "status"):

        async def incomplete(resource, method, *args, field=field, **kwargs):
            result = await read(resource, method, *args, **kwargs)
            if resource == "Refund":
                result["data"][0][field] = None
            return result

        result, _ = await capture_invoice(billing(incomplete), value, "evt_incomplete_refund")
        assert result["detail_status"] == "partial"


@pytest.mark.asyncio
async def test_outer_cancellation_is_not_swallowed_as_a_partial_display():
    import asyncio

    value = provider_invoice()
    value["lines"]["has_more"] = True
    with pytest.raises(asyncio.CancelledError):
        await capture_invoice(
            billing(AsyncMock(side_effect=asyncio.CancelledError())), value, "evt_cancelled"
        )


@pytest.mark.asyncio
async def test_installed_sdk_invoice_line_and_adjustment_read_paths(monkeypatch):
    import json
    from urllib.parse import parse_qs, urlparse

    import stripe

    from app.domain.services.billing_service import BillingService

    seen = []

    class Transport(stripe.HTTPClient):
        name = "synthetic-offline"

        def __init__(self, timeout):
            super().__init__()
            assert timeout == 5

        def request(self, method, url, headers, post_data=None, **kwargs):
            seen.append((method, url))
            return (
                json.dumps(
                    {"object": "list", "data": [], "has_more": False, "url": urlparse(url).path}
                ),
                200,
                {"request-id": "req_synthetic"},
            )

        def close(self):
            pass

    monkeypatch.setattr(stripe, "RequestsClient", Transport)
    svc = BillingService.__new__(BillingService)
    svc.billing_mode, svc._stripe_api_key = "test", "sk_test_synthetic"
    await svc._stripe_call(
        "InvoiceLineItem", "list", "in_synthetic", limit=100, starting_after="il_last"
    )
    await svc._stripe_call("CreditNote", "list", invoice="in_synthetic", limit=100)
    await svc._stripe_call(
        "InvoicePayment",
        "list",
        payment={"type": "payment_intent", "payment_intent": "pi_synthetic"},
        limit=100,
    )
    await svc._stripe_call("Refund", "list", payment_intent="pi_synthetic", limit=100)
    assert all(method.lower() == "get" for method, _ in seen)
    assert [urlparse(url).path for _, url in seen] == [
        "/v1/invoices/in_synthetic/lines",
        "/v1/credit_notes",
        "/v1/invoice_payments",
        "/v1/refunds",
    ]
    assert parse_qs(urlparse(seen[0][1]).query) == {"limit": ["100"], "starting_after": ["il_last"]}
    assert parse_qs(urlparse(seen[2][1]).query)["payment[payment_intent]"] == ["pi_synthetic"]


@pytest.mark.parametrize(
    "url",
    [
        "http://invoice.stripe.com/test",
        "https://invoice.stripe.com.evil.example/test",
        "javascript:alert(1)",
        "https://user@pay.stripe.com/test",
        "https://pay.stripe.com:444/test",
    ],
)
def test_provider_pdf_links_reject_untrusted_or_unsafe_targets(url):
    from app.domain.services.billing_invoice_projection import provider_document_url

    assert provider_document_url(url) is None


@pytest.mark.parametrize("currency,exponent", [("jpy", 0), ("kwd", 3), ("usd", 2), ("zzz", None)])
@pytest.mark.asyncio
async def test_currency_conventions_and_boolean_amounts_are_not_coerced(currency, exponent):
    value = provider_invoice()
    value["currency"] = currency
    value["total"] = True
    value["lines"]["data"][0]["currency"] = currency
    projection, _ = await capture_invoice(billing(), value, "evt_units")
    assert projection["currency_exponent"] == exponent and projection["total"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_due", None),
        ("amount_paid", True),
        ("amount_remaining", None),
        ("total_taxes", [{"amount": "200"}]),
        ("total_discount_amounts", [{"amount": False}]),
        ("currency", "usd-invalid"),
    ],
)
@pytest.mark.asyncio
async def test_missing_or_malformed_components_are_never_labelled_complete(field, value):
    invoice = provider_invoice()
    invoice[field] = value
    result, _ = await capture_invoice(billing(), invoice, "evt_missing")
    assert result["detail_status"] != "complete"
    if field == "currency":
        assert result["currency"] is None and result["currency_exponent"] is None


def test_snapshot_dedup_includes_observation_identity_for_a_b_a():
    one = {"source_reference": "evt_1", "amount_remaining": 10}
    assert snapshot_digest(one) == snapshot_digest(deepcopy(one))
    assert snapshot_digest(one) != snapshot_digest({**one, "source_reference": "evt_3"})
