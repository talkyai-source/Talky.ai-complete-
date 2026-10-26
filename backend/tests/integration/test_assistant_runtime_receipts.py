"""Restricted-role PostgreSQL runtime receipts and real formal-hold ordering.

Only the explicitly designated disposable database is used. Providers/sockets
are not invoked; the synthetic SDK/actual-handler tests cover those boundaries.
"""
import asyncio
from uuid import UUID

import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.core.security import assistant_runtime as runtime_api
from app.core.security.principal import PrincipalUnavailable
from app.core.security.sessions import create_session
from app.domain.services.suspension_service import SuspensionType
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def runtime_db(authority_db, monkeypatch):  # noqa: F811
    db = authority_db
    await db.owner.execute(f'GRANT INSERT ON security_sessions TO "{db.role}"')
    db.actor = await db.new_user()
    db.target = await db.new_user(profile_role="user")
    async with acquire_with_tenant(db.pool, None) as conn:
        _, sid = await create_session(conn, user_id=str(db.target), ip_address="127.0.0.1",
            bind_to_ip=False, bind_to_fingerprint=False, return_session_id=True)
    db.claims = {"sub": str(db.target), "tenant_id": str(db.tenant_id), "sid": sid}
    # The shared fixture owns the chain-signing key; real logger and SQL remain.
    monkeypatch.setattr(runtime_api, "AuditLogger", lambda _: db.audit)
    try:
        yield db
    finally:
        await db.owner.execute("DELETE FROM audit_logs WHERE tenant_id=$1 AND resource_type=$2",
                               db.tenant_id, runtime_api.RESOURCE_TYPE)


async def register(db):
    return await runtime_api.AssistantRuntime.register(db.pool, db.claims, endpoint_kind="voice")


async def hold(db):
    return await db.service.suspend_user(db.target, SuspensionType.TEMPORARY,
        "synthetic", "Synthetic runtime hold", suspended_by=db.actor, notify_user=False)


async def test_runtime_receipts_are_exact_and_durable_under_restricted_role(runtime_db):
    db = runtime_db
    runtime = await register(db)
    await runtime.record_stopped(close_frame_status="sent")
    rows = await db.owner.fetch("SELECT * FROM audit_logs WHERE resource_id=$1 ORDER BY event_time",
                                UUID(runtime.metadata["runtime_id"]))
    assert len(rows) == 2
    assert runtime_api.stop_receipt_matches(*rows)
    assert str(rows[0]["event_id"]) == runtime.registration_event_id
    assert str(rows[1]["event_id"]) == runtime.stopped_event_id
    assert rows[0]["entry_hash"] == rows[1]["previous_hash"]


async def test_required_registration_audit_denial_leaves_no_registration(runtime_db):
    db = runtime_db
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    with pytest.raises(Exception, match="permission denied"):
        await register(db)
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", db.target) == 0


async def test_final_audit_denial_keeps_registration_and_unknown_stop(runtime_db):
    db = runtime_db
    runtime = await register(db)
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    assert await runtime.record_stopped() is None
    rows = await db.owner.fetch("SELECT action FROM audit_logs WHERE resource_id=$1",
                               UUID(runtime.metadata["runtime_id"]))
    assert [row["action"] for row in rows] == [runtime_api.REGISTER_ACTION]


@pytest.mark.parametrize("first", ["registration", "hold"])
async def test_hold_and_runtime_registration_serialize_with_current_recheck(runtime_db, monkeypatch, first):
    db = runtime_db
    locked, second_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = db.audit.lock_transaction
    attempts = []
    async def ordered_lock(conn):
        index = len(attempts)
        attempts.append(conn.get_server_pid())
        if index == 1:
            second_started.set()
        await original(conn)
        if index == 0:
            locked.set()
            await asyncio.wait_for(release.wait(), 5)
    monkeypatch.setattr(db.audit, "lock_transaction", ordered_lock)
    one = asyncio.create_task(register(db) if first == "registration" else hold(db))
    two = None
    try:
        await asyncio.wait_for(locked.wait(), 5)
        two = asyncio.create_task(hold(db) if first == "registration" else register(db))
        await asyncio.wait_for(second_started.wait(), 5)
        waiting = False
        for _ in range(100):
            waiting = await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 "
                "AND locktype='advisory' AND NOT granted)", attempts[1])
            if waiting:
                break
            await asyncio.sleep(.01)
        assert waiting, "Real concurrent transaction must wait for the shared audit lock"
        release.set()
        results = await asyncio.gather(one, two, return_exceptions=True)
        if first == "registration":
            assert isinstance(results[0], runtime_api.AssistantRuntime)
            assert not isinstance(results[1], Exception)
            assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND action=$2",
                db.target, runtime_api.REGISTER_ACTION) == 1
        else:
            assert not isinstance(results[0], Exception)
            assert isinstance(results[1], PrincipalUnavailable)
            assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1 AND action=$2",
                db.target, runtime_api.REGISTER_ACTION) == 0
        with pytest.raises(PrincipalUnavailable):
            await register(db)
    finally:
        release.set()
        await asyncio.gather(*(task for task in (one, two) if task), return_exceptions=True)


async def test_expired_or_replaced_security_session_is_not_a_new_runtime(runtime_db):
    db = runtime_db
    await db.owner.execute("UPDATE security_sessions SET created_at=NOW()-INTERVAL '2 minutes', "
                           "last_active_at=NOW()-INTERVAL '2 minutes', expires_at=NOW()-INTERVAL '1 minute' WHERE id=$1",
                           UUID(db.claims["sid"]))
    with pytest.raises(PrincipalUnavailable):
        await register(db)
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", db.target) == 0
