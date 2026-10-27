"""Exact-event evidence on migrated restricted PostgreSQL; no provider egress."""

import asyncio
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.domain.services import formal_enforcement as proof
from app.domain.services.suspension_service import SuspensionType
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401
from tests.integration import test_formal_account_holds as account_fixtures
from tests.integration.test_formal_user_credential_hold import credentials

account_db = account_fixtures.account_db
pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def proof_db(account_db):
    db = account_db
    await db.owner.execute(f'GRANT SELECT ON calls,call_legs,call_events TO "{db.role}"')
    db.old = await credentials(db, db.target)
    db.call_ids = []
    try:
        yield db
    finally:
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            await db.owner.execute(
                "DELETE FROM call_events WHERE call_id=ANY($1::uuid[])", db.call_ids
            )
            await db.owner.execute(
                "DELETE FROM call_legs WHERE call_id=ANY($1::uuid[])", db.call_ids
            )
            await db.owner.execute("DELETE FROM calls WHERE id=ANY($1::uuid[])", db.call_ids)


async def hold(db, target="tenant", **kwargs):
    if target == "user":
        return await db.service.suspend_user(
            db.target,
            SuspensionType.ADMIN,
            "synthetic",
            "Evidence fixture",
            suspended_by=db.actor,
            notify_user=False,
            **kwargs,
        )
    return await account_fixtures.hold(db, target, **kwargs)


async def call(db, *, answered=False):
    identity = uuid4()
    db.call_ids.append(identity)
    binding = "pbx1:" + "a" * 64
    await db.owner.execute(
        """INSERT INTO calls(id,tenant_id,phone_number,status,direction,provider,provider_call_id,route_snapshot,answered_at)
        VALUES($1,$2,'+15550001111','in_progress','inbound','asterisk',$3,$4,
            CASE WHEN $5 THEN clock_timestamp()-INTERVAL '1 minute' ELSE NULL END)""",
        identity,
        db.tenant_id,
        f"channel-{identity}",
        json.dumps({"control_backend": binding}),
        answered,
    )
    return identity


async def termination_receipt(db, identity, *, version=3):
    from datetime import datetime, timezone

    row = await db.owner.fetchrow("SELECT * FROM calls WHERE id=$1", identity)
    await db.owner.execute("UPDATE calls SET status='termination_pending' WHERE id=$1", identity)
    receipt = {
        "version": version,
        "call_id": str(identity),
        "tenant_id": str(db.tenant_id),
        "provider": "asterisk",
        "provider_call_id": row["provider_call_id"],
        "control_backend": "pbx1:" + "a" * 64,
        "provider_hangup_confirmed": True,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "legs": [],
    }
    if version == 3:
        receipt.update(leg_inventory=[], inventory_coverage="complete_preproof")
    async with db.owner.transaction():
        await db.owner.execute("SET LOCAL app.bypass_rls='on'")
        await db.owner.execute(
            "INSERT INTO call_events(call_id,event_type,event_data) VALUES($1,'admin_termination_confirmed',$2)",
            identity,
            json.dumps(receipt),
        )


def dimension(result, name):
    return next(item for item in result["dimensions"] if item["key"] == name)


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_new_hold_captures_original_credentials_and_never_fakes_runtime_coverage(
    proof_db, target
):
    db = proof_db
    saved = await hold(db, target)
    status = await db.service.get_propagation_status(saved.suspension_id)
    credential = dimension(status, "credentials")
    assert credential["status"] == "confirmed", status
    assert credential["coverage"] == "complete" and credential["counts"]["captured"] >= 1
    assert status["original_audit_event_id"] == saved.audit_event_id
    assert dimension(status, "assistant_runtimes")["coverage"] == "unknown"
    assert status["propagation_status"] != "confirmed"
    assert dimension(status, "telephony")["status"] == (
        "not_applicable" if target == "user" else "confirmed"
    )
    assert status["current_authority"]["status"] == "restricted"


