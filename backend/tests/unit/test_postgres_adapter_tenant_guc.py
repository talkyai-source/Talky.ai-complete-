"""The legacy adapter puts only a validated UUID into SET LOCAL app.current_tenant_id.

SET LOCAL takes no bind parameters, so both adapter paths (QueryBuilder and
RPC) interpolated the request's tenant id into the SQL text unchecked. Today
every caller validates it first (acquire_with_tenant and the membership check,
or the platform-admin bypass that never uses it), but a single careless caller
would have turned a tenant id into SQL. db_utils.acquire_with_tenant already
validates; the adapter now does the same.
"""
from __future__ import annotations

import pytest

from app.core import postgres_adapter
from app.core.postgres_adapter import Client, QueryBuilder
from app.core.security.tenant_isolation import set_bypass_rls, set_current_tenant_id
from tests.unit.test_postgres_adapter import FakeConn

TENANT = "3f2b8c1e-5d4a-4c7b-9e1f-2a6b8c0d4e5f"
HOSTILE = "00000000-0000-0000-0000-000000000000'; DROP TABLE calls; --"


@pytest.fixture
def conn(monkeypatch):
    fake = FakeConn()

    async def connect(_dsn):
        return fake

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", connect)
    set_bypass_rls(False)
    yield fake
    set_current_tenant_id(None)
    set_bypass_rls(False)


def _set_local_tenant_values(fake):
    return [sql for sql, _ in fake.execute_calls if "app.current_tenant_id" in sql]


def test_query_path_refuses_a_tenant_id_that_is_not_a_uuid(conn):
    set_current_tenant_id(HOSTILE)

    response = QueryBuilder(None, "f203_guc_probe").select("id").execute()

    assert response.error is not None
    assert not any("DROP TABLE" in sql for sql, _ in conn.execute_calls)
    assert conn.fetch_calls == []


def test_rpc_path_refuses_a_tenant_id_that_is_not_a_uuid(conn):
    set_current_tenant_id(HOSTILE)

    response = Client(None).rpc(
        "increment_campaign_counter", {"p_campaign_id": "camp_1", "p_counter": "calls_completed"}
    ).execute()

    assert response.error is not None
    assert not any("DROP TABLE" in sql for sql, _ in conn.execute_calls)


def test_a_valid_tenant_is_set_exactly_as_before(conn):
    set_current_tenant_id(TENANT)

    response = QueryBuilder(None, "f203_guc_probe").select("id").execute()

    assert response.error is None
    assert _set_local_tenant_values(conn) == [f"SET LOCAL app.current_tenant_id = '{TENANT}'"]
