"""Shared durable receipt on migrated restricted-role PostgreSQL; no PBX I/O."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.domain.services.telephony import termination_receipts as receipts
from app.domain.services.telephony.termination import mark_termination_pending_and_load_context
from tests.integration.test_control_backend_binding import bound_call, claim  # noqa: F401
from tests.integration.test_op05_pre_attempt_holds import hold_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def proof_call(bound_call):  # noqa: F811
    value = bound_call
    db = value.db
    await db.admin.execute(f'GRANT SELECT,INSERT ON call_events TO "{db.role}"')
    assert (await claim(value, "pbx1:" + "a" * 64))[0]
    value.leg_id = uuid4()
    await db.admin.execute(
        """INSERT INTO call_legs(id,call_id,leg_type,provider,provider_leg_id,status)
        VALUES($1,$2,'pstn_transfer','asterisk','child','in_progress')""",
        value.leg_id, value.call_id,
    )
    value.context = await mark_termination_pending_and_load_context(
        db.pool, call_reference=str(value.call_id), tenant_id=str(db.tenant),
    )
    try:
        yield value
    finally:
        async with db.admin.transaction():
            await db.admin.execute("SET LOCAL app.bypass_rls='on'")
            await db.admin.execute("DELETE FROM call_events WHERE call_id=$1", value.call_id)
        await db.admin.execute("DELETE FROM call_legs WHERE call_id=$1", value.call_id)


async def count(value):
    return await value.db.admin.fetchval(
        "SELECT COUNT(*) FROM call_events WHERE call_id=$1 AND event_type=$2",
        value.call_id, receipts._TERMINATION_RECEIPT_EVENT,
    )


async def test_restricted_writer_saves_original_backend_and_all_proved_legs(proof_call):
    value = proof_call
    assert (await receipts.persist_termination_receipt(value.db.pool, value.context, requested=True)).saved
    receipt = json.loads(await value.db.admin.fetchval(
        "SELECT event_data FROM call_events WHERE call_id=$1", value.call_id,
    ))
    call = dict(await value.db.admin.fetchrow("SELECT * FROM calls WHERE id=$1", value.call_id))
    legs = [dict(row) for row in await value.db.admin.fetch("SELECT * FROM call_legs WHERE call_id=$1", value.call_id)]
    assert receipts.receipt_matches(call, legs, receipt)
    assert receipt["legs"] == [{"id": str(value.leg_id), "provider": "asterisk", "provider_leg_id": "child"}]
    assert receipt["control_backend"] == value.context.control_backend
    assert receipt["version"] == 3
    assert receipt["inventory_coverage"] == "complete_preproof"
    assert receipt["leg_inventory"] == receipt["legs"]
    # Transaction-local bypass used by the service cannot leak to a tenant read.
    async with value.db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT current_setting('app.bypass_rls',true)") in (None, "", "off")


@pytest.mark.parametrize("change", ["parent", "backend", "leg", "late_leg", "late_terminal_leg", "tenant", "reactivated"])
async def test_changed_durable_identity_does_not_save_false_proof(proof_call, change):
    value = proof_call
    context = value.context
    if change == "parent":
        await value.db.admin.execute("UPDATE calls SET provider_call_id='replacement' WHERE id=$1", value.call_id)
    elif change == "backend":
        await value.db.admin.execute("UPDATE calls SET route_snapshot=jsonb_set(route_snapshot,'{control_backend}',to_jsonb($2::text)) WHERE id=$1", value.call_id, "pbx1:" + "b" * 64)
    elif change == "leg":
        await value.db.admin.execute("UPDATE call_legs SET provider_leg_id='replacement' WHERE id=$1", value.leg_id)
    elif change == "late_leg":
        await value.db.admin.execute("INSERT INTO call_legs(call_id,leg_type,provider,provider_leg_id,status) VALUES($1,'pstn_transfer','asterisk','new-child','in_progress')", value.call_id)
    elif change == "late_terminal_leg":
        await value.db.admin.execute("INSERT INTO call_legs(call_id,leg_type,provider,provider_leg_id,status,created_at) VALUES($1,'pstn_transfer','asterisk','late-ended','ended',now()-interval '1 day')", value.call_id)
    elif change == "tenant":
        context = replace(context, tenant_id=str(uuid4()))
    else:
        await value.db.admin.execute("UPDATE calls SET status='in_call' WHERE id=$1", value.call_id)
    result = await receipts.persist_termination_receipt(value.db.pool, context, requested=True)
    assert not result.saved and not result.current_identity_verified and result.code == "identity_changed"
    assert await count(value) == 0


async def test_insert_denial_is_unknown_evidence_and_keeps_pending_call(proof_call):
    value = proof_call
    await value.db.admin.execute(f'REVOKE INSERT ON call_events FROM "{value.db.role}"')
    result = await receipts.persist_termination_receipt(value.db.pool, value.context, requested=True)
    assert not result.saved and result.current_identity_verified and result.code == "storage_unavailable"
    assert await count(value) == 0
    assert await value.db.admin.fetchval("SELECT status FROM calls WHERE id=$1", value.call_id) == "termination_pending"


async def test_snapshot_keeps_prior_terminal_identity_and_rejects_rebinding(proof_call):
    value = proof_call
    old_leg = uuid4()
    await value.db.admin.execute("INSERT INTO call_legs(id,call_id,leg_type,provider,provider_leg_id,status) VALUES($1,$2,'pstn_transfer','asterisk','old-ended','ended')", old_leg, value.call_id)
    context = await mark_termination_pending_and_load_context(
        value.db.pool, call_reference=str(value.call_id), tenant_id=str(value.db.tenant),
    )
    assert context.provider_leg_ids == ("child",)
    assert len(context.leg_inventory) == 2
    assert (await receipts.persist_termination_receipt(value.db.pool, context, requested=True)).saved
    await value.db.admin.execute("UPDATE call_legs SET provider_leg_id='changed-ended' WHERE id=$1", old_leg)
    assert not (await receipts.persist_termination_receipt(value.db.pool, context, requested=True)).saved
    assert await count(value) == 1


async def test_truncated_inventory_cannot_emit_a_new_weaker_receipt(proof_call):
    value = proof_call
    await value.db.admin.execute("""INSERT INTO call_legs(call_id,leg_type,provider,provider_leg_id,status)
        SELECT $1,'pstn_transfer','asterisk','old-'||n,'ended' FROM generate_series(1,1024) n""", value.call_id)
    context = await mark_termination_pending_and_load_context(
        value.db.pool, call_reference=str(value.call_id), tenant_id=str(value.db.tenant),
    )
    assert context.leg_inventory is None
    assert not (await receipts.persist_termination_receipt(value.db.pool, context, requested=True)).saved
    assert await count(value) == 0


async def test_restart_fences_active_call_then_records_proof_before_settlement(proof_call, monkeypatch):
    from app.core import container
    from app.domain.services.telephony import lifecycle, termination
    value = proof_call
    await value.db.admin.execute("UPDATE calls SET status='in_call' WHERE id=$1", value.call_id)
    row = dict(await value.db.admin.fetchrow("SELECT * FROM calls WHERE id=$1", value.call_id))
    context = {**row, "durable_call_id": str(value.call_id)}
    order = []
    async def proof(*args, **kwargs):
        assert await value.db.admin.fetchval("SELECT status FROM calls WHERE id=$1", value.call_id) == "termination_pending"
        assert kwargs["provider_legs"] == (("child", "asterisk"),)
        order.append("proved")
        return termination.HangupProof(True, True, "confirmed")
    async def settle(*args, **kwargs):
        assert await count(value) == 1
        order.append("settled")
        return True
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=value.db.pool))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: object())
    monkeypatch.setattr(termination, "request_confirmed_hangup", proof)
    monkeypatch.setattr(lifecycle, "_on_call_ended", settle)
    assert await lifecycle._force_end_and_hangup(
        row["provider_call_id"], recovery_context=context, provider_leg_ids=[],
    )
    assert order == ["proved", "settled"]


@pytest.mark.parametrize("change", ["late_active", "rebound", "late_terminal"])
async def test_real_current_inventory_change_after_provider_proof_prevents_recovery_settlement(proof_call, monkeypatch, change):
    from unittest.mock import AsyncMock
    from app.core import container
    from app.domain.services.telephony import lifecycle, termination
    value = proof_call
    await value.db.admin.execute("UPDATE calls SET status='in_call' WHERE id=$1", value.call_id)
    row = dict(await value.db.admin.fetchrow("SELECT * FROM calls WHERE id=$1", value.call_id))
    original = {**row, "durable_call_id": str(value.call_id)}
    async def proof(*_, **kwargs):
        assert kwargs["provider_legs"] == (("child", "asterisk"),)
        if change == "rebound":
            await value.db.admin.execute("UPDATE call_legs SET provider_leg_id='replacement' WHERE id=$1", value.leg_id)
        else:
            await value.db.admin.execute("""INSERT INTO call_legs(call_id,leg_type,provider,provider_leg_id,status,created_at)
                VALUES($1,'pstn_transfer','asterisk','late-child',$2,now()-interval '1 day')""",
                value.call_id, "in_progress" if change == "late_active" else "ended")
        return termination.HangupProof(True, True, "confirmed")
    finalize = AsyncMock()
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=value.db.pool))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: object())
    monkeypatch.setattr(termination, "request_confirmed_hangup", proof)
    monkeypatch.setattr(lifecycle, "_on_call_ended", finalize)
    assert await lifecycle._force_end_and_hangup(row["provider_call_id"], recovery_context=original) is False
    assert original["_awaiting_all_leg_absence_proof"] is True
    assert "_pbx_all_leg_absence_confirmed" not in original
    finalize.assert_not_awaited()
    assert await count(value) == 0
    assert await value.db.admin.fetchval("SELECT status FROM calls WHERE id=$1", value.call_id) == "termination_pending"


@pytest.mark.parametrize("cancel", [False, True])
async def test_locked_database_bounds_save_and_cancellation_preserves_caller(proof_call, monkeypatch, cancel):
    value = proof_call
    monkeypatch.setattr(receipts, "_RECEIPT_WRITE_TIMEOUT_SECONDS", 0.15)
    async with value.db.admin.transaction():
        await value.db.admin.fetchrow("SELECT id FROM calls WHERE id=$1 FOR UPDATE", value.call_id)
        task = asyncio.create_task(receipts.persist_termination_receipt(value.db.pool, value.context, requested=True))
        if cancel:
            await asyncio.sleep(0.04)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await asyncio.wait_for(task, 2)).current_identity_verified is False
    assert await count(value) == 0
    # Lock release/retry saves actual evidence; canceled transactions did not poison the pool.
    monkeypatch.setattr(receipts, "_RECEIPT_WRITE_TIMEOUT_SECONDS", 5.0)
    assert (await receipts.persist_termination_receipt(value.db.pool, value.context, requested=False)).saved
