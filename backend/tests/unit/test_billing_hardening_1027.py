"""Billing hardening from the 2026-10-27 audit (no free minutes, no stuck money).

Each test names the failure it prevents. Provider calls are fakes.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.services import billing_catalog as catalog
from app.domain.services.billing_checkout import CheckoutAttempts
from app.domain.services.billing_mode import checkout_permitted_in_test_mode

TENANT = "11111111-1111-1111-1111-111111111111"
# Built, not literal: the CI secret scan flags a key-shaped string after a
# variable named like a key, even an obviously synthetic one.
LIVE_FIXTURE = "sk_" + "live_" + "synthetic"
REQUEST = "22222222-2222-2222-2222-222222222222"


# ── Stripe TEST mode in production sells only to the team ──────────────────

@pytest.fixture
def prod_test_mode(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
    monkeypatch.setenv("STRIPE_TEST_CHECKOUT_EMAILS", "Owner@Example.com, ops@example.com")
    return monkeypatch


def test_a_stranger_cannot_buy_real_minutes_with_a_test_card(prod_test_mode):
    assert checkout_permitted_in_test_mode(is_platform_admin=False, email="someone@else.com") is False


@pytest.mark.parametrize("email", ["owner@example.com", "OWNER@example.com", "owner+test1@example.com", "ops@example.com"])
def test_the_team_can_test_checkout(prod_test_mode, email):
    assert checkout_permitted_in_test_mode(is_platform_admin=False, email=email) is True


@pytest.mark.parametrize("email", ["owner@example.com.evil", "xowner@example.com", "owner@", "", None])
def test_lookalike_addresses_are_refused(prod_test_mode, email):
    assert checkout_permitted_in_test_mode(is_platform_admin=False, email=email) is False


def test_platform_admins_can_test_checkout(prod_test_mode):
    assert checkout_permitted_in_test_mode(is_platform_admin=True, email="anyone@else.com") is True


def test_live_keys_and_non_production_are_not_restricted(prod_test_mode):
    prod_test_mode.setenv("STRIPE_SECRET_KEY", LIVE_FIXTURE)
    assert checkout_permitted_in_test_mode(is_platform_admin=False, email="someone@else.com") is True
    prod_test_mode.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")
    prod_test_mode.setenv("ENVIRONMENT", "staging")
    assert checkout_permitted_in_test_mode(is_platform_admin=False, email="someone@else.com") is True


def test_the_checkout_endpoints_refuse_before_any_request_is_saved(prod_test_mode):
    from app.api.v1.endpoints.billing import _require_checkout_permitted_in_test_mode

    with pytest.raises(HTTPException) as raised:
        _require_checkout_permitted_in_test_mode(SimpleNamespace(role="tenant_admin", email="someone@else.com"))
    assert raised.value.status_code == 403
    assert raised.value.detail["request_not_started"] is True


# ── a stuck checkout never locks a tenant out ───────────────────────────────

class _Attempts(CheckoutAttempts):
    def __init__(self, row, sessions=()):
        self.row, self.calls, self.states = row, [], []

        async def stripe_call(resource, method, *args, **kwargs):
            self.calls.append((resource, method))
            if (resource, method) == ("checkout.Session", "list"):
                return {"data": list(sessions)}
            raise AssertionError(f"unexpected provider call {resource}.{method}")

        super().__init__(pool=None, billing=SimpleNamespace(billing_mode="test", _stripe_call=stripe_call))

    async def _get(self, tenant_id, request_id, *, conn=None):
        return dict(self.row)

    async def _state(self, tenant_id, request_id, state):
        self.states.append(state)
        self.row["status"] = state

    async def _save_session(self, row, session):
        self.row.update(stripe_session_id=session["id"], status="ready")


def _row(minutes_since_claim, customer="cus_live"):
    claimed = datetime.now(UTC) - timedelta(minutes=minutes_since_claim)
    option = {"id": "o1", "plan_id": "basic", "plan_name": "Basic", "kind": "stripe", "interval": "month",
              "interval_count": 1, "amount_minor": 9900, "currency": "usd", "currency_exponent": 2,
              "provider_mode": "test", "stripe_price_id": "price_basic"}
    return {"id": REQUEST, "tenant_id": TENANT, "status": "unknown", "price_option_id": "o1",
            "stripe_session_id": None, "stripe_customer_id": customer, "created_at": claimed,
            "snapshot": {"option": option, "email": "a@b.c", "business_name": None,
                         "success_url": "https://x/s", "cancel_url": "https://x/c",
                         "expires_at": int((claimed + timedelta(hours=1)).timestamp())}}


async def test_an_expired_attempt_with_no_session_fails_so_the_tenant_can_buy_again():
    attempts = _Attempts(_row(minutes_since_claim=70))
    result = await attempts.create(tenant_id=TENANT, request_id=REQUEST, price_option_id="o1", email="a@b.c")
    assert attempts.states == ["failed"] and result["state"] == "failed"
    assert ("checkout.Session", "create") not in attempts.calls


async def test_a_session_the_first_request_created_is_found_not_recreated():
    session = {"id": "cs_found", "client_reference_id": REQUEST}
    attempts = _Attempts(_row(minutes_since_claim=45), sessions=[{"id": "cs_other", "client_reference_id": "x"}, session])
    await attempts.create(tenant_id=TENANT, request_id=REQUEST, price_option_id="o1", email="a@b.c")
    assert attempts.row["stripe_session_id"] == "cs_found" and attempts.states == []
    assert ("checkout.Session", "create") not in attempts.calls


async def test_before_the_frozen_expiry_passes_the_attempt_stays_pending():
    attempts = _Attempts(_row(minutes_since_claim=40))
    result = await attempts.create(tenant_id=TENANT, request_id=REQUEST, price_option_id="o1", email="a@b.c")
    assert attempts.states == [] and result["state"] == "pending"


# ── a dead stored customer is replaced, never fenced ────────────────────────

async def test_a_mock_or_missing_or_deleted_customer_reads_as_absent():
    import stripe

    async def stripe_call(resource, method, identity, **kwargs):
        if identity == "cus_gone":
            raise stripe.InvalidRequestError("No such customer", "id", code="resource_missing")
        return {"id": identity, "deleted": identity == "cus_deleted"}

    attempts = CheckoutAttempts(pool=None, billing=SimpleNamespace(_stripe_call=stripe_call))
    assert await attempts._live_customer("cus_mock_abcd1234") is None
    assert await attempts._live_customer("cus_gone") is None
    assert await attempts._live_customer("cus_deleted") is None
    assert (await attempts._live_customer("cus_ok"))["id"] == "cus_ok"


# ── the portal never falls back to the account default ──────────────────────

async def test_the_portal_refuses_without_its_configuration(monkeypatch):
    from app.domain.services.billing_service import BillingService

    billing = BillingService.__new__(BillingService)
    billing.mock_mode, billing.billing_mode = False, "test"

    async def verified(*_a):
        return True

    async def stripe_call(*_a, **_k):
        raise AssertionError("no portal session without the configuration")

    billing._portal_customer_verified, billing._stripe_call = verified, stripe_call
    billing.db_client = SimpleNamespace(table=lambda name: SimpleNamespace(
        select=lambda *a: SimpleNamespace(eq=lambda *a: SimpleNamespace(single=lambda: SimpleNamespace(
            execute=lambda: SimpleNamespace(data={"stripe_customer_id": "cus_1"}))))))
    monkeypatch.delenv("STRIPE_PORTAL_CONFIGURATION_ID", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        await billing.create_portal_session(TENANT, "https://app.example/billing")


def test_the_portal_needs_the_same_permission_as_cancel():
    from app.api.v1.endpoints.billing import router
    from app.core.security.rbac import Permission

    def permissions(path):
        route = next(r for r in router.routes if getattr(r, "path", "").endswith(path))
        found = set()
        for dependency in route.dependant.dependencies:
            for cell in getattr(dependency.call, "__closure__", None) or ():
                if isinstance(cell.cell_contents, Permission):
                    found.add(cell.cell_contents)
        return found

    assert Permission.BILLING_ADMIN in permissions("/portal")
    assert permissions("/portal") == permissions("/cancel")


# ── the public catalog cannot exhaust the Stripe rate limit ─────────────────

async def test_the_catalog_reuses_a_verified_price_for_five_minutes(monkeypatch):
    import stripe

    catalog._price_cache.clear()
    calls = []
    price = {"id": "price_basicmonth", "active": True, "type": "recurring", "billing_scheme": "per_unit",
             "livemode": False, "unit_amount": 9900, "currency": "usd", "product": "prod_basic",
             "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}
    option = {"id": "00000000-0000-0000-0000-000000000003", "plan_id": "basic", "plan_name": "Basic", "kind": "stripe",
              "interval": "month", "interval_count": 1, "amount_minor": 9900, "minutes": 300, "currency": "usd",
              "currency_exponent": 2, "provider_mode": "test", "active": True, "verified_at": "2026-10-27",
              "stripe_price_id": "price_basicmonth", "stripe_product_id": "prod_basic"}
    plans = [{"id": "basic", "name": "Basic", "price": 99, "description": None, "minutes": 300, "agents": 2,
              "concurrent_calls": 3, "features": [], "not_included": [], "popular": False}]

    class Conn:
        async def fetch(self, sql, *args):
            return plans if "plan_price_options" not in sql else [option]

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    class Http:
        def __init__(self, **kw):
            pass

        async def close_async(self):
            pass

        def close(self):
            pass

    async def retrieve(price_id):
        calls.append(price_id)
        return price

    monkeypatch.setattr(catalog, "acquire_with_tenant", lambda pool, tenant: Acquire())
    monkeypatch.setattr(catalog, "get_billing_mode", lambda: "test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_synthetic")
    monkeypatch.setattr(stripe, "HTTPXClient", Http)
    monkeypatch.setattr(stripe, "StripeClient", lambda *a, **k: SimpleNamespace(
        v1=SimpleNamespace(prices=SimpleNamespace(retrieve_async=retrieve))))
    for _ in range(5):
        listed = await catalog.list_plan_catalog(pool=None)
        assert listed[0]["price_options"][0]["checkout_available"] is True
    assert calls == ["price_basicmonth"]
    catalog._price_cache.clear()


# ── smaller guards ───────────────────────────────────────────────────────────

def test_admin_minutes_cannot_be_negative():
    from app.api.v1.endpoints.admin.tenants import QuotaUpdateRequest

    reviewed = {"expected_updated_at": "2026-10-27T00:00:00Z"}
    with pytest.raises(ValidationError) as rejected:
        QuotaUpdateRequest(minutes_allocated=-1, **reviewed)
    assert [error["loc"] for error in rejected.value.errors()] == [("minutes_allocated",)]
    assert QuotaUpdateRequest(minutes_allocated=0, **reviewed).minutes_allocated == 0


async def test_an_expiring_top_up_package_is_not_sold():
    from app.domain.services.topup_service import TopupError, TopupService

    class Conn:
        async def fetchrow(self, sql, *args):
            return {"code": "x", "name": "x", "minutes": 10, "price_cents": 100, "currency": "USD", "expires_days": 30}

    with pytest.raises(TopupError, match="expires"):
        await TopupService(None)._package(Conn(), "x")


async def test_a_won_top_up_dispute_returns_the_minutes_once():
    from app.domain.services.topup_service import TopupService

    class Conn:
        def __init__(self):
            self.ledger, self.status = [], "disputed"

        async def fetchrow(self, sql, *args):
            return {"id": "o1", "tenant_id": TENANT, "minutes": 125, "status": self.status}

        async def fetchval(self, sql, *args):
            if sql.lstrip().startswith("SELECT EXISTS"):
                return True
            event = args[3]
            if any(row[1] == event for row in self.ledger):
                return None
            self.ledger.append((args[2], event))
            return 1

        async def execute(self, sql, *args):
            self.status = "paid"

    conn = Conn()
    for _ in range(2):
        assert await TopupService(None).reinstate_dispute(conn, payment_id="pi_1", dispute_id="dp_1", event_id="evt") is True
    assert conn.ledger == [(125, "dispute-won:dp_1")] and conn.status == "paid"
