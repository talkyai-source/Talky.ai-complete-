"""Actual authenticated Admin PATCH audit truth on owned migrated PostgreSQL.

The existing normal-auth fixture supplies infrastructure only. Password/MFA,
JWT/cookies, configured middleware, current authority, mutation and audit are
production paths. No provider lifespan or identity/handler override is used.
"""
import asyncio
import json
import re
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pyotp
import pytest

from tests.integration.test_admin_real_auth_journey import API_ORIGIN, ORIGIN, journey_db
from tests.integration.test_admin_users_tenants_crud import prepare_write

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@asynccontextmanager
async def authenticated_admin(db, index=0):
    # Exact optional /me meter prerequisites, not broader application grants.
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT ON billing_ledger,topup_orders,topup_packages TO "{role}"')
    # Keep this owned fixture in its own loopback IP bucket. The real login
    # limiter keys by IP, so a different client port cannot isolate tests.
    client_ip = f"127.2.{db.users[0].bytes[-2]}.{db.users[0].bytes[-1]}"
    db.report["synthetic_client_ip"] = client_ip
    transport = httpx.ASGITransport(app=db.app, client=(client_ip, 59103), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=API_ORIGIN,
                                headers={"Origin": ORIGIN, "User-Agent": "Synthetic-Admin-Name-Audit"}) as client:
        login = await request(db, client, "POST", "/auth/login", json={
            "email": f"{db.users[index]}@example.com", "password": db.password,
        })
        verified = login
        if index == 0:
            assert login.json()["mfa_required"] is True
            verified = await request(db, client, "POST", "/auth/mfa/verify", json={
                "challenge_token": login.json()["mfa_challenge_token"], "code": pyotp.TOTP(db.totp).now(),
            })
        client.headers["Authorization"] = "Bearer " + verified.json()["access_token"]
        me = await request(db, client, "GET", "/auth/me")
        assert me.json()["id"] == str(db.users[index]) and me.json()["role"] == "platform_admin"
        assert {cookie.name for cookie in client.cookies.jar} >= {"talky_at", "talky_rt", "talky_sid"}
        yield client


async def request(db, client, method, path, expected=200, **kwargs):
    response = await client.request(method, "/api/v1" + path, **kwargs)
    db.report["steps"].append({"method": method, "path": path, "status": response.status_code})
    assert response.status_code == expected, f"{method} {path}: HTTP{response.status_code}"
    assert response.headers.get("x-request-id")
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.headers.get("access-control-allow-credentials") == "true"
    return response


async def reviewed_body(db, client, body):
    path=f"/admin/users/{db.users[1]}"
    row=await request(db,client,"GET",path)
    return await prepare_write(db,client,"PATCH",path,{"expected_review_token":row.json()["review_token"],**body})


async def committed_receipt(db, response):
    event_id = UUID(response.json()["audit_event_id"])
    row = await db.owner.fetchrow("""SELECT event_id,actor_id,resource_id,action,metadata
        FROM audit_logs WHERE event_id=$1""", event_id)
    assert row is not None, "Returned receipt must already be committed on a separate connection"
    assert row["actor_id"] == db.users[0] and row["resource_id"] == db.users[1]
    assert row["action"] == "user_updated"
    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    return {"audit_event_id": str(event_id), "name_changed": metadata["name_changed"],
            "new_role": metadata.get("new_role")}


