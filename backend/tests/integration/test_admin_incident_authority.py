"""Normal-auth incident authority ordering on exact owned migrated PG records.

Barriers only pause real application/SQL boundaries; they do not replace auth,
current-principal, audit, transition or prepared Users mutation decisions.
"""
# ruff: noqa: F811 -- pytest discovers imported fixtures by parameter name.
import asyncio
from contextlib import suppress
import re
import time
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints.admin import users
from app.api.v1.endpoints.admin.health import incidents
from app.core.security.platform_admin_guard import lock_user_administration
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, crud_db, prepare_write, signed_in

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def incident_authority_db(crud_db):
    db = crud_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT,UPDATE ON incidents TO "{role}"')
    # Infrastructure-owned second principal, authenticated by the real login
    # path. The first principal retains normal MFA and can revoke this actor.
    await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[1])
    created = []

    async def new_incident():
        incident_id = uuid4()
        created.append(incident_id)
        await db.owner.execute("INSERT INTO incidents(id,title,severity) VALUES($1,$2,'warning')",
                               incident_id, "Synthetic current-authority incident")
        return incident_id

    db.new_incident = new_incident
    try:
        yield db
    finally:
        await db.owner.execute("DELETE FROM incidents WHERE id=ANY($1::uuid[])", created)


async def reviewed_revocation(db, admin, change):
    path = f"/admin/users/{db.users[1]}"
    reviewed = await call(db, admin, "GET", path)
    assert reviewed.status_code == 200
    body = await prepare_write(db, admin, "PATCH", path,
                               {"expected_review_token": reviewed.json()["review_token"], **change})
    return path, body


async def cancel_task(task):
    if task is not None and not task.done():
        task.cancel()
    if task is not None:
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("action,change", [
    ("acknowledge", {"is_active": False}), ("resolve", {"role": "readonly"}),
], ids=["block-before-acknowledge", "demote-before-resolve"])
async def test_revocation_after_dependency_before_transition_rejects_without_effect(incident_authority_db, monkeypatch, action, change):
    db = incident_authority_db
    incident_id = await db.new_incident()
    entered, release = asyncio.Event(), asyncio.Event()
    original = incidents._transition

    async def paused(owned_id, target, actor, client):
        if owned_id == incident_id:
            assert str(actor.id) == str(db.users[1]) and actor.role == "platform_admin"
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        return await original(owned_id, target, actor, client)

    monkeypatch.setattr(incidents, "_transition", paused)
    pending = None
    async with signed_in(db) as administrator, signed_in(db, 1) as old_actor:
        path, body = await reviewed_revocation(db, administrator, change)
        try:
            pending = asyncio.create_task(call(db, old_actor, "POST", f"/admin/incidents/{incident_id}/{action}"))
            await asyncio.wait_for(entered.wait(), 5)
            revoked = await call(db, administrator, "PATCH", path, json=body)
            assert revoked.status_code == 200, revoked.text
            receipt = revoked.json()
            assert receipt["operation_id"] == body["operation_id"]
            assert await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM audit_logs WHERE event_id=$1 AND actor_id=$2)",
                                           UUID(receipt["audit_event_id"]), db.users[0])
            release.set()
            response = await asyncio.wait_for(pending, 5)
            saved = dict(await db.owner.fetchrow("SELECT status,acknowledged_by,resolved_by FROM incidents WHERE id=$1", incident_id))
            RUN_EVIDENCE.append({"case": "revocation_first", "action": action, "authority_change": change,
                                 "response_status": response.status_code,
                                 "saved": {key: str(value) if isinstance(value, UUID) else value for key, value in saved.items()}})
            assert response.status_code == 403, response.text
            assert saved == {"status": "open", "acknowledged_by": None, "resolved_by": None}
        finally:
            release.set()
            await cancel_task(pending)


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
async def test_accepted_transition_holds_authority_until_commit_then_revocation_applies(incident_authority_db, monkeypatch, action):
    db = incident_authority_db
    incident_id = await db.new_incident()
    row_owned, release, revoke_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_fetch = asyncpg.Connection.fetchrow
    original_lock = users.lock_user_administration
    waiter_pid = None

    async def paused_fetch(conn, query, *args, **kwargs):
        row = await original_fetch(conn, query, *args, **kwargs)
        if "SELECT * FROM incidents WHERE id=$1::uuid FOR UPDATE" in query and str(args[0]) == str(incident_id):
            row_owned.set()
            await asyncio.wait_for(release.wait(), 10)
        return row

    async def observe_revoke_lock(conn):
        nonlocal waiter_pid
        waiter_pid = conn.get_server_pid()
        revoke_entered.set()
        return await original_lock(conn)

    mutation = revocation = None
    async with signed_in(db) as administrator, signed_in(db, 1) as actor:
        path, body = await reviewed_revocation(db, administrator, {"is_active": False})
        monkeypatch.setattr(asyncpg.Connection, "fetchrow", paused_fetch)
        monkeypatch.setattr(users, "lock_user_administration", observe_revoke_lock)
        try:
            mutation = asyncio.create_task(call(db, actor, "POST", f"/admin/incidents/{incident_id}/{action}"))
            await asyncio.wait_for(row_owned.wait(), 5)
            revocation = asyncio.create_task(call(db, administrator, "PATCH", path, json=body))
            await asyncio.wait_for(revoke_entered.wait(), 5)
            blocked = False
            for _ in range(50):
                blocked = await db.owner.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", waiter_pid)
                if blocked or revocation.done():
                    break
                await asyncio.sleep(0.02)
            assert blocked and not revocation.done(), "Authority revocation must wait for the accepted transition"
            release.set()
            first = await asyncio.wait_for(mutation, 5)
            second = await asyncio.wait_for(revocation, 5)
            expected = "acknowledged" if action == "acknowledge" else "resolved"
            assert first.status_code == second.status_code == 200
            assert first.json()["status"] == expected
            saved = await db.owner.fetchrow("SELECT status,acknowledged_by,resolved_by FROM incidents WHERE id=$1", incident_id)
            assert saved["status"] == expected
            assert saved["acknowledged_by" if action == "acknowledge" else "resolved_by"] == db.users[1]
            assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[1]) is False
            denied = await call(db, actor, "POST", f"/admin/incidents/{incident_id}/resolve")
            assert denied.status_code in {401, 403}
            RUN_EVIDENCE.append({"case": "accepted_transition_first", "action": action,
                                 "observed_pg_lock_wait": blocked, "statuses": [first.status_code, second.status_code],
                                 "later_stale_request": denied.status_code})
        finally:
            release.set()
            await cancel_task(mutation)
            await cancel_task(revocation)


