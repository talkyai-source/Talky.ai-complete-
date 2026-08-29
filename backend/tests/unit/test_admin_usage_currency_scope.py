"""Admin usage estimates must never mix inbound ledger currencies into USD."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.admin.usage import (
    _legacy_outbound_call_cost,
    get_admin_usage_breakdown,
    get_admin_usage_summary,
)


CALLS = [
    {
        "id": "inbound-call",
        "tenant_id": "tenant-1",
        "direction": "inbound",
        "duration_seconds": 120,
        "cost": 99.0,
        "tenants": {"business_name": "Acme"},
    },
    {
        "id": "outbound-call",
        "tenant_id": "tenant-1",
        "direction": "outbound",
        "duration_seconds": 60,
        "cost": 2.5,
        "tenants": {"business_name": "Acme"},
    },
]


class _Builder:
    def __init__(self, client: "_FakeClient", table: str):
        self.client = client
        self.table = table

    def select(self, fields: str, **_kwargs):
        self.client.selects.append((self.table, fields))
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def execute(self):
        data = CALLS if self.table == "calls" else []
        return SimpleNamespace(data=data, error=None)


class _FakeClient:
    def __init__(self):
        self.selects: list[tuple[str, str]] = []

    def table(self, name: str) -> _Builder:
        return _Builder(self, name)


def test_legacy_cost_helper_excludes_inbound_and_defaults_old_rows_to_outbound() -> None:
    assert _legacy_outbound_call_cost(CALLS) == 2.5
    assert _legacy_outbound_call_cost([{"cost": 1.25}]) == 1.25


@pytest.mark.asyncio
async def test_summary_keeps_all_usage_but_excludes_inbound_calls_cost() -> None:
    db = _FakeClient()
    summary = await get_admin_usage_summary(
        admin_user=SimpleNamespace(),
        db_client=db,
        from_date="2026-08-01",
        to_date="2026-08-28",
    )

    assert summary.total_call_minutes == 3
    voice = next(item for item in summary.providers if item.usage_type == "voice")
    assert voice.total_units == 3
    assert voice.estimated_cost == 2.5
    assert summary.total_cost == 2.62
    assert summary.cost_currency == "USD"
    assert summary.legacy_calls_cost_scope == "outbound_only"
    assert summary.authoritative_inbound_monetary_totals_included is False
    assert summary.authoritative_inbound_monetary_source == "inbound_usage_transactions"
    assert "ledger-only" in summary.monetary_note
    calls_select = next(fields for table, fields in db.selects if table == "calls")
    assert "direction" in calls_select


@pytest.mark.asyncio
@pytest.mark.parametrize("group_by", ["tenant", "type", "provider"])
async def test_every_breakdown_keeps_counts_and_excludes_inbound_calls_cost(
    group_by: str,
) -> None:
    response = await get_admin_usage_breakdown(
        admin_user=SimpleNamespace(),
        db_client=_FakeClient(),
        group_by=group_by,
        from_date="2026-08-01",
        to_date="2026-08-28",
    )

    assert response["legacy_calls_cost_scope"] == "outbound_only"
    assert response["authoritative_inbound_monetary_totals_included"] is False
    assert response["authoritative_inbound_monetary_source"] == ("inbound_usage_transactions")
    if group_by == "tenant":
        row = response["breakdown"][0]
        assert row["call_count"] == 2
        assert row["total_minutes"] == 3
        assert row["total_cost"] == 2.5
    elif group_by == "type":
        row = response["breakdown"][0]
        assert row["count"] == 2
        assert row["total_units"] == 3
        assert row["total_cost"] == 2.5
    else:
        voice = next(row for row in response["breakdown"] if row["usage_type"] == "voice")
        assert voice["total_units"] == 3
        assert voice["estimated_cost"] == 2.5
