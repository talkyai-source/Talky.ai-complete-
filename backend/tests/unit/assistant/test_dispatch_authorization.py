"""Fail-closed authorization contract for assistant action dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.security.rbac import Permission, ROLE_DEFAULT_PERMISSIONS, UserRole
from app.infrastructure.assistant import proposals
from app.infrastructure.assistant.tools import ACTION_TOOLS, ALL_TOOLS
from app.infrastructure.assistant.tools import dispatch as dispatch_module
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS


TENANT_ID = "10000000-0000-0000-0000-000000000001"
ACTOR_ID = "40000000-0000-0000-0000-000000000004"


def _install_tool(monkeypatch, tool_name: str, result=None):
    calls: list[dict] = []

    async def tool(_tenant_id, _db_client, **kwargs):
        calls.append(kwargs)
        return result or {"success": True}

    monkeypatch.setitem(ALL_TOOLS[tool_name], "function", tool)
    return calls


def _permissions(monkeypatch, initial):
    state = {"permissions": set(initial), "calls": 0}

    async def resolve(pool, user_id, tenant_id):
        assert pool is not None
        assert user_id == ACTOR_ID
        assert tenant_id == TENANT_ID
        state["calls"] += 1
        return set(state["permissions"])

    monkeypatch.setattr(dispatch_module, "get_effective_permissions", resolve)
    return state


def _db():
    return SimpleNamespace(pool=object())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args", "grants", "required"),
    [
        ("create_campaign", {"name": "New", "confirm": False}, {Permission.CAMPAIGNS_READ}, "campaigns:create"),
        ("start_campaign", {"campaign_id": "c1"}, {Permission.CAMPAIGNS_READ}, "campaigns:update"),
        ("update_campaign_config", {"campaign_id": "c1", "changes": {}, "confirm": False}, {Permission.CAMPAIGNS_READ}, "campaigns:update"),
        ("manage_lead", {"campaign_id": "c1", "action": "add", "confirm": False}, {Permission.CAMPAIGNS_READ}, "campaigns:update"),
        ("apply_campaign_voice", {"campaign_ids": ["c1"], "confirm": False}, {Permission.CAMPAIGNS_READ}, "campaigns:update"),
        ("initiate_call", {"phone_number": "+14155551234"}, {Permission.BILLING_UPDATE}, "calls:create"),
    ],
)
async def test_action_permissions_deny_before_tool_execution(
    monkeypatch,
    tool_name,
    args,
    grants,
    required,
):
    calls = _install_tool(monkeypatch, tool_name)
    _permissions(monkeypatch, grants)

    result = await dispatch_module.dispatch_tool(
        tool_name,
        TENANT_ID,
        _db(),
        None,
        args,
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "permission_denied"
    assert result["required"] == required
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "tool_name", "args", "required"),
    [
        (UserRole.READONLY, "start_campaign", {"campaign_id": "c1"}, "campaigns:update"),
        (
            UserRole.AGENT,
            "update_campaign_config",
            {"campaign_id": "c1", "changes": {}, "confirm": False},
            "campaigns:update",
        ),
        (
            UserRole.BILLING_USER,
            "initiate_call",
            {"phone_number": "+14155551234"},
            "calls:create",
        ),
    ],
)
async def test_canonical_non_mutating_roles_cannot_use_assistant_as_bypass(
    monkeypatch,
    role,
    tool_name,
    args,
    required,
):
    calls = _install_tool(monkeypatch, tool_name)
    _permissions(monkeypatch, ROLE_DEFAULT_PERMISSIONS[role])

    result = await dispatch_module.dispatch_tool(
        tool_name,
        TENANT_ID,
        _db(),
        None,
        args,
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "permission_denied"
    assert result["required"] == required
    assert calls == []


@pytest.mark.asyncio
async def test_current_grant_allows_and_revocation_blocks_next_execution(monkeypatch):
    calls = _install_tool(monkeypatch, "start_campaign")
    permission_state = _permissions(monkeypatch, {Permission.CAMPAIGNS_UPDATE})

    allowed = await dispatch_module.dispatch_tool(
        "start_campaign",
        TENANT_ID,
        _db(),
        None,
        {"campaign_id": "c1"},
        actor_user_id=ACTOR_ID,
    )
    permission_state["permissions"].clear()
    denied = await dispatch_module.dispatch_tool(
        "start_campaign",
        TENANT_ID,
        _db(),
        None,
        {"campaign_id": "c1"},
        actor_user_id=ACTOR_ID,
    )

    assert allowed["success"] is True
    assert denied["error"] == "permission_denied"
    assert len(calls) == 1
    assert permission_state["calls"] == 2


@pytest.mark.asyncio
async def test_campaign_overwrite_requires_create_and_update(monkeypatch):
    calls = _install_tool(monkeypatch, "create_campaign")
    _permissions(monkeypatch, {Permission.CAMPAIGNS_CREATE})

    result = await dispatch_module.dispatch_tool(
        "create_campaign",
        TENANT_ID,
        _db(),
        None,
        {
            "name": "Replacement",
            "overwrite_campaign_id": "campaign-1",
            "confirm": False,
        },
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "permission_denied"
    assert result["required"] == "campaigns:update"
    assert calls == []


@pytest.mark.asyncio
async def test_missing_actor_fails_closed_before_permission_lookup(monkeypatch):
    calls = _install_tool(monkeypatch, "start_campaign")

    async def must_not_resolve(*_args, **_kwargs):
        raise AssertionError("missing actor must fail before database access")

    monkeypatch.setattr(
        dispatch_module,
        "get_effective_permissions",
        must_not_resolve,
    )
    result = await dispatch_module.dispatch_tool(
        "start_campaign",
        TENANT_ID,
        _db(),
        None,
        {"campaign_id": "c1"},
    )

    assert result["error"] == "permission_denied"
    assert calls == []


@pytest.mark.asyncio
async def test_permission_database_failure_is_stable_and_fails_closed(monkeypatch):
    calls = _install_tool(monkeypatch, "start_campaign")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("secret database detail")

    monkeypatch.setattr(dispatch_module, "get_effective_permissions", broken)
    result = await dispatch_module.dispatch_tool(
        "start_campaign",
        TENANT_ID,
        _db(),
        None,
        {"campaign_id": "c1"},
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "authorization_unavailable"
    assert "secret database detail" not in str(result)
    assert calls == []


@pytest.mark.asyncio
async def test_malformed_permission_result_is_stable_and_fails_closed(monkeypatch):
    calls = _install_tool(monkeypatch, "start_campaign")

    async def malformed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dispatch_module, "get_effective_permissions", malformed)
    result = await dispatch_module.dispatch_tool(
        "start_campaign",
        TENANT_ID,
        _db(),
        None,
        {"campaign_id": "c1"},
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "authorization_unavailable"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["send_email", "send_sms", "report_issue"])
async def test_actions_without_canonical_permission_are_explicitly_unsupported(
    monkeypatch,
    tool_name,
):
    calls = _install_tool(monkeypatch, tool_name)
    _permissions(monkeypatch, {Permission.PLATFORM_ADMIN})

    result = await dispatch_module.dispatch_tool(
        tool_name,
        TENANT_ID,
        _db(),
        None,
        {},
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "tool_authorization_policy_unavailable"
    assert calls == []


@pytest.mark.asyncio
async def test_unclassified_action_is_denied_even_to_platform_admin(monkeypatch):
    calls: list[dict] = []

    async def future_action(_tenant_id, _db_client, **kwargs):
        calls.append(kwargs)
        return {"success": True}

    entry = {"function": future_action, "description": "test", "input_schema": None}
    monkeypatch.setitem(ACTION_TOOLS, "future_mutation", entry)
    monkeypatch.setitem(ALL_TOOLS, "future_mutation", entry)
    _permissions(monkeypatch, {Permission.PLATFORM_ADMIN})

    result = await dispatch_module.dispatch_tool(
        "future_mutation",
        TENANT_ID,
        _db(),
        None,
        {},
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "tool_authorization_policy_unavailable"
    assert calls == []


@pytest.mark.asyncio
async def test_model_confirm_true_cannot_bypass_proposal_apply(monkeypatch):
    calls = _install_tool(monkeypatch, "update_campaign_config")
    _permissions(monkeypatch, {Permission.CAMPAIGNS_UPDATE})

    result = await dispatch_module.dispatch_tool(
        "update_campaign_config",
        TENANT_ID,
        _db(),
        None,
        {
            "campaign_id": "c1",
            "changes": {"goal": "changed"},
            "confirm": "true",
            "trusted_proposal_apply": True,
        },
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "proposal_confirmation_required"
    assert calls == []


@pytest.mark.asyncio
async def test_actor_bound_server_apply_can_confirm_after_live_reauthorization(monkeypatch):
    calls = _install_tool(monkeypatch, "update_campaign_config")
    permission_state = _permissions(monkeypatch, {Permission.CAMPAIGNS_UPDATE})
    args = {"campaign_id": "c1", "changes": {"goal": "changed"}}

    preview = await dispatch_module.dispatch_tool(
        "update_campaign_config",
        TENANT_ID,
        _db(),
        None,
        {**args, "confirm": False},
        actor_user_id=ACTOR_ID,
    )
    permission_state["permissions"].clear()
    denied_apply = await dispatch_module.dispatch_tool(
        "update_campaign_config",
        TENANT_ID,
        _db(),
        None,
        {**args, "confirm": True},
        actor_user_id=ACTOR_ID,
        trusted_proposal_apply=True,
    )

    assert preview["success"] is True
    assert denied_apply["error"] == "permission_denied"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_actor_bound_server_apply_with_current_grant_can_confirm(monkeypatch):
    calls = _install_tool(monkeypatch, "update_campaign_config")
    _permissions(monkeypatch, {Permission.CAMPAIGNS_UPDATE})

    result = await dispatch_module.dispatch_tool(
        "update_campaign_config",
        TENANT_ID,
        _db(),
        None,
        {"campaign_id": "c1", "changes": {}, "confirm": True},
        actor_user_id=ACTOR_ID,
        trusted_proposal_apply=True,
    )

    assert result["success"] is True
    assert calls == [{"campaign_id": "c1", "changes": {}, "confirm": True}]


@pytest.mark.asyncio
async def test_non_exposed_nested_action_plan_stays_default_denied(monkeypatch):
    calls = _install_tool(monkeypatch, "execute_action_plan")
    _permissions(monkeypatch, {Permission.PLATFORM_ADMIN})

    result = await dispatch_module.dispatch_tool(
        "execute_action_plan",
        TENANT_ID,
        _db(),
        None,
        {"intent": "send and dial", "actions": []},
        actor_user_id=ACTOR_ID,
    )

    assert result["error"] == "tool_authorization_policy_unavailable"
    assert calls == []


def test_every_llm_exposed_action_has_an_explicit_authorization_policy():
    exposed_names = {
        schema["function"]["name"]
        for schema in GROQ_TOOL_SCHEMAS
        if schema.get("type") == "function"
    }
    exposed_actions = exposed_names & set(ACTION_TOOLS)

    assert exposed_actions <= set(dispatch_module.ACTION_AUTHORIZATION_POLICIES)
    assert dispatch_module.ACTION_AUTHORIZATION_POLICIES["update_knowledge_node"].delegated
    assert not dispatch_module.ACTION_AUTHORIZATION_POLICIES["send_email"].supported
    assert proposals.PROPOSAL_TOOLS <= set(ACTION_TOOLS)