@pytest.mark.parametrize("case,initial_name,submitted,expected_name,expected_changed", [
    ("unchanged_with_role", "Synthetic HTTP user", "Synthetic HTTP user", "Synthetic HTTP user", False),
    ("padded_equivalent", "Synthetic HTTP user", "  Synthetic HTTP user  ", "Synthetic HTTP user", False),
    ("real_rename", "Synthetic HTTP user", "  Synthetic renamed user  ", "Synthetic renamed user", True),
    ("name_omitted", "Synthetic HTTP user", None, "Synthetic HTTP user", False),
    ("legacy_null_to_empty", None, "  ", "", True),
], ids=["unchanged-with-role", "padded-equivalent", "real-rename", "name-omitted", "legacy-null-to-empty"])
async def test_audit_reports_actual_normalized_name_change(
    journey_db, case, initial_name, submitted, expected_name, expected_changed,  # noqa: F811
):
    db = journey_db
    # Fixture-owned setup only; null is a supported historical stored value.
    await db.owner.execute("UPDATE user_profiles SET name=$1 WHERE id=$2", initial_name, db.users[1])
    observation = {"case": case, "expected_name_changed": expected_changed}
    RUN_EVIDENCE.append(observation)
    async with authenticated_admin(db) as admin:
        body = {"role": "readonly"}
        if submitted is not None:
            body["name"] = submitted
        response = await request(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=await reviewed_body(db,admin,body))
        receipt = await committed_receipt(db, response)
        observation.update(receipt)
        stored = await db.owner.fetchrow("""SELECT up.name,up.role,r.name AS membership_role
            FROM user_profiles up JOIN tenant_users tu ON tu.user_id=up.id AND tu.tenant_id=up.tenant_id
            JOIN roles r ON r.id=tu.role_id WHERE up.id=$1""", db.users[1])
        assert stored["name"] == response.json()["name"] == expected_name
        assert stored["role"] == stored["membership_role"] == "readonly"
        assert receipt["new_role"] == "readonly"
        observation["stored_name_matches_expected"] = True
        observation["role_and_membership_committed"] = True
        assert receipt["name_changed"] is expected_changed


async def blocked_backend(db, blocker_pid, locktype=None):
    """Observe only owned runtime connections blocked by this test's lock."""
    for _ in range(100):
        pid = await db.owner.fetchval("""SELECT l.pid FROM pg_locks l
            WHERE NOT l.granted AND $1=ANY(pg_blocking_pids(l.pid))
              AND ($2::text IS NULL OR l.locktype=$2) ORDER BY l.pid LIMIT 1""", blocker_pid, locktype)
        if pid is not None:
            return pid
        await asyncio.sleep(0.02)
    raise AssertionError("The expected real PostgreSQL lock wait was not observed")


async def test_waiting_identical_name_update_compares_latest_locked_value(journey_db):  # noqa: F811
    db = journey_db
    observation = {"case": "serialized_same_value", "expected_name_changed": [True, False]}
    RUN_EVIDENCE.append(observation)
    tasks = []
    await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1",db.users[2])
    async with authenticated_admin(db) as admin, authenticated_admin(db,2) as second:
        first_body=await reviewed_body(db,admin,{"name":"Synthetic serialized name"})
        second_body=await reviewed_body(db,second,{"name":"Synthetic serialized name"})
        try:
            # No production method or SQL result is replaced. Hold only the
            # exact owned target row, then observe the existing audit fence.
            async with db.owner.transaction():
                await db.owner.fetchrow("SELECT id FROM user_profiles WHERE id=$1 FOR UPDATE", db.users[1])
                tasks.append(asyncio.create_task(request(db, admin, "PATCH", f"/admin/users/{db.users[1]}",
                    json=first_body)))
                first_pid = await blocked_backend(db, db.owner.get_server_pid())
                tasks.append(asyncio.create_task(request(db, second, "PATCH", f"/admin/users/{db.users[1]}",
                    expected=409, json=second_body)))
                second_pid = await blocked_backend(db, first_pid, "advisory")
                assert second_pid != first_pid
                observation["first_waited_on_owned_row"] = True
                observation["second_waited_on_existing_audit_fence"] = True
            responses = await asyncio.wait_for(asyncio.gather(*tasks), 10)
            assert responses[0].status_code == 200 and responses[1].status_code == 409
            # The old reviewed second intent cannot write after the first. A
            # fresh deliberate same-value save still proves name_changed=False.
            fresh = await request(db, admin, "PATCH", f"/admin/users/{db.users[1]}",
                                  json=await reviewed_body(db,admin,{"name":"Synthetic serialized name"}))
            receipts = [await committed_receipt(db, responses[0]), await committed_receipt(db, fresh)]
            observation["receipts"] = receipts
            assert len({row["audit_event_id"] for row in receipts}) == 2
            assert await db.owner.fetchval("SELECT name FROM user_profiles WHERE id=$1", db.users[1]) == "Synthetic serialized name"
            assert [row["name_changed"] for row in receipts] == [True, False]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
