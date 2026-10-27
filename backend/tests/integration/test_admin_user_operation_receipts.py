"""Normal-auth HTTP reviewed writes and exact create-request recovery on owned PG."""
import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest

from tests.integration.test_admin_users_tenants_crud import (
    crud_db, signed_in, call, create_body, prepare_write, prepared_call,
)
from tests.integration.test_admin_real_auth_journey import journey_db

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


async def read_user(db, admin, target):
    result = await call(db, admin, "GET", f"/admin/users/{target}")
    assert result.status_code == 200
    return result.json()


def reviewed(row):
    # The baseline has no token. It ignores this valid-shaped unknown field,
    # exposing the stale-write failure instead of ending at a fixture KeyError.
    return {"expected_review_token": row.get("review_token", "v1:" + "0" * 64)}


async def patch(db, admin, target, **changes):
    row = await read_user(db, admin, target)
    return await prepared_call(db, admin, "PATCH", f"/admin/users/{target}", json={**reviewed(row), **changes})


async def receipt(db, payload, action, actor_index=0):
    event = await db.owner.fetchrow(
        "SELECT * FROM audit_logs WHERE event_id=$1", UUID(payload["audit_event_id"]))
    assert event and event["actor_id"] == db.users[actor_index] and event["action"] == action
    return event


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_mutation_requires_reviewed_user_version(crud_db, method):  # noqa: F811
    db = crud_db
    async with signed_in(db) as admin:
        result = await call(db, admin, method, f"/admin/users/{db.users[1]}",
                            json={"operation_id": str(uuid4()), **({"name": "Must not write"} if method == "PATCH" else {})})
        RUN_EVIDENCE.append({"case": "missing_review", "method": method, "status": result.status_code})
        assert result.status_code == 422
        assert await db.owner.fetchval("SELECT name FROM user_profiles WHERE id=$1", db.users[1]) == "Synthetic HTTP user"
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1", db.users[1]) == 0


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("intervening", ["aba", "keep"])
async def test_delayed_write_after_recovery_read_and_aba_cannot_apply(crud_db, method, intervening):  # noqa: F811
    db = crud_db
    entered, release = asyncio.Event(), asyncio.Event()
    await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[2])

    async def gated_app(scope, receive, send):
        if (scope.get("method") == method and scope.get("path") == f"/api/v1/admin/users/{db.users[1]}"
                and (b"x-synthetic-delay", b"owned") in scope.get("headers", [])):
            entered.set()
            await asyncio.wait_for(release.wait(), 15)
        await db.app(scope, receive, send)

    async with signed_in(db) as admin, signed_in(db, 2) as other_admin:
        original = await read_user(db, admin, db.users[1])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gated_app, raise_app_exceptions=False),
                                     base_url=admin.base_url, cookies=admin.cookies, headers=admin.headers) as delayed:
            body = reviewed(original)
            if method == "PATCH":
                body["name"] = "Stale late overwrite"
            body = await prepare_write(db, admin, method, f"/admin/users/{db.users[1]}", body)
            task = asyncio.create_task(call(db, delayed, method, f"/admin/users/{db.users[1]}",
                                            json=body, headers={"X-Synthetic-Delay": "owned"}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                observed = await read_user(db, admin, db.users[1])
                assert observed["name"] == original["name"]
                if intervening == "aba":
                    assert (await patch(db, other_admin, db.users[1], name="Intervening change")).status_code == 200
                    assert (await patch(db, other_admin, db.users[1], name=original["name"])).status_code == 200
                else:
                    kept = await patch(db, other_admin, db.users[1], is_active=original["is_active"])
                    assert kept.status_code == 200 and kept.json()["review_token"] != original["review_token"]
                    await receipt(db, kept.json(), "user_updated", 2)
                release.set()
                result = await asyncio.wait_for(task, 10)
                saved = await db.owner.fetchval("SELECT name FROM user_profiles WHERE id=$1", db.users[1])
                count = await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1 AND action='user_updated'", db.users[1])
                RUN_EVIDENCE.append({"case": "delayed_aba", "method": method, "intervening": intervening, "status": result.status_code,
                                     "original_state_retained": saved == original["name"], "audit_count": count})
                assert result.status_code == 409
                assert saved == original["name"] and count == (2 if intervening == "aba" else 1)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_independent_membership_aba_invalidates_review(crud_db):  # noqa: F811
    db = crud_db
    async with signed_in(db) as admin:
        row = await read_user(db, admin, db.users[1])
        body = await prepare_write(db, admin, "PATCH", f"/admin/users/{db.users[1]}",
                                   {**reviewed(row), "name": "Must not overwrite"})
        # Actual SQL writers, not fabricated query results; triggers must cover
        # writers outside Admin PATCH and even both transitions in one transaction.
        async with db.owner.transaction():
            await db.owner.execute("UPDATE tenant_users SET status='suspended' WHERE user_id=$1", db.users[1])
            await db.owner.execute("UPDATE tenant_users SET status='active' WHERE user_id=$1", db.users[1])
        response = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}",
                              json=body)
        RUN_EVIDENCE.append({"case": "membership_aba", "status": response.status_code})
        assert response.status_code == 409


