"""Owned normal-auth HTTP CRUD contracts on the existing migrated PG fixture."""
from contextlib import asynccontextmanager
import re
from uuid import UUID, uuid4

import httpx
import asyncpg
import pyotp
import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import API_ORIGIN, ORIGIN, journey_db

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def crud_db(journey_db, monkeypatch):  # noqa: F811
    db = journey_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT ON billing_ledger,topup_orders,topup_packages,tenant_quotas TO "{role}"')
    await db.owner.execute(f'GRANT INSERT,DELETE ON user_profiles,tenant_users TO "{role}"')
    await db.owner.execute(f'GRANT INSERT ON tenants,tenant_quotas TO "{role}"')
    db.owned_emails = []
    db.created_tenants = []
    db.owned_tenant_names = []
    db.client_ip = "127.0.1." + str(1 + uuid4().int % 250)
    db.report["database_errors"] = []
    for method in ("execute", "fetch", "fetchrow", "fetchval"):
        original = getattr(asyncpg.Connection, method)
        async def observed(conn, query, *args, _original=original, **kwargs):
            try:
                return await _original(conn, query, *args, **kwargs)
            except Exception as exc:
                db.report["database_errors"].append({"type": type(exc).__name__,
                    "sqlstate": getattr(exc, "sqlstate", None), "table": getattr(exc, "table_name", None),
                    "operation": query.strip().split()[0]})
                raise
        monkeypatch.setattr(asyncpg.Connection, method, observed)
    try:
        yield db
    finally:
        # Include even unexpectedly successful baseline creates in the exact
        # parent's owned cleanup. A hard delete can null login_attempts.user_id.
        created = await db.owner.fetch("SELECT id FROM user_profiles WHERE email=ANY($1::text[])", db.owned_emails)
        db.users.extend(row["id"] for row in created if row["id"] not in db.users)
        created_tenants = await db.owner.fetch(
            "SELECT id FROM tenants WHERE business_name=ANY($1::text[])", db.owned_tenant_names)
        for row in created_tenants:
            if row["id"] not in db.tenants:
                db.tenants.append(row["id"])
            if row["id"] not in db.created_tenants:
                db.created_tenants.append(row["id"])
        await db.owner.execute("DELETE FROM login_attempts WHERE email=ANY($1::text[])", db.owned_emails)
        await db.owner.execute("DELETE FROM tenant_quotas WHERE tenant_id=ANY($1::uuid[])", db.created_tenants)


async def call(db, client, method, path, **kwargs):
    response = await client.request(method, "/api/v1" + path, **kwargs)
    entry = {"method": method, "path": path, "status": response.status_code}
    if response.status_code >= 400:
        error = response.json().get("error")
        if isinstance(error, dict):
            entry["error_code"] = error.get("code")
            entry["error_message"] = error.get("message")
        detail = response.json().get("detail")
        if isinstance(detail, str):
            entry["error_detail"] = detail[:200]
        elif isinstance(detail, dict):
            entry["error_code"] = detail.get("code")
    db.report["steps"].append(entry)
    assert response.headers.get("x-request-id")
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.headers.get("access-control-allow-credentials") == "true"
    return response


@asynccontextmanager
async def signed_in(db, index=0):
    transport = httpx.ASGITransport(app=db.app, client=(db.client_ip, 59104), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=API_ORIGIN,
                                headers={"Origin": ORIGIN, "User-Agent": "Synthetic-Admin-CRUD"}) as client:
        response = await call(db, client, "POST", "/auth/login", json={
            "email": f"{db.users[index]}@example.com", "password": db.password})
        assert response.status_code == 200
        if index == 0:
            assert response.json()["mfa_required"] is True
            response = await call(db, client, "POST", "/auth/mfa/verify", json={
                "challenge_token": response.json()["mfa_challenge_token"], "code": pyotp.TOTP(db.totp).now()})
            assert response.status_code == 200
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        me = await call(db, client, "GET", "/auth/me")
        assert me.status_code == 200 and me.json()["id"] == str(db.users[index])
        yield client


