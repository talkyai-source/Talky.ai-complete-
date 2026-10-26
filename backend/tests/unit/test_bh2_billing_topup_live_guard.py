"""bughunt billing-money-credentials-4 (containment) - top-ups cannot be SOLD
with a real Stripe key until the owner decides how purchased minutes expire.

Purchased top-up minutes are added to ``tenants.minutes_allocated`` permanently
(they recur every month and never expire). The design fix needs owner
decisions; until then, when BillingService is not in mock mode the checkout
endpoint refuses with 503 BEFORE it creates an order or a Stripe session,
unless BILLING_TOPUPS_LIVE_ENABLED is true. Webhook crediting of sessions that
were already paid is deliberately not gated (never drop paid money).
"""
from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_client
from app.api.v1.endpoints import billing_topups as bt
from app.core.error_handlers import register_error_handlers
from app.domain.services import billing_checkout
from app.domain.services import billing_service as bs
from app.domain.services.billing_service import BillingService
from tests.unit.test_bh2_billing_customer_race import TENANT, FakeStripe, FakeTenantsDb

DETAIL = "Minute top-ups are not available yet"
ORDER = {
    "id": uuid4(), "minutes": 100, "price_cents": 1000, "currency": "usd",
    "name": "100 minutes", "package_code": "m100",
}


class _Topups:
    """Stands in for TopupService: counts the order rows that would be written."""

    def __init__(self):
        self.orders = 0
        self.created = []
        self.attached = []

    async def create_order(self, **_kw):
        self.orders += 1
        order = dict(ORDER, id=uuid4())
        self.created.append(order)
        return order

    async def attach_session(self, order_id, session_id):
        self.attached.append((order_id, session_id))


class _NoStripe(ModuleType):
    """The SDK must never be reached by a refused checkout."""

    def __getattr__(self, name):
        raise AssertionError(f"the Stripe SDK was touched: stripe.{name}")


class _ModeBilling(BillingService):
    """The REAL __init__, so ``mock_mode`` comes from the real mode resolution
    (the guard's input must vary with configuration, not be wired to a constant);
    only the network call is replaced and recorded."""

    sessions: list = []
    instances: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)

    async def create_topup_checkout_session(self, **kw):
        type(self).sessions.append(kw)
        return {"session_id": "cs_x", "checkout_url": "https://pay.example/x", "mock_mode": self.mock_mode}


@pytest.fixture
def world(monkeypatch):
    topups = _Topups()
    _ModeBilling.sessions, _ModeBilling.instances = [], []
    monkeypatch.setattr(bt, "_service", lambda: topups)
    monkeypatch.setattr(bt, "BillingService", _ModeBilling)
    # Top-ups add to a paid plan (2026-10-27); these accounts have one.
    monkeypatch.setattr(bt, "_has_paid_plan", AsyncMock(return_value=True))
    monkeypatch.setattr(
        billing_checkout, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://app.example", allowed_origins=["https://app.example"]),
    )
    monkeypatch.setattr(bs, "stripe", _NoStripe("stripe"))
    for name in ("STRIPE_SECRET_KEY", "STRIPE_MOCK_MODE", "STRIPE_BILLING_DISABLED", "BILLING_TOPUPS_LIVE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    return topups


def set_mode(monkeypatch, mode):
    if mode == "mock":
        monkeypatch.setenv("STRIPE_MOCK_MODE", "true")
    elif mode in {"test", "live"}:
        monkeypatch.setenv("STRIPE_SECRET_KEY", f"sk_{mode}_synthetic")
    elif mode == "disabled":
        monkeypatch.setenv("STRIPE_BILLING_DISABLED", "true")
    else:
        assert mode == "unconfigured"


def set_flag(monkeypatch, flag):
    if flag is not None:
        monkeypatch.setenv("BILLING_TOPUPS_LIVE_ENABLED", flag)


async def checkout(tenant_id=None, db_client=None):
    return await bt.create_topup_checkout(
        body=bt.CheckoutIn(package_code="m100"),
        request=SimpleNamespace(headers={}, client=SimpleNamespace(host="1.2.3.4")),
        current_user=SimpleNamespace(tenant_id=tenant_id or str(uuid4()), id=str(uuid4()), email="a@b.c"),
        db_client=db_client or object(),
        audit_logger=SimpleNamespace(log=AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["test", "live", "unconfigured", "disabled"])
@pytest.mark.parametrize("flag", [None, "", "0", "false", "no", "off", "enabled", "2"])
async def test_real_provider_mode_without_the_flag_is_503_before_any_order_or_session(
    world, monkeypatch, mode, flag
):
    set_mode(monkeypatch, mode)
    set_flag(monkeypatch, flag)
    with pytest.raises(HTTPException) as refused:
        await checkout()
    assert refused.value.status_code == 503
    assert refused.value.detail == DETAIL
    assert world.orders == 0, "an order row was written for a checkout that cannot start"
    assert _ModeBilling.sessions == [], "a payment session was requested"
    # The guard read the real service, and its input varies with configuration.
    assert _ModeBilling.instances[-1].billing_mode == mode
    assert _ModeBilling.instances[-1].mock_mode is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["test", "live"])
@pytest.mark.parametrize("flag", ["true", "TRUE", "1", " yes ", "on"])
async def test_flag_on_lets_a_real_provider_mode_proceed(world, monkeypatch, mode, flag):
    set_mode(monkeypatch, mode)
    set_flag(monkeypatch, flag)
    result = await checkout()
    assert world.orders == 1
    assert len(_ModeBilling.sessions) == 1
    assert world.attached == [(str(world.created[0]["id"]), "cs_x")]
    assert result.mock_mode is False
    assert result.checkout_url == "https://pay.example/x"
    assert _ModeBilling.instances[-1].billing_mode == mode


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "false", "true"])
async def test_mock_mode_is_unchanged_whatever_the_flag_says(world, monkeypatch, flag):
    set_mode(monkeypatch, "mock")
    set_flag(monkeypatch, flag)
    result = await checkout()
    assert world.orders == 1
    assert len(_ModeBilling.sessions) == 1
    assert world.attached == [(str(world.created[0]["id"]), "cs_x")]
    assert result.mock_mode is True
    assert _ModeBilling.instances[-1].mock_mode is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["test", "live"])