@pytest.mark.parametrize("linked", [False, True], ids=["deleted", "deactivated"])
async def test_delete_receipt_names_exact_target_and_effect(crud_db, linked):  # noqa: F811
    db = crud_db
    if linked:
        await db.owner.execute("UPDATE tenants SET suspended_by=$1 WHERE id=$2", db.users[1], db.tenants[1])
    async with signed_in(db) as admin:
        row = await read_user(db, admin, db.users[1])
        result = await prepared_call(db, admin, "DELETE", f"/admin/users/{db.users[1]}", json=reviewed(row))
        assert result.status_code == 200
        payload = result.json()
        RUN_EVIDENCE.append({"case": "delete_receipt", "linked": linked, "fields": sorted(payload)})
        assert payload["id"] == str(db.users[1])
        assert payload["outcome"] == ("deactivated" if linked else "deleted")
        await receipt(db, payload, "user_deactivated" if linked else "user_deleted")
        assert (payload["user"] is not None) == linked
        if linked:
            assert payload["user"]["id"] == str(db.users[1]) and payload["user"]["is_active"] is False
            assert payload["user"]["review_token"] != row["review_token"]


async def test_create_request_replay_recovers_original_receipt_without_recreating_deleted_user(crud_db):  # noqa: F811
    db = crud_db
    body = {**create_body(db), "request_id": str(uuid4())}
    async with signed_in(db) as admin:
        body = await prepare_write(db, admin, "POST", "/admin/users", body)
        first = await call(db, admin, "POST", "/admin/users", json=body)
        assert first.status_code == 201
        payload = first.json()
        RUN_EVIDENCE.append({"case": "create_receipt", "fields": sorted(payload)})
        assert payload["state"] == "created" and payload["request_id"] == body["request_id"]
        target = payload["user_id"]
        event = await receipt(db, payload, "user_created")
        metadata = json.loads(event["metadata"]) if isinstance(event["metadata"], str) else event["metadata"]
        assert body["password"] not in json.dumps(metadata)
        assert len(metadata["user_create_intent_hmac_v1"]) == 64
        recovered = await call(db, admin, "GET", f"/admin/users/operations/{body['operation_id']}")
        assert recovered.status_code == 200 and recovered.json()["state"] == "committed"
        assert recovered.json()["receipt"]["audit_event_id"] == payload["audit_event_id"]
        replay = await call(db, admin, "POST", "/admin/users", json=body)
        assert replay.status_code == 200 and replay.json()["audit_event_id"] == payload["audit_event_id"]
        conflict = await call(db, admin, "POST", "/admin/users", json={**body, "password": body["password"] + "!"})
        assert conflict.status_code == 409
        row = await read_user(db, admin, target)
        deleted = await prepared_call(db, admin, "DELETE", f"/admin/users/{target}", json=reviewed(row))
        assert deleted.status_code == 200 and deleted.json()["outcome"] == "deleted"
        replay_deleted = await call(db, admin, "POST", "/admin/users", json=body)
        assert replay_deleted.status_code == 200
        assert replay_deleted.json()["user_id"] == target and replay_deleted.json()["user"] == payload["user"]
        assert (await call(db, admin, "GET", f"/admin/users/{target}")).status_code == 404
        assert replay_deleted.json()["audit_event_id"] == payload["audit_event_id"]
        assert not await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM user_profiles WHERE email=$1)", body["email"])


