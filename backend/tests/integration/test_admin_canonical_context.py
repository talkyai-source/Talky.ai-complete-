"""Real auth and restricted PostgreSQL proof of canonical adapter context.

The normal application routes, middleware, JWT/MFA/session checks and fluent SQL
adapter run unchanged. A temporary authenticated observation route exposes only
owned IDs and safe context fields, allowing RLS to be tested independently of a
platform-only endpoint's role gate. No principal or query result is mocked.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import copy
import os
import re
from uuid import uuid4

from fastapi import Depends, Request
import httpx
import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import API_ORIGIN, ORIGIN, journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call, crud_db, reviewed_write, signed_in,  # noqa: F401
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def context_db(crud_db, monkeypatch):  # noqa: F811
    from app.api.v1 import dependencies
    from app.core import postgres_adapter
    from app.core.security import tenant_isolation as scope

    db = crud_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT ON crm_deliveries,connectors TO "{role}"')
    db.context_calls = [uuid4() for _ in db.tenants]
    db.context_campaigns = [uuid4() for _ in db.tenants]
    db.context_evidence = {"role": role, "adapter_reads": [], "observations": [],
                           "test_observation_route": True, "auth_overridden": False}
    RUN_EVIDENCE.append(db.context_evidence)
    restricted_dsn = os.environ["TEST_ADMIN_DATABASE_URL"] + "?role=" + role
    monkeypatch.setattr(postgres_adapter, "_DATABASE_URL", restricted_dsn)
    original_execute = postgres_adapter.QueryBuilder._execute_with_conn

    async def observed_execute(query, conn):
        # Observe the actual adapter connection after its own SET LOCAL calls.
        row = dict(await conn.fetchrow("""SELECT current_user AS role,rolsuper,rolbypassrls,
            current_setting('app.current_tenant_id',TRUE) AS tenant_id,
            current_setting('app.bypass_rls',TRUE) AS bypass
            FROM pg_roles WHERE rolname=current_user"""))
        assert row["role"] == role and not row["rolsuper"] and not row["rolbypassrls"]
        db.context_evidence["adapter_reads"].append({"table": query.table_name, **row})
        return await original_execute(query, conn)

    monkeypatch.setattr(postgres_adapter.QueryBuilder, "_execute_with_conn", observed_execute)
    db.context_gate = False
    db.context_entered = {str(user): asyncio.Event() for user in db.users}
    db.context_release = asyncio.Event()
    db.context_path = "/__test__/canonical-context-" + uuid4().hex
    original_routes = list(db.app.router.routes)

    async def observation(request: Request, user=Depends(dependencies.get_current_user)):
        if db.context_gate:
            db.context_entered[user.id].set()
            await asyncio.wait_for(db.context_release.wait(), 5)
        context_user = scope.get_current_user_context()
        safe_context = {"id": user.id, "tenant_id": scope.get_current_tenant_id(),
            "bypass": scope.get_bypass_rls(), "role": user.role,
            "context_user_id": getattr(context_user, "id", None),
            "context_user_role": getattr(context_user, "role", None),
            "state_tenant_id": getattr(request.state, "tenant_id", None),
            "state_user_id": getattr(request.state, "user_id", None),
            "state_user_role": getattr(request.state, "user_role", None),
            "state_target_tenant_id": getattr(request.state, "target_tenant_id", None),
            "state_cross_tenant_access": getattr(request.state, "cross_tenant_access", False)}
        result = dependencies.get_db_client().table("calls").select("id,tenant_id").in_(
            "id", [str(value) for value in db.context_calls]).order("id").execute()
        assert result.error is None
        safe_context["visible"] = result.data
        db.context_evidence["observations"].append(safe_context)
        return safe_context

    db.app.add_api_route("/api/v1" + db.context_path, observation, methods=["GET"], include_in_schema=False)
    try:
        for index, tenant in enumerate(db.tenants):
            await db.owner.execute("INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic canonical context')", db.context_campaigns[index], tenant)
            await db.owner.execute("""INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status)
                VALUES($1,$2,$3,'+15555550001','completed')""", db.context_calls[index], tenant, db.context_campaigns[index])
        flags = await db.owner.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='calls'::regclass")
        assert flags["relrowsecurity"] and flags["relforcerowsecurity"]
        yield db
    finally:
        db.context_release.set()
        db.app.router.routes[:] = original_routes
        async with db.owner.transaction():
            for table in ("crm_deliveries", "call_events", "call_legs"):
                await db.owner.execute(f"DELETE FROM {table} WHERE call_id=ANY($1::uuid[])", db.context_calls)
            await db.owner.execute("DELETE FROM calls WHERE id=ANY($1::uuid[]) AND tenant_id=ANY($2::uuid[])", db.context_calls, db.tenants)
            await db.owner.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[]) AND tenant_id=ANY($2::uuid[])", db.context_campaigns, db.tenants)


@asynccontextmanager
async def transported(db, original, mode):
    """Use only credentials actually minted by normal password/MFA endpoints."""
    cookies = httpx.Cookies()
    names = {"access-cookie": {"talky_at"}, "legacy-sid": {"talky_sid"},
             "combined": {"talky_at", "talky_rt", "talky_sid"}, "bearer": set()}[mode]
    for cookie in original.cookies.jar:
        if cookie.name in names:
            cookies.jar.set_cookie(copy(cookie))
    headers = {"Origin": ORIGIN, "User-Agent": "Synthetic-Admin-CRUD"}
    if mode in {"bearer", "combined"}:
        headers["Authorization"] = original.headers["Authorization"]
    transport = httpx.ASGITransport(app=db.app, client=(db.client_ip, 59104), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=API_ORIGIN, headers=headers, cookies=cookies) as client:
        yield client


def assert_context(db, body, index, role, platform=False, null_tenant=False):
    tenant = None if null_tenant else str(db.tenants[index])
    assert body["id"] == body["context_user_id"] == body["state_user_id"] == str(db.users[index])
    assert body["role"] == body["context_user_role"] == body["state_user_role"] == role
    assert body["tenant_id"] == body["state_tenant_id"] == tenant
    assert body["bypass"] is platform
    expected = db.context_calls if platform else [db.context_calls[index]]
    assert {row["id"] for row in body["visible"]} == set(map(str, expected))


@pytest.mark.parametrize("mode", ["bearer", "access-cookie", "combined", "legacy-sid"])
async def test_normal_admin_transport_has_actual_cross_tenant_adapter_context(context_db, mode):
    db = context_db
    async with signed_in(db) as issued, transported(db, issued, mode) as client:
        detail = await call(db, client, "GET", f"/admin/calls/{db.context_calls[1]}")
        if detail.status_code != 200:
            payload = detail.json()
            error = payload.get("error")
            db.context_evidence["failed_detail"] = {
                "status": detail.status_code,
                "detail": payload.get("detail") if isinstance(payload.get("detail"), str) else None,
                "error_message": error.get("message") if isinstance(error, dict) else None,
            }
        assert detail.status_code == 200
        assert detail.json()["id"] == str(db.context_calls[1]) and detail.json()["tenant_id"] == str(db.tenants[1])
        observed = await call(db, client, "GET", db.context_path)
        assert observed.status_code == 200
        assert_context(db, observed.json(), 0, "platform_admin", platform=True)


@pytest.mark.parametrize("mode", ["bearer", "access-cookie", "combined", "legacy-sid"])
async def test_normal_member_transport_is_tenant_scoped_in_real_adapter(context_db, mode):
    db = context_db
    async with signed_in(db, 1) as issued, transported(db, issued, mode) as client:
        assert (await call(db, client, "GET", f"/admin/calls/{db.context_calls[2]}")).status_code == 403
        observed = await call(db, client, "GET", db.context_path)
        assert observed.status_code == 200
        assert_context(db, observed.json(), 1, "tenant_admin")


async def test_null_primary_admin_cookie_has_no_stale_tenant_and_can_read(context_db):
    db = context_db
    await db.owner.execute("UPDATE user_profiles SET tenant_id=NULL WHERE id=$1", db.users[0])
    async with signed_in(db) as issued, transported(db, issued, "access-cookie") as client:
        observed = await call(db, client, "GET", db.context_path)
        assert observed.status_code == 200
        assert_context(db, observed.json(), 0, "platform_admin", platform=True, null_tenant=True)
        assert (await call(db, client, "GET", f"/admin/calls/{db.context_calls[1]}")).status_code == 200


async def test_actual_admin_demotion_overwrites_old_jwt_bypass_and_role(context_db):
    db = context_db
    await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[2])
    async with signed_in(db) as issued, signed_in(db, 2) as authority, transported(db, issued, "bearer") as old:
        changed = await reviewed_write(db, authority, "PATCH", db.users[0], role="readonly", expected_role="platform_admin")
        assert changed.status_code == 200 and changed.json()["audit_event_id"]
        from app.core.jwt_security import decode_and_validate_token
        assert decode_and_validate_token(old.headers["Authorization"].split()[1])["role"] == "platform_admin"
        observed = await call(db, old, "GET", db.context_path)
        assert observed.status_code == 200
        assert_context(db, observed.json(), 0, "readonly")
        assert (await call(db, old, "GET", f"/admin/calls/{db.context_calls[1]}")).status_code == 403


@pytest.mark.parametrize("conflict", ["access-cookie", "legacy-sid", "expected-identity"])
async def test_identity_conflicts_never_reach_adapter_or_publish_context(context_db, conflict):
    db = context_db
    async with signed_in(db) as admin, signed_in(db, 1) as member, transported(db, admin, "bearer") as client:
        expected = 409
        if conflict == "expected-identity":
            client.headers["X-Talky-Expected-User"] = str(db.users[1])
        else:
            name = "talky_at" if conflict == "access-cookie" else "talky_sid"
            for cookie in member.cookies.jar:
                if cookie.name == name:
                    client.cookies.jar.set_cookie(copy(cookie))
            if conflict == "legacy-sid":
                expected = 401
        before = len(db.context_evidence["adapter_reads"])
        response = await call(db, client, "GET", db.context_path)
        assert response.status_code == expected and "set-cookie" not in response.headers
        assert len(db.context_evidence["adapter_reads"]) == before
        client.cookies.clear()
        client.headers.pop("X-Talky-Expected-User", None)
        clean = await call(db, client, "GET", db.context_path)
        assert clean.status_code == 200
        assert_context(db, clean.json(), 0, "platform_admin", platform=True)


@pytest.mark.parametrize("revocation", ["blocked", "session"])
async def test_revoked_credentials_cannot_reach_adapter(context_db, revocation):
    db = context_db
    async with signed_in(db) as admin, transported(db, admin, "bearer") as client:
        if revocation == "blocked":
            await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", db.users[0])
        else:
            await db.owner.execute("UPDATE security_sessions SET revoked=TRUE,revoked_at=NOW(),revoke_reason='Synthetic context test' WHERE user_id=$1", db.users[0])
        before = len(db.context_evidence["adapter_reads"])
        response = await call(db, client, "GET", db.context_path)
        assert response.status_code == 401 and len(db.context_evidence["adapter_reads"]) == before


async def test_concurrent_cookie_admin_and_bearer_members_keep_independent_scope(context_db):
    db = context_db
    async with signed_in(db) as a, signed_in(db, 1) as b, signed_in(db, 2) as c:
        async with transported(db, a, "access-cookie") as admin, transported(db, b, "bearer") as member, transported(db, c, "legacy-sid") as other:
            db.context_gate = True
            tasks = [asyncio.create_task(call(db, client, "GET", db.context_path)) for client in (admin, member, other)]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in db.context_entered.values())), 5)
                db.context_release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
                assert all(response.status_code == 200 for response in results)
                assert_context(db, results[0].json(), 0, "platform_admin", platform=True)
                assert_context(db, results[1].json(), 1, "tenant_admin")
                assert_context(db, results[2].json(), 2, "tenant_admin")
            finally:
                db.context_release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


async def test_missing_call_stays_404_for_canonical_cookie_admin(context_db):
    db = context_db
    async with signed_in(db) as issued, transported(db, issued, "access-cookie") as client:
        response = await call(db, client, "GET", f"/admin/calls/{uuid4()}")
        assert response.status_code == 404


async def test_ordinary_account_without_primary_tenant_rejects_before_adapter(context_db):
    db = context_db
    async with signed_in(db, 1) as issued, transported(db, issued, "legacy-sid") as client:
        # Retain a real prior session, but remove the account's canonical tenant.
        # Legacy SID ensures rejection is not merely an old JWT tenant mismatch.
        await db.owner.execute("UPDATE user_profiles SET tenant_id=NULL WHERE id=$1", db.users[1])
        reads = len(db.context_evidence["adapter_reads"])
        observations = len(db.context_evidence["observations"])
        response = await call(db, client, "GET", db.context_path)
        assert response.status_code == 401
        assert len(db.context_evidence["adapter_reads"]) == reads
        assert len(db.context_evidence["observations"]) == observations


async def test_ordinary_foreign_target_header_does_not_widen_adapter_scope(context_db):
    db = context_db
    async with signed_in(db, 1) as issued, transported(db, issued, "bearer") as client:
        client.headers["X-Tenant-ID"] = str(db.tenants[2])
        response = await call(db, client, "GET", db.context_path)
        assert response.status_code == 200
        body = response.json()
        assert_context(db, body, 1, "tenant_admin")
        assert body["state_target_tenant_id"] is None
        assert body["state_cross_tenant_access"] is False
        assert (await call(db, client, "GET", f"/admin/calls/{db.context_calls[2]}")).status_code == 403


async def test_partner_admin_remains_scoped_with_cookie_and_bearer(context_db):
    db = context_db
    partner_role = await db.owner.fetchval("SELECT id FROM roles WHERE name='partner_admin'")
    assert partner_role is not None, "Canonical migrated partner_admin role is required"
    async with db.owner.transaction():
        await db.owner.execute("UPDATE user_profiles SET role='partner_admin' WHERE id=$1", db.users[1])
        changed = await db.owner.execute("UPDATE tenant_users SET role_id=$1 WHERE user_id=$2 AND tenant_id=$3", partner_role, db.users[1], db.tenants[1])
        assert changed == "UPDATE 1"
    async with signed_in(db, 1) as issued:
        for mode in ("access-cookie", "bearer"):
            async with transported(db, issued, mode) as client:
                client.headers["X-Tenant-ID"] = str(db.tenants[2])
                response = await call(db, client, "GET", db.context_path)
                assert response.status_code == 200
                assert_context(db, response.json(), 1, "partner_admin")
                assert response.json()["state_target_tenant_id"] is None
                assert (await call(db, client, "GET", f"/admin/calls/{db.context_calls[2]}")).status_code == 403
