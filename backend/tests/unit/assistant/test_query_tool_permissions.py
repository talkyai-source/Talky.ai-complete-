"""Assistant read tools respect the user's role (QA F4-04, QA-SEC-026/123).

Only action tools were authorized, so a readonly or billing user could read
calls, leads, email and calendar through the assistant although the REST API
refuses them the same data.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.security.rbac import ROLE_DEFAULT_PERMISSIONS, UserRole
from app.infrastructure.assistant.tools import ALL_TOOLS, QUERY_TOOLS, dispatch

TENANT = "10000000-0000-0000-0000-000000000001"
ACTOR = "40000000-0000-0000-0000-000000000004"


def _as(monkeypatch, role):
    async def permissions(*_args, **_kwargs):
        return {permission.value for permission in ROLE_DEFAULT_PERMISSIONS[role]}

    monkeypatch.setattr(dispatch, "get_effective_permissions", permissions)


def _tool_ran(monkeypatch, name):
    ran = []

    async def tool(*_args, **_kwargs):
        ran.append(name)
        return {"ok": True}

    monkeypatch.setitem(ALL_TOOLS[name], "function", tool)
    return ran


async def _run(name, actor=ACTOR):
    return await dispatch.dispatch_tool(
        name, TENANT, SimpleNamespace(pool=object()), None, {}, actor_user_id=actor,
    )


def test_every_query_tool_has_an_authorization_rule():
    assert set(QUERY_TOOLS) == set(dispatch.QUERY_TOOL_PERMISSIONS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,tool,required",
    [
        (UserRole.BILLING_USER, "get_recent_calls", "calls:read"),
        (UserRole.BILLING_USER, "get_leads", "calls:read"),
        (UserRole.BILLING_USER, "read_emails", "connectors:read"),
        (UserRole.READONLY, "read_calendar_events", "calendar:read"),
        (UserRole.READONLY, "get_usage_info", "billing:read"),
    ],
)
async def test_a_role_without_the_permission_cannot_read_through_the_assistant(
    monkeypatch, role, tool, required,
):
    _as(monkeypatch, role)
    ran = _tool_ran(monkeypatch, tool)
    result = await _run(tool)
    assert result["error"] == "permission_denied" and result["required"] == required
    assert ran == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_recent_calls", "read_calendar_events", "get_usage_info"])
async def test_the_account_owner_still_reads_everything(monkeypatch, tool):
    _as(monkeypatch, UserRole.TENANT_ADMIN)
    ran = _tool_ran(monkeypatch, tool)
    assert (await _run(tool)) == {"ok": True} and ran == [tool]


@pytest.mark.asyncio
async def test_a_read_without_an_authenticated_user_is_refused(monkeypatch):
    _as(monkeypatch, UserRole.TENANT_ADMIN)
    ran = _tool_ran(monkeypatch, "get_dashboard_stats")
    result = await _run("get_dashboard_stats", actor=None)
    assert result["error"] == "permission_denied" and ran == []