async def test_cancel_unrecorded_create_retires_actual_delayed_request(crud_db):  # noqa: F811
    db = crud_db
    body = {**create_body(db), "request_id": str(uuid4())}
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated_app(scope, receive, send):
        if scope.get("method") == "POST" and scope.get("path") == "/api/v1/admin/users":
            entered.set()
            await asyncio.wait_for(release.wait(), 15)
        await db.app(scope, receive, send)

    async with signed_in(db) as admin:
        body = await prepare_write(db, admin, "POST", "/admin/users", body)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gated_app, raise_app_exceptions=False),
                                     base_url=admin.base_url, cookies=admin.cookies, headers=admin.headers) as delayed:
            task = asyncio.create_task(call(db, delayed, "POST", "/admin/users", json=body))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                path = f"/admin/users/operations/{body['operation_id']}"
                empty = await call(db, admin, "GET", path)
                assert empty.status_code == 200 and empty.json()["state"] == "prepared"
                cancelled = await call(db, admin, "POST", path + "/cancel")
                assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
                await receipt(db, {"audit_event_id": cancelled.json()["terminal_audit_event_id"]}, "user_operation_cancelled")
                repeat = await call(db, admin, "POST", path + "/cancel")
                assert repeat.json() == cancelled.json()
                release.set()
                result = await asyncio.wait_for(task, 10)
                RUN_EVIDENCE.append({"case": "cancel_delayed_create", "late_status": result.status_code})
                assert result.status_code == 409
                assert not await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM user_profiles WHERE email=$1)", body["email"])
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("operation", ["create", "cancel"])
async def test_create_request_audit_failure_has_no_business_or_operation_receipt(crud_db, operation):  # noqa: F811
    db = crud_db
    body = {**create_body(db), "request_id": str(uuid4())}
    async with signed_in(db) as admin:
        body = await prepare_write(db, admin, "POST", "/admin/users", body)
        async with db.pool.acquire() as conn:
            role = await conn.fetchval("SELECT current_user")
        await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{role}"')
        result = await call(db, admin, "POST", "/admin/users" if operation == "create" else
                            f"/admin/users/operations/{body['operation_id']}/cancel",
                            **({"json": body} if operation == "create" else {}))
        assert result.status_code >= 500
        assert not await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM user_profiles WHERE email=$1)", body["email"])
        assert not await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM audit_logs WHERE metadata->>'user_operation_id'=$1 AND action <> 'user_operation_prepared')", body["operation_id"])


async def test_concurrent_same_create_request_has_one_committed_target(crud_db):  # noqa: F811
    db = crud_db
    body = {**create_body(db), "request_id": str(uuid4())}
    async with signed_in(db) as admin:
        body = await prepare_write(db, admin, "POST", "/admin/users", body)
        responses = await asyncio.gather(*[
            call(db, admin, "POST", "/admin/users", json=body) for _ in range(2)])
        assert sorted(response.status_code for response in responses) == [200, 201]
        assert responses[0].json()["user_id"] == responses[1].json()["user_id"]
        assert responses[0].json()["audit_event_id"] == responses[1].json()["audit_event_id"]
        assert await db.owner.fetchval("SELECT count(*) FROM user_profiles WHERE email=$1", body["email"]) == 1
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'user_create_request_id'=$1", body["request_id"]) == 1
        # A cancellation arriving after creation returns the historical created
        # receipt; it does not remove/deactivate that account.
        closed = await call(db, admin, "POST", f"/admin/users/operations/{body['operation_id']}/cancel")
        assert closed.status_code == 200 and closed.json()["state"] == "committed"
        assert closed.json()["receipt"]["audit_event_id"] == responses[0].json()["audit_event_id"]


async def test_same_request_uuid_is_scoped_to_current_operator(crud_db):  # noqa: F811
    db = crud_db
    async with signed_in(db) as first:
        assert (await patch(db, first, db.users[2], role="platform_admin")).status_code == 200
        body = await prepare_write(db, first, "POST", "/admin/users", create_body(db))
        operation_id = body["operation_id"]
        first_result = await call(db, first, "POST", f"/admin/users/operations/{operation_id}/cancel")
        assert first_result.status_code == 200
        async with signed_in(db, 2) as second:
            other = await call(db, second, "GET", f"/admin/users/operations/{operation_id}")
            assert other.status_code == 404
            await db.owner.execute("UPDATE user_profiles SET role='tenant_admin' WHERE id=$1", db.users[2])
            denied = await call(db, second, "POST", f"/admin/users/operations/{operation_id}/cancel")
            assert denied.status_code == 403
            assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE metadata->>'user_operation_id'=$1 AND action='user_operation_cancelled'", operation_id) == 1