def create_body(db, **changes):
    email = f"{uuid4()}@example.com"
    db.owned_emails.append(email)
    request_id = str(uuid4())
    return {"request_id": request_id, "operation_id": request_id, "name": "Synthetic created account", "email": email, "password": db.password,
            "role": "tenant_admin", "tenant_id": str(db.tenants[1]), **changes}


async def prepare_write(db, client, method, path, body):
    """Explicit preparation for positive/reviewed paths; generic call stays raw."""
    body = dict(body)
    intent = {k:v for k,v in body.items() if k not in {"operation_id","request_id","password"}}
    create = method == "POST" and path == "/admin/users"
    kind = "create" if create else "primary_membership" if path.endswith("/primary-membership") else "delete" if method == "DELETE" else "update"
    target = None if create else path.split("/")[3]
    if create:
        intent.setdefault("tenant_id",None)
        intent.setdefault("role","user")
    payload = {"kind":kind,"target_user_id":target,"intent":intent}
    if create:payload["password"] = body["password"]
    response = await call(db,client,"POST","/admin/users/operations",json=payload)
    assert response.status_code == 201, response.text
    body["operation_id"] = response.json()["operation_id"]
    if create:body["request_id"] = body["operation_id"]
    return body


async def prepared_call(db, client, method, path, *, json):
    return await call(db,client,method,path,json=await prepare_write(db,client,method,path,json))


async def cancel_prepared(db, client, body):
    response = await call(db,client,"POST",f"/admin/users/operations/{body['operation_id']}/cancel")
    assert response.status_code == 200 and response.json()["state"] == "cancelled"


async def reviewed_write(db, client, method, target, **changes):
    row = await call(db, client, "GET", f"/admin/users/{target}")
    assert row.status_code == 200
    return await prepared_call(db, client, method, f"/admin/users/{target}",
                      json={"expected_review_token": row.json()["review_token"], **changes})


async def audit(db, response, action):
    event = await db.owner.fetchrow("SELECT * FROM audit_logs WHERE event_id=$1", UUID(response.json()["audit_event_id"]))
    assert event and event["action"] == action
    return event


@pytest.mark.parametrize("changes", [
    {"password": "a" * 129}, {"password": "a" * 200},
    {"name": "   "}, {"name": "\t\n"}, {"tenant_id": "not-a-uuid"},
], ids=["password129", "password200", "spaces-name", "whitespace-name", "malformed-tenant"])
async def test_create_invalid_input_is_422_without_user_or_receipt(crud_db, changes):
    db = crud_db
    body = create_body(db, **changes)
    async with signed_in(db) as admin:
        response = await call(db, admin, "POST", "/admin/users", json=body)
        created = await db.owner.fetchval("SELECT id FROM user_profiles WHERE email=$1", body["email"])
        count = await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND action='user_created'", db.users[0])
        RUN_EVIDENCE.append({"case": "invalid_create", "field": next(iter(changes)),
                             "status": response.status_code, "user_created": created is not None, "create_receipts": count})
        assert response.status_code == 422
        assert created is None and count == 0


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_malformed_user_id_is_422(crud_db, method):
    db = crud_db
    async with signed_in(db) as admin:
        body = {"operation_id": str(uuid4()), "expected_review_token": "v1:" + "0" * 64}
        if method == "PATCH":
            body["name"] = "Synthetic harmless name"
        response = await call(db, admin, method, "/admin/users/not-a-uuid",
                              **({"json": body} if method != "GET" else {}))
        RUN_EVIDENCE.append({"case": "malformed_user_id", "method": method, "status": response.status_code})
        assert response.status_code == 422


