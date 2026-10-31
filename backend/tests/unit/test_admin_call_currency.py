"""Authoritative inbound money projection for the Admin call detail API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.admin.calls import (
    _admin_call_money_projection,
    get_admin_call_detail,
)


def test_outbound_cost_projection_remains_backward_compatible() -> None:
    assert _admin_call_money_projection(
        {"direction": "outbound", "cost": "1.2500"},
        [],
    ) == (1.25, None)


def test_finalized_inbound_cost_comes_from_parent_ledger_not_call_projection() -> None:
    assert _admin_call_money_projection(
        {"direction": "inbound", "billing_status": "finalized", "cost": "999.0000"},
        [
            {
                "call_leg_id": None,
                "transaction_type": "finalize",
                "amount": "12.345600",
                "currency": "eur",
            },
            {
                "call_leg_id": "transfer-leg",
                "transaction_type": "finalize",
                "amount": "800.000000",
                "currency": "USD",
            },
        ],
    ) == (12.3456, "EUR")


def test_reversed_inbound_cost_is_current_zero_state_in_original_currency() -> None:
    assert _admin_call_money_projection(
        {"direction": "inbound", "billing_status": "reversed", "cost": "0"},
        [
            {
                "call_leg_id": None,
                "transaction_type": "finalize",
                "amount": "12.345600",
                "currency": "USD",
            },
            {
                "call_leg_id": None,
                "transaction_type": "reverse",
                "amount": "-12.345600",
                "currency": "USD",
            },
        ],
    ) == (0.0, "USD")


def test_inbound_projection_fails_closed_for_mixed_or_unsettled_money() -> None:
    assert _admin_call_money_projection(
        {"direction": "inbound", "billing_status": "held", "cost": "5.0000"},
        [],
    ) == (None, None)
    assert _admin_call_money_projection(
        {"direction": "inbound", "billing_status": "finalized", "cost": "5.0000"},
        [
            {
                "call_leg_id": None,
                "transaction_type": "finalize",
                "amount": "5.000000",
                "currency": "USD",
            },
            {
                "call_leg_id": None,
                "transaction_type": "reverse",
                "amount": "-5.000000",
                "currency": "EUR",
            },
        ],
    ) == (None, None)


class _Builder:
    def __init__(self, client: "_FakeClient", table: str):
        self.client = client
        self.table = table
        self.filters: list[tuple[str, object]] = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column: str, value: object):
        self.filters.append((column, value))
        return self

    def single(self):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.client.queries.append((self.table, tuple(self.filters)))
        return SimpleNamespace(data=self.client.rows[self.table], error=None)


class _FakeClient:
    def __init__(self, call: dict, usage: list[dict]):
        self.rows = {
            "calls": call,
            "inbound_usage_transactions": usage,
            "tenants": {"business_name": "Acme"},
            "call_legs": [],
        }
        self.queries: list[tuple[str, tuple[tuple[str, object], ...]]] = []

    def table(self, name: str) -> _Builder:
        return _Builder(self, name)


@pytest.mark.asyncio
async def test_admin_detail_reads_tenant_scoped_authoritative_inbound_ledger() -> None:
    call_id = "10000000-0000-0000-0000-000000000001"
    tenant_id = "20000000-0000-0000-0000-000000000002"
    db = _FakeClient(
        {
            "id": call_id,
            "tenant_id": tenant_id,
            "phone_number": "+15555550100",
            "campaign_id": None,
            "status": "completed",
            "created_at": "2026-08-28T00:00:00+00:00",
            "direction": "inbound",
            "billing_status": "finalized",
            "cost": "999.0000",
        },
        [
            {
                "call_leg_id": None,
                "transaction_type": "finalize",
                "amount": "2.500000",
                "currency": "EUR",
            }
        ],
    )

    detail = await get_admin_call_detail(
        call_id=call_id,
        admin_user=SimpleNamespace(),
        db_client=db,
    )

    assert detail.cost == 2.5
    assert detail.currency == "EUR"
    assert (
        "inbound_usage_transactions",
        (("tenant_id", tenant_id), ("call_id", call_id)),
    ) in db.queries


@pytest.mark.asyncio
async def test_admin_detail_preserves_outbound_cost_without_ledger_dependency() -> None:
    call_id = "30000000-0000-0000-0000-000000000003"
    db = _FakeClient(
        {
            "id": call_id,
            "tenant_id": "40000000-0000-0000-0000-000000000004",
            "phone_number": "+15555550101",
            "campaign_id": None,
            "status": "completed",
            "created_at": "2026-08-28T00:00:00+00:00",
            "direction": "outbound",
            "billing_status": "finalized",
            "cost": "3.7500",
        },
        [],
    )

    detail = await get_admin_call_detail(
        call_id=call_id,
        admin_user=SimpleNamespace(),
        db_client=db,
    )

    assert detail.cost == 3.75
    assert detail.currency is None
    assert all(table != "inbound_usage_transactions" for table, _ in db.queries)
