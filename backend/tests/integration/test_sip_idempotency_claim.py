"""SIP trunk idempotency claims expire and do not pin transient failures.

Bug hunt 2026-10-10 (telephony-infra-media-9, completion): the runtime claim
helper was fixed, but telephony_sip/_shared.py kept the identical
ON CONFLICT DO NOTHING claim, so a trunk request that got a 429/5xx replayed
that failure for 24 hours, and a claim abandoned by a crash blocked its key
until expiry. Same real-SQL harness as the runtime test (TEMP table on a local
``*_test`` database via TEST_DATABASE_URL).
"""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.api.v1.endpoints.telephony_sip import _shared as sip_shared
from tests.integration.test_bh_telephony_control_idempotency_claim import H1, H2, conn  # noqa: F401

pytestmark = pytest.mark.integration
_OP = "sip_trunk:update"


async def _claim(connection, tenant, key, request_hash=H1):
    return await sip_shared._claim_idempotency(
        connection, tenant_id=tenant, operation=_OP, idempotency_key=key, request_hash=request_hash)


async def _store(connection, tenant, key, status):
    await sip_shared._store_idempotency_result(
        connection, tenant_id=tenant, operation=_OP, idempotency_key=key,
        response_body={"status": status}, status_code=status, resource_type="sip_trunk", resource_id=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503])
async def test_transient_failures_are_not_replayed(conn, status):  # noqa: F811
    tenant = uuid4()
    await _claim(conn, tenant, "k")
    await _store(conn, tenant, "k", status)
    assert (await _claim(conn, tenant, "k"))[0] == "new"


@pytest.mark.asyncio
async def test_expired_and_abandoned_claims_are_reclaimed_but_results_replay(conn):  # noqa: F811
    tenant = uuid4()
    await _claim(conn, tenant, "done")
    await _store(conn, tenant, "done", 201)
    assert (await _claim(conn, tenant, "done"))[0] == "replay"
    await conn.execute("UPDATE tenant_telephony_idempotency SET expires_at = NOW() - INTERVAL '1 hour' "
                       "WHERE tenant_id = $1 AND idempotency_key = 'done'", tenant)
    assert (await _claim(conn, tenant, "done", H2))[0] == "new"

    await _claim(conn, tenant, "stale")
    assert (await _claim(conn, tenant, "stale"))[0] == "in_progress"
    await conn.execute("UPDATE tenant_telephony_idempotency SET created_at = NOW() - $2::interval "
                       "WHERE tenant_id = $1 AND idempotency_key = 'stale'", tenant, timedelta(minutes=30))
    assert (await _claim(conn, tenant, "stale"))[0] == "new"
