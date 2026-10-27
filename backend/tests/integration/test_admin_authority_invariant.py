"""Real PostgreSQL controls for concurrent administrator authority changes.

Requires an explicitly designated disposable local migrated database. Each
fixture owns its rows and restricted role; existing application rows are never
modified. HTTP authentication and external audit/notification delivery remain
separate acceptance boundaries.
"""

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import AsyncMock

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
import pytest
import pytest_asyncio

from app.api.v1.endpoints.admin import users, user_operations
from app.api.v1.endpoints import suspensions as suspension_routes
from app.core.security import platform_admin_guard as guard
from app.domain.services import suspension_service as suspensions
from app.domain.services.audit_logger import AuditLogger


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def authority_db(monkeypatch):
    dsn = os.getenv("TEST_ADMIN_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("An explicit disposable TEST_ADMIN_DATABASE_URL is required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.endswith("_test")
    owner = await asyncpg.connect(dsn)
    role = "ad04_" + uuid4().hex
    tenant_id = uuid4()
    owned_users = []
    pool = None
    role_created = False
    try:
        # The invariant is global: do not silently use an unrelated administrator
        # to make an isolated last-admin fixture pass.
        assert await owner.fetchval(
            "SELECT count(*) FROM user_profiles WHERE role='platform_admin' AND is_active AND is_verified"
        ) == 0, "Use a dedicated disposable database without existing active administrators"
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        role_created = True
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON user_profiles,tenant_users,tenants,suspension_events TO "{role}"'
        )
        await owner.execute(f'GRANT SELECT ON roles TO "{role}"')
        await owner.execute(f'GRANT SELECT ON white_label_partners TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT ON audit_logs TO "{role}"')
        await owner.execute(
            f'GRANT SELECT,UPDATE ON security_sessions,refresh_tokens,mfa_challenges,webauthn_challenges TO "{role}"'
        )
        await owner.execute(
            "INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic AD04 authority fixture')", tenant_id
        )

        async def new_user(*, profile_role="platform_admin", verified=True, active=True):
            user_id = uuid4()
            owned_users.append(user_id)
            await owner.execute(
                """INSERT INTO user_profiles(id,email,tenant_id,role,is_active,is_verified,email_verified_at)
                   VALUES($1,$2,$3,$4,$5,$6,CASE WHEN $6 THEN NOW() ELSE NULL END)""",
                user_id, f"{user_id}@example.invalid", tenant_id, profile_role, active, verified,
            )
            await owner.execute(
                """INSERT INTO tenant_users(user_id,tenant_id,role_id,status,is_primary)
                   SELECT $1,$2,id,'active',TRUE FROM roles WHERE name='user'""", user_id, tenant_id,
            )
            return user_id

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout = '15s'")

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, setup=init)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert tuple(flags.values()) == (False, False)
        audit = AuditLogger(pool, signing_key="synthetic-ad04-local-fixture")
        service = suspensions.SuspensionService(pool, audit_logger=audit)
        monkeypatch.setattr(service, "_propagate_to_services", AsyncMock(return_value={"success": [], "failed": []}))
        monkeypatch.setattr(users, "hash_password", lambda _: "synthetic-unused-hash")
        db = SimpleNamespace(owner=owner, pool=pool, client=SimpleNamespace(pool=pool),
                             new_user=new_user, tenant_id=tenant_id, users=owned_users, role=role,
                             audit=audit, service=service)
        yield db
    finally:
        if pool:
            await pool.close()
        await owner.execute("DELETE FROM suspension_events WHERE target_id=ANY($1::uuid[])", owned_users)
        await owner.execute("DELETE FROM audit_logs WHERE actor_id=ANY($1::uuid[]) OR resource_id=ANY($1::uuid[])", owned_users)
        await owner.execute("UPDATE tenants SET suspended_by=NULL WHERE id=$1", tenant_id)
        await owner.execute("DELETE FROM tenant_users WHERE user_id=ANY($1::uuid[])", owned_users)
        # Real issuer regressions record attempts. Their FK becomes NULL when
        # profiles are removed, so delete only this fixture's attempts first.
        await owner.execute("DELETE FROM login_attempts WHERE user_id=ANY($1::uuid[])", owned_users)
        await owner.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[])", owned_users)
        await owner.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
        if role_created:
            await owner.execute(f'DROP OWNED BY "{role}"')
            await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