async def test_flag_on_two_simultaneous_first_checkouts_share_one_customer_end_to_end(
    world, monkeypatch, mode
):
    """The reported scenario through the real endpoint and the real service
    (only the SDK and the tenants row are fakes): two first checkouts at once
    must leave ONE Stripe customer, bound to the tenant, on both sessions."""
    set_mode(monkeypatch, mode)
    set_flag(monkeypatch, "true")
    fake = FakeStripe()
    monkeypatch.setattr(bs, "stripe", fake)
    monkeypatch.setattr(bt, "BillingService", BillingService)
    db = FakeTenantsDb()

    first, second = await asyncio.gather(checkout(TENANT, db), checkout(TENANT, db))

    (customer,) = fake.created("customer")
    sessions = fake.created("session")
    assert len(sessions) == 2
    assert {s["params"]["customer"] for s in sessions} == {customer["id"]}
    assert db.bound() == customer["id"]
    assert customer["options"] == {"idempotency_key": f"cust-{TENANT}"}
    assert {s["options"]["idempotency_key"] for s in sessions} == {f"topup-{o['id']}" for o in world.created}
    assert all(s["via"] == "client" for s in fake.calls)
    assert first.checkout_url != second.checkout_url
    assert {sid for _, sid in world.attached} == {s["id"] for s in sessions}
    assert {oid for oid, _ in world.attached} == {str(o["id"]) for o in world.created}


@pytest.mark.asyncio
async def test_mock_mode_checkout_end_to_end_is_unchanged_and_never_touches_the_sdk(world, monkeypatch):
    set_mode(monkeypatch, "mock")
    monkeypatch.setattr(bt, "BillingService", BillingService)
    db = FakeTenantsDb()

    result = await checkout(TENANT, db)

    order_id = str(world.created[0]["id"])
    assert result.mock_mode is True
    assert result.session_id == f"cs_mock_topup_{order_id[:8]}"
    query = parse_qs(urlsplit(result.checkout_url).query)
    assert query["order_id"] == [order_id] and query["mock"] == ["true"]
    assert db.bound() == f"cus_mock_{TENANT[:8]}"  # persisted exactly as before
    assert world.attached == [(order_id, result.session_id)]


@pytest.mark.asyncio
async def test_http_refusal_is_the_503_envelope_the_ui_reads(world, monkeypatch):
    set_mode(monkeypatch, "live")
    app = FastAPI()
    app.include_router(bt.router, prefix="/api/v1")
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), tenant_id=str(uuid4()), email="synthetic@example.com", role="platform_admin"
    )
    app.dependency_overrides[get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())
    app.dependency_overrides[get_db_client] = lambda: object()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/billing/topups/checkout", json={"package_code": "m100"})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "service_unavailable"
    assert error["message"] == DETAIL
    assert world.orders == 0
    assert _ModeBilling.sessions == []
