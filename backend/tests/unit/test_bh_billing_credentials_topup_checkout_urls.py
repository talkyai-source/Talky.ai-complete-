"""bughunt billing-money-credentials-9 (URL part) - the top-up checkout must send
the customer back to the configured, allow-listed frontend origin; neither the
request body nor the Origin header can choose where a paid customer lands.

The Stripe customer race and the blocking SDK calls live in billing_service.py
and are tested in test_bh2_billing_customer_race.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import billing_topups as bt
from app.domain.services import billing_checkout

ORDER = {
    "id": uuid4(), "minutes": 100, "price_cents": 1000, "currency": "usd",
    "name": "100 minutes", "package_code": "m100",
}


class _Svc:
    def __init__(self):
        self.created = 0

    async def create_order(self, **_kw):
        self.created += 1
        return dict(ORDER)

    async def attach_session(self, *_a):
        return None


class _Billing:
    last: dict = {}
    # The endpoint now reads the service's mode before it creates an order
    # (top-ups are sold only in mock mode unless BILLING_TOPUPS_LIVE_ENABLED);
    # this stub models the mock-mode service these tests have always exercised.
    mock_mode = True

    def __init__(self, *_a, **_k):
        pass

    async def create_topup_checkout_session(self, **kw):
        type(self).last = kw
        return {"session_id": "cs_x", "checkout_url": "https://stripe.example/x", "mock_mode": True}


def _request(origin=None):
    headers = {"origin": origin} if origin else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host="1.2.3.4"))


def _settings(frontend="https://app.example", allowed=("https://app.example",)):
    return lambda: SimpleNamespace(frontend_url=frontend, allowed_origins=list(allowed))


@pytest.fixture
def svc(monkeypatch):
    s = _Svc()
    monkeypatch.setattr(bt, "_service", lambda: s)
    monkeypatch.setattr(bt, "BillingService", _Billing)
    monkeypatch.setattr(billing_checkout, "get_settings", _settings())
    # Top-ups add to a paid plan (2026-10-27); this account has one.
    monkeypatch.setattr(bt, "_has_paid_plan", AsyncMock(return_value=True))
    _Billing.last = {}
    return s


async def _checkout(body, request):
    return await bt.create_topup_checkout(
        body=body,
        request=request,
        current_user=SimpleNamespace(tenant_id=str(uuid4()), id=str(uuid4()), email="a@b.c"),
        db_client=object(),
        audit_logger=SimpleNamespace(log=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_body_urls_and_origin_header_cannot_redirect_the_customer(svc):
    body = bt.CheckoutIn(
        package_code="m100",
        success_url="https://evil.example/steal",
        cancel_url="https://evil.example/cancel",
    )
    await _checkout(body, _request(origin="https://evil.example"))
    sent = _Billing.last
    assert urlsplit(sent["success_url"]).netloc == "app.example"
    assert urlsplit(sent["cancel_url"]).netloc == "app.example"
    assert parse_qs(urlsplit(sent["success_url"]).query) == {"topup": ["success"]}
    assert parse_qs(urlsplit(sent["cancel_url"]).query) == {"topup": ["cancelled"]}


@pytest.mark.asyncio
async def test_missing_origin_header_does_not_fall_back_to_localhost(svc):
    await _checkout(bt.CheckoutIn(package_code="m100"), _request(origin=None))
    assert "localhost" not in _Billing.last["success_url"]
    assert _Billing.last["success_url"].startswith("https://app.example/billing")


@pytest.mark.asyncio
async def test_unconfigured_return_destination_is_503_and_no_order_is_created(svc, monkeypatch):
    monkeypatch.setattr(
        billing_checkout, "get_settings", _settings(frontend="https://app.example", allowed=())
    )
    with pytest.raises(HTTPException) as ei:
        await _checkout(bt.CheckoutIn(package_code="m100"), _request())
    assert ei.value.status_code == 503
    assert svc.created == 0  # no orphan order for a checkout that cannot start
