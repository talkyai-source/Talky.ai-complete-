"""F2-03: a checkout session may attach only to the authenticated tenant's order.

Real service SQL runs against SQLite after casts/NOW adaptation; the helper's
transaction-local commands are recorded, not used to emulate RLS filtering.
No payment provider or external database is contacted.
"""
from contextlib import asynccontextmanager
import inspect
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import billing, billing_topups
from app.domain.services.topup_service import TopupError, TopupService


OWNER = "a1111111-bbbb-4111-8111-ccccccccccc1"
OTHER = "a2222222-bbbb-4222-8222-ccccccccccc2"
ORDER = "c3333333-bbbb-4333-8333-ddddddddddd3"
FOREIGN_ORDER = "f6666666-bbbb-4666-8666-aaaaaaaaaaa6"


class Pool:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("CREATE TABLE topup_orders (id TEXT PRIMARY KEY, tenant_id TEXT, provider_session_id TEXT, updated_at TEXT);")
        self.db.executemany("INSERT INTO topup_orders VALUES (?, ?, NULL, NULL)", [(ORDER, OWNER), (FOREIGN_ORDER, OTHER)])
        self.events = []
        self.entries = self.releases = 0

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        self.entries += 1
        try:
            yield self
        finally:
            self.releases += 1

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        self.events.append((sql, args))
        if re.fullmatch(r"SET LOCAL (?:statement_timeout|lock_timeout) = '[0-9]+ms'", sql):
            return "SET"
        if re.fullmatch(r"SET LOCAL app\.current_tenant_id = '[a-f0-9-]+'", sql) or sql == "SET LOCAL app.bypass_rls = 'on'":
            return "SET"
        assert sql.lstrip().startswith("UPDATE topup_orders"), sql
        statement = re.sub(r"::uuid\b", "", sql)
        statement = re.sub(r"NOW\(\)", "CURRENT_TIMESTAMP", statement)
        bindings = {}

        def replace(match):
            index = int(match.group(1))
            bindings[f"p{index}"] = str(args[index - 1])
            return f":p{index}"

        result = self.db.execute(re.sub(r"\$(\d+)", replace, statement), bindings)
        return f"UPDATE {result.rowcount}"


@pytest.fixture
def pool():
    value = Pool()
    try:
        yield value
    finally:
        value.db.close()


async def attach(service, order_id):
    if "tenant_id" in inspect.signature(service.attach_session).parameters:
        return await service.attach_session(order_id, "cs_synthetic", tenant_id=OWNER)
    return await service.attach_session(order_id, "cs_synthetic")


@pytest.mark.asyncio
async def test_session_attach_refuses_foreign_order_even_without_rls_filtering(pool):
    service = TopupService(pool)
    with pytest.raises(TopupError):
        await attach(service, FOREIGN_ORDER)
    assert pool.db.execute("SELECT provider_session_id FROM topup_orders WHERE id=?", (FOREIGN_ORDER,)).fetchone()[0] is None
    assert pool.entries == pool.releases == 1


@pytest.mark.asyncio
async def test_session_attach_updates_own_order_and_preserves_other_order(pool):
    await attach(TopupService(pool), ORDER)
    rows = dict(pool.db.execute("SELECT id, provider_session_id FROM topup_orders").fetchall())
    assert rows == {ORDER: "cs_synthetic", FOREIGN_ORDER: None}
    assert pool.entries == pool.releases == 1


@pytest.mark.asyncio
async def test_session_attach_without_tenant_refuses_before_database(pool):
    with pytest.raises(TopupError):
        await TopupService(pool).attach_session(ORDER, "cs_synthetic")
    assert pool.entries == pool.releases == 0
    assert pool.events == []


@pytest.mark.asyncio
async def test_checkout_passes_authenticated_tenant_into_session_attachment(monkeypatch):
    order = {"id": ORDER, "minutes": 100, "price_cents": 1000, "currency": "usd", "name": "Synthetic", "package_code": "m100"}
    service = SimpleNamespace(create_order=AsyncMock(return_value=order), attach_session=AsyncMock())
    provider = SimpleNamespace(mock_mode=True, create_topup_checkout_session=AsyncMock(return_value={
        "session_id": "cs_synthetic", "checkout_url": "https://synthetic.invalid/checkout", "mock_mode": True,
    }))
    monkeypatch.setattr(billing_topups, "_service", lambda: service)
    monkeypatch.setattr(billing_topups, "BillingService", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(billing_topups, "_purchase_audience", AsyncMock(return_value="subscriber"))
    monkeypatch.setattr(billing_topups, "billing_return_urls", lambda: "https://synthetic.invalid/billing")
    monkeypatch.setattr(billing, "_require_checkout_permitted_in_test_mode", lambda _user: None)

    response = await billing_topups.create_topup_checkout(
        billing_topups.CheckoutIn(package_code="m100"), SimpleNamespace(client=None, headers={}),
        current_user=SimpleNamespace(tenant_id=OWNER, id="b3333333-bbbb-4333-8333-ddddddddddd3", email="synthetic@example.invalid"),
        db_client=object(), audit_logger=SimpleNamespace(log=AsyncMock()),
    )
    assert response.checkout_url == "https://synthetic.invalid/checkout"
    service.attach_session.assert_awaited_once_with(ORDER, "cs_synthetic", tenant_id=OWNER)
    assert service.create_order.await_args.kwargs["tenant_id"] == OWNER
    assert provider.create_topup_checkout_session.await_args.kwargs["tenant_id"] == OWNER
