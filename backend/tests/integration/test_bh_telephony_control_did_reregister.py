"""Bughunt telephony-control-10: re-registering a revoked/suspended DID must not look like success.

``create_pending`` upserts on (tenant_id, e164) and RETURNs the existing row,
so registering a revoked number answered 201 with status 'revoked' and a
dead end. The service must refuse with a conflict error - and must not
silently reset the status (a tenant admin must not undo a platform
revocation/suspension) nor touch the row's label/metadata.

Real SQL against a disposable local ``*_test`` Postgres; the table is a TEMP
table so nothing else in the database is touched.
"""
import json
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.tenant_phone_number_service import (
    TenantPhoneNumberError,
    TenantPhoneNumberService,
)

pytestmark = pytest.mark.integration

DDL = """
CREATE TEMP TABLE tenant_phone_numbers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    e164 TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'manual_admin',
    status TEXT NOT NULL DEFAULT 'pending_verification',
    verification_method TEXT,
    verification_sent_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    verified_by TEXT,
    stir_shaken_token TEXT,
    label TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, e164)
)
"""
E164 = "+442046132300"


@pytest_asyncio.fixture
async def svc(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    conn = await asyncpg.connect(dsn)
    await conn.execute(DDL)

    @asynccontextmanager
    async def one_connection(_pool, _tenant_id, **_kwargs):
        yield conn

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", one_connection)
    try:
        yield TenantPhoneNumberService(object()), conn
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["revoked", "suspended"])
async def test_reregistering_a_blocked_number_is_refused_and_leaves_the_row_alone(svc, blocked):
    service, conn = svc
    tenant = str(uuid4())
    first = await service.create_pending(tenant_id=tenant, e164=E164, label="front desk", metadata={"a": 1})
    await conn.execute("UPDATE tenant_phone_numbers SET status=$2 WHERE id=$1::uuid", first.id, blocked)

    with pytest.raises(TenantPhoneNumberError, match=blocked):
        await service.create_pending(tenant_id=tenant, e164=E164, label="renamed", metadata={"b": 2})

    row = await conn.fetchrow("SELECT status, label, metadata FROM tenant_phone_numbers WHERE id=$1::uuid", first.id)
    assert row["status"] == blocked
    assert row["label"] == "front desk"
    assert json.loads(row["metadata"]) == {"a": 1}


@pytest.mark.asyncio
async def test_reregistering_a_pending_number_is_still_idempotent(svc):
    service, _conn = svc
    tenant = str(uuid4())
    first = await service.create_pending(tenant_id=tenant, e164=E164, label="one", metadata={"a": 1})
    again = await service.create_pending(tenant_id=tenant, e164=E164, label="two", metadata={"b": 2})
    assert again.id == first.id
    assert again.status == "pending_verification"
    assert again.label == "two"
    assert again.metadata == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_reregistering_a_verified_number_returns_it_unchanged_in_status(svc):
    service, conn = svc
    tenant = str(uuid4())
    first = await service.create_pending(tenant_id=tenant, e164=E164)
    await conn.execute("UPDATE tenant_phone_numbers SET status='verified' WHERE id=$1::uuid", first.id)
    again = await service.create_pending(tenant_id=tenant, e164=E164)
    assert again.id == first.id and again.status == "verified"


@pytest.mark.asyncio
async def test_same_number_under_another_tenant_is_independent(svc):
    service, conn = svc
    a, b = str(uuid4()), str(uuid4())
    first = await service.create_pending(tenant_id=a, e164=E164)
    await conn.execute("UPDATE tenant_phone_numbers SET status='revoked' WHERE id=$1::uuid", first.id)
    other = await service.create_pending(tenant_id=b, e164=E164)
    assert other.status == "pending_verification"