async def test_membership_insert_key_share_does_not_invert_profile_review_lock(crud_db):  # noqa: F811
    db = crud_db
    await db.owner.execute("DELETE FROM tenant_users WHERE user_id=$1", db.users[1])
    async with signed_in(db) as admin:
        original = await read_user(db, admin, db.users[1])
        # This is the exact profile lock mode used by the reviewed writer. The
        # real RBAC insert must finish while it is held, proving its FK key-share
        # is compatible. No method/SQL result is replaced.
        async with db.owner.transaction():
            await db.owner.fetchrow("SELECT id FROM user_profiles WHERE id=$1 FOR NO KEY UPDATE", db.users[1])
            inserted = await asyncio.wait_for(call(db, admin, "POST", "/rbac/tenant-users", json={
                "user_id": str(db.users[1]), "tenant_id": str(db.tenants[1]),
                "role_name": "tenant_admin", "is_primary": True,
            }), 5)
            assert inserted.status_code == 201
        stale = await prepared_call(db, admin, "PATCH", f"/admin/users/{db.users[1]}",
                           json={**reviewed(original), "name": "Old missing-membership decision"})
        assert stale.status_code == 409


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_membership_insert_after_review_cannot_be_adopted_by_old_mutation(crud_db, monkeypatch, method):  # noqa: F811
    from app.api.v1.endpoints.admin import users

    db = crud_db
    await db.owner.execute("DELETE FROM tenant_users WHERE user_id=$1", db.users[1])
    entered, release = asyncio.Event(), asyncio.Event()
    original = users._require_review

    async def paused_review(conn, user_id, expected_token):
        result = await original(conn, user_id, expected_token)
        if not entered.is_set() and str(user_id) == str(db.users[1]):
            entered.set()
            await asyncio.wait_for(release.wait(), 15)
        return result

    # Synchronization only: real review SQL and result are forwarded unchanged.
    monkeypatch.setattr(users, "_require_review", paused_review)
    async with signed_in(db) as admin:
        row = await read_user(db, admin, db.users[1])
        body = reviewed(row)
        if method == "PATCH":
            body["role"] = "readonly"
        body = await prepare_write(db, admin, method, f"/admin/users/{db.users[1]}", body)
        task = asyncio.create_task(call(db, admin, method, f"/admin/users/{db.users[1]}", json=body))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            inserted = await asyncio.wait_for(call(db, admin, "POST", "/rbac/tenant-users", json={
                "user_id": str(db.users[1]), "tenant_id": str(db.tenants[1]),
                "role_name": "tenant_admin", "is_primary": True,
            }), 5)
            assert inserted.status_code == 201
            release.set()
            result = await asyncio.wait_for(task, 10)
            membership = await db.owner.fetchval("""SELECT r.name FROM tenant_users tu
                JOIN roles r ON r.id=tu.role_id WHERE tu.user_id=$1""", db.users[1])
            RUN_EVIDENCE.append({"case": "post_review_membership_insert", "method": method,
                                 "status": result.status_code, "new_membership_preserved": membership == "tenant_admin"})
            assert result.status_code == 409 and membership == "tenant_admin"
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_explicit_self_rejection_is_no_write_code_not_unknown(crud_db):  # noqa: F811
    db = crud_db
    async with signed_in(db) as admin:
        original = await read_user(db, admin, db.users[0])
        result = await prepared_call(db, admin, "PATCH", f"/admin/users/{db.users[0]}",
                            json={**reviewed(original), "is_active": False})
        assert result.status_code == 400 and result.json()["error"]["code"] == "user_write_rejected"
        assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[0]) is True
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE action='user_updated' AND resource_id=$1", db.users[0]) == 0


async def test_exact_user_absence_has_a_canonical_code(crud_db):  # noqa: F811
    db = crud_db
    async with signed_in(db) as admin:
        existing = await read_user(db, admin, db.users[1])
        assert existing["id"] == str(db.users[1]) and existing["review_token"].startswith("v1:")
        absent = await call(db, admin, "GET", f"/admin/users/{uuid4()}")
        assert absent.status_code == 404 and absent.json()["error"]["code"] == "user_not_found"
