"""TP3 actual migrated account policy/ack binding; no live PBX or provider I/O."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.api.v1.endpoints import telephony_bridge as bridge
from app.domain.services.telephony import lifecycle
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401
from tests.integration.test_formal_account_holds import account_db, hold  # noqa: F401

pytestmark = pytest.mark.integration
BINDING = "pbx1:" + "b" * 64


@pytest_asyncio.fixture
async def live_call(account_db, monkeypatch):  # noqa: F811
    from app.core import container as container_module
    db = account_db
    db.call = uuid4()
    db.campaign = uuid4()
    db.channel = "synthetic-tp3-" + uuid4().hex
    await db.owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON calls,call_legs TO "{db.role}"')
    await db.owner.execute(f'GRANT SELECT,UPDATE ON campaigns TO "{db.role}"')
    await db.owner.execute(
        "INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES($1,$2,'Synthetic TP3','running','outbound')",
        db.campaign, db.tenant_id,
    )
    await db.owner.execute(
        """INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,direction,provider,
            provider_call_id,external_call_uuid,route_snapshot)
            VALUES($1,$2,$5,'+15550001001','dialing','outbound','asterisk',$3,$3,$4::jsonb)""",
        db.call, db.tenant_id, db.channel, json.dumps({"control_backend": BINDING}), db.campaign,
    )
    state = SimpleNamespace(is_telephony_owner=lambda: True)
    adapter = SimpleNamespace(name="asterisk")
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_account_hold_scan_after", None)
    monkeypatch.setattr(container_module, "get_container", lambda: SimpleNamespace(db_pool=db.pool))
    try:
        yield db
    finally:
        await db.owner.execute("DELETE FROM call_legs WHERE call_id=$1", db.call)
        await db.owner.execute("DELETE FROM calls WHERE id=$1", db.call)
        await db.owner.execute("DELETE FROM campaigns WHERE id=$1", db.campaign)


async def bind(db):
    await bridge._bind_durable_outbound_call_provider(
        db.pool, tenant_id=str(db.tenant_id), durable_call_id=str(db.call),
        provider="asterisk", provider_call_id=db.channel,
    )


async def saved(db):
    return await db.owner.fetchrow("SELECT * FROM calls WHERE id=$1", db.call)


async def test_actual_provider_ack_precedes_non_immediate_hold_and_survives_rebind(live_call):
    db = live_call
    assert lifecycle._proven_call_started_at(await saved(db)) is None
    await bind(db)
    before = lifecycle._proven_call_started_at(await saved(db))
    assert before is not None
    await hold(db, immediate=False)
    await bind(db)
    assert lifecycle._proven_call_started_at(await saved(db)) == before
    await lifecycle._require_call_authority(str(db.tenant_id), pool=db.pool, existing_call_id=str(db.call))
    with pytest.raises(HTTPException) as error:
        await lifecycle._require_call_authority(str(db.tenant_id), pool=db.pool)
    assert error.value.status_code == 409


async def test_provider_ack_after_hold_cannot_invent_existing_call_exemption(live_call):
    db = live_call
    await hold(db, immediate=False)
    await bind(db)
    with pytest.raises(HTTPException) as error:
        await lifecycle._require_call_authority(str(db.tenant_id), pool=db.pool, existing_call_id=str(db.call))
    assert error.value.status_code == 409


async def test_actual_alias_preserves_ack_time_and_binds_actual_channel(live_call):
    db = live_call
    await bind(db)
    before = lifecycle._proven_call_started_at(await saved(db))
    original = db.channel
    actual = "actual-" + original
    await bridge._persist_durable_outbound_channel_alias(
        db.pool, tenant_id=str(db.tenant_id), durable_call_id=str(db.call),
        provider="asterisk", original_call_id=original, actual_call_id=actual,
    )
    row = await saved(db)
    assert row["provider_call_id"] == actual
    assert lifecycle._proven_call_started_at(row) == before
    # A later HTTP reply for the planned leg cannot erase the proved alias/time.
    await bind(db)
    assert lifecycle._proven_call_started_at(await saved(db)) == before


async def test_actual_owner_query_sees_terminal_parent_with_active_transfer_leg(live_call, monkeypatch):
    db = live_call
    await bind(db)
    await hold(db, immediate=True)
    await db.owner.execute(
        """UPDATE calls SET status='ended',ended_at=now(),provider_terminated_at=now(),
           terminal_settled_at=now() WHERE id=$1""", db.call,
    )
    await db.owner.execute(
        """INSERT INTO call_legs(id,call_id,direction,leg_type,provider,provider_leg_id,status)
           VALUES($1,$2,'outbound','pstn_transfer','asterisk',$3,'in_progress')""",
        uuid4(), db.call, "child-" + db.channel,
    )
    observed = []
    async def capture(row, **_kwargs):
        observed.append(str(row["id"]))
        return False  # Synthetic PBX boundary; never mutate another fixture.
    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", capture)
    await lifecycle._reconcile_account_call_authority()
    await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
    await asyncio.sleep(0)
    assert str(db.call) in observed


async def test_existing_call_scan_excludes_non_immediate_acknowledged_call(live_call, monkeypatch):
    db = live_call
    await bind(db)
    await hold(db, immediate=False)
    observed = []
    async def capture(row, **_kwargs):
        observed.append(str(row["id"]))
        return False
    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", capture)
    await lifecycle._reconcile_account_call_authority()
    await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
    await asyncio.sleep(0)
    assert str(db.call) not in observed