async def prepare_mutation(db, operation, actor, target, *, email=None):
    if operation in {"suspend", "restore"}:
        return None
    if operation == "create":
        intent = {"name": "Synthetic receipt", "email": email, "role": "platform_admin", "tenant_id": None}
        password = "Synthetic-unused-password"
        kind = "create"
    else:
        reviewed = await users._fetch_user(db.owner, str(target))
        assert reviewed is not None
        intent = {"expected_review_token": reviewed.review_token}
        if operation != "delete":
            intent.update({"is_active": False} if operation == "block" else {"role": "user"})
        kind = "delete" if operation == "delete" else "update"
        password = None
    prepared = await user_operations.prepare_user_operation(
        user_operations.PrepareUserOperationRequest(kind=kind, target_user_id=None if kind == "create" else target,
                                                    intent=intent, password=password),
        SimpleNamespace(id=str(actor), role="platform_admin"), db.client, db.audit,
    )
    body = {**intent, "operation_id": prepared.operation_id}
    if kind == "create":
        body.update(request_id=prepared.operation_id, password=password)
    return body


async def cancel_mutation(db, actor, body):
    result = await user_operations.cancel_user_operation(
        body["operation_id"], SimpleNamespace(id=str(actor), role="platform_admin"), db.client, db.audit)
    assert result.state == "cancelled"


async def mutate(db, operation, actor, target, *, prepared_body=None):
    principal = SimpleNamespace(id=str(actor), role="platform_admin")
    if operation == "suspend":
        return await db.service.suspend_user(
            target, suspensions.SuspensionType.ADMIN, "synthetic", "AD04 fixture",
            suspended_by=actor, notify_user=False,
        )
    body = prepared_body if prepared_body is not None else await prepare_mutation(db, operation, actor, target)
    if operation == "delete":
        return await users.delete_user(
            str(target), users.DeleteUserRequest(**body),
            principal, db.client, db.audit,
        )
    return await users.update_user(str(target), users.UpdateUserRequest(**body), principal, db.client, db.audit)


@pytest.mark.parametrize("first,second", [
    ("block", "block"), ("demote", "demote"), ("delete", "delete"),
    ("suspend", "block"), ("block", "suspend"),
])
async def test_concurrent_cross_removal_preserves_an_effective_admin(authority_db, monkeypatch, first, second):
    db = authority_db
    a, b = await db.new_user(), await db.new_user()
    first_locked, second_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    attempts = []
    real_lock = db.audit.lock_transaction
    first_body = await prepare_mutation(db, first, a, b)
    second_body = await prepare_mutation(db, second, b, a)

    async def scheduled_lock(conn):
        number = len(attempts)
        attempts.append(conn.get_server_pid())
        if number == 1:
            second_started.set()
        await real_lock(conn)
        if number == 0:
            first_locked.set()
            await asyncio.wait_for(release.wait(), 5)

    monkeypatch.setattr(db.audit, "lock_transaction", scheduled_lock)
    one = asyncio.create_task(mutate(db, first, a, b, prepared_body=first_body))
    two = None
    try:
        await asyncio.wait_for(first_locked.wait(), 5)
        two = asyncio.create_task(mutate(db, second, b, a, prepared_body=second_body))
        await asyncio.wait_for(second_started.wait(), 5)
        for _ in range(100):
            waiting = await db.owner.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND NOT granted)", attempts[1]
            )
            if waiting:
                break
            await asyncio.sleep(0.01)
        assert waiting, "Second real transaction must wait on the shared advisory lock"
        release.set()
        results = await asyncio.gather(one, two, return_exceptions=True)
        assert not isinstance(results[0], Exception), results[0]
        assert isinstance(results[1], HTTPException) and results[1].status_code == 403
        assert await db.owner.fetchval(
            "SELECT count(*) FROM user_profiles WHERE role='platform_admin' AND is_active AND is_verified"
        ) == 1
        survivor = await db.owner.fetchval(
            "SELECT id FROM user_profiles WHERE role='platform_admin' AND is_active AND is_verified"
        )
        assert survivor == a
    finally:
        release.set()
        await asyncio.gather(*(task for task in (one, two) if task), return_exceptions=True)


