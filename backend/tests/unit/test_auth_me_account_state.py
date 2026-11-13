"""G5 / D4-4: /auth/me tells the customer app about an Admin "Suspend".

Admin "Suspend" writes ``tenants.subscription_status = 'suspended'`` while a
formal hold writes ``tenants.status = 'suspended'``. Before this change
/auth/me read only ``tenants.status``, so an Admin suspension never reached
the customer app's banner.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import re

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.endpoints.auth import profile


TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
ACTOR = "bbccddee-1234-4abc-8def-0123456789ab"


@pytest.fixture
def mounted(monkeypatch):
    state = SimpleNamespace(subscription="active", tenant_status="active", partner_status=None, reads=0)

    class Connection:
        async def fetchrow(self, query, *args):
            assert args == (ACTOR, TENANT)
            state.reads += 1
            row = {"tenant_id": TENANT, "partner_id": None, "tenant_status": state.tenant_status,
                   "partner_status": state.partner_status, "tenant_suspended_at": None,
                   "tenant_suspension_reason": None, "partner_suspended_at": None,
                   "partner_suspension_reason": None, "account_timezone": None}
            # Only a query that actually selects the column can return it.
            if re.search(r"t\.subscription_status\s+AS\s+subscription_status", query, re.I):
                row["subscription_status"] = state.subscription
            return row

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Connection()

    async def acceptance_status(conn, *, tenant_id, user_id):
        return {"legal_acceptance_required": False}

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.legal_acceptance.acceptance_status", acceptance_status)
    user = SimpleNamespace(id=ACTOR, tenant_id=TENANT, email="owner@example.test", name="Owner",
                           business_name="Synthetic", role="tenant_admin", minutes_remaining=None,
                           minutes_state="unavailable")
    app = FastAPI()
    app.include_router(profile.router, prefix="/api/v1/auth")
    app.dependency_overrides[profile.get_current_user] = lambda: user
    app.dependency_overrides[profile.get_db_client] = lambda: SimpleNamespace(pool=object())
    return app, state


async def _me(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    return response.json()


async def test_admin_suspend_reaches_auth_me_and_resume_clears_it(mounted):
    app, state = mounted
    state.subscription = "suspended"
    suspended = await _me(app)
    state.subscription = "active"
    resumed = await _me(app)

    assert suspended["subscription_status"] == "suspended"
    assert suspended["account_state"] == "suspended_subscription"
    assert suspended["account_state_reason"] == "subscription_suspended"
    assert resumed["subscription_status"] == "active"
    assert resumed["account_state"] == "active"
    assert resumed["account_state_reason"] is None
    assert state.reads == 2, "still one profile read per request"


@pytest.mark.parametrize("stored,expected", [
    ("past_due", "past_due"), ("canceled", "cancelled"), ("  Suspended ", "suspended"),
    ("inactive", "inactive"), (None, None), ("", None),
])
async def test_subscription_status_is_returned_in_the_canonical_spelling(mounted, stored, expected):
    app, state = mounted
    state.subscription = stored
    body = await _me(app)
    assert body["subscription_status"] == expected
    # Only an Admin suspension is a suspension; billing states stay billing states.
    assert body["account_state"] == ("suspended_subscription" if expected == "suspended" else "active")


@pytest.mark.parametrize("tenant_status,partner_status", [("suspended", None), ("active", "suspended")])
async def test_a_formal_hold_outranks_the_subscription_state(mounted, tenant_status, partner_status):
    app, state = mounted
    state.subscription = "suspended"
    state.tenant_status = tenant_status
    state.partner_status = partner_status
    body = await _me(app)
    assert body["account_state"] == "suspended_hold"
    assert body["account_state_reason"] == ("tenant_hold" if tenant_status == "suspended" else "partner_hold")
    assert body["subscription_status"] == "suspended"
