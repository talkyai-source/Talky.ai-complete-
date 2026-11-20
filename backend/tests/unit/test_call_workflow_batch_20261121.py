"""Bulk call-workflow read: real HTTP handler, fake tenant-bound connection."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.api.v1.endpoints import call_workflow_batch as endpoint
from app.core.error_handlers import register_error_handlers


TENANT = "a0000000-0000-4000-8000-000000000001"
OTHER = "b0000000-0000-4000-8000-000000000002"
ACTOR = "c0000000-0000-4000-8000-000000000003"
C1 = "d0000000-0000-4000-8000-000000000001"
C2 = "d0000000-0000-4000-8000-000000000002"
C3 = "d0000000-0000-4000-8000-000000000003"
FOREIGN = "d0000000-0000-4000-8000-000000000009"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def row(call_id, tenant=TENANT, note="note"):
    return {"tenant_id": UUID(tenant), "call_id": UUID(call_id), "lead_type": "hot",
            "notes": note, "contact": "c", "interest": "i", "next_step": "n",
            "completed": True, "updated_by": UUID(ACTOR), "created_at": NOW, "updated_at": NOW}


class Store:
    def __init__(self):
        self.rows = [row(C1, note="one"), row(C2, note="two"),
                     row(FOREIGN, tenant=OTHER, note="foreign secret")]
        self.fetches = []
        self.touched = 0
        self.fail = False

    @asynccontextmanager
    async def acquire(self):
        self.touched += 1
        yield Conn(self)


class Conn:
    def __init__(self, store):
        self.store = store
        self.depth = 0

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self):
        self.depth += 1
        try:
            yield self
        finally:
            self.depth -= 1

    async def execute(self, sql, *args):
        return "SET"

    async def fetch(self, sql, *args):
        query = " ".join(sql.lower().split())
        self.store.fetches.append((query, args))
        if self.store.fail:
            raise RuntimeError("synthetic database failure")
        assert "tenant_id = $1" in query and "call_id = any($2::uuid[])" in query
        tenant, ids = args
        return [r for r in self.store.rows if r["tenant_id"] == tenant and r["call_id"] in ids]


@pytest.fixture
def harness(monkeypatch):
    store = Store()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(endpoint.router, prefix="/api/v1")
    principal = {"id": ACTOR, "tenant_id": TENANT, "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        email="synthetic@example.invalid", **principal)
    app.dependency_overrides[get_db_pool] = lambda: store
    monkeypatch.setattr("app.core.container.get_db_pool_from_container", lambda: None)
    return app, store, principal


async def post(app, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://test") as client:
        return await client.post("/api/v1/calls/workflow/batch", json=body)


async def test_only_saved_calls_are_returned_in_the_single_read_shape(harness):
    app, store, _ = harness
    response = await post(app, {"call_ids": [C1, C2, C3]})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert set(items) == {C1, C2}
    assert items[C1] == {"leadType": "hot", "notes": "one",
                         "form": {"contact": "c", "interest": "i", "nextStep": "n", "completed": True},
                         "updatedAt": NOW.isoformat()}
    assert len(store.fetches) == 1


async def test_other_tenant_call_is_absent_and_sql_is_bound_to_callers_tenant(harness):
    app, store, _ = harness
    response = await post(app, {"call_ids": [C1, FOREIGN]})
    assert response.status_code == 200
    assert set(response.json()["items"]) == {C1}
    assert "foreign secret" not in response.text
    _, args = store.fetches[-1]
    assert str(args[0]) == TENANT
    assert {str(i) for i in args[1]} == {C1, FOREIGN}


async def test_duplicates_collapse(harness):
    app, store, _ = harness
    response = await post(app, {"call_ids": [C1, C1, C1.upper()]})
    assert response.status_code == 200
    assert list(response.json()["items"]) == [C1]
    assert [str(i) for i in store.fetches[-1][1][1]] == [C1]


@pytest.mark.parametrize("body", [
    {"call_ids": [f"d0000000-0000-4000-8000-{n:012d}" for n in range(201)]},
    {"call_ids": []},
    {"call_ids": ["not-a-uuid"]},
    {"call_ids": [C1], "extra": 1},
    {},
])
async def test_invalid_bodies_are_422_before_database_work(harness, body):
    app, store, _ = harness
    response = await post(app, body)
    assert response.status_code == 422, response.text
    assert store.touched == 0


async def test_exactly_200_ids_is_accepted(harness):
    app, _, _ = harness
    ids = [f"d0000000-0000-4000-8000-{n:012d}" for n in range(200)]
    assert (await post(app, {"call_ids": ids})).status_code == 200


async def test_role_without_calls_read_is_403_and_database_untouched(harness):
    app, store, principal = harness
    principal["role"] = "billing_user"
    response = await post(app, {"call_ids": [C1]})
    assert response.status_code == 403
    assert store.touched == 0


async def test_user_without_tenant_is_403(harness):
    app, store, principal = harness
    principal.update(role="platform_admin", tenant_id=None)
    response = await post(app, {"call_ids": [C1]})
    assert response.status_code == 403
    assert store.touched == 0


async def test_non_retryable_database_error_is_503_with_plain_sentence(harness):
    app, store, _ = harness
    store.fail = True
    response = await post(app, {"call_ids": [C1]})
    assert response.status_code == 503, response.text
    assert "synthetic" not in response.text


async def test_path_resolves_to_batch_handler_through_the_real_api_router(harness):
    from app.api.v1.routes import api_router

    _, store, principal = harness
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        email="synthetic@example.invalid", **principal)
    app.dependency_overrides[get_db_pool] = lambda: store
    response = await post(app, {"call_ids": [C1]})
    assert response.status_code == 200, response.text
    assert set(response.json()["items"]) == {C1}
    assert len(store.fetches) == 1