async def test_normal_authorized_replay_preserves_original_actor_and_resolution_is_monotonic(incident_authority_db):
    db = incident_authority_db
    incident_id = await db.new_incident()
    async with signed_in(db) as first_actor, signed_in(db, 1) as second_actor:
        path = f"/admin/incidents/{incident_id}"
        first = await call(db, first_actor, "POST", path + "/acknowledge")
        replay = await call(db, second_actor, "POST", path + "/acknowledge")
        assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
        assert first.json()["acknowledged_by"] == str(db.users[0])
        resolved = await call(db, second_actor, "POST", path + "/resolve")
        resolved_replay = await call(db, first_actor, "POST", path + "/resolve")
        assert resolved.status_code == resolved_replay.status_code == 200 and resolved.json() == resolved_replay.json()
        assert resolved.json()["resolved_by"] == str(db.users[1])
        assert (await call(db, first_actor, "POST", path + "/acknowledge")).status_code == 409
        detail = await call(db, first_actor, "GET", path)
        assert detail.status_code == 200 and detail.json()["status"] == "resolved"


async def test_error_after_real_incident_update_rolls_back_without_success_receipt(incident_authority_db, monkeypatch):
    db = incident_authority_db
    incident_id = await db.new_incident()
    original = asyncpg.Connection.fetchrow
    real_update = False

    async def fail_after_update(conn, query, *args, **kwargs):
        nonlocal real_update
        row = await original(conn, query, *args, **kwargs)
        if query.startswith("UPDATE incidents SET") and str(args[0]) == str(incident_id):
            real_update = True
            assert row["status"] == "acknowledged"
            raise RuntimeError("Synthetic post-SQL pre-commit incident failure")
        return row

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", fail_after_update)
    async with signed_in(db) as admin:
        response = await call(db, admin, "POST", f"/admin/incidents/{incident_id}/acknowledge")
        assert real_update and response.status_code == 503
        saved = await db.owner.fetchrow("SELECT status,acknowledged_at,acknowledged_by FROM incidents WHERE id=$1", incident_id)
        assert dict(saved) == {"status": "open", "acknowledged_at": None, "acknowledged_by": None}


async def test_authority_contention_has_bounded_unavailable_outcome_and_no_transition(incident_authority_db):
    db = incident_authority_db
    incident_id = await db.new_incident()
    async with signed_in(db) as admin:
        async with db.owner.transaction():
            await lock_user_administration(db.owner)
            started = time.monotonic()
            response = await asyncio.wait_for(call(db, admin, "POST", f"/admin/incidents/{incident_id}/acknowledge"), 7)
            elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert elapsed < 6
        assert await db.owner.fetchval("SELECT status FROM incidents WHERE id=$1", incident_id) == "open"
        RUN_EVIDENCE.append({"case": "bounded_authority_contention", "status": response.status_code, "elapsed_seconds": elapsed})


async def test_ordinary_authenticated_user_cannot_transition_incident(incident_authority_db):
    db = incident_authority_db
    incident_id = await db.new_incident()
    async with signed_in(db, 2) as ordinary:
        for action in ["acknowledge", "resolve"]:
            response = await call(db, ordinary, "POST", f"/admin/incidents/{incident_id}/{action}")
            assert response.status_code == 403
    assert await db.owner.fetchval("SELECT status FROM incidents WHERE id=$1", incident_id) == "open"
