"""Prepared recovery through normal MFA/current-authority HTTP on restricted PG.

Only explicitly owned disposable fixtures are used. No handler, auth, principal,
or audit dependency is replaced. Fault tests identify their injected boundary.
"""
# ruff: noqa: F811 -- pytest discovers the imported fixture by its parameter name.
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.api.v1.endpoints.admin import user_operations, users
from app.core.db_utils import acquire_with_tenant
from app.domain.services.audit_logger import AuditLogger
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call, create_body, crud_db, prepare_write, signed_in,
)

pytestmark = pytest.mark.integration
BASE = "/admin/users/operations"


async def update_body(db, client, target=None, **changes):
    target = target or db.users[1]
    response = await call(db, client, "GET", f"/admin/users/{target}")
    assert response.status_code == 200
    body = {"expected_review_token": response.json()["review_token"], "name": "Prepared actual change", **changes}
    return await prepare_write(db, client, "PATCH", f"/admin/users/{target}", body)


async def test_actual_prepare_discovery_commit_and_original_snapshot_after_current_change(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        listed = await call(db, admin, "GET", BASE)
        assert listed.status_code == 200 and listed.json()["items"][0]["operation_id"] == body["operation_id"]
        response = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body)
        assert response.status_code == 200, response.text
        original = response.json()
        second = await update_body(db, admin, name="Later independently reviewed name")
        assert (await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=second)).status_code == 200
        recovered = await call(db, admin, "GET", BASE + "/" + body["operation_id"])
        assert recovered.status_code == 200 and recovered.json()["receipt"] == original
        replay = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body)
        assert replay.status_code == 200 and replay.json() == original
        assert await db.owner.fetchval("SELECT name FROM user_profiles WHERE id=$1", db.users[1]) == "Later independently reviewed name"
        count = await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'user_operation_id'=$1", body["operation_id"])
        assert count == 1


async def test_actual_cancel_retirement_rejects_delayed_update_and_actor_pending_slot_reopens(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        old = await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1])
        cancelled = await call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
        repeated = await call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel")
        assert repeated.json() == cancelled.json()
        late = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body)
        assert late.status_code == 409
        assert await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1]) == old
        next_body = await update_body(db, admin)
        assert next_body["operation_id"] != body["operation_id"]


async def test_actual_create_password_mismatch_then_exact_commit_and_cancel_returns_saved_receipt(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        body = await prepare_write(db, admin, "POST", "/admin/users", create_body(db))
        wrong = await call(db, admin, "POST", "/admin/users", json={**body, "password": "Different-synthetic-password!27"})
        assert wrong.status_code == 409
        assert await db.owner.fetchval("SELECT count(*) FROM user_profiles WHERE email=$1", body["email"]) == 0
        saved = await call(db, admin, "POST", "/admin/users", json=body)
        assert saved.status_code == 201, saved.text
        cancelled = await call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "committed"
        assert cancelled.json()["receipt"] == saved.json()
        rows = await db.owner.fetch("SELECT metadata FROM audit_logs WHERE actor_id=$1", db.users[0])
        assert db.password not in json.dumps([dict(row) for row in rows], default=str)


async def test_missing_or_forged_preparation_never_runs_any_existing_mutation(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        reviewed = (await call(db, admin, "GET", f"/admin/users/{db.users[1]}")).json()
        invented = str(uuid4())
        cases = [
            ("POST", "/admin/users", create_body(db, request_id=invented, operation_id=invented)),
            ("PATCH", f"/admin/users/{db.users[1]}", {"operation_id": invented, "expected_review_token": reviewed["review_token"], "name": "Forged change"}),
            ("DELETE", f"/admin/users/{db.users[1]}", {"operation_id": invented, "expected_review_token": reviewed["review_token"]}),
            ("POST", f"/admin/users/{db.users[1]}/primary-membership", {"operation_id": invented,
                "tenant_id": str(db.tenants[1]), "role": "readonly", "expected_review_token": "pm1:" + "a" * 64}),
        ]
        before = await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1])
        for method, path, body in cases:
            forged = await call(db, admin, method, path, json=body)
            assert forged.status_code == 404, forged.text
            absent = await call(db, admin, method, path, json={k:v for k,v in body.items() if k != "operation_id"})
            assert absent.status_code == 422
        assert await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1]) == before
        assert await db.owner.fetchval("SELECT count(*) FROM user_profiles WHERE email=$1", cases[0][2]["email"]) == 0
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND metadata ? 'user_operation_id'", db.users[0]) == 0


