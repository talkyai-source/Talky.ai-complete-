"""Durable intake concurrency and platform-only RLS on migrated PostgreSQL.

Only an explicitly supplied disposable *_test database is accepted. No Redis,
email, provider or customer call is used. Each test removes only its own UUIDs.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.api.v1.endpoints import contact_enquiries as api
from app.core.db_utils import acquire_with_tenant
from app.core.error_handlers import register_error_handlers
from app.domain.services import contact_enquiry_service as service

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def enquiry_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    if not urlparse(dsn).path.removeprefix("/").endswith("_test"):
        pytest.fail("Contact enquiry integration requires an explicit disposable *_test database")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6, timeout=5, command_timeout=10)
    request_ids = []

    def body(**changes):
        request_id = uuid4()
        request_ids.append(request_id)
        return service.ContactEnquiryInput(request_id=request_id, name="Synthetic Buyer",
            email="buyer@example.com", company="Example", message="An intake fixture.", **changes)

    fixture = SimpleNamespace(pool=pool, body=body, operator=uuid4())
    try:
        yield fixture
    finally:
        try:
            async with acquire_with_tenant(pool, None) as conn:
                await conn.execute("DELETE FROM public_contact_enquiries WHERE id=ANY($1::uuid[])", request_ids)
        finally:
            await pool.close()


async def test_http_submit_commit_admin_list_and_status_round_trip(enquiry_db, monkeypatch):
    fixture = enquiry_db
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.include_router(api.admin_router, prefix="/api/v1/admin")
    register_error_handlers(app)
    app.state.limiter = api.limiter
    monkeypatch.setattr(api.limiter, "enabled", False)
    app.dependency_overrides[get_db_pool] = lambda: fixture.pool
    # Synthetic platform operator: credential login is outside this DB test.
    # The actual require_platform_admin dependency and endpoints still execute.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(fixture.operator), email="operator@example.com", role="platform_admin")
    body = fixture.body()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        accepted = await client.post("/api/v1/public/contact-enquiries", json=body.model_dump(mode="json"))
        assert accepted.status_code == 201
        assert accepted.json()["receipt_id"] == str(body.request_id)
        async with acquire_with_tenant(fixture.pool, None) as independent:
            assert await independent.fetchval("SELECT COUNT(*) FROM public_contact_enquiries WHERE id=$1", body.request_id) == 1
        listed = await client.get("/api/v1/admin/contact-enquiries?status=new")
        assert listed.status_code == 200
        item = next(item for item in listed.json()["items"] if item["id"] == str(body.request_id))
        assert item["message"] == body.message and item["status"] == "new"
        handled = await client.patch(f"/api/v1/admin/contact-enquiries/{body.request_id}", json={"status": "handled"})
        assert handled.status_code == 200
        assert handled.json()["handled_at"]
        after = await client.get("/api/v1/admin/contact-enquiries?status=handled")
        assert any(item["id"] == str(body.request_id) for item in after.json()["items"])
        replay = await client.post("/api/v1/public/contact-enquiries", json=body.model_dump(mode="json"))
        assert replay.status_code == 200
        assert replay.json() == accepted.json()
        async with acquire_with_tenant(fixture.pool, None) as independent:
            row = await independent.fetchrow("SELECT status,handled_by FROM public_contact_enquiries WHERE id=$1", body.request_id)
        assert row["status"] == "handled" and row["handled_by"] == fixture.operator


async def test_receipt_is_committed_and_survives_lost_response_retry(enquiry_db):
    fixture = enquiry_db
    body = fixture.body()
    receipt, created = await service.submit_enquiry(fixture.pool, body)
    assert created is True
    async with acquire_with_tenant(fixture.pool, None) as independent:
        row = await independent.fetchrow("SELECT * FROM public_contact_enquiries WHERE id=$1", receipt["receipt_id"])
    assert row["id"] == body.request_id == receipt["receipt_id"]
    assert row["created_at"] == receipt["accepted_at"]
    assert row["status"] == "new"
    assert row["handled_at"] is None
    # A browser that never received the first response retries the same request.
    replay, created = await service.submit_enquiry(fixture.pool, body)
    assert created is False
    assert replay == receipt


async def test_concurrent_same_request_has_one_durable_row_and_receipt(enquiry_db):
    fixture = enquiry_db
    body = fixture.body()
    results = await asyncio.gather(*(service.submit_enquiry(fixture.pool, body) for _ in range(12)))
    assert sum(created for _, created in results) == 1
    assert all(receipt == results[0][0] for receipt, _ in results)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM public_contact_enquiries WHERE id=$1", body.request_id) == 1


async def test_same_identity_different_content_cannot_overwrite(enquiry_db):
    fixture = enquiry_db
    body = fixture.body()
    changed = body.model_copy(update={"message": "Different content."})
    results = await asyncio.gather(service.submit_enquiry(fixture.pool, body),
        service.submit_enquiry(fixture.pool, changed), return_exceptions=True)
    assert sum(isinstance(result, service.EnquiryConflict) for result in results) == 1
    winner = next(result for result in results if not isinstance(result, Exception))
    assert winner[1] is True
    async with acquire_with_tenant(fixture.pool, None) as conn:
        row = await conn.fetchrow("SELECT message FROM public_contact_enquiries WHERE id=$1", body.request_id)
    assert row["message"] in (body.message, changed.message)


async def test_admin_status_filter_pagination_and_handled_timestamp(enquiry_db):
    fixture = enquiry_db
    receipts = [(await service.submit_enquiry(fixture.pool, fixture.body()))[0] for _ in range(3)]
    page1 = await service.list_enquiries(fixture.pool, status="new", limit=2, offset=0)
    page2 = await service.list_enquiries(fixture.pool, status="new", limit=2, offset=2)
    expected = {receipt["receipt_id"] for receipt in receipts}
    assert page1["total"] == page2["total"] == 3
    assert {item["id"] for item in page1["items"] + page2["items"]} == expected
    receipt = receipts[0]
    item = await service.update_enquiry_status(fixture.pool, receipt["receipt_id"], "handled", fixture.operator)
    assert item["status"] == "handled" and item["handled_at"] is not None
    replay = await service.update_enquiry_status(fixture.pool, receipt["receipt_id"], "handled", uuid4())
    assert replay["handled_at"] == item["handled_at"]
    handled = await service.list_enquiries(fixture.pool, status="handled", limit=50, offset=0)
    assert [item["id"] for item in handled["items"]] == [receipt["receipt_id"]]
    reopened = await service.update_enquiry_status(fixture.pool, receipt["receipt_id"], "new", fixture.operator)
    assert reopened["handled_at"] is None
    assert await service.update_enquiry_status(fixture.pool, uuid4(), "handled", fixture.operator) is None


async def test_force_rls_denies_real_non_bypass_role(enquiry_db):
    fixture = enquiry_db
    body = fixture.body()
    receipt, _ = await service.submit_enquiry(fixture.pool, body)
    # Generated identifier only; never interpolate user input into role SQL.
    role = "enquiry_test_" + uuid4().hex
    async with fixture.pool.acquire() as conn:
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        try:
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            await conn.execute(f'GRANT SELECT, INSERT, UPDATE ON public_contact_enquiries TO "{role}"')
            async with conn.transaction():
                await conn.execute(f'SET LOCAL ROLE "{role}"')
                await conn.execute("SELECT set_config('app.bypass_rls','off',true)")
                await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(uuid4()))
                role_flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
                assert not role_flags["rolsuper"] and not role_flags["rolbypassrls"]
                assert await conn.fetchval("SELECT COUNT(*) FROM public_contact_enquiries") == 0
                assert await conn.execute("UPDATE public_contact_enquiries SET status='new' WHERE id=$1", receipt["receipt_id"]) == "UPDATE 0"
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.execute("""INSERT INTO public_contact_enquiries
                            (id,payload_hash,name,email,message) VALUES ($1,$2,'Forbidden','no@example.com','Not accepted')""",
                            uuid4(), "0" * 64)
            flags = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public_contact_enquiries'::regclass")
            assert flags["relrowsecurity"] and flags["relforcerowsecurity"]
        finally:
            await conn.execute(f'DROP OWNED BY "{role}"')
            await conn.execute(f'DROP ROLE "{role}"')
