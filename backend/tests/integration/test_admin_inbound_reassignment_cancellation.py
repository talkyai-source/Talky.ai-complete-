"""Actual normal-auth cancellation of stranded requests; no provider effects.

The existing owned fixture supplies real PostgreSQL, current principals and
mandatory audit. Synchronization wrappers only pause real methods; no auth,
query results or cancellation receipts are manufactured.
"""
# Imported pytest fixture names intentionally match injected parameters.
# ruff: noqa: F811
import asyncio
from datetime import datetime
import json
from uuid import UUID, uuid4

import pytest

from app.domain.services.inbound_campaign_service import InboundCampaignService
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call, crud_db, reviewed_write, signed_in,  # noqa: F401
)
from tests.integration.test_admin_inbound_current_authority import inbound_authority_db  # noqa: F401
from tests.integration.test_admin_inbound_reassignment_reads import reassignment_db  # noqa: F401

pytestmark = pytest.mark.integration


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


def decision(value):
    """Audit JSON uses spaces; HTTP JSON uses T/Z for the same UTC instants."""
    result = dict(obj(value))
    for field in ("requested_at", "decided_at"):
        if result.get(field) is not None:
            result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
    return result


async def create_request(db, actor, version=1):
    response = await call(db, actor, "POST", "/admin/inbound/reassignments",
        headers={"Idempotency-Key": "cancel-test-create-"+str(uuid4())}, json={
            "assignment_id": str(db.inbound_ids["assignment"]), "target_tenant_id": str(db.tenants[2]),
            "target_campaign_id": str(db.read_ids["campaign"]), "expected_version": version,
            "reason": "Synthetic reviewed reassignment"})
    assert response.status_code == 201, response.text
    return response.json()


async def decide(db, actor, row, action="cancel", *, key=None, reason="Synthetic cancellation reviewed"):
    return await call(db, actor, "POST", f"/admin/inbound/reassignments/{row['id']}/{action}",
        headers={"Idempotency-Key": key or "cancel-test-"+str(uuid4())}, json={"reason": reason})


async def assignment(db):
    return dict(await db.owner.fetchrow("SELECT * FROM inbound_did_assignments WHERE id=$1", db.inbound_ids["assignment"]))


async def test_stranded_request_can_be_cancelled_then_replaced_without_reactivation(reassignment_db):
    db = reassignment_db
    async with signed_in(db, 0) as requester, signed_in(db, 1) as approver:
        original = await create_request(db, requester)
        released = await call(db, requester, "POST", f"/admin/inbound/assignments/{db.inbound_ids['assignment']}/unquarantine",
            headers={"Idempotency-Key": "cancel-test-release-"+str(uuid4())},
            json={"expected_version": 2, "reason": "Synthetic reviewed source restoration"})
        assert released.status_code == 200 and released.json()["status"] == "paused"
        stale = await decide(db, approver, original, "approve")
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "assignment_state_changed"
        blocked = await call(db, requester, "POST", "/admin/inbound/reassignments",
            headers={"Idempotency-Key": "cancel-test-blocked-"+str(uuid4())}, json={
                "assignment_id": str(db.inbound_ids["assignment"]), "target_tenant_id": str(db.tenants[2]),
                "target_campaign_id": str(db.read_ids["campaign"]), "expected_version": released.json()["version"],
                "reason": "Synthetic replacement request"})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "reassignment_pending"
        before = await assignment(db)
        cancelled = await decide(db, requester, original)
        assert cancelled.status_code == 200, cancelled.text
        assert await assignment(db) == before
        replacement = await create_request(db, requester, version=before["version"])
        assert replacement["id"] != original["id"] and replacement["status"] == "pending"
        assert (await assignment(db))["status"] == "quarantined"
        assert (await call(db, approver, "GET", f"/admin/inbound/reassignments/{original['id']}")).json()["status"] == "cancelled"
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_did_assignments WHERE config_id=$1", db.read_ids["config"]) == 0
    assert await db.owner.fetchval("SELECT assignment_id FROM calls WHERE id=$1", db.read_ids["call"]) == db.inbound_ids["assignment"]


