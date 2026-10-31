"""Administrator-operated appeals on the canonical disposable PostgreSQL schema."""
import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import suspensions as routes
from app.domain.services.suspension_service import SuspensionType
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401
from tests.integration import test_formal_account_holds as account_fixtures

account_db = account_fixtures.account_db
hold = account_fixtures.hold

pytestmark = pytest.mark.integration


async def new_hold(db, target="tenant"):
    if target == "user":
        return await db.service.suspend_user(db.target, SuspensionType.ADMIN, "synthetic", "Appeal fixture",
                                            suspended_by=db.actor, notify_user=False)
    return await hold(db, target)


async def submit(db, saved, reason="Please review the recorded evidence"):
    return await routes.submit_appeal(saved.suspension_id, routes.AppealRequest(appeal_reason=reason),
                                     {"id": str(db.actor)}, db.service)


async def test_submission_returns_committed_audit_receipt(account_db):
    db = account_db
    saved = await new_hold(db)
    result = await submit(db, saved)
    assert result["audit_event_id"]
    assert await db.owner.fetchval("SELECT action FROM audit_logs WHERE event_id=$1", result["audit_event_id"]) == "appeal_submitted"
    assert result["status"] == "pending_review" and result["changed"] is True


async def test_optional_hint_failure_cannot_hide_committed_submission(account_db):
    db = account_db
    saved = await new_hold(db)
    db.service.redis = type("BrokenHint", (), {"publish": AsyncMock(side_effect=RuntimeError("Synthetic hint failure"))})()
    result = await submit(db, saved)
    assert result["audit_event_id"] and result["hint_status"] == "failed"
    assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1", saved.suspension_id) == "pending"