@pytest.mark.parametrize("operation", ["block", "demote", "delete"])
@pytest.mark.parametrize("state", ["inactive", "unverified", "demoted"])
async def test_stale_actor_authority_cannot_mutate_another_user(authority_db, operation, state):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    body = await prepare_mutation(db, operation, actor, target)
    change = {"inactive": "is_active=FALSE", "unverified": "is_verified=FALSE,email_verified_at=NULL", "demoted": "role='user'"}[state]
    await db.owner.execute(f"UPDATE user_profiles SET {change} WHERE id=$1", actor)
    with pytest.raises(HTTPException) as denied:
        await mutate(db, operation, actor, target, prepared_body=body)
    assert denied.value.status_code == 403
    row = await db.owner.fetchrow("SELECT role,is_active,is_verified FROM user_profiles WHERE id=$1", target)
    assert tuple(row.values()) == ("platform_admin", True, True)
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND action <> 'user_operation_prepared'", actor) == 0


async def test_unverified_assignment_cannot_be_the_only_surviving_admin(authority_db):
    db = authority_db
    valid = await db.new_user()
    await db.new_user(verified=False)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls='on'")
            await conn.execute("SET LOCAL app.current_tenant_id='00000000-0000-0000-0000-000000000000'")
            assert await guard.count_active_platform_admins(conn, exclude_id=valid) == 0
    with pytest.raises(HTTPException) as denied:
        await db.service.suspend_user(valid, suspensions.SuspensionType.ADMIN, "synthetic", "fixture", notify_user=False)
    assert denied.value.status_code == 400
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", valid)
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1", valid) == 0


async def test_lost_authority_while_waiting_cannot_create_an_admin(authority_db):
    db = authority_db
    actor, other = await db.new_user(), await db.new_user()
    email = f"{uuid4()}@example.com"
    body = await prepare_mutation(db, "create", actor, None, email=email)
    async with db.owner.transaction():
        await guard.lock_user_administration(db.owner)
        pending = asyncio.create_task(users.create_user(
            users.CreateUserRequest(**body),
            SimpleNamespace(id=str(actor)), db.client, db.audit,
        ))
        for _ in range(100):
            waits = await db.owner.fetchval("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted")
            if waits:
                break
            await asyncio.sleep(0.01)
        assert waits
        await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", actor)
    with pytest.raises(HTTPException) as denied:
        await asyncio.wait_for(pending, 5)
    assert denied.value.status_code == 403
    assert await db.owner.fetchval("SELECT count(*) FROM user_profiles WHERE email=$1", email) == 0
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", other)


async def test_fk_protected_delete_retains_membership_and_reports_deactivation(authority_db):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    await db.owner.execute("UPDATE tenants SET suspended_by=$1 WHERE id=$2", target, db.tenant_id)
    result = await mutate(db, "delete", actor, target)
    assert "deactivated" in result["detail"]
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target) is False
    assert await db.owner.fetchval("SELECT count(*) FROM tenant_users WHERE user_id=$1", target) == 1
    assert await db.owner.fetchval("SELECT action FROM audit_logs WHERE event_id=$1", result["audit_event_id"]) == "user_deactivated"


async def test_self_removal_stays_rejected(authority_db):
    db = authority_db
    actor = await db.new_user()
    await db.new_user()
    for operation in ("block", "demote", "delete", "suspend"):
        body = await prepare_mutation(db, operation, actor, actor)
        with pytest.raises(HTTPException) as denied:
            await mutate(db, operation, actor, actor, prepared_body=body)
        assert denied.value.status_code == 400
        if body is not None:
            await cancel_mutation(db, actor, body)
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", actor)


async def audit_mutation(db, operation, actor, target, email, *, prepared_body=None):
    if operation == "create":
        body = prepared_body if prepared_body is not None else await prepare_mutation(db, operation, actor, target, email=email)
        result = await users.create_user(
            users.CreateUserRequest(**body),
            SimpleNamespace(id=str(actor)), db.client, db.audit,
        )
        db.users.append(result.user_id)
        return result
    if operation == "restore":
        return await db.service.restore_user(target, actor, "Synthetic restoration")
    return await mutate(db, operation, actor, target, prepared_body=prepared_body)


@pytest.mark.parametrize("operation", ["create", "block", "demote", "delete", "suspend", "restore"])
async def test_required_audit_failure_rolls_back_user_mutation(authority_db, operation):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    email = f"{uuid4()}@example.com"
    if operation == "restore":
        await mutate(db, "suspend", actor, target)
    body = await prepare_mutation(db, operation, actor, target, email=email)
    before = await db.owner.fetchrow("SELECT * FROM user_profiles WHERE id=$1", target)
    before_memberships = await db.owner.fetch("SELECT * FROM tenant_users WHERE user_id=$1", target)
    before_suspensions = await db.owner.fetch("SELECT * FROM suspension_events WHERE target_id=$1", target)
    before_audit = await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", actor)
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await audit_mutation(db, operation, actor, target, email, prepared_body=body)
    assert await db.owner.fetchrow("SELECT * FROM user_profiles WHERE id=$1", target) == before
    assert await db.owner.fetch("SELECT * FROM tenant_users WHERE user_id=$1", target) == before_memberships
    assert await db.owner.fetch("SELECT * FROM suspension_events WHERE target_id=$1", target) == before_suspensions
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", actor) == before_audit
    assert await db.owner.fetchval("SELECT count(*) FROM user_profiles WHERE email=$1", email) == 0