async def test_renewed_credentials_discover_pending_but_foreign_actor_cannot_read_cancel_or_execute(crud_db):
    db = crud_db
    async with signed_in(db) as admin, signed_in(db, 1) as foreign:
        body = await update_body(db, admin, target=db.users[2])
        # Owned fixture role setup only; normal current-principal reads now see
        # this existing session's administrator assignment, without an override.
        await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[1])
        assert (await call(db, foreign, "GET", "/auth/me")).json()["role"] == "platform_admin"
        assert (await call(db, foreign, "GET", BASE)).json()["items"] == []
        assert (await call(db, foreign, "GET", BASE + "/" + body["operation_id"])).status_code == 404
        assert (await call(db, foreign, "POST", BASE + "/" + body["operation_id"] + "/cancel")).status_code == 404
        assert (await call(db, foreign, "PATCH", f"/admin/users/{db.users[2]}", json=body)).status_code == 404
        other = await update_body(db, foreign, target=db.users[2], name="Other actor's reviewed name")
        assert other["operation_id"] != body["operation_id"]
        # Renewed credentials on the same authenticated session, with no
        # operation ID supplied to discovery. This is not a browser reload proof.
        refreshed = await call(db, admin, "POST", "/auth/refresh")
        assert refreshed.status_code == 200
        admin.headers["Authorization"] = "Bearer " + refreshed.json()["access_token"]
        listed = await call(db, admin, "GET", BASE)
        assert listed.json()["items"][0]["operation_id"] == body["operation_id"]


async def test_two_simultaneous_preparations_allow_only_one_pending_for_actor(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        reviewed = (await call(db, admin, "GET", f"/admin/users/{db.users[1]}")).json()
        payload = {"kind": "update", "target_user_id": str(db.users[1]),
                   "intent": {"expected_review_token": reviewed["review_token"], "name": "Concurrent reviewed name"}}
        responses = await asyncio.gather(*(call(db, admin, "POST", BASE, json=payload) for _ in range(2)))
        assert sorted(response.status_code for response in responses) == [201, 409]
        saved = next(response.json() for response in responses if response.status_code == 201)
        listed = await call(db, admin, "GET", BASE)
        assert [item["operation_id"] for item in listed.json()["items"]] == [saved["operation_id"]]
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND action='user_operation_prepared'", db.users[0]) == 1


async def test_expired_real_db_window_denies_dispatch_but_explicit_cancel_is_required(crud_db, monkeypatch):
    db = crud_db
    # Test-only shortened configured interval; timestamps and passage of time
    # remain PostgreSQL's actual clock. No token or clock is rewritten.
    monkeypatch.setattr(user_operations, "DISPATCH_WINDOW", timedelta(milliseconds=150))
    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        before = await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1])
        await asyncio.sleep(0.2)
        view = await call(db, admin, "GET", BASE + "/" + body["operation_id"])
        assert view.json()["state"] == "prepared" and view.json()["dispatch_eligible"] is False
        assert (await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body)).status_code == 409
        assert await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1]) == before
        blocked = await call(db, admin, "POST", BASE, json={"kind": "delete", "target_user_id": str(db.users[1]),
            "intent": {"expected_review_token": body["expected_review_token"]}})
        assert blocked.status_code == 409
        cancelled = await call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel")
        assert cancelled.json()["state"] == "cancelled"


async def test_cancel_before_delayed_request_takes_locks_fences_actual_effect(crud_db, monkeypatch):
    db = crud_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = users.acquire_with_tenant

    @asynccontextmanager
    async def pause_before_user_locks(*args, **kwargs):
        async with original(*args, **kwargs) as conn:
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
            yield conn

    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        before = await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1])
        monkeypatch.setattr(users, "acquire_with_tenant", pause_before_user_locks)
        delayed = asyncio.create_task(call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            cancelled = await call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
        finally:
            release.set()
        rejected = await asyncio.wait_for(delayed, 5)
        assert rejected.status_code == 409
        assert await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1]) == before


