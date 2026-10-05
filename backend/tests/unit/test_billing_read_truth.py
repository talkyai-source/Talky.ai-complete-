"""Billing read responses must not invent metering or financial evidence."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.dependencies import get_current_user, get_db_client, get_db_pool
from app.api.v1.endpoints import billing as api
from app.domain.services.billing_service import BillingService
from app.domain.services.minutes_quota import _status_from


class Pool:
    def __init__(self):
        self.queries = []
        self.conn = SimpleNamespace(
            execute=AsyncMock(side_effect=self.execute), fetchval=AsyncMock(return_value=True)
        )
        self.conn.transaction = self.transaction

    async def execute(self, sql, *args):
        self.queries.append((sql, args))

    @asynccontextmanager
    async def transaction(self):
        yield

    @asynccontextmanager
    async def acquire(self, **kwargs):
        yield self.conn


def service(pool):
    result = BillingService.__new__(BillingService)
    result.db_client = SimpleNamespace(pool=pool)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allocated,used,remaining,overage,unlimited",
    [
        (350, 150, 200, 0, False),
        (100, 150, 0, 50, False),
        (0, 150, 0, 0, True),
    ],
)
async def test_usage_reads_canonical_quota_under_tenant_context(
    monkeypatch, allocated, used, remaining, overage, unlimited
):
    pool, tenant = Pool(), str(uuid4())

    async def quota(conn, tenant_id):
        assert conn is pool.conn and str(tenant_id) == tenant
        assert any("app.current_tenant_id" in sql and tenant in sql for sql, _ in pool.queries)
        return _status_from(allocated, used * 60, unlimited=unlimited)

    monkeypatch.setattr("app.domain.services.minutes_quota.compute_minutes_status", quota)
    result = await service(pool).get_usage_summary(tenant)
    assert result == {
        "usage_type": "minutes",
        "total_used": used,
        "allocated": allocated,
        "remaining": remaining,
        "overage": overage,
        "unlimited": unlimited,
        "metering_period": "calendar_month",
    }


@pytest.mark.asyncio
async def test_unknown_usage_does_not_become_zero(monkeypatch):
    monkeypatch.setattr(
        "app.domain.services.minutes_quota.compute_minutes_status",
        AsyncMock(side_effect=RuntimeError("synthetic database failure")),
    )
    with pytest.raises(RuntimeError):
        await service(Pool()).get_usage_summary(str(uuid4()))


@pytest.mark.asyncio
async def test_unsupported_usage_is_rejected_without_querying():
    pool = Pool()
    with pytest.raises(ValueError, match="usage_type"):
        await service(pool).get_usage_summary(str(uuid4()), "tokens")
    assert pool.queries == []


@pytest.mark.asyncio
async def test_usage_endpoint_marks_failure_unavailable_not_zero():
    billing = SimpleNamespace(get_usage_summary=AsyncMock(side_effect=RuntimeError("synthetic")))
    with pytest.raises(HTTPException) as error:
        await api.get_usage_summary("minutes", SimpleNamespace(tenant_id=str(uuid4())), billing)
    assert error.value.status_code == 503 and error.value.detail["code"] == "usage_unavailable"


@pytest.mark.asyncio
async def test_alert_uses_actual_allowance_without_making_up_price():
    billing = SimpleNamespace(
        get_usage_summary=AsyncMock(
            return_value={
                "usage_type": "minutes",
                "total_used": 150,
                "allocated": 100,
                "remaining": 0,
                "overage": 50,
                "unlimited": False,
                "metering_period": "calendar_month",
            }
        )
    )
    alerts = await api.get_overage_alerts(SimpleNamespace(tenant_id=str(uuid4())), billing, None)
    assert len(alerts) == 1 and alerts[0]["exceededBy"] == 50
    assert alerts[0]["estimatedCharge"] is None and alerts[0]["currency"] is None
    assert alerts[0]["currency_exponent"] is None


@pytest.mark.asyncio
async def test_alert_failure_does_not_mean_no_overage():
    billing = SimpleNamespace(get_usage_summary=AsyncMock(side_effect=RuntimeError("synthetic")))
    with pytest.raises(HTTPException) as error:
        await api.get_overage_alerts(SimpleNamespace(tenant_id=str(uuid4())), billing, None)
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/billing/usage",
        "/billing/usage/daily",
        "/billing/invoices",
        "/billing/invoices/" + str(uuid4()),
        "/billing/overage-alerts",
        "/billing/adjustments",
        "/billing/subscription",
    ],
)
async def test_campaign_manager_cannot_read_billing_without_permission(monkeypatch, path):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=str(uuid4()), tenant_id=str(uuid4()), role="campaign_manager"
    )
    app.dependency_overrides[get_db_pool] = lambda: None
    app.dependency_overrides[get_db_client] = lambda: None
    app.dependency_overrides[api.get_billing_service] = lambda: None
    monkeypatch.setattr("app.core.container.get_db_pool_from_container", lambda: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 403


def invoice_row(**changes):
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "stripe_invoice_id": "in_synthetic",
        "status": "paid",
        "amount_due": 800,
        "amount_paid": 800,
        "currency": "usd",
        "plan_name": "Current unrelated catalog name",
        "plan_minutes": 9999,
        "projection": None,
        **changes,
    }


def test_summary_only_invoice_keeps_unknown_components_unavailable():
    result = api._invoice_public(invoice_row())
    assert result["amount_due"] == result["amount_paid"] == 800
    assert result["detail_status"] == "unavailable" and result["detail_source"] == "stored_summary"
    assert result["total"] is result["subtotal"] is result["taxes"] is result["line_items"] is None
    assert "usedMinutes" not in result and "planName" not in result


@pytest.mark.parametrize(
    "currency,exponent", [("jpy", 0), ("gbp", 2), ("bhd", 3), ("unknown", None)]
)
def test_invoice_money_is_exact_minor_units_never_assumed_two_decimals(currency, exponent):
    result = api._invoice_public(invoice_row(currency=currency, amount_due=1000))
    assert result["amount_due"] == 1000 and result["currency_exponent"] == exponent
    assert result["currency"] == currency


def test_historical_capture_survives_current_catalog_edits_and_retains_distinct_totals():
    snapshot = {
        "detail_status": "complete",
        "source": "stripe",
        "source_reference": "evt_synthetic",
        "currency": "gbp",
        "currency_exponent": 2,
        "subtotal": 1000,
        "total": 900,
        "amount_due": 800,
        "amount_paid": 800,
        "amount_remaining": 0,
        "taxes": [{"amount": 100}],
        "discounts": [{"amount": 200}],
        "credits": [],
        "line_items": [
            {"id": "il_synthetic", "description": "Purchased historical product", "amount": 1000}
        ],
    }
    row = invoice_row(projection=snapshot, captured_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    before = api._invoice_public(row)
    row.update(plan_name="Changed name", plan_minutes=1, plan_concurrent_calls=99)
    assert api._invoice_public(row) == before
    assert before["subtotal"] == 1000 and before["total"] == 900 and before["amount_due"] == 800
    assert (
        before["taxes"] == [{"amount": 100}]
        and before["captured_at"] == "2026-01-02T00:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_invoice_list_and_detail_share_projection_and_bound_tenant():
    row, pool = invoice_row(), Pool()
    pool.conn.fetch = AsyncMock(return_value=[row])
    user = SimpleNamespace(tenant_id=str(row["tenant_id"]))
    listed = await api.list_invoices(10, user, pool)
    detail = await api.get_invoice(str(row["id"]), user, pool)
    assert listed["invoices"] == [detail]
    for call in pool.conn.fetch.await_args_list:
        assert call.args[1] == row["tenant_id"]
    assert pool.conn.fetch.await_args_list[1].args[2] == row["id"]
    assert any(
        str(row["tenant_id"]) in sql and "app.current_tenant_id" in sql for sql, _ in pool.queries
    )


@pytest.mark.asyncio
async def test_foreign_or_absent_invoice_does_not_fall_back_to_provider_lookup():
    pool = Pool()
    pool.conn.fetch = AsyncMock(return_value=[])
    with pytest.raises(HTTPException) as error:
        await api.get_invoice(str(uuid4()), SimpleNamespace(tenant_id=str(uuid4())), pool)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_daily_usage_retains_seconds_and_does_not_round_each_day_up():
    pool = Pool()
    today = datetime.now(timezone.utc).date()
    pool.conn.fetch = AsyncMock(
        return_value=[
            {"day": today, "total_seconds": 179, "total_calls": 2, "successful": 2, "failed": 0}
        ]
    )
    result = await api.get_daily_usage(1, SimpleNamespace(tenant_id=str(uuid4())), pool)
    assert result == [
        {
            "date": today.isoformat(),
            "minutesUsed": 2,
            "secondsUsed": 179,
            "totalCalls": 2,
            "successfulCalls": 2,
            "failedCalls": 0,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("currency,exponent", [("GBP", 2), ("JPY", 0), ("KWD", 3), ("ZZZ", None)])
async def test_both_ledger_endpoints_publish_the_same_exact_contract(
    monkeypatch, currency, exponent
):
    from app.api.v1.endpoints import billing_topups

    order_id = uuid4()
    created = datetime(2026, 10, 4, tzinfo=timezone.utc)
    rows = [
        {
            "id": 2**53 + 1,
            "order_id": order_id,
            "provider_event_id": "evt_synthetic",
            "provider_payment_id": "pi_synthetic",
            "kind": "refund",
            "amount_cents": -2500,
            "currency": currency,
            "minutes_delta": -250,
            "note": None,
            "created_at": created,
        }
    ]
    ledger = AsyncMock(return_value=rows)
    monkeypatch.setattr("app.domain.services.topup_service.TopupService.ledger", ledger)
    monkeypatch.setattr(billing_topups, "_service", lambda: SimpleNamespace(ledger=ledger))
    tenant = str(uuid4())
    user = SimpleNamespace(tenant_id=tenant)
    adjustments = await api.get_adjustments(user, None)
    topups = await billing_topups.list_ledger(100, user)
    expected = [
        {
            **rows[0],
            "id": str(2**53 + 1),
            "order_id": str(order_id),
            "created_at": created.isoformat(),
            "currency_exponent": exponent,
        }
    ]
    assert adjustments == topups["entries"] == expected
    assert ledger.await_count == 2
    assert all(call.args[0] == tenant for call in ledger.await_args_list)


def test_read_only_snapshot_refresh_status_wins_without_mutating_base_row():
    row = invoice_row(
        status="open",
        amount_paid=0,
        projection={
            "detail_status": "complete",
            "status": "paid",
            "amount_paid": 800,
            "paid_at": "2026-01-02T00:00:00+00:00",
            "refunds": [],
        },
    )
    result = api._invoice_public(row)
    assert result["status"] == "paid" and result["amount_paid"] == 800
    assert result["paid_at"] == "2026-01-02T00:00:00+00:00" and result["refunds"] == []
    assert row["status"] == "open" and row["amount_paid"] == 0


def test_legacy_invalid_document_or_unsafe_integer_is_unavailable_without_losing_invoice():
    row = invoice_row(
        invoice_pdf="javascript:alert(1)",
        hosted_invoice_url="https://invoice.stripe.com/i/synthetic",
        amount_due=2**53,
    )
    result = api._invoice_public(row)
    assert result["invoice_pdf"] is None and result["amount_due"] is None
    assert result["hosted_invoice_url"] == "https://invoice.stripe.com/i/synthetic"
    assert result["id"] == str(row["id"]) and result["amount_paid"] == 800