async def test_created_user_can_login_and_current_role_block_restore_and_delete_are_real(crud_db):
    db = crud_db
    body = create_body(db, password="a" * 128)
    async with signed_in(db) as admin:
        created = await prepared_call(db, admin, "POST", "/admin/users", json=body)
        assert created.status_code == 201
        user = UUID(created.json()["user_id"])
        db.users.append(user)
        assert created.json()["user"]["effective_role"] == "tenant_admin"
        assert created.json()["user"]["tenant_id"] == str(db.tenants[1]) and created.json()["user"]["is_verified"]
        await audit(db, created, "user_created")
        duplicate_body = await prepare_write(db,admin,"POST","/admin/users",{**body,"email":body["email"].upper()})
        duplicate = await call(db, admin, "POST", "/admin/users", json=duplicate_body)
        assert duplicate.status_code == 409
        await cancel_prepared(db,admin,duplicate_body)
        transport = httpx.ASGITransport(app=db.app, client=(db.client_ip, 59105), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=API_ORIGIN, headers={"Origin": ORIGIN}) as member:
            login = await call(db, member, "POST", "/auth/login", json={"email": body["email"], "password": body["password"]})
            assert login.status_code == 200
            member.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            changed = await reviewed_write(db, admin, "PATCH", user, role="readonly", expected_role="tenant_admin")
            assert changed.status_code == 200
            await audit(db, changed, "user_updated")
            me = await call(db, member, "GET", "/auth/me")
            assert me.status_code == 200 and me.json()["role"] == "readonly"
            blocked = await reviewed_write(db, admin, "PATCH", user, is_active=False)
            assert blocked.status_code == 200
            await audit(db, blocked, "user_updated")
            assert (await call(db, member, "GET", "/auth/me")).status_code == 401
            restored = await reviewed_write(db, admin, "PATCH", user, is_active=True)
            assert restored.status_code == 200
            # The ordinary block does not revoke every credential in its write.
            # Existing cookie validation revokes a session observed while blocked.
            assert (await call(db, member, "GET", "/auth/me")).status_code == 401
            member.headers.pop("Authorization")
            login = await call(db, member, "POST", "/auth/login", json={"email": body["email"], "password": body["password"]})
            assert login.status_code == 200
            member.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            assert (await call(db, member, "GET", "/auth/me")).status_code == 200
            removed = await reviewed_write(db, admin, "DELETE", user)
            assert removed.status_code == 200
            event = await db.owner.fetchrow("SELECT action FROM audit_logs WHERE event_id=$1", UUID(removed.json()["audit_event_id"]))
            assert event["action"] in ("user_deleted", "user_deactivated")
            stored = await db.owner.fetchrow("SELECT is_active FROM user_profiles WHERE id=$1", user)
            assert stored is None or stored["is_active"] is False
            assert (await call(db, member, "GET", "/auth/me")).status_code == 401
            RUN_EVIDENCE.append({"case": "user_crud_auth", "delete_action": event["action"],
                                 "current_role_and_block_enforced": True, "blocked_observed_session_stays_revoked": True,
                                 "fresh_login_after_ordinary_restore": True})


async def test_stale_reviewed_role_cannot_overwrite_second_admin_change(crud_db):
    db = crud_db
    async with signed_in(db) as first:
        promoted = await reviewed_write(db, first, "PATCH", db.users[2], role="platform_admin")
        assert promoted.status_code == 200
        reviewed = await call(db, first, "GET", f"/admin/users/{db.users[1]}")
        assert reviewed.json()["role"] == "tenant_admin"
        async with signed_in(db, 2) as second:
            changed = await prepared_call(db, second, "PATCH", f"/admin/users/{db.users[1]}",
                                 json={"expected_review_token": reviewed.json()["review_token"], "role": "readonly", "expected_role": reviewed.json()["role"]})
            assert changed.status_code == 200
            stale = await prepared_call(db, first, "PATCH", f"/admin/users/{db.users[1]}",
                               json={"expected_review_token": reviewed.json()["review_token"], "name": "Synthetic stale edit", "role": "user", "expected_role": reviewed.json()["role"]})
            stored = await db.owner.fetchrow("SELECT name,role FROM user_profiles WHERE id=$1", db.users[1])
            receipts = await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1 AND action='user_updated'", db.users[1])
            RUN_EVIDENCE.append({"case": "stale_role", "status": stale.status_code, "stored_role": stored["role"], "target_receipts": receipts})
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "user_changed"
            assert stored["role"] == "readonly" and stored["name"] == "Synthetic HTTP user" and receipts == 1


