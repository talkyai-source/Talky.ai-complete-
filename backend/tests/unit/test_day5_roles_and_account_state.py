"""Day 5: D5-8 inbound switch read, D5-9 telephony reads (pinned), D5-27 account restrictions.

Behaviour tests. The role -> permission table is read from ROLE_DEFAULT_PERMISSIONS;
the "current" matrices below were recorded from the code before D5-8 was changed
and are written out literally so a later change to either side fails loudly.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException
import httpx
import pytest

import app.api.v1.endpoints.inbound_campaigns as inbound_endpoints
from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.security.rbac import ROLE_DEFAULT_PERMISSIONS, Permission, UserRole

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
OTHER_TENANT = "11223344-1234-4abc-8def-0123456789ab"
ACTOR = "bbccddee-1234-4abc-8def-0123456789ab"
CONFIG = str(uuid.uuid4())
ALL_ROLES = [role.value for role in UserRole]
ADMIN_ROLES = {"tenant_admin", "partner_admin", "platform_admin"}


def _perms(role: str) -> set:
    return set(ROLE_DEFAULT_PERMISSIONS[UserRole(role)])


async def _call(app, method, path, **kwargs):
    transport = httpx.ASGITransport(app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://synthetic.invalid") as client:
        return await client.request(method, path, **kwargs)


# --------------------------------------------------------------------------
# D5-8: inbound controls
# --------------------------------------------------------------------------

FULL_CONTROLS = {
    "inbound_enabled": False,
    "version": 7,
    "reason": "private operator note",
    "updated_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
}

# GET /inbound-campaigns/controls, who gets 200. BEFORE D5-8: inbound:controls
# (tenant_admin, partner_admin, platform_admin). AFTER: inbound:read.
GET_CONTROLS_BEFORE = {"tenant_admin", "partner_admin", "platform_admin"}
GET_CONTROLS_AFTER = {
    "readonly", "user", "agent", "campaign_manager",
    "tenant_admin", "partner_admin", "platform_admin",
}

# Recorded from the code before the change: every changing route and the
# permissions it needs. (POST "/" needs both manage and assign.)
CHANGING_ROUTES = [
    ("POST", "/inbound-campaigns/", {Permission.INBOUND_MANAGE, Permission.INBOUND_ASSIGN}),
    ("PATCH", "/inbound-campaigns/controls", {Permission.INBOUND_CONTROLS}),
    ("PATCH", f"/inbound-campaigns/{CONFIG}", {Permission.INBOUND_MANAGE}),
    ("PUT", f"/inbound-campaigns/{CONFIG}", {Permission.INBOUND_MANAGE}),
    ("POST", f"/inbound-campaigns/{CONFIG}/assign", {Permission.INBOUND_ASSIGN}),
    ("POST", f"/inbound-campaigns/{CONFIG}/activate", {Permission.INBOUND_MANAGE}),
    ("POST", f"/inbound-campaigns/{CONFIG}/pause", {Permission.INBOUND_MANAGE}),
    ("POST", f"/inbound-campaigns/{CONFIG}/deactivate", {Permission.INBOUND_MANAGE}),
    ("POST", f"/inbound-campaigns/{CONFIG}/archive", {Permission.INBOUND_MANAGE}),
]


@pytest.fixture
def inbound_app(monkeypatch):
    state = SimpleNamespace(role="readonly", tenant=TENANT, tenants_read=[])

    async def effective(pool, user_id, tenant_id):
        return _perms(state.role)

    class FakeService:
        def __init__(self, pool):
            pass

        async def get_tenant_controls(self, *, tenant_id):
            state.tenants_read.append(tenant_id)
            return dict(FULL_CONTROLS)

    monkeypatch.setattr(inbound_endpoints, "get_effective_permissions", effective)
    monkeypatch.setattr(inbound_endpoints, "InboundCampaignService", FakeService)
    app = FastAPI()
    app.include_router(inbound_endpoints.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ACTOR, email="u@example.test", tenant_id=state.tenant, role=state.role
    )
    app.dependency_overrides[get_db_pool] = lambda: object()
    return app, state


@pytest.mark.parametrize("role", ALL_ROLES)
async def test_get_controls_status_for_every_role(inbound_app, role):
    app, state = inbound_app
    state.role = role
    response = await _call(app, "GET", "/api/v1/inbound-campaigns/controls")
    assert (response.status_code == 200) == (role in GET_CONTROLS_AFTER), (role, response.text)
    if role not in GET_CONTROLS_AFTER:
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "permission_denied"


def test_before_and_after_tables_match_the_role_table():
    # AFTER is exactly "holds inbound:read"; BEFORE was exactly "holds inbound:controls".
    assert {r for r in ALL_ROLES if Permission.INBOUND_READ in _perms(r)} == GET_CONTROLS_AFTER
    assert {r for r in ALL_ROLES if Permission.INBOUND_CONTROLS in _perms(r)} == GET_CONTROLS_BEFORE


@pytest.mark.parametrize("role", ALL_ROLES)
async def test_get_controls_shape_depends_on_the_controls_permission(inbound_app, role):
    app, state = inbound_app
    state.role = role
    response = await _call(app, "GET", "/api/v1/inbound-campaigns/controls")
    if role not in GET_CONTROLS_AFTER:
        return
    body = response.json()
    assert body["inbound_enabled"] is False
    if role in GET_CONTROLS_BEFORE:
        assert body["reason"] == "private operator note"
        assert body["updated_at"] is not None
    else:
        # A read-only caller gets the state itself and nothing else.
        assert body["reason"] is None and body["updated_at"] is None
        assert "private operator note" not in response.text


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("method,path,required", CHANGING_ROUTES)
async def test_changing_inbound_routes_are_unchanged(inbound_app, role, method, path, required):
    app, state = inbound_app
    state.role = role
    response = await _call(
        app, method, f"/api/v1{path}", json={}, headers={"Idempotency-Key": "abcdefgh12345"}
    )
    allowed = required <= _perms(role)
    assert (response.status_code != 403) == allowed, (role, method, path, response.status_code)


async def test_controls_read_is_always_for_the_callers_own_tenant(inbound_app):
    app, state = inbound_app
    state.role = "readonly"
    state.tenant = TENANT
    await _call(app, "GET", "/api/v1/inbound-campaigns/controls?tenant_id=" + OTHER_TENANT)
    assert state.tenants_read == [TENANT]


async def test_controls_read_without_a_tenant_is_refused(inbound_app):
    app, state = inbound_app
    state.role = "readonly"
    state.tenant = None
    response = await _call(app, "GET", "/api/v1/inbound-campaigns/controls")
    assert response.status_code == 403 and state.tenants_read == []


# --------------------------------------------------------------------------
# D5-9: telephony reads (pinned as they are today; see the report)
# --------------------------------------------------------------------------

import app.api.v1.endpoints.telephony_sip.trunks as trunks_mod  # noqa: E402
from app.api.v1.endpoints.telephony_sip import router as sip_router  # noqa: E402

TELEPHONY_READS = [
    "/api/v1/telephony/sip/trunks/pool",
    "/api/v1/telephony/sip/trunks/pool-assignment",
    "/api/v1/telephony/sip/trunks/outbound-route",
]
SECRET_WORDS = ("password", "secret", "credential", "auth_password", "token")


@pytest.fixture
def sip_app(monkeypatch):
    state = SimpleNamespace(role="tenant_admin", tenant=TENANT)

    class Conn:
        async def fetch(self, *a, **k):
            return []

        async def fetchval(self, *a, **k):
            return None

        @asynccontextmanager
        async def transaction(self):
            yield

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        yield Conn()

    async def noop(*a, **k):
        return None

    async def readiness(*a, **k):
        return {"ready": True, "reason_code": "ok"}

    monkeypatch.setattr(trunks_mod, "acquire_with_tenant", acquire)
    monkeypatch.setattr(trunks_mod, "apply_tenant_rls_context", noop)
    monkeypatch.setattr(
        "app.domain.services.telephony.outbound_readiness.evaluate_outbound_readiness", readiness
    )
    app = FastAPI()
    app.include_router(sip_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ACTOR, email="u@example.test", tenant_id=state.tenant, role=state.role
    )
    app.dependency_overrides[get_db_pool] = lambda: object()
    return app, state


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("path", TELEPHONY_READS)
async def test_telephony_reads_today_are_admin_only_and_carry_no_secret(sip_app, role, path):
    app, state = sip_app
    state.role = role
    response = await _call(app, "GET", path)
    if role in ADMIN_ROLES:
        assert response.status_code == 200, response.text
        assert not any(word in response.text.lower() for word in SECRET_WORDS)
    else:
        assert response.status_code == 403


# --------------------------------------------------------------------------
# D5-27: /auth/me account_restrictions
# --------------------------------------------------------------------------

from app.api.v1.endpoints import campaigns as campaigns_mod  # noqa: E402
from app.api.v1.endpoints.auth import profile  # noqa: E402
from app.core.security import principal as principal_mod  # noqa: E402
from app.domain.services import call_guard as call_guard_mod  # noqa: E402
from app.domain.services.payg import PurchaseRefused, tenant_purchase_audience  # noqa: E402
from app.domain.services.telephony.call_authority import read_call_authority  # noqa: E402

STATES = ["active", "suspended_subscription", "suspended_hold"]


def _authority_row(state):
    return {
        "id": TENANT,
        "status": "suspended" if state == "suspended_hold" else "active",
        "subscription_status": "suspended" if state == "suspended_subscription" else "active",
        "suspended_at": None, "suspended_by": None, "suspension_reason": None,
        "white_label_partner_id": None, "formal_partner_id": None,
        "formal_partner_status": None, "active_holds": [], "authority_scope_ok": True,
    }


def _conn(state):
    class Conn:
        async def fetchrow(self, query, *args):
            if "stripe_subscription_id" in query and "FROM tenants WHERE" in query:
                return {"stripe_subscription_id": None,
                        "subscription_status": _authority_row(state)["subscription_status"]}
            return _authority_row(state)

        async def fetchval(self, *args):
            return "on"  # app.bypass_rls already on: _require_formal_account_access reads directly

    return Conn()


async def _refused_actions(monkeypatch, state) -> set:
    """Probe every enforcing code path the doc names, return the actions refused."""
    conn = _conn(state)
    refused = set()

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        yield conn

    monkeypatch.setattr(campaigns_mod, "acquire_with_tenant", acquire)
    monkeypatch.setattr(call_guard_mod, "acquire_with_tenant", acquire)

    try:
        await campaigns_mod._require_account_can_call(SimpleNamespace(pool=object()), TENANT)
    except HTTPException as exc:
        assert exc.status_code == 403 and exc.detail["code"] == "tenant_suspended"
        refused.add("start_campaign")

    guard = call_guard_mod.CallGuard(db_pool=object(), redis_client=None)
    if not (await guard._check_tenant_active(TENANT)).passed:
        refused.add("place_calls")

    try:
        await tenant_purchase_audience(conn, TENANT)
    except PurchaseRefused:
        refused.add("buy_minutes")

    if not (await read_call_authority(conn, TENANT)).allowed:
        refused.add("receive_inbound_calls")

    try:
        await principal_mod._require_formal_account_access(conn, TENANT)
    except principal_mod.PrincipalUnavailable as exc:
        assert exc.code == "account_restricted"
        refused.add("sign_in")
    return refused


@pytest.mark.parametrize("state", STATES)
async def test_account_restrictions_equal_what_the_server_refuses(monkeypatch, state):
    refused = await _refused_actions(monkeypatch, state)
    assert set(profile.account_restrictions(state)) == refused


def test_account_restrictions_values():
    assert profile.account_restrictions("active") == []
    assert profile.account_restrictions("suspended_subscription") == [
        "start_campaign", "place_calls", "buy_minutes", "receive_inbound_calls",
    ]
    assert profile.account_restrictions("suspended_hold") == [
        "sign_in", "start_campaign", "place_calls", "buy_minutes", "receive_inbound_calls",
    ]
    # Billing and Support are never restricted by a subscription suspension.
    assert not {"billing", "support"} & set(profile.account_restrictions("suspended_subscription"))


@pytest.fixture
def me_app(monkeypatch):
    state = SimpleNamespace(subscription="active", tenant_status="active", partner_status=None)

    class Connection:
        async def fetchrow(self, query, *args):
            return {"tenant_id": TENANT, "partner_id": None, "tenant_status": state.tenant_status,
                    "partner_status": state.partner_status, "tenant_suspended_at": None,
                    "tenant_suspension_reason": None, "partner_suspended_at": None,
                    "partner_suspension_reason": None, "account_timezone": None,
                    "subscription_status": state.subscription}

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Connection()

    async def acceptance_status(conn, *, tenant_id, user_id):
        return {"legal_acceptance_required": False}

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.legal_acceptance.acceptance_status", acceptance_status)
    user = SimpleNamespace(id=ACTOR, tenant_id=TENANT, email="o@example.test", name="O",
                           business_name="B", role="tenant_admin", minutes_remaining=None,
                           minutes_state="unavailable")
    app = FastAPI()
    app.include_router(profile.router, prefix="/api/v1/auth")
    app.dependency_overrides[profile.get_current_user] = lambda: user
    app.dependency_overrides[profile.get_db_client] = lambda: SimpleNamespace(pool=object())
    return app, state


@pytest.mark.parametrize("state_name,subscription,tenant_status,partner_status", [
    ("active", "active", "active", None),
    ("suspended_subscription", "suspended", "active", None),
    ("suspended_hold", "active", "suspended", None),
    ("suspended_hold", "suspended", "active", "suspended"),
])
async def test_auth_me_carries_account_restrictions(me_app, state_name, subscription, tenant_status, partner_status):
    app, state = me_app
    state.subscription, state.tenant_status, state.partner_status = subscription, tenant_status, partner_status
    response = await _call(app, "GET", "/api/v1/auth/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_state"] == state_name
    assert body["account_restrictions"] == profile.account_restrictions(state_name)
    if state_name == "active":
        assert body["account_restrictions"] == []


@pytest.mark.parametrize("billing_state", ["past_due", "cancelled", "unpaid", None])
async def test_non_suspension_billing_states_stay_unrestricted_in_the_field(me_app, billing_state):
    # The field mirrors account_state, which only knows the three states above.
    # Other billing states are refused by the call guard but are not reported
    # here (see "Beyond brief" in the Day 5 report).
    app, state = me_app
    state.subscription = billing_state
    body = (await _call(app, "GET", "/api/v1/auth/me")).json()
    assert body["account_state"] == "active" and body["account_restrictions"] == []
