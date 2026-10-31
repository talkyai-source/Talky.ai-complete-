"""Actual Admin projection methods; synthetic rows, no provider/DB effects."""

from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.admin.usage import (
    get_admin_usage_breakdown,
    get_admin_usage_summary,
)


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table

    def select(self, fields):
        self.db.reads.append((self.table, fields))
        return self

    def eq(self, *args):
        self.db.filters.append(("eq", args))
        return self

    def gte(self, *args):
        self.db.bounds.append(("gte", args))
        return self

    def lte(self, *args):
        self.db.bounds.append(("lte", args))
        return self

    def lt(self, *args):
        self.db.bounds.append(("lt", args))
        return self

    def execute(self):
        return SimpleNamespace(data=self.db.rows[self.table], error=None)


class DB:
    def __init__(self, calls=(), actions=()):
        self.rows = {"calls": list(calls), "assistant_actions": list(actions)}
        self.reads, self.filters, self.bounds = [], [], []

    def table(self, name):
        return Query(self, name)


def call(seconds=600, cost=None, **kwargs):
    return dict(
        id="synthetic-call",
        tenant_id="synthetic-tenant",
        duration_seconds=seconds,
        cost=cost,
        direction="outbound",
        provider="asterisk",
        tenants={"business_name": "Synthetic"},
        **kwargs,
    )


async def summary(db):
    return await get_admin_usage_summary(
        admin_user=SimpleNamespace(),
        db_client=db,
        tenant_id=None,
        from_date="2026-10-01",
        to_date="2026-10-05",
    )


async def breakdown(db, group):
    return await get_admin_usage_breakdown(
        admin_user=SimpleNamespace(),
        db_client=db,
        tenant_id=None,
        from_date="2026-10-01",
        to_date="2026-10-05",
        group_by=group,
    )


@pytest.mark.asyncio
async def test_real_call_and_failed_action_do_not_fabricate_provider_spend():
    result = await summary(
        DB([call(cost=5)], [{"id": "action", "type": "send_sms", "status": "failed"}])
    )
    assert result.total_cost is None
    assert result.providers == []
    assert result.supplier_cost_status == "unavailable"
    assert result.total_action_records == result.total_api_calls == 1
    assert result.total_call_seconds == 600
    assert result.legacy_outbound_estimate.recorded_total == 5
    assert result.legacy_outbound_estimate.covered_call_count == 1
    assert result.legacy_outbound_estimate.coverage == "recorded_rows_only"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "calls", [[], [call()], [call(cost=float("nan"))], [call(cost=float("inf"))]]
)
async def test_empty_unpriced_or_invalid_money_is_unavailable_not_zero(calls):
    result = await summary(DB(calls))
    assert result.total_cost is None
    assert result.legacy_outbound_estimate.recorded_total is None
    assert result.legacy_outbound_estimate.covered_call_count == 0
    assert result.legacy_outbound_estimate.missing_call_count == len(calls)


@pytest.mark.asyncio
async def test_known_zero_is_distinct_from_missing_and_inbound_money_is_excluded():
    inbound = call(cost=99)
    inbound["direction"] = "inbound"
    result = await summary(DB([call(cost=0), call(), inbound]))
    assert result.total_cost is None
    estimate = result.legacy_outbound_estimate
    assert estimate.recorded_total == 0
    assert estimate.covered_call_count == 1
    assert estimate.missing_call_count == 1
    assert estimate.coverage == "partial"
    assert result.authoritative_inbound_monetary_totals_included is False


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["tenant", "type", "provider"])
async def test_all_breakdowns_preserve_unavailable_supplier_cost(group):
    result = await breakdown(DB([call(cost=5), call()], [{"type": "send_sms"}]), group)
    assert result["supplier_cost_status"] == "unavailable"
    if group == "provider":
        assert result["breakdown"] == []
    else:
        assert all(row["total_cost"] is None for row in result["breakdown"])
        assert result["breakdown"][0]["legacy_outbound_estimate"]["recorded_total"] == 5
        assert result["breakdown"][0]["legacy_outbound_estimate"]["missing_call_count"] == 1


@pytest.mark.asyncio
async def test_grouped_duration_aggregates_seconds_before_display_rounding():
    db = DB([call(59), call(59)])
    result = await summary(db)
    assert result.total_call_seconds == 118
    assert result.total_call_minutes == 1
    tenant = (await breakdown(db, "tenant"))["breakdown"][0]
    kind = (await breakdown(db, "type"))["breakdown"][0]
    assert tenant["total_seconds"] == kind["total_seconds"] == 118
    assert tenant["total_minutes"] == kind["total_units"] == 1


@pytest.mark.asyncio
async def test_both_reads_keep_explicit_tenant_filter():
    db = DB()
    await get_admin_usage_summary(
        admin_user=SimpleNamespace(),
        db_client=db,
        tenant_id="chosen-tenant",
        from_date="2026-10-01",
        to_date="2026-10-05",
    )
    await get_admin_usage_breakdown(
        admin_user=SimpleNamespace(),
        db_client=db,
        tenant_id="chosen-tenant",
        group_by="type",
        from_date="2026-10-01",
        to_date="2026-10-05",
    )
    assert db.filters == [("eq", ("tenant_id", "chosen-tenant"))] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["summary", "tenant", "type", "provider"])
@pytest.mark.parametrize("failure", ["error-envelope", "exception", "missing-data"])
async def test_query_failure_is_not_zero_usage_and_does_not_expose_database_detail(
    monkeypatch, read, failure
):
    def failed(self):
        if failure == "exception":
            raise RuntimeError("private database connection detail")
        return SimpleNamespace(
            data=None if failure == "missing-data" else [],
            error="private database connection detail" if failure == "error-envelope" else None,
        )

    monkeypatch.setattr(Query, "execute", failed)
    with pytest.raises(HTTPException) as exc:
        await (summary(DB()) if read == "summary" else breakdown(DB(), read))
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["summary", "type"])
async def test_report_end_date_includes_the_whole_selected_utc_day(read):
    db = DB([call(cost=5)])
    await (summary(db) if read == "summary" else breakdown(db, read))
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert db.bounds == [("gte", ("created_at", start)), ("lt", ("created_at", end))] * 2
    for instant, included in [(start, True), (end.replace(day=5, hour=23, minute=59, second=59), True), (end, False)]:
        assert (start <= instant < end) is included


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["summary", "breakdown"])
@pytest.mark.parametrize("start,end", [("invalid", "2026-10-05"), ("2026-10-06", "2026-10-05"), ("20261001", "2026-10-05"), ("2026-10-01", "2026-02-30")])
async def test_invalid_or_reversed_dates_fail_before_query(read, start, end):
    db = DB()
    fn = get_admin_usage_summary if read == "summary" else get_admin_usage_breakdown
    with pytest.raises(HTTPException) as exc:
        await fn(admin_user=SimpleNamespace(), db_client=db, tenant_id=None,
                 from_date=start, to_date=end)
    assert exc.value.status_code == 400
    assert db.reads == []


@pytest.mark.asyncio
async def test_default_period_uses_exclusive_tomorrow_in_utc():
    db = DB()
    result = await get_admin_usage_summary(admin_user=SimpleNamespace(), db_client=db,
                                          tenant_id=None, from_date=None, to_date=None)
    today = datetime.now(timezone.utc).date()
    assert result.period_end == today.isoformat()
    assert db.bounds[1][0] == "lt"
    assert db.bounds[1][1][1].date().toordinal() == today.toordinal() + 1