async def test_tenantless_admin_demotion_stays_explicit_conflict(crud_db):
    db = crud_db
    body = create_body(db, role="platform_admin", tenant_id=None)
    async with signed_in(db) as admin:
        created = await prepared_call(db, admin, "POST", "/admin/users", json=body)
        assert created.status_code == 201
        user = UUID(created.json()["user_id"])
        db.users.append(user)
        response = await reviewed_write(db, admin, "PATCH", user, role="readonly", expected_role="platform_admin")
        assert response.status_code == 409
        assert await db.owner.fetchval("SELECT role FROM user_profiles WHERE id=$1", user) == "platform_admin"
        RUN_EVIDENCE.append({"case": "tenantless_demotion", "status": response.status_code, "membership_not_invented": True})


async def test_expected_role_uses_current_membership_assignment(crud_db):
    db = crud_db
    await db.owner.execute("UPDATE user_profiles SET role='user' WHERE id=$1", db.users[1])
    async with signed_in(db) as admin:
        reviewed = await call(db, admin, "GET", f"/admin/users/{db.users[1]}")
        assert reviewed.json()["role"] == "tenant_admin"
        changed = await prepared_call(db, admin, "PATCH", f"/admin/users/{db.users[1]}",
                             json={"expected_review_token": reviewed.json()["review_token"], "role": "readonly", "expected_role": reviewed.json()["role"]})
        assert changed.status_code == 200 and changed.json()["role"] == "readonly"
        await audit(db, changed, "user_updated")
        RUN_EVIDENCE.append({"case": "canonical_membership_role", "status": changed.status_code})


async def test_tenant_create_quota_version_and_subscription_receipts(crud_db):
    db = crud_db
    plan = await db.owner.fetchval("SELECT id FROM plans ORDER BY id LIMIT 1")
    assert plan is not None
    name = "Synthetic CRUD " + uuid4().hex
    db.owned_tenant_names.append(name)
    async with signed_in(db) as admin:
        created = await call(db, admin, "POST", "/admin/tenants", json={"business_name": name, "plan_id": plan,
                            "minutes_allocated": 50, "max_concurrent_calls": 3})
        assert created.status_code == 201
        assert created.json()["plan_id"] == plan and created.json()["minutes_allocated"] == 50
        target = UUID(created.json()["id"])
        db.tenants.append(target)
        db.created_tenants.append(target)
        await audit(db, created, "tenant_created")
        assert await db.owner.fetchval("SELECT count(*) FROM tenant_quotas WHERE tenant_id=$1", target) == 1
        replay = await call(db, admin, "POST", "/admin/tenants", json={"business_name": name, "plan_id": plan})
        assert replay.status_code == 200 and replay.json()["created"] is False and replay.json()["id"] == str(target)
        before = (await call(db, admin, "GET", f"/admin/tenants/{target}")).json()
        quota = await call(db, admin, "PATCH", f"/admin/tenants/{target}/quota", json={
            "expected_updated_at": before["updated_at"], "minutes_allocated": 80, "max_concurrent_calls": 4})
        assert quota.status_code == 200 and quota.json()["minutes_allocated"] == 80
        await audit(db, quota, "tenant_quota_updated")
        stale = await call(db, admin, "POST", f"/admin/tenants/{target}/archive", json={"expected_updated_at": before["updated_at"]})
        assert stale.status_code == 409
        version = quota.json()["updated_at"]
        for operation, expected in [("suspend", "suspended"), ("resume", "active"), ("archive", "cancelled"), ("resume", "active")]:
            response = await call(db, admin, "POST", f"/admin/tenants/{target}/{operation}", json={"expected_updated_at": version})
            assert response.status_code == 200 and response.json()["status"] == expected
            assert await db.owner.fetchval("SELECT subscription_status FROM tenants WHERE id=$1", target) == expected
            assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", target) == "active"
            assert await db.owner.fetchval("SELECT 1 FROM audit_logs WHERE event_id=$1", UUID(response.json()["audit_event_id"])) == 1
            version = response.json()["updated_at"]
        RUN_EVIDENCE.append({"case": "tenant_crud", "quota_receipt_and_stale_conflict": True,
                             "subscription_transitions": 4, "formal_account_status_unchanged": True})