async def test_committing_mutation_wins_cancel_race_and_cancel_returns_exact_saved_receipt(crud_db, monkeypatch):
    db = crud_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = AuditLogger.log_in_transaction

    async def pause_before_terminal_insert(self, conn, **kwargs):
        if kwargs.get("action") == "user_updated":
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original(self, conn, **kwargs)

    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        monkeypatch.setattr(AuditLogger, "log_in_transaction", pause_before_terminal_insert)
        mutation = asyncio.create_task(call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body))
        cancellation = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            cancellation = asyncio.create_task(call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel"))
            await asyncio.sleep(0.05)
            assert not cancellation.done()
        finally:
            release.set()
        saved = await asyncio.wait_for(mutation, 5)
        cancelled = await asyncio.wait_for(cancellation, 5)
        assert saved.status_code == 200 and cancelled.status_code == 200
        assert cancelled.json()["state"] == "committed" and cancelled.json()["receipt"] == saved.json()
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'user_operation_id'=$1", body["operation_id"]) == 1


async def test_required_terminal_audit_failure_rolls_back_actual_update_and_keeps_prepared(crud_db, monkeypatch):
    db = crud_db
    original = AuditLogger.log_in_transaction
    entered = []

    async def fail_terminal(self, conn, **kwargs):
        if kwargs.get("action") == "user_updated":
            entered.append(await conn.fetchval("SELECT name FROM user_profiles WHERE id=$1", db.users[1]))
            raise RuntimeError("Synthetic required terminal audit failure")
        return await original(self, conn, **kwargs)

    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        before = await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1])
        monkeypatch.setattr(AuditLogger, "log_in_transaction", fail_terminal)
        failed = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=body)
        assert failed.status_code == 500 and entered == [body["name"]]
        assert await db.owner.fetchrow("SELECT name,admin_revision FROM user_profiles WHERE id=$1", db.users[1]) == before
        listed = await call(db, admin, "GET", BASE)
        assert listed.json()["items"][0]["state"] == "prepared"
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'user_operation_id'=$1", body["operation_id"]) == 0


async def test_actual_global_terminal_index_blocks_second_cancel_or_commit_even_other_actor(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        saved = await call(db, admin, "POST", BASE + "/" + body["operation_id"] + "/cancel")
        assert saved.status_code == 200
        row = await db.owner.fetchrow("SELECT * FROM audit_logs WHERE event_id=$1", UUID(saved.json()["terminal_audit_event_id"]))
        # Real restricted-role INSERT must hit0076, without relying on helper
        # locks. The conflicting row is rolled back, so the audit chain stays intact.
        async with acquire_with_tenant(db.pool, None) as conn:
            with pytest.raises(asyncpg.UniqueViolationError) as duplicate:
                async with conn.transaction():
                    await conn.execute("""INSERT INTO audit_logs(event_id,event_time,event_type,event_category,severity,
                        actor_id,actor_type,resource_type,resource_id,action,metadata)
                        VALUES($1,clock_timestamp(),$2,$3,$4,$5,'user','user',$6,'user_updated',$7::jsonb)""",
                        uuid4(), row["event_type"], row["event_category"], row["severity"], db.users[1], db.users[1], row["metadata"])
            assert duplicate.value.constraint_name == "audit_user_operation_terminal_unique"
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'user_operation_id'=$1", body["operation_id"]) == 1


async def test_original_create_receipt_survives_actual_target_delete_and_terminal_pagination(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        body = await prepare_write(db, admin, "POST", "/admin/users", create_body(db))
        saved = await call(db, admin, "POST", "/admin/users", json=body)
        assert saved.status_code == 201
        target = saved.json()["user_id"]
        delete = await prepare_write(db, admin, "DELETE", f"/admin/users/{target}",
            {"expected_review_token": saved.json()["user"]["review_token"]})
        deleted = await call(db, admin, "DELETE", f"/admin/users/{target}", json=delete)
        assert deleted.status_code == 200 and deleted.json()["outcome"] == "deleted", deleted.text
        assert await db.owner.fetchval("SELECT id FROM user_profiles WHERE id=$1", UUID(target)) is None
        original = await call(db, admin, "GET", BASE + "/" + body["operation_id"])
        assert original.json()["receipt"] == saved.json()
        first = await call(db, admin, "GET", BASE, params={"state": "terminal", "limit": 1})
        assert first.status_code == 200 and first.json()["next_cursor"]
        second = await call(db, admin, "GET", BASE, params={"state": "terminal", "limit": 1, "cursor": first.json()["next_cursor"]})
        assert second.status_code == 200 and second.json()["next_cursor"] is None
        ids = {first.json()["items"][0]["operation_id"], second.json()["items"][0]["operation_id"]}
        assert ids == {body["operation_id"], delete["operation_id"]}


async def test_current_principal_revocation_after_preparation_denies_reads_cancel_and_mutation(crud_db):
    db = crud_db
    async with signed_in(db) as admin:
        body = await update_body(db, admin)
        await db.owner.execute("UPDATE user_profiles SET role='readonly' WHERE id=$1", db.users[0])
        for method, path, options in [("GET", BASE, {}), ("GET", BASE + "/" + body["operation_id"], {}),
            ("POST", BASE + "/" + body["operation_id"] + "/cancel", {}),
            ("PATCH", f"/admin/users/{db.users[1]}", {"json": body})]:
            denied = await call(db, admin, method, path, **options)
            assert denied.status_code == 403
        assert await db.owner.fetchval("SELECT name FROM user_profiles WHERE id=$1", db.users[1]) == "Synthetic HTTP user"
