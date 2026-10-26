"""Original control backend is committed by the actual migrated call CAS.

Disposable localhost *_test DB only; restricted tenant role; no PBX/network
provider calls. Reuses the existing durable outbound fixture and ownership.
"""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.api.v1.endpoints import telephony_bridge as bridge
from app.domain.services.telephony.termination import mark_termination_pending_and_load_context
from tests.integration.test_op05_pre_attempt_holds import hold_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def bound_call(hold_db):  # noqa: F811 - reuse the imported pytest fixture
    db = hold_db
    call_id = uuid4()
    await db.admin.execute(f'GRANT SELECT,UPDATE ON calls TO "{db.role}"')
    await db.admin.execute(f'GRANT SELECT ON call_legs TO "{db.role}"')
    await db.admin.execute(
        """INSERT INTO calls(id,tenant_id,campaign_id,lead_id,dialer_job_id,
           dialer_attempt_number,talklee_call_id,phone_number,status,direction,route_snapshot)
           VALUES($1,$2,$3,$4,$5,1,$6,'+15550001001','initiated','outbound',
                  '{"preserve_original_route":true}'::jsonb)""",
        call_id,
        db.tenant,
        db.campaign,
        db.lead,
        db.job_id,
        "AD10" + uuid4().hex[:16],
    )
    intent = bridge._InternalDialerIntent(
        call_id=str(call_id),
        tenant_id=str(db.tenant),
        campaign_id=str(db.campaign),
        lead_id=str(db.lead),
        dialer_job_id=str(db.job_id),
        attempt_number=1,
        talklee_call_id="fixture",
        destination="+15550001001",
        status="initiated",
        provider=None,
        provider_call_id=None,
    )
    try:
        yield SimpleNamespace(db=db, call_id=call_id, intent=intent)
    finally:
        await db.admin.execute("DELETE FROM calls WHERE id=$1", call_id)


async def claim(value, binding):
    return await bridge._claim_internal_dialer_intent(
        value.db.pool,
        intent=value.intent,
        provider="asterisk",
        planned_provider_call_id="talky-out-" + str(value.call_id),
        control_backend=binding,
    )


async def test_original_backend_is_atomic_before_provider_and_survives_alias_binding(bound_call):
    value = bound_call
    binding = "pbx1:" + "a" * 64
    won, _ = await claim(value, binding)
    assert won
    snapshot = json.loads(
        await value.db.admin.fetchval("SELECT route_snapshot FROM calls WHERE id=$1", value.call_id)
    )
    assert snapshot == {"preserve_original_route": True, "control_backend": binding}
    await bridge._bind_durable_outbound_call_provider(
        value.db.pool,
        tenant_id=str(value.db.tenant),
        durable_call_id=str(value.call_id),
        provider="asterisk",
        provider_call_id="actual-" + str(value.call_id),
    )
    context = await mark_termination_pending_and_load_context(
        value.db.pool, call_reference=str(value.call_id), tenant_id=str(value.db.tenant)
    )
    assert context.control_backend == binding
    assert context.provider_call_id == "actual-" + str(value.call_id)
    saved_after_ack = json.loads(
            await value.db.admin.fetchval(
                "SELECT route_snapshot FROM calls WHERE id=$1", value.call_id
            )
        )
    assert all(saved_after_ack[key] == item for key, item in snapshot.items())
    assert saved_after_ack["provider_acknowledgement"]["provider"] == "asterisk"
    assert saved_after_ack["provider_acknowledgement"]["provider_call_id"] == "actual-" + str(value.call_id)
    assert saved_after_ack["provider_acknowledgement"]["control_backend"] == binding
    assert saved_after_ack["provider_acknowledgement"]["acknowledged_at"]


async def test_competing_backend_claim_cannot_replace_original_identity(bound_call):
    value = bound_call
    bindings = ["pbx1:" + "a" * 64, "pbx1:" + "b" * 64]
    results = await asyncio.gather(*(claim(value, binding) for binding in bindings))
    assert [won for won, _ in results].count(True) == 1
    winner = next(binding for binding, (won, _) in zip(bindings, results) if won)
    saved = json.loads(
        await value.db.admin.fetchval("SELECT route_snapshot FROM calls WHERE id=$1", value.call_id)
    )
    assert saved == {"preserve_original_route": True, "control_backend": winner}
    assert all(
        result.provider_call_id == "talky-out-" + str(value.call_id) for _, result in results
    )
