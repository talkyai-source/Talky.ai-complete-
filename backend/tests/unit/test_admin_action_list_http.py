"""Actual Admin HTTP serialization must not turn adapter outages into empty success."""
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_db_client, require_admin
from app.api.v1.endpoints.admin import actions
from app.core import postgres_adapter


def app_for(db):
    app = FastAPI()
    app.include_router(actions.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_db_client] = lambda: db
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id=str(uuid4()), role="platform_admin", tenant_id=None,
    )
    return app


async def request(db, query=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_for(db)), base_url="http://synthetic.invalid"
    ) as client:
        return await client.get("/api/v1/admin/actions" + query)


class AdapterDB:
    def table(self, name):
        return postgres_adapter.QueryBuilder(None, name)


class ResponseQuery:
    def __init__(self, result):
        self.result = result

    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def execute(self):
        return self.result


def response_db(data, count, error=None):
    result = postgres_adapter.PostgrestResponse(data=data, count=count, error=error)
    return SimpleNamespace(table=lambda _name: ResponseQuery(result))


@pytest.mark.parametrize("query", ["", "?status=failed&type=send_email&search=123&from=2026-10-01&to=2026-10-21&page=2"])
async def test_actual_adapter_connection_failure_is_http503_not_empty_success(monkeypatch, query):
    async def unavailable(*_args, **_kwargs):
        raise ConnectionError("synthetic database unavailable; private connection detail")

    # Exercise the real QueryBuilder error envelope without any network connection.
    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", unavailable)
    response = await request(AdapterDB(), query)
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Action records are unavailable. Please retry."}
    assert "private connection detail" not in response.text


@pytest.mark.parametrize("data,count,error", [
    (None, None, None),
    ([], None, None),
    ([], -1, None),
    ([], True, None),
    ([], 0, "query failed after partial response"),
])
async def test_incomplete_or_failed_adapter_observation_is_not_reported_as_empty(data, count, error):
    response = await request(response_db(data, count, error))
    assert response.status_code == 503, response.text


async def test_confirmed_empty_result_remains_http200_with_exact_count():
    response = await request(response_db([], 0))
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


async def test_page_beyond_end_preserves_nonzero_exact_total():
    response = await request(response_db([], 25), "?page=3&page_size=20")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 25, "page": 3, "page_size": 20}


async def test_successful_action_response_serializes_actual_route_contract():
    identity, tenant = str(uuid4()), str(uuid4())
    row = {"id": identity, "tenant_id": tenant, "type": "send_email", "status": "failed",
           "created_at": "2026-10-21T00:00:00Z", "tenants": {"business_name": "Synthetic tenant"},
           "leads": {"first_name": "Synthetic", "last_name": "Person", "phone_number": "+15550000000"}}
    response = await request(response_db([row], 1))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1 and payload["page_size"] == 20
    assert payload["items"][0]["id"] == identity
    assert payload["items"][0]["tenant_id"] == tenant
    assert payload["items"][0]["lead_name"] == "Synthetic Person"