@pytest.mark.parametrize("cancel_actor", [0, 1])
async def test_cancellation_preserves_quarantine_when_target_is_unavailable(reassignment_db, cancel_actor):
    db = reassignment_db
    async with signed_in(db, 0) as requester, signed_in(db, 1) as other:
        row = await create_request(db, requester)
        # Exact owned setup, not a fake service result. Cancellation must not need
        # target readiness or change source quarantine to recover this request.
        await db.owner.execute("UPDATE inbound_campaign_configs SET status='archived' WHERE id=$1", db.read_ids["config"])
        before = await assignment(db)
        response = await decide(db, requester if cancel_actor == 0 else other, row)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["status"] == "cancelled" and saved["approved_by"] is None and saved["approved_assignment_id"] is None
        assert saved["decision_reason"] == "Synthetic cancellation reviewed" and saved["decided_at"]
        assert saved["requested_by"] == row["requested_by"] and saved["target_campaign_id"] == row["target_campaign_id"]
    assert before["status"] == "quarantined" and await assignment(db) == before
    assert await db.owner.fetchval("SELECT status FROM inbound_campaign_configs WHERE id=$1", db.read_ids["config"]) == "archived"
    audit = await db.owner.fetchrow("SELECT * FROM inbound_audit_events WHERE resource_id=$1 AND event_type='inbound_reassignment_cancelled'", UUID(row["id"]))
    assert audit["actor_id"] == db.users[cancel_actor] and audit["actor_role"] == "platform_admin"
    assert decision(obj(audit["before_state"])["request"]) == decision(row)
    assert decision(obj(audit["after_state"])["request"]) == decision(saved)


async def test_same_actor_replay_and_fresh_read_recover_original_cancellation(reassignment_db):
    db = reassignment_db
    key = "cancel-replay-"+str(uuid4())
    async with signed_in(db, 0) as actor, signed_in(db, 1) as other:
        row = await create_request(db, actor)
        original = await decide(db, actor, row, key=key, reason="  Synthetic cancellation reviewed  ")
        assert original.status_code == 200, original.text
        replay = await decide(db, actor, row, key=key)
        assert replay.status_code == 200 and replay.json() == original.json()
        changed = await decide(db, actor, row, key=key, reason="Different cancellation review")
        foreign = await decide(db, other, row, key=key)
        for denied in (changed, foreign):
            assert denied.status_code == 409 and denied.json()["error"]["code"] == "idempotency_mismatch"
        terminal = await decide(db, actor, row)
        assert terminal.status_code == 409 and terminal.json()["error"]["code"] == "already_decided"
    # Fresh normal session for the other admin; do not replay the requester's
    # consumed TOTP within its timestep just to test a durable read.
    async with signed_in(db, 1) as fresh:
        exact = await call(db, fresh, "GET", f"/admin/inbound/reassignments/{row['id']}")
        history = await call(db, fresh, "GET", "/admin/inbound/reassignments?status=cancelled&limit=100")
        assert exact.status_code == 200 and exact.json() == original.json()
        assert next(item for item in history.json()["items"] if item["id"] == row["id"]) == original.json()
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_audit_events WHERE resource_id=$1 AND event_type='inbound_reassignment_cancelled'", UUID(row["id"])) == 1
    receipt = await db.owner.fetchrow("SELECT response_body,status_code FROM inbound_operation_idempotency WHERE operation=$1 AND idempotency_key=$2",
        "platform_inbound_reassignment.cancel:"+row["id"], key)
    assert receipt["status_code"] == 200 and decision(receipt["response_body"]) == decision(original.json())


async def test_approved_request_cannot_be_cancelled(reassignment_db):
    db = reassignment_db
    async with signed_in(db, 0) as requester, signed_in(db, 1) as approver:
        row = await create_request(db, requester)
        approved = await decide(db, approver, row, "approve")
        assert approved.status_code == 200, approved.text
        before = await assignment(db)
        denied = await decide(db, requester, row)
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "already_decided"
        assert (await call(db, requester, "GET", f"/admin/inbound/reassignments/{row['id']}")).json() == approved.json()
    assert await assignment(db) == before
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_audit_events WHERE resource_id=$1 AND event_type='inbound_reassignment_cancelled'", UUID(row["id"])) == 0


async def test_denied_missing_and_invalid_cancellation_have_no_effect(reassignment_db):
    db = reassignment_db
    async with signed_in(db, 0) as actor, signed_in(db, 2) as ordinary:
        row = await create_request(db, actor)
        before = await assignment(db)
        assert (await decide(db, ordinary, row)).status_code == 403
        missing = await decide(db, actor, {"id": str(uuid4())})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "reassignment_not_found"
        assert (await decide(db, actor, {"id": "not-a-uuid"})).status_code == 422
        for reason in ("  ", "xx", "x"*2001):
            assert (await decide(db, actor, row, reason=reason)).status_code == 422
        assert (await call(db, actor, "GET", f"/admin/inbound/reassignments/{row['id']}")).json()["status"] == "pending"
    assert await assignment(db) == before


