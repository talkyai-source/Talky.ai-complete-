"""voice-pipeline-5: connected voice actions must resolve the tenant and call
through lead_slot_capture.resolve_call_binding, not session.tenant_id/call_id.

On a cascaded call voice_orchestrator builds CallSession without a tenant, and
session.call_id is a random uuid4 that matches no calls row; the authoritative
ids are the _dialer_* values bind_telephony_call stamps. Before the fix the
action layer saw no context and offered only end_call.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline import action_execution as ax
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState

TENANT = "11111111-1111-1111-1111-111111111111"
CAMPAIGN = "22222222-2222-2222-2222-222222222222"
DB_CALL = "33333333-3333-3333-3333-333333333333"
RANDOM_SESSION_CALL = "44444444-4444-4444-4444-444444444444"


class _Conn:
    def __init__(self, log):
        self.log = log

    async def fetchrow(self, _sql, *args):
        self.log["fetchrow_args"] = args
        tenant, campaign, call = args
        if (tenant, campaign, call) != (TENANT, CAMPAIGN, DB_CALL):
            return None  # the real query matches calls.id / provider ids only
        return {
            "call_id": DB_CALL, "provider_call_id": "pbx-1", "lead_id": "lead-1",
            "provider": "asterisk", "call_status": "in_progress", "campaign_id": CAMPAIGN,
            "campaign_status": "running", "direction": "outbound",
            "script_config": {"campaign_brief": {
                "approved_next_actions": ["send_email"],
                "email_action": {"subject": "Details", "body": "Approved text"},
            }},
            "phone_number": "+14155552671", "do_not_call": False, "lead_status": "new",
            "call_direction": "outbound", "route_snapshot": None, "admission_status": None,
            "processing_status": None, "billing_status": None, "reserved_seconds": 0,
        }

    async def fetchval(self, *_a):
        return True


@pytest.fixture
def db(monkeypatch):
    log = {"acquire_tenants": []}

    @asynccontextmanager
    async def _acquire(_pool, tenant):
        log["acquire_tenants"].append(tenant)
        yield _Conn(log)

    monkeypatch.setattr(ax, "acquire_with_tenant", _acquire)
    return log


def _cascaded_session(**stamps):
    # What voice_orchestrator hands the pipeline: no tenant, a random call id.
    return SimpleNamespace(
        tenant_id=None, campaign_id=CAMPAIGN, call_id=RANDOM_SESSION_CALL,
        _voice_action_pool=object(), **stamps,
    )


@pytest.mark.asyncio
async def test_cascaded_call_is_offered_connected_actions_via_dialer_binding(db):
    session = _cascaded_session(_dialer_call_id=DB_CALL, _dialer_tenant_id=TENANT)
    actions = await ax.prepare_voice_action_context(session)
    assert "send_email" in actions, actions
    assert db["fetchrow_args"] == (TENANT, CAMPAIGN, DB_CALL)
    assert set(db["acquire_tenants"]) == {TENANT}


@pytest.mark.asyncio
async def test_missing_binding_is_not_cached_as_no_actions_forever(db):
    # bind_telephony_call has not completed yet: nothing resolvable.
    session = _cascaded_session()
    first = await ax.prepare_voice_action_context(session)
    assert first == {"end_call"}

    # Binding completes before a later turn; the lookup must run again.
    session._dialer_call_id = DB_CALL
    session._dialer_tenant_id = TENANT
    second = await ax.prepare_voice_action_context(session)
    assert "send_email" in second, second


@pytest.mark.asyncio
async def test_session_that_already_carries_tenant_and_call_still_works(db):
    # The realtime path hands over a namespace with the real ids directly.
    session = SimpleNamespace(
        tenant_id=TENANT, campaign_id=CAMPAIGN, call_id=DB_CALL, _voice_action_pool=object(),
    )
    assert "send_email" in await ax.prepare_voice_action_context(session)


@pytest.mark.asyncio
async def test_execution_uses_the_bound_tenant_not_session_tenant(db, monkeypatch):
    seen = {}

    class _Executor:
        def __init__(self, _pool):
            pass

        async def execute(self, **kw):
            seen.update(kw)
            return {"success": True}

    monkeypatch.setattr(ax, "DurableActionExecutor", _Executor)
    monkeypatch.setattr(ax, "_confirmed", lambda *_a: True)
    capture = ContactCaptureState(
        kind="email", status=CaptureStatus.CONFIRMED, normalized_value="caller@example.test",
        confirmed_at=datetime.now(timezone.utc), from_caller=True,
    )
    session = _cascaded_session(_dialer_call_id=DB_CALL, _dialer_tenant_id=TENANT)
    session.captured_slots = SimpleNamespace(email_capture=capture)
    session.turn_id = 2

    await ax.execute_connected_voice_action(session, "send_email", {}, "yes please")

    assert seen.get("tenant_id") == TENANT, seen
    assert seen.get("call_id") == DB_CALL
