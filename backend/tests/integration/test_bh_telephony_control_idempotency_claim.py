"""Bughunt telephony-control-9: runtime idempotency claims expire and do not pin transient failures.

Real SQL against a disposable Postgres (``TEST_DATABASE_URL`` pointing at a
local ``*_test`` database). The table is created as a TEMP table so nothing in
the database is touched beyond this session.
"""
import json
import os
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints.telephony_runtime import _shared as runtime_shared

pytestmark = pytest.mark.integration

DDL = """
CREATE TEMP TABLE tenant_telephony_idempotency (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    operation VARCHAR(120) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    response_body JSONB,
    status_code INTEGER CHECK (status_code IS NULL OR status_code BETWEEN 100 AND 599),
    resource_type VARCHAR(64),
    resource_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    CONSTRAINT uq_tenant_telephony_idempotency UNIQUE (tenant_id, operation, idempotency_key)
)
"""
H1, H2 = "a" * 64, "b" * 64


@pytest_asyncio.fixture
async def conn():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    connection = await asyncpg.connect(dsn)
    await connection.set_type_codec(
        "jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
    )
    await connection.execute(DDL)
    try:
        yield connection
    finally:
        await connection.close()


async def _claim(conn, tenant, key, request_hash=H1):
    return await runtime_shared._claim_idempotency(
        conn, tenant_id=tenant, operation="runtime_policy:activate",
        idempotency_key=key, request_hash=request_hash,
    )


async def _store(conn, tenant, key, status, body=None):
    await runtime_shared._store_idempotency_result(
        conn, tenant_id=tenant, operation="runtime_policy:activate", idempotency_key=key,
        response_body=body or {"ok": status < 400}, status_code=status,
        resource_type="runtime_policy_error", resource_id=None,
    )


async def _age(conn, tenant, key, *, expires=None, created=None):
    if expires:
        await conn.execute(
            "UPDATE tenant_telephony_idempotency SET expires_at = NOW() - $3::interval WHERE tenant_id=$1 AND idempotency_key=$2",
            tenant, key, expires)
    if created:
        await conn.execute(
            "UPDATE tenant_telephony_idempotency SET created_at = NOW() - $3::interval WHERE tenant_id=$1 AND idempotency_key=$2",
            tenant, key, created)


from datetime import timedelta  # noqa: E402


@pytest.mark.asyncio
async def test_baseline_claim_replay_and_in_progress(conn):
    tenant = uuid4()
    assert (await _claim(conn, tenant, "k"))[0] == "new"
    assert (await _claim(conn, tenant, "k"))[0] == "in_progress"
    assert (await _claim(conn, tenant, "k", H2))[0] == "hash_mismatch"
    await _store(conn, tenant, "k", 200)
    state, body, code = await _claim(conn, tenant, "k")
    assert (state, body, code) == ("replay", {"ok": True}, 200)


@pytest.mark.asyncio
async def test_expired_key_can_be_claimed_again(conn):
    tenant = uuid4()
    await _claim(conn, tenant, "k")
    await _store(conn, tenant, "k", 200)
    await _age(conn, tenant, "k", expires=timedelta(hours=1))

    assert (await _claim(conn, tenant, "k", H2))[0] == "new"
    # the reclaimed row now carries the new request and no stale result
    assert (await _claim(conn, tenant, "k", H2))[0] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503])
async def test_transient_failures_are_not_replayed(conn, status):
    tenant = uuid4()
    await _claim(conn, tenant, "k")
    await _store(conn, tenant, "k", status, {"status": status})
    assert (await _claim(conn, tenant, "k"))[0] == "new"


@pytest.mark.asyncio
async def test_deterministic_client_errors_still_replay(conn):
    tenant = uuid4()
    await _claim(conn, tenant, "k")
    await _store(conn, tenant, "k", 422, {"status": 422})
    assert (await _claim(conn, tenant, "k"))[:1] == ("replay",)


@pytest.mark.asyncio
async def test_abandoned_in_progress_claim_is_reclaimed_but_fresh_one_is_not(conn):
    tenant = uuid4()
    await _claim(conn, tenant, "fresh")
    assert (await _claim(conn, tenant, "fresh"))[0] == "in_progress"

    await _claim(conn, tenant, "stale")
    await _age(conn, tenant, "stale", created=timedelta(minutes=30))
    assert (await _claim(conn, tenant, "stale"))[0] == "new"


@pytest.mark.asyncio
async def test_other_tenant_keys_are_independent(conn):
    a, b = uuid4(), uuid4()
    await _claim(conn, a, "k")
    await _store(conn, a, "k", 502, {"status": 502})
    assert (await _claim(conn, b, "k"))[0] == "new"
    assert (await _claim(conn, a, "k"))[0] == "new"
