"""The admin payments read hands the database real dates, not text.

`GET /admin/billing/reconciliation?since=&until=` passed both bounds to the
service as the strings it received. The statement binds them as
`$1::timestamptz`, and the database driver refuses a string for that
parameter, so any request with a date filter failed. No page sent a filter
until the admin Payments page did.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import billing_topups


@pytest.fixture
def service(monkeypatch):
    fake = SimpleNamespace(reconciliation=AsyncMock(return_value=[]))
    monkeypatch.setattr(billing_topups, "_service", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_a_plain_date_and_a_browser_timestamp_reach_the_service_as_utc_datetimes(service):
    await billing_topups.reconciliation(
        since="2026-11-01", until="2026-11-18T00:00:00.000Z", limit=10, fmt="json", _admin=None)
    sent = service.reconciliation.await_args.kwargs
    assert sent["since"] == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert sent["until"] == datetime(2026, 11, 18, tzinfo=timezone.utc)
    assert isinstance(sent["since"], datetime) and sent["since"].tzinfo is not None


@pytest.mark.asyncio
async def test_a_bound_with_its_own_zone_keeps_that_instant(service):
    await billing_topups.reconciliation(
        since="2026-11-01T09:00:00+05:00", until=None, limit=10, fmt="json", _admin=None)
    sent = service.reconciliation.await_args.kwargs
    assert sent["since"] == datetime(2026, 11, 1, 9, tzinfo=timezone(timedelta(hours=5)))
    assert sent["until"] is None


@pytest.mark.asyncio
async def test_no_bounds_and_blank_bounds_mean_no_filter(service):
    await billing_topups.reconciliation(limit=10, _admin=None)
    assert service.reconciliation.await_args.kwargs["since"] is None
    await billing_topups.reconciliation(since=" ", until="", limit=10, fmt="json", _admin=None)
    sent = service.reconciliation.await_args.kwargs
    assert sent["since"] is None and sent["until"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["yesterday", "2026-13-40", "11/01/2026", "2026-11-01; DROP TABLE x"])
async def test_a_bound_that_is_not_a_date_is_refused_before_the_database(service, bad):
    with pytest.raises(HTTPException) as refused:
        await billing_topups.reconciliation(since=bad, until=None, limit=10, fmt="json", _admin=None)
    assert refused.value.status_code == 422
    assert bad not in str(refused.value.detail)  # the answer never echoes the input
    service.reconciliation.assert_not_awaited()