@pytest.mark.parametrize("plan_fields", [{}, {"plan_id": None}], ids=["omitted-plan", "null-plan"])
async def test_tenant_default_is_planless_zero_with_explicit_quota_authority(crud_db, plan_fields):
    db = crud_db
    name = "Synthetic planless " + uuid4().hex
    db.owned_tenant_names.append(name)
    async with signed_in(db) as admin:
        created = await call(db, admin, "POST", "/admin/tenants", json={"business_name": name, **plan_fields})
        assert created.status_code == 201, created.text
        target = UUID(created.json()["id"])
        db.tenants.append(target)
        db.created_tenants.append(target)
        assert created.json()["plan_id"] is None and created.json()["plan_name"] is None
        assert created.json()["minutes_allocated"] == created.json()["minutes_used"] == 0
        await audit(db, created, "tenant_created")
        persisted = await db.owner.fetchrow(
            "SELECT plan_id,minutes_allocated,minutes_used FROM tenants WHERE id=$1", target)
        assert dict(persisted) == {"plan_id": None, "minutes_allocated": 0, "minutes_used": 0}
        assert await db.owner.fetchval("SELECT count(*) FROM tenant_quotas WHERE tenant_id=$1", target) == 1
        reviewed = await call(db, admin, "GET", f"/admin/tenants/{target}")
        assert reviewed.status_code == 200 and reviewed.json()["plan_id"] is None
        assert reviewed.json()["minutes_allocated"] == 0
        quota = await call(db, admin, "PATCH", f"/admin/tenants/{target}/quota", json={
            "expected_updated_at": reviewed.json()["updated_at"], "minutes_allocated": 80})
        assert quota.status_code == 200 and quota.json()["minutes_allocated"] == 80
        await audit(db, quota, "tenant_quota_updated")
        replay = await call(db, admin, "POST", "/admin/tenants", json={"business_name": name})
        assert replay.status_code == 200 and replay.json()["created"] is False
        assert replay.json()["plan_id"] is None and replay.json()["minutes_allocated"] == 80
        assert await db.owner.fetchval(
            "SELECT count(*) FROM audit_logs WHERE tenant_id=$1 AND action='tenant_created'", target) == 1
        RUN_EVIDENCE.append({"case": "planless_tenant_default", "explicit_null": bool(plan_fields),
                             "persisted_zero_allowance": True, "explicit_quota_retained_on_replay": True})


async def test_default_create_replay_preserves_existing_legacy_tenant(crud_db):
    db = crud_db
    target = db.tenants[2]
    assert await db.owner.fetchval("SELECT id FROM plans WHERE id='free'") == "free"
    await db.owner.execute(
        "UPDATE tenants SET plan_id='free',minutes_allocated=30,minutes_used=7 WHERE id=$1", target)
    before = await db.owner.fetchval("SELECT to_jsonb(t)::text FROM tenants t WHERE id=$1", target)
    name = await db.owner.fetchval("SELECT business_name FROM tenants WHERE id=$1", target)
    async with signed_in(db) as admin:
        replay = await call(db, admin, "POST", "/admin/tenants", json={"business_name": name})
        assert replay.status_code == 200 and replay.json()["created"] is False
        assert replay.json()["id"] == str(target) and replay.json()["plan_id"] == "free"
        assert replay.json()["minutes_allocated"] == 30 and replay.json()["minutes_used"] == 7
        assert await db.owner.fetchval("SELECT to_jsonb(t)::text FROM tenants t WHERE id=$1", target) == before
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", target) == 0
        RUN_EVIDENCE.append({"case": "existing_legacy_tenant_replay", "row_unchanged": True,
                             "created_audits": 0})
