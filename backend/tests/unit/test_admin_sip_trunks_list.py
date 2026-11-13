"""G4: platform admins can list SIP trunks across tenants, without secrets.

The Admin plan lists "trunks/connectors". Connectors had an admin directory;
SIP trunks only had the tenant's own /telephony/sip/trunks. GET
/admin/sip-trunks is a read-only, paginated directory with the tenant, the
trunk's address, whether it is on, its role and the live registration state
the trunk-status updater keeps. It never reads the credential columns' values.
"""
import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.dependencies import require_platform_admin
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.admin import sip_trunks as admin_trunks

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
ROW = {"id": UUID("11111111-2222-4333-8444-555555555555"), "tenant_id": UUID(TENANT),
       "tenant_name": "Acme Roofing", "trunk_name": "UK line", "sip_domain": "sip.example.net",
       "port": 5060, "transport": "udp", "direction": "both", "is_active": True,
       "auth_configured": True, "role": None, "live_registration_status": "registered",
       "live_status_detail": None, "live_status_checked_at": datetime(2026, 11, 9, tzinfo=timezone.utc),
       "last_tested_at": None, "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
       "updated_at": None}


@pytest.fixture
def conn(monkeypatch):
    state = SimpleNamespace(fetch=[], fetchval=[], scopes=[])

    class Conn:
        async def fetchval(self, query, *args):
            state.fetchval.append((query, args))
            return 1

        async def fetch(self, query, *args):
            state.fetch.append((query, args))
            return [ROW]

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        state.scopes.append(tenant_id)
        yield Conn()

    monkeypatch.setattr(admin_trunks, "acquire_with_tenant", acquire)
    return state


async def _list(**overrides):
    kwargs = dict(admin_user=SimpleNamespace(id="a1", role="platform_admin"),
                  db_client=SimpleNamespace(pool=object()), tenant_id=None, is_active=None,
                  registration=None, page=1, page_size=20)
    kwargs.update(overrides)
    return await admin_trunks.list_admin_sip_trunks(**kwargs)


def test_the_route_is_platform_admin_only():
    app = FastAPI()
    app.include_router(admin_router)
    assert "get" in app.openapi()["paths"]["/admin/sip-trunks"]
    guard = inspect.signature(admin_trunks.list_admin_sip_trunks).parameters["admin_user"].default
    assert guard.dependency is require_platform_admin


async def test_lists_trunks_across_tenants_without_credentials(conn):
    response = await _list()
    item = response.items[0]
    assert (item.tenant_id, item.tenant_name, item.trunk_name) == (TENANT, "Acme Roofing", "UK line")
    assert (item.is_active, item.auth_configured, item.live_registration_status) == (True, True, "registered")
    assert conn.scopes == [None], "a platform directory reads every tenant"
    query, args = conn.fetch[0]
    assert "s.deleted_at IS NULL" in query
    assert "auth_password_encrypted IS NOT NULL" in query
    assert "auth_password_encrypted," not in query and "s.auth_username" not in query
    assert not {"auth_username", "auth_password_encrypted"} & set(item.model_dump())
    assert args == (20, 0)


async def test_filters_bind_in_order_and_the_count_uses_them(conn):
    await _list(tenant_id=TENANT, is_active=False, registration="rejected", page=2, page_size=5)
    query, args = conn.fetch[0]
    assert "s.tenant_id=$1" in query and "s.is_active=$2" in query
    assert "s.live_registration_status=$3" in query
    assert args == (UUID(TENANT), False, "rejected", 5, 5)
    count_query, count_args = conn.fetchval[0]
    assert "s.deleted_at IS NULL" in count_query and count_args == args[:3]


async def test_a_bad_tenant_id_is_a_422(conn):
    with pytest.raises(HTTPException) as refused:
        await _list(tenant_id="nope")
    assert refused.value.status_code == 422
    assert conn.fetch == []
