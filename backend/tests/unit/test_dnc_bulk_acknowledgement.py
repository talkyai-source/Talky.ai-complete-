"""Acknowledged suppression, expiry, and tenant boundaries on actual service."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.dnc_service import DNCService
from tests.unit.test_dnc_service import _FakePool

TENANT = "20000000-0000-4000-8000-000000000017"
FOREIGN = "20000000-0000-4000-8000-000000000018"
PHONE = "+15555550107"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,accepted", [("caller_opt_out", 1), ("bulk_import", 0), ("manual_admin", 0)])
async def test_expired_same_source_is_not_falsely_acknowledged(source, accepted):
    pool = _FakePool()
    service = DNCService(pool)
    row = await service.add(tenant_id=TENANT, e164=PHONE, source=source)
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    pool.store["by_id"][row.id]["expires_at"] = expired
    result = await service.bulk_import(tenant_id=TENANT, numbers=[PHONE], source=source)
    assert result["accepted_count"] == accepted
    assert result["skipped_count"] == 1 - accepted
    assert pool.store["by_id"][row.id]["expires_at"] == (None if accepted else expired)


@pytest.mark.asyncio
async def test_active_duplicate_acknowledges_existing_suppression_without_changing_expiry():
    pool = _FakePool()
    service = DNCService(pool)
    expiry = datetime.now(timezone.utc) + timedelta(days=2)
    row = await service.add(tenant_id=TENANT, e164=PHONE, source="bulk_import", expires_at=expiry)
    result = await service.bulk_import(tenant_id=TENANT, numbers=[PHONE, "+1 (555) 555-0107"], source="bulk_import")
    assert result["accepted_count"] == 2
    assert len(pool.store["by_id"]) == 1
    assert pool.store["by_id"][row.id]["expires_at"] == expiry


@pytest.mark.asyncio
async def test_same_phone_foreign_global_and_other_source_rows_are_not_mutated():
    pool = _FakePool()
    service = DNCService(pool)
    entries = []
    expiry = datetime.now(timezone.utc) - timedelta(days=1)
    for tenant, source in [(FOREIGN, "caller_opt_out"), (None, "caller_opt_out"), (TENANT, "manual_admin")]:
        row = await service.add(tenant_id=tenant, e164=PHONE, source=source)
        pool.store["by_id"][row.id]["expires_at"] = expiry
        entries.append(row.id)
    result = await service.bulk_import(tenant_id=TENANT, numbers=[PHONE], source="caller_opt_out")
    assert result["accepted_count"] == 1
    assert all(pool.store["by_id"][key]["expires_at"] == expiry for key in entries)


@pytest.mark.asyncio
async def test_tenant_scope_acquisition_failure_is_not_reported_as_partial_success():
    class BrokenPool:
        def acquire(self):
            raise RuntimeError("synthetic tenant connection failure")
    with pytest.raises(RuntimeError, match="tenant connection"):
        await DNCService(BrokenPool()).bulk_import(tenant_id=TENANT, numbers=[PHONE], source="bulk_import")
