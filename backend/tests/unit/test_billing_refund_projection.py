"""Provider status is independent of ledger reversal and browser return flags."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from app.domain.services.billing_refund_projection import normalize_refunds, public_refund_details
from app.domain.services.billing_service import BillingService


def refund_data():
    charge = {"id": "ch_a", "payment_intent": "pi_a", "currency": "kwd", "amount_refunded": 1234}
    refund = {
        "id": "re_a",
        "charge": "ch_a",
        "payment_intent": "pi_a",
        "currency": "kwd",
        "amount": 1234,
        "status": "pending",
        "created": 1700000000,
    }
    return charge, {"data": [refund], "has_more": False}


def test_refund_status_and_exact_minor_currency_are_retained_without_bank_claim():
    charge, page = refund_data()
    result = normalize_refunds(charge, page)
    assert result["detail_status"] == "complete"
    assert result["refunds"][0]["amount"] == 1234
    assert result["refunds"][0]["currency_exponent"] == 3
    assert result["refunds"][0]["status"] == "pending"
    assert "bank_received" not in result


@pytest.mark.parametrize("state", ["pending", "requires_action", "succeeded", "failed", "canceled"])
def test_all_recorded_provider_states_are_preserved(state):
    charge, page = refund_data()
    page["data"][0]["status"] = state
    assert normalize_refunds(charge, page)["refunds"][0]["status"] == state


@pytest.mark.parametrize(
    "mutation",
    [
        "truncated",
        "wrong_charge",
        "wrong_payment",
        "wrong_currency",
        "duplicate",
        "invalid_amount",
        "missing_status",
        "missing_date",
    ],
)
def test_incomplete_or_conflicting_refund_evidence_never_looks_complete(mutation):
    charge, page = refund_data()
    row = page["data"][0]
    if mutation == "truncated":
        page["has_more"] = True
    elif mutation == "wrong_charge":
        row["charge"] = "ch_other"
    elif mutation == "wrong_payment":
        row["payment_intent"] = "pi_other"
    elif mutation == "wrong_currency":
        row["currency"] = "usd"
    elif mutation == "duplicate":
        page["data"].append(deepcopy(row))
    elif mutation == "invalid_amount":
        row["amount"] = True
    elif mutation == "missing_date":
        row.pop("created")
    else:
        row.pop("status")
    assert normalize_refunds(charge, page)["detail_status"] == "partial"


def test_unrecorded_is_null_not_zero_or_empty():
    charge, _ = refund_data()
    assert normalize_refunds(charge, None) == {"detail_status": "unavailable", "refunds": None}
    assert public_refund_details({}) == {
        "detail_status": "unavailable",
        "refunds": None,
        "captured_at": None,
        "source_event_id": None,
    }
    assert normalize_refunds(charge, {"data": [], "has_more": False})["detail_status"] == "partial"


def test_refund_list_cannot_be_complete_when_known_amounts_are_insufficient():
    charge, page = refund_data()
    charge["amount_refunded"] = 2000
    assert normalize_refunds(charge, page)["detail_status"] == "partial"
    # A list can also contain failed/pending attempts; its total need not equal
    # the charge's recorded refunded amount, and it never defines minute policy.
    charge["amount_refunded"] = 500
    page["data"][0]["status"] = "failed"
    assert normalize_refunds(charge, page)["detail_status"] == "complete"


@pytest.mark.parametrize("invalid", [True, False, -1, "1234", 1.5, 2**53])
def test_invalid_charge_refunded_amount_cannot_support_complete_detail(invalid):
    charge, page = refund_data()
    charge["amount_refunded"] = invalid
    assert normalize_refunds(charge, page)["detail_status"] == "partial"
    assert normalize_refunds(charge, None) == {"detail_status": "unavailable", "refunds": None}


def test_confirmed_zero_refunds_can_be_empty_complete():
    charge, _ = refund_data()
    charge["amount_refunded"] = 0
    assert normalize_refunds(charge, {"data": [], "has_more": False}) == {
        "detail_status": "complete",
        "refunds": [],
    }


@pytest.mark.asyncio
async def test_checkout_return_preserves_existing_query_and_identifies_order():
    billing = BillingService.__new__(BillingService)
    billing.billing_mode = "mock"
    billing.mock_mode = True
    billing._require_billing_enabled = lambda: None
    billing.create_or_get_customer = AsyncMock(return_value={"customer_id": "cus_a"})
    result = await billing.create_topup_checkout_session(
        tenant_id="tenant",
        email="test@example.invalid",
        order_id="order-a",
        minutes=250,
        price_cents=2500,
        currency="gbp",
        product_name="Test",
        success_url="https://app.example.invalid/billing?topup=success#history",
        cancel_url="https://app.example.invalid/billing?topup=cancelled",
    )
    parsed = urlsplit(result["checkout_url"])
    assert parsed.fragment == "history"
    assert parse_qs(parsed.query) == {
        "topup": ["success"],
        "order_id": ["order-a"],
        "session_id": ["cs_mock_topup_order-a"],
        "mock": ["true"],
    }


@pytest.mark.asyncio
async def test_reconciliation_never_adds_different_currencies_or_claims_unbounded_totals(
    monkeypatch,
):
    from app.api.v1.endpoints import billing_topups

    rows = []
    for amount, currency in ((100, "USD"), (-30, "USD"), (100, "JPY"), (50, "GBP")):
        rows.append(
            {
                "created_at": None,
                "tenant_id": "tenant",
                "business_name": "Synthetic",
                "kind": "topup" if amount > 0 else "refund",
                "minutes_delta": amount,
                "amount_cents": amount,
                "currency": currency,
                "package_code": "test",
                "provider_event_id": "evt_test",
                "provider_payment_id": "pi_test",
                "order_status": "paid",
                "note": "Synthetic",
            }
        )
    service = SimpleNamespace(reconciliation=AsyncMock(return_value=rows))
    monkeypatch.setattr(billing_topups, "_service", lambda: service)
    result = await billing_topups.reconciliation(limit=3, _admin=None)
    assert result["truncated"] is True and len(result["entries"]) == 3
    assert result["totals"]["scope"] == "returned_rows"
    assert "net_cents" not in result["totals"]
    assert result["totals"]["by_currency"] == [
        {
            "currency": "usd",
            "currency_exponent": 2,
            "gross_cents": 100,
            "reversed_cents": 30,
            "net_cents": 70,
        },
        {
            "currency": "jpy",
            "currency_exponent": 0,
            "gross_cents": 100,
            "reversed_cents": 0,
            "net_cents": 100,
        },
    ]
