"""F2-13: validate real HTTP handlers before storage, with no provider/DB calls."""
import importlib
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, StreamingResponse

from app.api.v1 import dependencies
from app.api.v1.endpoints import calls, campaigns, clients, contact_enquiries
from app.api.v1.endpoints.admin import calls as admin_calls
from app.core.api_security_middleware import APISecurityMiddleware
from app.core.error_handlers import register_error_handlers
from tests.unit.test_campaign_visibility_w5 import (
    CAMPAIGN, TENANT, _FakeDB, _campaign_row, _patch_update_dependencies,
)

profile = importlib.import_module("app.api.v1.endpoints.auth.profile")
USER_ID = "aabbccdd-1234-4abc-8def-0123456789ab"
HUGE = 10 ** 30


class Storage:
    """Adapter-shaped recorder: invalid UUIDs/ranges fail as storage would."""
    def __init__(self):
        self.events = []
        self.pool = self

    def table(self, name):
        self.events.append(("table", name))
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        if column in {"id", "tenant_id", "campaign_id", "call_id"}:
            UUID(str(value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self.events.append(("range", start, end))
        if start < 0 or end < start or end > 2 ** 63 - 1:
            raise ValueError("Synthetic storage pagination failure")
        return self

    def execute(self):
        return SimpleNamespace(data=[], count=0, error=None)

    @asynccontextmanager
    async def acquire(self):
        self.events.append(("acquire",))
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, *_args):
        return None

    async def fetchval(self, *_args):
        return 0

    async def fetch(self, *_args):
        return []


def mounted(endpoint, path, *, method="GET", storage=None):
    """Use the real handler/signature; auth has already completed synthetically."""
    storage = storage or Storage()
    app = FastAPI()
    register_error_handlers(app)
    user = dependencies.CurrentUser(id=USER_ID, email="synthetic@example.invalid",
        tenant_id=TENANT, role="platform_admin", name="Before", business_name="Synthetic")
    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    app.dependency_overrides[dependencies.require_platform_admin] = lambda: user
    app.dependency_overrides[dependencies.get_db_client] = lambda: storage
    app.dependency_overrides[dependencies.get_db_read_client] = lambda: storage
    app.dependency_overrides[dependencies.get_db_pool] = lambda: storage.pool
    app.dependency_overrides[dependencies.get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())
    if hasattr(campaigns, "idempotency_dependency"):
        app.dependency_overrides[campaigns.idempotency_dependency] = lambda: None
    app.add_api_route(path, endpoint, methods=[method])
    # Some handlers wrap authentication with a permission/tenant dependency.
    # This test begins after those checks, with a known authorized identity.
    for dependency in app.routes[-1].dependant.dependencies:
        if dependency.name in {"current_user", "admin_user"}:
            app.dependency_overrides[dependency.call] = lambda: user
    return app, storage


async def request(app, method, path, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                base_url="http://synthetic") as client:
        return await asyncio.wait_for(client.request(method, path, **kwargs), timeout=2)


@pytest.mark.parametrize("name", ["x" * 256, "bad\x00name", "bad\ud800name"],
                         ids=["overlength", "nul", "surrogate"])
async def test_profile_rejects_invalid_name_before_any_database_write(monkeypatch, name):
    writes = []

    class ProfileConnection:
        @asynccontextmanager
        async def transaction(self):
            yield self

        async def execute(self, sql, *args):
            writes.append((sql, args))
            args[0].encode("utf-8", errors="strict")

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield ProfileConnection()

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    app, _ = mounted(profile.update_me, "/api/v1/auth/me", method="PATCH")
    response = await request(app, "PATCH", "/api/v1/auth/me",
        content=json.dumps({"name": name}, ensure_ascii=True), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert writes == [], "Invalid Unicode/length must never reach the database"


def campaign_body(**overrides):
    return {"name": "Synthetic", "persona_type": "lead_gen", "agent_names": ["Sarah"],
            "company_name": "Synthetic", **overrides}


@pytest.mark.parametrize("field,value", [("name", "x" * 256), ("description", "x" * 100001),
    ("goal", "x" * 100001), ("system_prompt", "x" * 100001), ("company_name", "x" * 100001)],
    ids=["name", "description", "goal", "system_prompt", "company_name"])
async def test_campaign_free_text_is_bounded_before_storage(field, value):
    app, storage = mounted(campaigns.create_campaign, "/api/v1/campaigns/", method="POST")
    response = await request(app, "POST", "/api/v1/campaigns/", json=campaign_body(**{field: value}))
    assert response.status_code == 422, response.text
    assert storage.events == []


@pytest.mark.parametrize("params", [{"page": 0}, {"page": -1}, {"page_size": 0},
    {"page_size": -1}, {"page_size": 201}, {"page": HUGE}])
async def test_admin_history_rejects_invalid_pagination_before_query(params):
    app, storage = mounted(admin_calls.get_call_history, "/api/v1/admin/calls/history")
    response = await request(app, "GET", "/api/v1/admin/calls/history", params=params)
    assert response.status_code == 422, response.text
    assert storage.events == []


async def test_admin_history_first_page_still_queries_normally():
    app, storage = mounted(admin_calls.get_call_history, "/api/v1/admin/calls/history")
    response = await request(app, "GET", "/api/v1/admin/calls/history", params={"page": 1, "page_size": 20})
    assert response.status_code == 200, response.text
    assert ("range", 0, 19) in storage.events


@pytest.mark.parametrize("params", [{"offset": HUGE}, {"offset": -1}, {"limit": 0}, {"limit": 201}])
async def test_admin_enquiry_limit_and_offset_are_bounded_before_service(monkeypatch, params):
    query = AsyncMock(side_effect=RuntimeError("Unexpected storage access"))
    monkeypatch.setattr(contact_enquiries.service, "list_enquiries", query)
    app, _ = mounted(contact_enquiries.list_contact_enquiries, "/api/v1/admin/contact-enquiries")
    response = await request(app, "GET", "/api/v1/admin/contact-enquiries", params=params)
    assert response.status_code == 422, response.text
    query.assert_not_awaited()


@pytest.mark.parametrize("endpoint,path", [
    (campaigns.get_campaign, "/api/v1/campaigns/{campaign_id}"),
    (calls.get_call, "/api/v1/calls/{call_id}"),
    (calls.get_call_events, "/api/v1/calls/{call_id}/events"),
    (admin_calls.get_admin_call_detail, "/api/v1/admin/calls/{call_id}"),
    (clients.get_client, "/api/v1/clients/{client_id}"),
])
async def test_business_path_ids_reject_malformed_uuid_before_storage(endpoint, path):
    app, storage = mounted(endpoint, path)
    actual_path = path.format(campaign_id="not-a-uuid", call_id="not-a-uuid", client_id="not-a-uuid")
    response = await request(app, "GET", actual_path)
    assert response.status_code == 422, response.text
    assert storage.events == []


async def test_maximum_campaign_version_is_validation_error_not_datetime_overflow(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row()
    row.update(name="Before", updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    app, _ = mounted(campaigns.update_campaign, "/api/v1/campaigns/{campaign_id}",
                     method="PUT", storage=_FakeDB([row]))
    response = await request(app, "PUT", f"/api/v1/campaigns/{CAMPAIGN}", json=campaign_body(
        expected_updated_at="9999-12-31T23:59:59.999999Z"))
    assert response.status_code == 422, response.text
    assert row["name"] == "Before"


@pytest.mark.parametrize("path,kind", [("/api/v1/private", "json"),
    ("/api/v1/auth/me", "json"), ("/api/v1/private", "error"),
    ("/api/v1/private", "stream")])
async def test_authenticated_responses_override_cacheable_headers(path, kind):
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(APISecurityMiddleware)

    @app.get(path)
    async def authenticated(request: Request):
        # The real auth dependency sets this after checking the identity.
        request.state.authenticated_user_id = USER_ID
        if kind == "error":
            raise HTTPException(409, "Synthetic conflict")
        if kind == "stream":
            async def chunks():
                yield b"private stream"
            return StreamingResponse(chunks(), headers={"Cache-Control": "public, max-age=3600"})
        return JSONResponse({"private": True}, headers={"Cache-Control": "public, max-age=3600"})

    response = await request(app, "GET", path)
    assert response.status_code == (409 if kind == "error" else 200), response.text
    assert response.headers.get("Cache-Control") == "no-store"
    if kind == "stream":
        assert response.content == b"private stream"


async def test_public_response_does_not_get_misclassified_as_authenticated():
    app = FastAPI()
    app.add_middleware(APISecurityMiddleware)

    @app.get("/health")
    async def public_health():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "public, max-age=30"})

    response = await request(app, "GET", "/health")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=30"
