"""The platform-admin "Add DNC entry" endpoint writes through the DNC service.

Bug hunt 2026-10-10 (billing-money-credentials-5): POST /admin/dnc upserted
with ON CONFLICT (tenant_id, normalized_number). Migration 0061 replaced that
constraint with partial unique indexes per source, so on Postgres every call
failed: "there is no unique or exclusion constraint matching the ON CONFLICT
specification" (reproduced on Postgres 16 with the production schema).
Operators could not add a suppression through it at all.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.api.v1.endpoints.call_limits import DncEntrySchema, add_dnc_entry
from tests.unit.test_dnc_service import _FakePool


async def test_admin_add_creates_a_tenant_entry_through_the_one_writer():
    pool, tenant = _FakePool(), uuid.uuid4()
    response = await add_dnc_entry(
        DncEntrySchema(phone_number="+1 415 555 1234", source="manual_admin", reason="complaint"),
        tenant_id=tenant, admin_user=SimpleNamespace(id=uuid.uuid4()),
        db_client=SimpleNamespace(pool=pool), audit_logger=None,
    )
    assert response.normalized_number == "+14155551234" and response.source == "manual_admin"
    assert list(pool.store["by_tuple"]) == [(str(tenant), "+14155551234", "manual_admin")]


async def test_admin_add_to_the_global_list_and_again_is_idempotent():
    pool = _FakePool()
    for _ in range(2):
        response = await add_dnc_entry(
            DncEntrySchema(phone_number="+14155550000", source="manual_admin"),
            tenant_id=None, admin_user=SimpleNamespace(id=uuid.uuid4()),
            db_client=SimpleNamespace(pool=pool), audit_logger=None,
        )
    assert response.expires_at is None
    assert list(pool.store["by_tuple"]) == [(None, "+14155550000", "manual_admin")]
