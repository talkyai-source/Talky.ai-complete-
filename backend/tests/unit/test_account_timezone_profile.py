"""H F1-32: read the account zone in the existing owned profile projection."""
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
    state = SimpleNamespace(zone="America/New_York", reads=[], leases=[])

    class Connection:
        async def fetchrow(self, query, *args):
            assert "up.id = $1 AND up.tenant_id IS NOT DISTINCT FROM $2::uuid" in query
            assert args == (ACTOR, TENANT)
            state.reads.append(query)
            row = {"tenant_id": TENANT, "partner_id": None, "tenant_status": "active",
                   "partner_status": None, "tenant_suspended_at": None,
                   "tenant_suspension_reason": None, "partner_suspended_at": None,
                   "partner_suspension_reason": None}
            # Model the actual projection, not a column the old SELECT never
            # returned. The fake executes no settings or cross-tenant lookup.
            if re.search(r"t\.calling_rules\s*->>\s*'timezone'\s+AS\s+account_timezone", query, re.I):
                row["account_timezone"] = state.zone
            return row

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        assert tenant_id == TENANT
        state.leases.append(tenant_id)
        yield Connection()

    async def acceptance_status(conn, *, tenant_id, user_id):
        assert (tenant_id, user_id) == (TENANT, ACTOR)
        return {"legal_acceptance_required": False}

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.legal_acceptance.acceptance_status", acceptance_status)
    user = SimpleNamespace(id=ACTOR, tenant_id=TENANT, email="reader@example.test", name="Reader",
                           business_name="Synthetic", role="readonly", minutes_remaining=None,
                           minutes_state="unavailable")
    app = FastAPI()
    app.include_router(profile.router, prefix="/api/v1/auth")
    app.dependency_overrides[profile.get_current_user] = lambda: user
    app.dependency_overrides[profile.get_db_client] = lambda: SimpleNamespace(pool=object())
    return app, state


@pytest.mark.parametrize("saved,expected", [
    ("America/New_York", "America/New_York"),
    ("Asia/Karachi", "Asia/Karachi"),
    (None, None), ("", None), ("Not/AZone", None), ("123", None),
], ids=["new-york", "karachi", "missing", "empty", "invalid", "non-zone"])
async def test_mounted_me_returns_valid_account_zone_without_an_extra_settings_query(mounted, saved, expected):
    app, state = mounted
    state.zone = saved
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "account_timezone" in body, "The verified account profile must expose its timezone contract"
    assert body["account_timezone"] == expected
    assert body["tenant_id"] == TENANT
    assert len(state.reads) == len(state.leases) == 1


async def test_profile_refresh_observes_the_new_owned_account_zone(mounted):
    app, state = mounted
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        first = await client.get("/api/v1/auth/me")
        state.zone = "Asia/Karachi"
        refreshed = await client.get("/api/v1/auth/me")
    assert first.status_code == refreshed.status_code == 200
    assert first.json().get("account_timezone") == "America/New_York"
    assert refreshed.json().get("account_timezone") == "Asia/Karachi"
    assert len(state.reads) == len(state.leases) == 2, "One existing profile read per refresh, never a second settings query"