async def test_required_audit_failure_rolls_back_cancellation_and_operation(reassignment_db):
    db = reassignment_db
    key = "cancel-audit-"+str(uuid4())
    async with signed_in(db, 0) as actor:
        row = await create_request(db, actor)
        before = await assignment(db)
        await db.owner.execute(f'REVOKE INSERT ON inbound_audit_events FROM "{db.read_role}"')
        try:
            failed = await decide(db, actor, row, key=key)
            assert failed.status_code == 503 and failed.json()["error"]["code"] == "inbound_admin_outcome_unconfirmed"
        finally:
            await db.owner.execute(f'GRANT INSERT ON inbound_audit_events TO "{db.read_role}"')
        assert (await call(db, actor, "GET", f"/admin/inbound/reassignments/{row['id']}")).json() == row
        assert await db.owner.fetchval("SELECT count(*) FROM inbound_operation_idempotency WHERE idempotency_key=$1", key) == 0
        assert (await decide(db, actor, row, key=key)).status_code == 200
    assert await assignment(db) == before


@pytest.mark.parametrize("revocation", ["block", "demote"])
async def test_cancellation_rechecks_actor_after_normal_dependency_admission(reassignment_db, monkeypatch, revocation):
    db = reassignment_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = InboundCampaignService.cancel_reassignment

    async def delayed(service, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return await original(service, **kwargs)

    async with signed_in(db, 0) as actor, signed_in(db, 1) as revoker:
        row = await create_request(db, actor)
        monkeypatch.setattr(InboundCampaignService, "cancel_reassignment", delayed)
        task = asyncio.create_task(decide(db, actor, row))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            changed = await reviewed_write(db, revoker, "PATCH", db.users[0],
                **({"is_active": False} if revocation == "block" else {"role": "readonly"}))
            assert changed.status_code == 200, changed.text
            release.set()
            denied = await asyncio.wait_for(task, 5)
            assert denied.status_code == 403
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await db.owner.fetchval("SELECT status FROM inbound_reassignment_requests WHERE id=$1", UUID(row["id"])) == "pending"
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_audit_events WHERE resource_id=$1 AND event_type='inbound_reassignment_cancelled'", UUID(row["id"])) == 0


@pytest.mark.parametrize("winner", ["cancel", "approve"])
async def test_cancel_and_approve_serialize_to_one_terminal_decision(reassignment_db, monkeypatch, winner):
    db = reassignment_db
    entered, release, other_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_claim = InboundCampaignService._claim_operation
    other = "approve" if winner == "cancel" else "cancel"
    original_other = getattr(InboundCampaignService, f"{other}_reassignment")

    async def pause_claim(service, conn, **kwargs):
        result = await original_claim(service, conn, **kwargs)
        if kwargs["operation"].startswith(f"platform_inbound_reassignment.{winner}:"):
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return result

    async def observe_other(service, **kwargs):
        other_entered.set()
        return await original_other(service, **kwargs)

    async with signed_in(db, 0) as requester, signed_in(db, 1) as approver:
        row = await create_request(db, requester)
        monkeypatch.setattr(InboundCampaignService, "_claim_operation", pause_claim)
        monkeypatch.setattr(InboundCampaignService, f"{other}_reassignment", observe_other)
        actors = {"cancel": requester, "approve": approver}
        first = asyncio.create_task(decide(db, actors[winner], row, winner))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second = asyncio.create_task(decide(db, actors[other], row, other))
            await asyncio.wait_for(other_entered.wait(), 5)
            release.set()
            accepted, refused = await asyncio.gather(first, second)
            assert accepted.status_code == 200, accepted.text
            assert refused.status_code == 409 and refused.json()["error"]["code"] == "already_decided"
        finally:
            release.set()
            tasks = [task for task in (first, second) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    expected = "cancelled" if winner == "cancel" else "approved"
    assert await db.owner.fetchval("SELECT status FROM inbound_reassignment_requests WHERE id=$1", UUID(row["id"])) == expected
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_audit_events WHERE resource_id=$1 AND event_type IN ('inbound_reassignment_cancelled','inbound_reassignment_approved')", UUID(row["id"])) == 1
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_did_assignments WHERE config_id=$1", db.read_ids["config"]) == int(winner == "approve")
