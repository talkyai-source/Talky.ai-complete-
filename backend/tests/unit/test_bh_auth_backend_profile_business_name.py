"""auth-sessions-10: PATCH /auth/me must not let a low-privilege member rename
the tenant. Unchanged business_name (the settings form re-sends it) and name
updates stay available to every role."""
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.auth.schemas import UpdateMeRequest

profile = importlib.import_module("app.api.v1.endpoints.auth.profile")

TENANT = str(uuid4())
USER = str(uuid4())


class Conn:
    def __init__(self):
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock(return_value={
            "id": USER, "email": "u@example.com", "name": "N", "role": "readonly",
            "tenant_id": TENANT, "business_name": "New Biz", "minutes_allocated": 0,
            "minutes_used": 0})

    @asynccontextmanager
    async def transaction(self):
        yield

    def tenant_updates(self):
        return [c for c in self.execute.await_args_list if "UPDATE tenants" in c.args[0]]


@pytest.fixture
def env(monkeypatch):
    conn = Conn()

    @asynccontextmanager
    async def acquire(pool, tenant_id, **_):
        yield conn

    meter = SimpleNamespace(allowance=lambda: {"minutes_remaining": None, "minutes_state": "unavailable"})
    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status",
                        AsyncMock(return_value=meter))
    audit = SimpleNamespace(log=AsyncMock())
    return conn, audit


def user(role, business_name="Old Biz"):
    return CurrentUser(id=USER, email="u@example.com", tenant_id=TENANT, role=role,
                       name="N", business_name=business_name)


async def _patch(env, role, **body):
    conn, audit = env
    return await profile.update_me(UpdateMeRequest(**body), user(role),
                                   SimpleNamespace(pool=object()), audit)


@pytest.mark.parametrize("role", ["readonly", "user", "agent", "billing_user", "campaign_manager"])
async def test_low_privilege_member_cannot_rename_the_tenant(env, role):
    conn, audit = env
    with pytest.raises(HTTPException) as caught:
        await _patch(env, role, business_name="Hijacked Inc")
    assert caught.value.status_code == 403
    assert conn.tenant_updates() == []
    audit.log.assert_not_awaited()


@pytest.mark.parametrize("role", ["tenant_admin", "owner", "platform_admin"])
async def test_tenant_admin_can_rename_and_it_is_audited(env, role):
    conn, audit = env
    await _patch(env, role, business_name="New Biz")
    updates = conn.tenant_updates()
    assert len(updates) == 1
    assert updates[0].args[1:] == ("New Biz", TENANT)
    audit.log.assert_awaited_once()
    kwargs = audit.log.await_args.kwargs
    assert kwargs["tenant_id"] == TENANT and kwargs["actor_id"] == USER
    assert kwargs["before_state"] == {"business_name": "Old Biz"}
    assert kwargs["after_state"] == {"business_name": "New Biz"}


async def test_readonly_can_still_rename_themselves(env):
    conn, _ = env
    await _patch(env, "readonly", name="New Name")
    assert any("UPDATE user_profiles SET name" in c.args[0] for c in conn.execute.await_args_list)


async def test_unchanged_business_name_from_the_profile_form_is_not_a_403(env):
    conn, audit = env
    await _patch(env, "readonly", name="New Name", business_name="Old Biz")
    assert conn.tenant_updates() == []
    audit.log.assert_not_awaited()


async def test_empty_body_early_return_is_untouched(env):
    conn, _ = env
    result = await _patch(env, "readonly")
    assert result.business_name == "Old Biz"
    conn.execute.assert_not_awaited()