async def test_appeal_and_restore_pointer_cannot_replace_original_suspend_proof(proof_db):
    db = proof_db
    saved = await hold(db)
    appeal = await db.service.submit_appeal(
        saved.suspension_id, "Reviewed evidence", submitted_by=db.actor
    )
    assert appeal["audit_event_id"] != saved.audit_event_id
    restored = await db.service.restore_tenant(
        db.tenant_id, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    status = await db.service.get_propagation_status(saved.suspension_id)
    assert (
        status["original_audit_event_id"] == saved.audit_event_id
        and status["audit_event_id"] == restored.audit_event_id
    )
    assert dimension(status, "credentials")["status"] == "confirmed"
    assert status["event_state"] == "restored"


@pytest.mark.parametrize("corruption", ["remove", "tamper", "caller"])
async def test_unknown_original_scope_or_forgery_cannot_become_complete(proof_db, corruption):
    db = proof_db
    saved = await hold(
        db,
        evidence=(
            {proof.EVIDENCE_KEY: {"version": 1, "cohort": {"credentials": {}}}}
            if corruption == "caller"
            else None
        ),
    )
    if corruption != "caller":
        evidence = json.loads(
            await db.owner.fetchval(
                "SELECT evidence FROM suspension_events WHERE suspension_id=$1", saved.suspension_id
            )
        )
        if corruption == "remove":
            del evidence[proof.EVIDENCE_KEY]
        else:
            evidence[proof.EVIDENCE_KEY]["cohort"]["credentials"] = {}
        await db.owner.execute(
            "UPDATE suspension_events SET evidence=$2 WHERE suspension_id=$1",
            saved.suspension_id,
            json.dumps(evidence),
        )
    status = await db.service.get_propagation_status(saved.suspension_id)
    credential = dimension(status, "credentials")
    assert credential["status"] == ("confirmed" if corruption == "caller" else "unverified")
    assert status["propagation_status"] != "confirmed"


async def test_actual_runtime_registration_and_stop_are_a_confirmed_subset_not_fleet_proof(
    proof_db,
):
    from app.core.security.assistant_runtime import AssistantRuntime

    db = proof_db
    runtime = await AssistantRuntime.register(
        db.pool,
        {"sub": str(db.target), "tenant_id": str(db.tenant_id), "sid": str(db.old.sid)},
        endpoint_kind="chat",
    )
    saved = await hold(db, "user")
    first = dimension(
        await db.service.get_propagation_status(saved.suspension_id), "assistant_runtimes"
    )
    assert first["counts"]["captured"] == 1 and first["counts"]["unknown"] == 1
    assert await runtime.record_stopped()
    final = await db.service.get_propagation_status(saved.suspension_id)
    runtime_proof = dimension(final, "assistant_runtimes")
    assert runtime_proof["counts"]["confirmed"] == 1 and runtime_proof["counts"]["unknown"] == 0
    assert runtime_proof["coverage"] == "unknown" and final["propagation_status"] != "confirmed"


async def test_unrelated_audit_pointer_cannot_be_reported_as_original_suspend_proof(proof_db):
    db = proof_db
    saved = await hold(db)
    appeal = await db.service.submit_appeal(
        saved.suspension_id, "Reviewed concern", submitted_by=db.actor
    )
    evidence = json.loads(
        await db.owner.fetchval(
            "SELECT evidence FROM suspension_events WHERE suspension_id=$1", saved.suspension_id
        )
    )
    evidence[proof.EVIDENCE_KEY]["original_suspend_audit_id"] = str(appeal["audit_event_id"])
    await db.owner.execute(
        "UPDATE suspension_events SET evidence=$2 WHERE suspension_id=$1",
        saved.suspension_id,
        json.dumps(evidence),
    )
    result = await db.service.get_propagation_status(saved.suspension_id)
    # Original action+target+event lookup recovers the real immutable audit.
    assert result["original_audit_event_id"] == saved.audit_event_id
    assert result["original_audit_event_id"] != appeal["audit_event_id"]


async def test_non_immediate_hold_does_not_score_revocation_as_failure(proof_db):
    db = proof_db
    saved = await hold(db, immediate=False)
    status = await db.service.get_propagation_status(saved.suspension_id)
    assert dimension(status, "credentials")["status"] == "not_applicable"
    assert not await db.owner.fetchval(
        "SELECT revoked FROM security_sessions WHERE id=$1", db.old.sid
    )


async def test_active_hold_detects_later_unrevoked_credential_but_restored_event_ignores_new_login(
    proof_db,
):
    db = proof_db
    saved = await hold(db, "user")
    later = uuid4()
    await db.owner.execute(
        "INSERT INTO security_sessions(id,user_id,session_token_hash,expires_at) VALUES($1,$2,$3,NOW()+INTERVAL '1 day')",
        later,
        db.target,
        uuid4().hex,
    )
    assert (
        dimension(await db.service.get_propagation_status(saved.suspension_id), "credentials")[
            "counts"
        ]["pending"]
        == 1
    )
    await db.service.restore_user(
        db.target, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    assert (
        dimension(await db.service.get_propagation_status(saved.suspension_id), "credentials")[
            "status"
        ]
        == "confirmed"
    )


async def test_read_failure_is_503_and_unknown_event_404(proof_db):
    db = proof_db
    with pytest.raises(HTTPException) as missing:
        await db.service.get_propagation_status(uuid4())
    assert missing.value.status_code == 404
    saved = await hold(db)
    await db.owner.execute(f'REVOKE SELECT ON call_events FROM "{db.role}"')
    with pytest.raises(HTTPException) as unavailable:
        await db.service.get_propagation_status(saved.suspension_id)
    assert unavailable.value.status_code == 503


async def test_repeatable_read_does_not_mix_late_credential_change_into_one_snapshot(
    proof_db, monkeypatch
):
    db = proof_db
    saved = await hold(db, "user")
    entered, release = asyncio.Event(), asyncio.Event()
    original = proof._credentials

    async def pause(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(proof, "_credentials", pause)
    task = asyncio.create_task(db.service.get_propagation_status(saved.suspension_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await db.owner.execute(
            "UPDATE security_sessions SET revoked=FALSE,revoked_at=NULL WHERE id=$1", db.old.sid
        )
        release.set()
        assert dimension(await task, "credentials")["status"] == "confirmed"
        assert (
            dimension(await db.service.get_propagation_status(saved.suspension_id), "credentials")[
                "status"
            ]
            == "pending"
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("version", [2, 3])
async def test_only_complete_v3_inventory_proves_recorded_call_effect(proof_db, version):
    db = proof_db
    identity = await call(db)
    saved = await hold(db)
    before = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert before["counts"]["pending"] == 1
    await termination_receipt(db, identity, version=version)
    after = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert after["counts"]["confirmed"] == int(version == 3)
    assert (after["status"] == "confirmed") is (version == 3)


@pytest.mark.parametrize("change", ["late_terminal_leg", "binding"])
async def test_late_rows_and_changed_original_binding_invalidate_full_call_proof(proof_db, change):
    db = proof_db
    identity = await call(db)
    saved = await hold(db)
    await termination_receipt(db, identity)
    assert (
        dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")[
            "status"
        ]
        == "confirmed"
    )
    if change == "late_terminal_leg":
        await db.owner.execute(
            "INSERT INTO call_legs(call_id,leg_type,provider,provider_leg_id,status) VALUES($1,'pstn_transfer','asterisk','late','completed')",
            identity,
        )
    else:
        await db.owner.execute(
            "UPDATE calls SET route_snapshot=jsonb_set(route_snapshot,'{control_backend}',to_jsonb($2::text)) WHERE id=$1",
            identity,
            "pbx1:" + "b" * 64,
        )
    changed = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert changed["status"] != "confirmed" and changed["counts"]["confirmed"] == 0


@pytest.mark.parametrize("answered", [False, True])
async def test_non_immediate_call_exemption_uses_actual_effect_and_shared_authority(
    proof_db, answered
):
    db = proof_db
    await call(db, answered=answered)
    saved = await hold(db, immediate=False)
    status = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert status["counts"]["exempt"] == int(answered)
    assert status["status"] == ("exempt_by_policy" if answered else "pending")
    await hold(db, "partner")
    overlap = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert overlap["counts"]["exempt"] == 0 and overlap["status"] == "pending"


async def test_calls_created_after_capture_are_observed_and_restored_event_ignores_later_legitimate_calls(
    proof_db,
):
    db = proof_db
    saved = await hold(db)
    identity = await call(db)
    status = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert status["counts"]["captured"] == 0 and status["counts"]["pending"] == 1
    await termination_receipt(db, identity)
    await db.service.restore_tenant(
        db.tenant_id, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    await call(db)
    status = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert status["counts"]["pending"] == 0


async def test_restoring_hold_does_not_hide_an_unproven_call_created_during_it(proof_db):
    db = proof_db
    saved = await hold(db)
    await call(db)
    await db.service.restore_tenant(
        db.tenant_id, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    result = dimension(await db.service.get_propagation_status(saved.suspension_id), "telephony")
    assert result["counts"]["captured"] == 0 and result["counts"]["observed"] == 1
    assert result["counts"]["pending"] == 1 and result["status"] == "pending"


async def test_user_historical_proof_reports_current_parent_hold_separately(proof_db):
    db = proof_db
    saved = await hold(db, "user")
    await db.service.restore_user(
        db.target, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    parent = await hold(db, "partner")
    result = await db.service.get_propagation_status(saved.suspension_id)
    assert result["event_state"] == "restored"
    assert result["current_authority"]["status"] == "restricted"
    assert str(parent.suspension_id) in result["current_authority"]["remaining_hold_ids"]


async def test_user_status_reports_current_parent_restriction_after_user_restore(proof_db):
    db = proof_db
    saved = await hold(db, "user")
    await db.service.restore_user(
        db.target, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    before = await db.service.get_status(user_id=db.target)
    assert before.effective_status == "active" and before.remaining_hold_ids == []
    parent = await hold(db, "partner")
    after = await db.service.get_status(user_id=db.target)
    assert after.active_suspension is None  # The user's own event was restored.
    assert after.is_suspended and after.effective_status == "restricted"
    assert after.remaining_hold_ids == [parent.suspension_id]


async def test_partner_ordinary_restriction_is_not_reported_as_an_active_formal_hold(proof_db):
    db = proof_db
    saved = await hold(db, "partner")
    await db.service.restore_partner(
        db.partner, db.actor, "Reviewed", expected_suspension_id=saved.suspension_id
    )
    await db.owner.execute(
        "UPDATE white_label_partners SET status='suspended' WHERE id=$1", db.partner
    )
    result = await db.service.get_propagation_status(saved.suspension_id)
    assert result["event_state"] == "restored"
    assert result["current_authority"] == {"status": "restricted", "remaining_hold_ids": []}


async def test_read_cap_and_capture_cap_are_explicit_not_zero_or_complete(proof_db, monkeypatch):
    db = proof_db
    await credentials(db, db.target)
    monkeypatch.setattr(proof, "MAX_ROWS", 1)
    saved = await hold(db)
    status = await db.service.get_propagation_status(saved.suspension_id)
    assert dimension(status, "credentials")["coverage"] == "incomplete"
    assert status["propagation_status"] != "confirmed"
    assert any("capture_truncated" in reason for reason in status["reconciliation_reasons"])


async def test_optional_capture_permission_failure_does_not_prevent_security_hold(proof_db):
    db = proof_db
    await db.owner.execute(f'REVOKE SELECT ON calls FROM "{db.role}"')
    saved = await hold(db)
    assert (
        await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", db.tenant_id)
        == "suspended"
    )
    await db.owner.execute(f'GRANT SELECT ON calls TO "{db.role}"')
    status = await db.service.get_propagation_status(saved.suspension_id)
    assert dimension(status, "credentials")["coverage"] != "complete"
    assert any(
        reason.startswith("capture_unavailable:") for reason in status["reconciliation_reasons"]
    )


async def test_partner_scope_includes_saved_reparented_tenant_and_new_current_child(proof_db):
    db = proof_db
    old = await call(db)
    saved = await hold(db, "partner")
    other = uuid4()
    try:
        await db.owner.execute(
            "UPDATE tenants SET white_label_partner_id=NULL WHERE id=$1", db.tenant_id
        )
        await db.owner.execute(
            "INSERT INTO tenants(id,business_name,white_label_partner_id) VALUES($1,'Synthetic later child',$2)",
            other,
            db.partner,
        )
        new = uuid4()
        db.call_ids.append(new)
        await db.owner.execute(
            "INSERT INTO calls(id,tenant_id,phone_number,status,direction,provider,provider_call_id) VALUES($1,$2,'+15550001111','in_progress','inbound','asterisk','new-child')",
            new,
            other,
        )
        status = dimension(
            await db.service.get_propagation_status(saved.suspension_id), "telephony"
        )
        assert status["counts"]["observed"] == 2 and status["counts"]["pending"] == 2
        assert status["coverage"] == "incomplete"
    finally:
        await db.owner.execute("DELETE FROM calls WHERE tenant_id=$1", other)
        await db.owner.execute("DELETE FROM tenants WHERE id=$1", other)


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_status_does_not_invent_an_active_unknown_target(proof_db, target):
    with pytest.raises(HTTPException) as missing:
        await proof_db.service.get_status(**{f"{target}_id": uuid4()})
    assert missing.value.status_code == 404