@pytest.mark.parametrize("operation", ["create", "block", "demote", "delete", "suspend", "restore"])
async def test_returned_user_receipt_is_already_durable(authority_db, operation):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    if operation == "restore":
        await mutate(db, "suspend", actor, target)
    result = await audit_mutation(db, operation, actor, target, f"{uuid4()}@example.com")
    receipt = result["audit_event_id"] if isinstance(result, dict) else result.audit_event_id
    saved = await db.owner.fetchrow("SELECT * FROM audit_logs WHERE event_id=$1", receipt)
    assert saved is not None and saved["actor_id"] == actor
    assert saved["resource_type"] == "user"
    assert len(saved["entry_hash"]) == 64 and len(saved["signature"]) == 64
    if operation == "suspend":
        assert await db.owner.fetchval("SELECT audit_log_id FROM suspension_events WHERE suspension_id=$1",
                                      result.suspension_id) == saved["event_id"]
    if operation == "delete":
        assert await db.owner.fetchval("SELECT id FROM user_profiles WHERE id=$1", target) is None
        assert saved["action"] == "user_deleted"


async def test_duplicate_formal_suspension_is_a_conflict_with_no_extra_effect(authority_db):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    original = await mutate(db, "suspend", actor, target)
    with pytest.raises(HTTPException) as denied:
        await mutate(db, "suspend", actor, target)
    assert denied.value.status_code == 409
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1", target) == 1
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1", target) == 1
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", original.suspension_id)


async def test_concurrent_duplicate_formal_suspensions_have_one_committed_winner(authority_db):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    results = await asyncio.gather(mutate(db, "suspend", actor, target),
                                   mutate(db, "suspend", actor, target), return_exceptions=True)
    assert sum(isinstance(item, suspensions.SuspensionResult) for item in results) == 1
    assert sum(isinstance(item, HTTPException) and item.status_code == 409 for item in results) == 1
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1", target) == 1


async def test_ambiguous_historical_holds_cannot_be_silently_restored(authority_db):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user()
    await mutate(db, "suspend", actor, target)
    await db.owner.execute("""INSERT INTO suspension_events
        (suspension_id,target_type,target_id,suspension_type,reason_category,reason_description,is_active)
        VALUES($1,'user',$2,'COMPLIANCE','synthetic','Separate historical hold',TRUE)""", uuid4(), target)
    with pytest.raises(HTTPException) as denied:
        await db.service.restore_user(target, actor, "Synthetic restore")
    assert denied.value.status_code == 409 and "multiple" in denied.value.detail
    assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target)
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1 AND is_active", target) == 2
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1", target) == 1


async def test_already_blocked_account_cannot_acquire_a_restorable_formal_hold(authority_db):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user(profile_role="user", active=False)
    with pytest.raises(HTTPException) as denied:
        await mutate(db, "suspend", actor, target)
    assert denied.value.status_code == 409
    assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target)
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1", target) == 0


async def test_restore_without_formal_hold_is_an_actionable_conflict(authority_db):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user(profile_role="user", active=False)
    with pytest.raises(HTTPException) as denied:
        await db.service.restore_user(target, actor, "Synthetic restore")
    assert denied.value.status_code == 409
    assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target)


@pytest.mark.parametrize("state", ["inactive", "unverified", "demoted"])
async def test_stale_actor_cannot_restore_a_formal_hold(authority_db, state):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user(profile_role="user")
    await mutate(db, "suspend", actor, target)
    change = {"inactive": "is_active=FALSE", "unverified": "is_verified=FALSE,email_verified_at=NULL", "demoted": "role='user'"}[state]
    await db.owner.execute(f"UPDATE user_profiles SET {change} WHERE id=$1", actor)
    with pytest.raises(HTTPException) as denied:
        await db.service.restore_user(target, actor, "Synthetic restore")
    assert denied.value.status_code == 403
    assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target)


