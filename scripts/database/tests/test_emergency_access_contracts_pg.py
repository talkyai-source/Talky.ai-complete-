"""Real emergency service/TOTP/row-lock qualification on an isolated PG16.

Reuses the synthetic native cluster fixture; never accepts an existing DSN.
Authenticated-session lookup, secret decryption, and the independent Redis
attempt budget are replaced (the Lua has separate Redis qualification). The
production transaction helper, TOTP verifier, SQL, JSON codec, and concurrent
independent PostgreSQL connections are real. No provider or alert delivery.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pyotp
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import text

from test_customer_support_contracts_pg import cluster, database, upgrade  # noqa: F401

from app.api.v1.dependencies import CurrentUser
from app.core.security import emergency_access as module

SECRET = "JBSWY3DPEHPK3PXP"


def authority(user_id):
    request = Request({"type": "http", "method": "POST", "path": "/admin/emergency", "headers": []})
    request.state.authenticated_user_id = str(user_id)
    request.state.authenticated_session_id = "synthetic-session-fence-replaced"
    return {"auth_request": request, "current_user": CurrentUser(
        id=str(user_id), email="synthetic@example.invalid", role="platform_admin",
    )}


async def _json_codecs(conn):
    await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")


@pytest.mark.asyncio
async def test_concurrent_verified_approvals_and_one_time_session(database, monkeypatch):  # noqa: F811 - imported pytest fixture
    engine, _, port, name = database
    with engine.begin() as conn:
        upgrade(conn)
        conn.exec_driver_sql("SET LOCAL app.bypass_rls='true'")
        actors = [conn.execute(text("INSERT INTO user_profiles(email) VALUES (:email) RETURNING id"),
                               dict(email=f"synthetic-emergency-{i}@example.invalid")).scalar() for i in range(3)]
        for actor in actors[1:]:
            conn.execute(text("INSERT INTO user_mfa(user_id,totp_secret_enc,enabled,verified_at) VALUES (:id,'synthetic-encrypted-secret',true,now())"),
                         dict(id=actor))

    async def authenticated_fence(conn, request, user):
        assert conn.is_in_transaction()
        assert request.state.authenticated_user_id == user.id
        return {"role": "platform_admin"}

    monkeypatch.setattr(module, "lock_authenticated_credential_change", authenticated_fence)
    monkeypatch.setattr(module, "decrypt_totp_secret", lambda encrypted: SECRET)
    pool = await asyncpg.create_pool(f"postgresql://support_owner@127.0.0.1:{port}/{name}",
                                     min_size=3, max_size=5, init=_json_codecs)
    service = module.EmergencyAccess(pool, redis_client=SimpleNamespace(
        eval=AsyncMock(return_value=1), publish=AsyncMock(return_value=1),
    ))
    try:
        request = await service.request(actors[0], module.EmergencyScenario.SECURITY_INCIDENT,
                                         "Synthetic incident for concurrent authorization qualification",
                                         ["existing:permission"], **authority(actors[0]))

        async def contended(operations):
            tasks = []
            try:
                async with pool.acquire() as blocker:
                    async with blocker.transaction():
                        await blocker.fetchrow("SELECT request_id FROM emergency_access_requests WHERE request_id=$1 FOR UPDATE", request.request_id)
                        tasks = [asyncio.create_task(operation) for operation in operations]
                        deadline = asyncio.get_running_loop().time() + 10
                        while True:
                            waiting = await blocker.fetchval("""SELECT count(DISTINCT pid) FROM pg_stat_activity
                                WHERE datname=current_database() AND wait_event_type='Lock'
                                  AND query LIKE '%FROM emergency_access_requests%FOR UPDATE%'""")
                            if waiting >= 2:
                                break
                            if asyncio.get_running_loop().time() >= deadline:
                                raise AssertionError("Both independent requests must contend on the real request-row lock")
                            await asyncio.sleep(.02)
                return await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        code = pyotp.TOTP(SECRET).now()
        approvals = await contended([
            service.approve(request.request_id, actor, verification_code=code, **authority(actor))
            for actor in actors[1:]
        ])
        assert all(isinstance(result, dict) for result in approvals), approvals
        assert sorted(result["approvals_received"] for result in approvals) == [1, 2]
        with pytest.raises(HTTPException) as replay:
            await service.approve(request.request_id, actors[1], verification_code=code, **authority(actors[1]))
        assert replay.value.status_code == 401

        sessions = await contended([
            service.create_session(request.request_id, **authority(actors[0])) for _ in range(2)
        ])
        assert sum(isinstance(result, module.EmergencySession) for result in sessions) == 1
        assert sum(isinstance(result, ValueError) for result in sessions) == 1

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM emergency_access_requests WHERE request_id=$1", request.request_id)
            assert row["status"] == "used"
            assert len(row["approvals"]) == 2
            assert {approval["approver_id"] for approval in row["approvals"]} == {str(actor) for actor in actors[1:]}
            assert all(approval["verification"] == "totp_step_up" for approval in row["approvals"])
            assert "verification_code" not in json.dumps(row["approvals"])
            assert sum(event["action"] == "session_created" for event in row["actions_taken"]) == 1
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls='true'")
                assert await conn.fetchval("SELECT count(*) FROM user_mfa WHERE user_id=ANY($1::uuid[]) AND last_used_at IS NOT NULL",
                                           actors[1:]) == 2
        expired_ids = []
        for status in ("pending", "approved"):
            expired = await service.request(actors[0], module.EmergencyScenario.SECURITY_INCIDENT,
                                             "Synthetic expired request for consistent operator views",
                                             ["existing:permission"], **authority(actors[0]))
            expired_ids.append(expired.request_id)
            async with pool.acquire() as conn:
                await conn.execute("UPDATE emergency_access_requests SET status=$2, expires_at=now()-interval '1 minute' WHERE request_id=$1",
                                   expired.request_id, status)
            detail = await service.get_request(expired.request_id, **authority(actors[0]))
            assert detail.status == module.EmergencyStatus.EXPIRED
        listed = await service.list_requests(status=module.EmergencyStatus.EXPIRED, **authority(actors[0]))
        assert {row.request_id for row in listed} == set(expired_ids)
        assert all(row.status == module.EmergencyStatus.EXPIRED for row in listed)
        assert not await service.list_requests(status=module.EmergencyStatus.PENDING, **authority(actors[0]))
    finally:
        await pool.close()
