"""G3: platform admins can list campaigns across tenants.

The Admin plan needs to view campaigns, but the only cross-tenant campaign
endpoint was GET /admin/inbound/campaigns. GET /admin/campaigns is a read-only,
paginated directory with the tenant, status and counts, for platform admins
only (the same guard as every other /admin directory).
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import require_platform_admin
from app.api.v1.endpoints.admin import campaigns as admin_campaigns
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.telephony_bridge import CAMPAIGN_LIVE_CALL_STATUSES

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
ROW = {"id": UUID("11111111-2222-4333-8444-555555555555"), "tenant_id": UUID(TENANT),
       "tenant_name": "Acme Roofing", "name": "Q4 renewals", "status": "running",
       "direction": "outbound", "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
       "updated_at": None, "started_at": datetime(2026, 11, 2, tzinfo=timezone.utc),
       "contacts": 120, "calls": 45, "live_calls": 2}


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

    monkeypatch.setattr(admin_campaigns, "acquire_with_tenant", acquire)
    return state


async def _list(**overrides):
    kwargs = dict(admin_user=SimpleNamespace(id="a1", role="platform_admin"),
                  db_client=SimpleNamespace(pool=object()), tenant_id=None, status=None,
                  direction=None, search=None, page=1, page_size=20)
    kwargs.update(overrides)
    return await admin_campaigns.list_admin_campaigns(**kwargs)


def test_the_route_is_platform_admin_only():
    import inspect

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(admin_router)
    assert "get" in app.openapi()["paths"]["/admin/campaigns"]
    guard = inspect.signature(admin_campaigns.list_admin_campaigns).parameters["admin_user"].default
    assert guard.dependency is require_platform_admin


async def test_lists_campaigns_across_tenants_with_counts(conn):
    response = await _list()
    item = response.items[0]
    assert (item.tenant_id, item.tenant_name, item.name, item.status) == (TENANT, "Acme Roofing", "Q4 renewals", "running")
    assert (item.contacts, item.calls, item.live_calls) == (120, 45, 2)
    assert (response.total, response.page, response.page_size) == (1, 1, 20)
    assert conn.scopes == [None], "a platform directory reads every tenant"
    query, args = conn.fetch[0]
    assert "c.status<>'deleted'" in query
    assert "l.tenant_id=c.tenant_id" in query and "k.tenant_id=c.tenant_id" in query
    assert args == (list(CAMPAIGN_LIVE_CALL_STATUSES), 20, 0)


async def test_filters_bind_in_order_and_the_count_uses_them(conn):
    await _list(tenant_id=TENANT, status="paused", direction="outbound", search="50%_off", page=3, page_size=10)
    query, args = conn.fetch[0]
    assert "c.tenant_id=$1" in query and "c.status=$2" in query and "c.direction=$3" in query
    assert "c.name ILIKE $4" in query and "t.business_name ILIKE $4" in query
    assert args[:4] == (UUID(TENANT), "paused", "outbound", "%50\\%\\_off%")
    assert args[-2:] == (10, 20)
    count_query, count_args = conn.fetchval[0]
    assert "c.tenant_id=$1" in count_query and count_args == args[:4]
    assert "c.status<>'deleted'" not in query, "an explicit status filter replaces the default"


async def test_a_bad_tenant_id_is_a_422(conn):
    with pytest.raises(HTTPException) as refused:
        await _list(tenant_id="not-a-uuid")
    assert refused.value.status_code == 422
    assert conn.fetch == []