@pytest.mark.parametrize("restoration", ["direct", "appeal"])
@pytest.mark.parametrize("delivery_fails", [False, True])
async def test_existing_http_routes_require_formal_restore_before_unblock(authority_db, restoration, delivery_fails, monkeypatch):
    # Only HTTP principal admission is synthetic; endpoint/service/database and
    # current-authority recheck use the real code and restricted-role pool.
    db = authority_db
    actor, target = await db.new_user(), await db.new_user(profile_role="user")
    if delivery_fails:
        monkeypatch.setattr(db.service, "_propagate_to_services", AsyncMock(side_effect=RuntimeError("Synthetic post-commit delivery failure")))
    from app.api.v1.dependencies import CurrentUser
    principal = CurrentUser(id=str(actor), email="synthetic@example.invalid", role="platform_admin")
    app = FastAPI()
    app.dependency_overrides[users.require_platform_admin] = lambda: principal
    app.dependency_overrides[users.get_db_client] = lambda: db.client
    app.dependency_overrides[users.get_audit_logger] = lambda: db.audit
    app.dependency_overrides[suspension_routes.get_suspension_service] = lambda: db.service
    for route in suspension_routes.router.routes:
        if route.name not in {"suspend_user", "restore_user", "review_appeal"}:
            continue
        for dependency in route.dependant.dependencies:
            if dependency.name == "current_user":
                app.dependency_overrides[dependency.call] = lambda: principal
    app.include_router(user_operations.router, prefix="/admin")
    app.include_router(users.router, prefix="/admin")
    app.include_router(suspension_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as http:
        suspended = await http.post(f"/admin/suspensions/users/{target}/suspend", json={
            "suspension_type": "ADMIN", "reason_category": "synthetic",
            "reason_description": "Synthetic route fixture", "notify_user": False,
        })
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["audit_event_id"]
        assert bool(suspended.json()["propagation_failed"]) is delivery_fails
        reviewed = await http.get(f"/admin/users/{target}")
        assert reviewed.status_code == 200
        intent = {"is_active": True, "expected_review_token": reviewed.json()["review_token"]}
        prepared = await http.post("/admin/users/operations", json={
            "kind": "update", "target_user_id": str(target), "intent": intent})
        assert prepared.status_code == 201, prepared.text
        blocked = await http.patch(f"/admin/users/{target}", json={
            **intent, "operation_id": prepared.json()["operation_id"]})
        assert blocked.status_code == 409 and "formal suspension" in blocked.json()["detail"]
        cancelled = await http.post(f"/admin/users/operations/{prepared.json()['operation_id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
        assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target)
        if restoration == "direct":
            restored = await http.post(f"/admin/suspensions/users/{target}/restore", json={"reason": "Reviewed fixture"})
        else:
            restored = await http.post(f"/admin/suspensions/{suspended.json()['suspension_id']}/appeal/review",
                                       json={"decision": "granted", "response": "Reviewed fixture"})
        assert restored.status_code == 200 and restored.json()["audit_event_id"]
        assert bool(restored.json()["propagation_failed"]) is delivery_fails
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1 AND action NOT IN ('user_operation_prepared','user_operation_cancelled')", target) == 2
        reviewed = await http.get(f"/admin/users/{target}")
        assert reviewed.status_code == 200
        intent = {"is_active": True, "expected_review_token": reviewed.json()["review_token"]}
        prepared = await http.post("/admin/users/operations", json={
            "kind": "update", "target_user_id": str(target), "intent": intent})
        assert prepared.status_code == 201, prepared.text
        unblocked = await http.patch(f"/admin/users/{target}", json={
            **intent, "operation_id": prepared.json()["operation_id"]})
        assert unblocked.status_code == 200 and unblocked.json()["audit_event_id"]


@pytest.mark.parametrize("callback", ["scheduled", "appeal"])
async def test_delayed_original_hold_callback_cannot_restore_replacement(authority_db, monkeypatch, callback):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user(profile_role="user")
    original = await db.service.suspend_user(
        target, suspensions.SuspensionType.TEMPORARY, "synthetic", "Original hold",
        suspended_by=actor, duration_hours=1, notify_user=False,
    )
    await db.owner.execute("UPDATE suspension_events SET suspended_until=NOW()-INTERVAL '1 minute' WHERE suspension_id=$1", original.suspension_id)
    entered, release = asyncio.Event(), asyncio.Event()
    real_restore = db.service.restore_user

    async def delayed_restore(*args, **kwargs):
        assert str(kwargs["expected_suspension_id"]) == str(original.suspension_id)
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        return await real_restore(*args, **kwargs)

    monkeypatch.setattr(db.service, "restore_user", delayed_restore)
    if callback == "scheduled":
        pending = asyncio.create_task(db.service.process_auto_restores())
    else:
        pending = asyncio.create_task(db.service.review_appeal(original.suspension_id, actor, "granted", "Reviewed original hold"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # Simulate a previously completed restoration followed by a new hold
        # while the original scheduled/review request is delayed.
        await db.owner.execute("UPDATE suspension_events SET is_active=FALSE,restored_at=NOW() WHERE suspension_id=$1", original.suspension_id)
        replacement = uuid4()
        await db.owner.execute("""INSERT INTO suspension_events
            (suspension_id,target_type,target_id,suspension_type,reason_category,reason_description,is_active)
            VALUES($1,'user',$2,'COMPLIANCE','synthetic','Replacement hold',TRUE)""", replacement, target)
        release.set()
        if callback == "scheduled":
            outcomes = await pending
            assert next(item for item in outcomes if item["suspension_id"] == str(original.suspension_id))["restored"] is False
        else:
            with pytest.raises(HTTPException) as denied:
                await pending
            assert denied.value.status_code == 409
        assert not await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target)
        assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", replacement)
        assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1", original.suspension_id) is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("audit_fails", [False, True])
async def test_granted_user_appeal_and_restore_share_required_audit_commit(authority_db, audit_fails):
    db = authority_db
    actor, target = await db.new_user(), await db.new_user(profile_role="user")
    original = await mutate(db, "suspend", actor, target)
    if audit_fails:
        await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.service.review_appeal(original.suspension_id, actor, "granted", "Reviewed hold")
    else:
        result = await db.service.review_appeal(original.suspension_id, actor, "granted", "Reviewed hold")
        assert result["restored"] is True
        assert await db.owner.fetchval("SELECT event_id FROM audit_logs WHERE event_id=$1", result["audit_event_id"])
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", target) is (not audit_fails)
    event = await db.owner.fetchrow("SELECT is_active,appeal_decision FROM suspension_events WHERE suspension_id=$1", original.suspension_id)
    assert event["is_active"] is audit_fails
    assert event["appeal_decision"] == (None if audit_fails else "granted")


@pytest.mark.parametrize("operation", ["suspend", "restore", "appeal"])
@pytest.mark.parametrize("failure", ["absent_queue", "notification", "timeout", "receipt"])
async def test_post_commit_delivery_failure_returns_committed_user_receipt(authority_db, monkeypatch, operation, failure):
    db=authority_db
    actor,target=await db.new_user(),await db.new_user(profile_role="user")
    original=None
    if operation!="suspend":
        original=await mutate(db,"suspend",actor,target)
    real=suspensions.SuspensionService._propagate_to_services.__get__(db.service)
    monkeypatch.setattr(db.service,"_propagate_to_services",real)
    async def publish(*_):
        if failure=="notification":
            raise RuntimeError("Synthetic notification unavailable")
        if failure=="timeout":
            await asyncio.Future()
        return 0
    db.service.redis=SimpleNamespace(publish=publish)
    if failure=="receipt":
        monkeypatch.setattr(db.service,"_propagate_to_services",AsyncMock(side_effect=RuntimeError("Synthetic enforcement receipt unavailable")))
    if operation=="appeal":
        result=await db.service.review_appeal(original.suspension_id,actor,"granted","Reviewed fixture")
    elif operation=="restore":
        result=(await db.service.restore_user(target,actor,"Reviewed fixture")).model_dump()
    else:
        result=(await mutate(db,"suspend",actor,target)).model_dump()
    assert result["audit_event_id"]
    assert await db.owner.fetchval("SELECT event_id FROM audit_logs WHERE event_id=$1",result["audit_event_id"])
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1",target) is (operation!="suspend")
    assert result["propagated_to"]==[]
    if failure=="receipt":
        assert result["propagation_failed"]==["assistant_session_check"]
        assert result["propagation_pending"]==[]
    else:
        assert result["propagation_pending"]==["assistant_session_check"]
        assert result["propagation_failed"]==(["suspension_events"] if failure in {"notification","timeout"} else [])
    assert await db.owner.fetchval("SELECT to_regclass('public.suspension_propagation_queue')") is None
    if operation=="appeal":
        assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1",original.suspension_id)=="granted"