async def test_delayed_submission_retry_cannot_reopen_denied_appeal(account_db):
    db = account_db
    saved = await new_hold(db)
    await submit(db, saved)
    decision = await db.service.review_appeal(saved.suspension_id, db.actor, "denied", "Reviewed")
    with pytest.raises(HTTPException) as conflict:
        await submit(db, saved)
    assert conflict.value.status_code == 409
    event = await db.owner.fetchrow("SELECT appeal_decision,audit_log_id FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert (event["appeal_decision"], event["audit_log_id"]) == ("denied", decision["audit_event_id"])


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
@pytest.mark.parametrize("decision", ["granted", "denied"])
async def test_submission_can_be_consumed_without_losing_hold_provenance(account_db, target, decision):
    db = account_db
    saved = await new_hold(db, target)
    submitted = await submit(db, saved)
    metadata = json.loads(await db.owner.fetchval("SELECT metadata FROM audit_logs WHERE event_id=$1", submitted["audit_event_id"]))
    assert (metadata.get("formal_account_hold_version") == 1) is (target != "user")
    result = await db.service.review_appeal(saved.suspension_id, db.actor, decision, "Reviewed")
    assert result["audit_event_id"]
    assert await db.owner.fetchval("SELECT audit_log_id FROM suspension_events WHERE suspension_id=$1", saved.suspension_id) == result["audit_event_id"]
    event = await db.owner.fetchrow("SELECT is_active,appeal_decision FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert event["appeal_decision"] == decision and event["is_active"] is (decision == "denied")
    if decision == "denied" and target != "user":
        restored = await getattr(db.service, f"restore_{target}")(
            db.tenant_id if target == "tenant" else db.partner, db.actor, "Separate reviewed restoration",
            expected_suspension_id=saved.suspension_id)
        assert restored.audit_event_id


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
@pytest.mark.parametrize("same_reason", [True, False])
async def test_concurrent_submissions_commit_one_immutable_receipt(account_db, target, same_reason):
    db = account_db
    saved = await new_hold(db, target)
    results = await asyncio.gather(submit(db, saved, "First evidence"),
        submit(db, saved, "First evidence" if same_reason else "Changed evidence"), return_exceptions=True)
    good = [item for item in results if isinstance(item, dict)]
    assert len(good) == (2 if same_reason else 1)
    if same_reason:
        assert good[0]["audit_event_id"] == good[1]["audit_event_id"]
        assert sorted(item["changed"] for item in good) == [False, True]
    else:
        conflict = next(item for item in results if isinstance(item, Exception))
        assert isinstance(conflict, HTTPException) and conflict.status_code == 409
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE resource_id=$1 AND action='appeal_submitted'", saved.suspension_id) == 1


@pytest.mark.parametrize("state", ["inactive", "unverified", "demoted"])
async def test_submission_rechecks_current_operator_in_transaction(account_db, state):
    db = account_db
    saved = await new_hold(db)
    change = {"inactive":"is_active=FALSE", "unverified":"is_verified=FALSE,email_verified_at=NULL", "demoted":"role='user'"}[state]
    await db.owner.execute(f"UPDATE user_profiles SET {change} WHERE id=$1", db.actor)
    with pytest.raises(HTTPException) as denied:
        await submit(db, saved)
    assert denied.value.status_code == 403
    assert await db.owner.fetchval("SELECT appeal_submitted_at FROM suspension_events WHERE suspension_id=$1", saved.suspension_id) is None


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_submission_audit_failure_rolls_back_all_appeal_fields(account_db, target):
    db = account_db
    saved = await new_hold(db, target)
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    with pytest.raises(Exception):
        await submit(db, saved)
    row = await db.owner.fetchrow("SELECT appeal_submitted_at,appeal_reason,appeal_decision,audit_log_id FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert (row["appeal_submitted_at"], row["appeal_reason"], row["appeal_decision"], row["audit_log_id"]) == (None, None, None, saved.audit_event_id)


async def test_stalled_hint_is_bounded_and_cancelled_after_commit(account_db):
    db = account_db
    saved = await new_hold(db)
    cancelled = asyncio.Event()
    async def stall(*_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    db.service.redis = type("StalledHint", (), {"publish": staticmethod(stall)})()
    result = await asyncio.wait_for(submit(db, saved), timeout=3)
    assert cancelled.is_set() and result["hint_status"] == "failed"
    assert result["notification_delivery_status"] == "unverified"
    replay = await submit(db, saved)
    assert replay["audit_event_id"] == result["audit_event_id"] and not replay["changed"]


@pytest.mark.parametrize("state", ["missing", "restored", "missing_target"])
async def test_submission_requires_exact_active_event_and_existing_target(account_db, state):
    db = account_db
    saved = await new_hold(db)
    target = db.tenant_id
    try:
        if state == "missing":
            saved.suspension_id = uuid4()
        elif state == "restored":
            await db.service.restore_tenant(db.tenant_id, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id)
        else:
            await db.owner.execute("UPDATE suspension_events SET target_id=$2 WHERE suspension_id=$1", saved.suspension_id, uuid4())
        with pytest.raises(HTTPException) as denied:
            await submit(db, saved)
        assert denied.value.status_code == (404 if state == "missing" else 409)
    finally:
        if state == "missing_target":
            await db.owner.execute("UPDATE suspension_events SET target_id=$2 WHERE suspension_id=$1", saved.suspension_id, target)


async def test_appeal_submission_cannot_bless_unaudited_caller_policy(account_db):
    db = account_db
    saved = await new_hold(db)
    await db.owner.execute("UPDATE suspension_events SET audit_log_id=NULL WHERE suspension_id=$1", saved.suspension_id)
    result = await submit(db, saved)
    metadata = json.loads(await db.owner.fetchval("SELECT metadata FROM audit_logs WHERE event_id=$1", result["audit_event_id"]))
    assert "formal_account_hold_version" not in metadata
    with pytest.raises(HTTPException) as denied:
        await db.service.review_appeal(saved.suspension_id, db.actor, "granted", "Reviewed")
    assert denied.value.status_code == 409
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_reviewed_decision_cannot_be_rewritten_or_granted_by_a_stale_request(account_db, target):
    db = account_db
    saved = await new_hold(db, target)
    await submit(db, saved)
    first = await db.service.review_appeal(saved.suspension_id, db.actor, "denied", "Reviewed")
    for decision, response in [("denied", "Changed answer"), ("granted", "Delayed grant")]:
        with pytest.raises(HTTPException) as conflict:
            await db.service.review_appeal(saved.suspension_id, db.actor, decision, response)
        assert conflict.value.status_code == 409
    row = await db.owner.fetchrow("SELECT is_active,appeal_decision,audit_log_id FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert tuple(row.values()) == (True, "denied", first["audit_event_id"])


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_concurrent_grant_and_denial_have_one_committed_winner(account_db, target):
    db = account_db
    saved = await new_hold(db, target)
    await submit(db, saved)
    outcomes = await asyncio.gather(
        db.service.review_appeal(saved.suspension_id, db.actor, "granted", "Grant evidence"),
        db.service.review_appeal(saved.suspension_id, db.actor, "denied", "Deny evidence"), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    failure = next(item for item in outcomes if isinstance(item, Exception))
    assert isinstance(failure, HTTPException) and failure.status_code == 409
    winner = next(item for item in outcomes if isinstance(item, dict))
    event = await db.owner.fetchrow("SELECT is_active,appeal_decision FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    assert event["appeal_decision"] == winner["decision"]
    assert event["is_active"] is (winner["decision"] == "denied")


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_concurrent_submission_and_denial_never_reopen_the_appeal(account_db, target):
    db = account_db
    saved = await new_hold(db, target)
    outcomes = await asyncio.gather(submit(db, saved),
        db.service.review_appeal(saved.suspension_id, db.actor, "denied", "Reviewed"), return_exceptions=True)
    assert isinstance(outcomes[1], dict)
    assert isinstance(outcomes[0], dict) or (isinstance(outcomes[0], HTTPException) and outcomes[0].status_code == 409)
    assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1", saved.suspension_id) == "denied"


@pytest.mark.parametrize("demoted", [False, True])
async def test_actual_http_keeps_principal_role_check_and_rechecks_database_actor(account_db, demoted):
    import httpx
    from fastapi import FastAPI
    from app.api.v1 import dependencies
    db = account_db
    saved = await new_hold(db)
    principal = dependencies.CurrentUser(id=str(db.actor), email="synthetic@example.invalid", role="platform_admin")
    app = FastAPI()
    # Authentication input is synthetic; the actual role dependency and PG
    # authority recheck remain in the route/service path.
    app.dependency_overrides[dependencies.get_current_user] = lambda: principal
    app.dependency_overrides[routes.get_suspension_service] = lambda: db.service
    app.include_router(routes.router)
    if demoted:
        await db.owner.execute("UPDATE user_profiles SET role='user' WHERE id=$1", db.actor)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as http:
        response = await http.post(f"/admin/suspensions/{saved.suspension_id}/appeal", json={"appeal_reason":"Reviewed HTTP input"})
    assert response.status_code == (403 if demoted else 200), response.text
    event = await db.owner.fetchrow("SELECT appeal_decision,audit_log_id FROM suspension_events WHERE suspension_id=$1", saved.suspension_id)
    if demoted:
        assert event["appeal_decision"] is None and event["audit_log_id"] == saved.audit_event_id
    else:
        assert str(event["audit_log_id"]) == response.json()["audit_event_id"]


async def test_waiting_submission_rechecks_actor_after_real_administration_fence(account_db, monkeypatch):
    from app.core.db_utils import acquire_with_tenant
    from app.domain.services import suspension_service as module
    db = account_db
    saved = await new_hold(db)
    pid = None
    real_lock = db.audit.lock_transaction
    async def observed_lock(conn):
        nonlocal pid
        pid = conn.get_server_pid()
        await real_lock(conn)
    monkeypatch.setattr(db.audit, "lock_transaction", observed_lock)
    task = None
    try:
        async with acquire_with_tenant(db.pool, None) as conn:
            await real_lock(conn)
            await module.lock_user_administration(conn)
            task = asyncio.create_task(submit(db, saved))
            for _ in range(200):
                if pid and await db.owner.fetchval("SELECT cardinality(pg_blocking_pids($1))>0", pid):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("Submission did not wait on the actual audit/administrator fence")
            await conn.execute("UPDATE user_profiles SET role='user' WHERE id=$1", db.actor)
        with pytest.raises(HTTPException) as denied:
            await task
        assert denied.value.status_code == 403
        assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1", saved.suspension_id) is None
    finally:
        if task:
            await asyncio.gather(task, return_exceptions=True)
