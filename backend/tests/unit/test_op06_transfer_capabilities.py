"""Actual voice capability/prompt paths; synthetic database and adapter only."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.telephony import adapter_registry, inbound_transfer
from app.domain.services.voice_pipeline import action_execution
from app.domain.services.voice_pipeline.action_tools import (
    action_tool_system_addendum,
    action_tools_for_turn,
)


TENANT = "11111111-1111-4111-8111-111111111111"
CAMPAIGN = "22222222-2222-4222-8222-222222222222"
CONFIG = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
DESTINATION = "+15555550123"


@pytest.fixture
def voice(monkeypatch):
    row = {
        "call_id": "synthetic-call", "provider_call_id": "synthetic-provider-call",
        "lead_id": None, "provider": "asterisk", "call_status": "in_progress",
        "campaign_id": CAMPAIGN, "campaign_status": "active",
        "direction": "inbound", "call_direction": "inbound",
        "admission_status": "allowed", "processing_status": "active",
        "billing_status": "reserved", "reserved_seconds": 120,
        "script_config": {"campaign_brief": {
            "approved_next_actions": ["transfer"], "transfer_destination": DESTINATION,
        }},
        "route_snapshot": {
            "route": {"config_id": CONFIG, "called_did": "+15555550999"},
            "inbound_config": {"transfer_policy": {
                "enabled": True, "destinations": [DESTINATION],
            }},
        },
    }
    state = SimpleNamespace(row=row, platform_enabled=True, platform_error=False,
                            platform_reads=0, connected=True)

    class Connection:
        async def fetchrow(self, query, *args):
            assert args == (TENANT, CAMPAIGN, "synthetic-call")
            return deepcopy(state.row)

        async def fetchval(self, query, *args):
            if "platform_runtime_controls" in query:
                state.platform_reads += 1
                if state.platform_error:
                    raise ConnectionError("synthetic unavailable policy store")
                return state.platform_enabled
            assert "connectors" in query
            return False

    @asynccontextmanager
    async def acquire(_pool, tenant):
        assert tenant == TENANT
        yield Connection()

    monkeypatch.setattr(action_execution, "acquire_with_tenant", acquire)
    monkeypatch.setattr(adapter_registry, "get_adapter", lambda: SimpleNamespace(connected=state.connected))
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "true")
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", TENANT)
    monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID", CONFIG)
    state.session = SimpleNamespace(tenant_id=TENANT, campaign_id=CAMPAIGN,
                                    call_id="synthetic-call", _voice_action_pool=object())
    return state


def assert_not_offered(session, actions):
    assert "transfer_call" not in actions
    prompt = action_tool_system_addendum(actions)
    assert "Available actions for this call: end_call." in prompt
    assert "unlisted transfer or team follow-up route is unavailable" in prompt
    provider = SimpleNamespace(supports_tools=True, stream_chat_with_tools=lambda: None)
    tools = action_tools_for_turn(
        [Message(role=MessageRole.USER, content="Please transfer me to a person.")],
        provider, session=session,
    )
    assert [tool["function"]["name"] for tool in tools] == ["end_call"]


@pytest.mark.asyncio
async def test_production_inbound_is_not_offered_despite_connected_adapter_and_saved_policy(voice, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert inbound_transfer.CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE is False
    assert_not_offered(voice.session, await action_execution.prepare_voice_action_context(voice.session))


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_gate", [
    "tenant_scope", "config_scope", "snapshot", "malformed_snapshot", "policy_disabled",
    "destination", "self_target", "platform_disabled", "platform_unavailable",
    "adapter_disconnected", "not_admitted", "not_active", "not_reserved", "no_reservation",
])
async def test_each_unavailable_inbound_gate_removes_offer(voice, monkeypatch, missing_gate):
    snapshot = voice.row["route_snapshot"]
    if missing_gate == "tenant_scope":
        monkeypatch.setenv("INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID", OTHER)
    elif missing_gate == "config_scope":
        snapshot["route"]["config_id"] = OTHER
    elif missing_gate == "snapshot":
        voice.row["route_snapshot"] = None
    elif missing_gate == "malformed_snapshot":
        voice.row["route_snapshot"] = {"route": [], "inbound_config": "invalid-json"}
    elif missing_gate == "policy_disabled":
        snapshot["inbound_config"]["transfer_policy"]["enabled"] = False
    elif missing_gate == "destination":
        snapshot["inbound_config"]["transfer_policy"]["destinations"] = ["+15555550456"]
    elif missing_gate == "self_target":
        snapshot["route"]["called_did"] = DESTINATION
    elif missing_gate == "platform_disabled":
        voice.platform_enabled = False
    elif missing_gate == "platform_unavailable":
        voice.platform_error = True
    elif missing_gate == "adapter_disconnected":
        voice.connected = False
    elif missing_gate == "not_admitted":
        voice.row["admission_status"] = "denied"
    elif missing_gate == "not_active":
        voice.row["processing_status"] = "completed"
    elif missing_gate == "not_reserved":
        voice.row["billing_status"] = "settled"
    elif missing_gate == "no_reservation":
        voice.row["reserved_seconds"] = 0
    assert_not_offered(voice.session, await action_execution.prepare_voice_action_context(voice.session))


@pytest.mark.asyncio
async def test_allowed_staging_scope_can_offer_without_executing_transfer(voice):
    actions = await action_execution.prepare_voice_action_context(voice.session)
    assert "transfer_call" in actions
    assert voice.platform_reads == 1


@pytest.mark.asyncio
async def test_call_direction_cannot_be_overridden_by_campaign_direction(voice, monkeypatch):
    voice.row["direction"] = "outbound"
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert_not_offered(voice.session, await action_execution.prepare_voice_action_context(voice.session))


@pytest.mark.asyncio
async def test_explicit_outbound_keeps_existing_transfer_capability(voice, monkeypatch):
    voice.row.update(direction="outbound", call_direction="outbound", route_snapshot=None)
    voice.row["script_config"]["campaign_brief"]["transfer_destination"] = "sales-extension"
    voice.platform_error = True
    monkeypatch.setenv("ENVIRONMENT", "production")
    actions = await action_execution.prepare_voice_action_context(voice.session)
    assert "transfer_call" in actions
    assert voice.platform_reads == 0


@pytest.mark.asyncio
async def test_refresh_removes_capability_after_platform_gate_closes(voice):
    assert "transfer_call" in await action_execution.prepare_voice_action_context(voice.session)
    voice.platform_enabled = False
    actions = await action_execution.prepare_voice_action_context(voice.session, refresh=True)
    assert_not_offered(voice.session, actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("closed_gate", ["runtime", "platform"])
async def test_direct_tool_rechecks_policy_despite_cached_offer(voice, monkeypatch, closed_gate):
    assert "transfer_call" in await action_execution.prepare_voice_action_context(voice.session)
    if closed_gate == "runtime":
        monkeypatch.setenv("ENVIRONMENT", "production")
    else:
        voice.platform_enabled = False

    async def forbidden_effect(*args, **kwargs):
        pytest.fail("Unavailable transfer must not claim or execute an action")

    monkeypatch.setattr(action_execution.DurableActionExecutor, "execute", forbidden_effect)
    monkeypatch.setattr(adapter_registry, "execute_transfer", forbidden_effect)
    result = await action_execution.execute_connected_voice_action(
        voice.session, "transfer_call", {}, "Please transfer me to a person.",
    )
    assert result["status"] == "unavailable"
    assert result["success"] is False
    assert result["confirmation_allowed"] is False
    assert not getattr(voice.session, "_voice_action_proposals", {})
