"""Day 5 items D5-18 (top-up order reference), D5-19 (audit actor names),
D5-24 (short audit reference) and D5-3 (plan limits document).

Behaviour tests with fakes: no database, no Stripe.
"""
from __future__ import annotations

import contextlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import audit_logs as al
from app.api.v1.endpoints import billing_topups as bt
from app.api.v1.endpoints.admin import tenants as admin_tenants
from app.domain.services import billing_checkout
from app.domain.services.audit_logger import AuditEvent, AuditLogEntry, EventCategory, Severity
from app.domain.services.billing_service import BillingService

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


# ── D5-18: the top-up return address carries the order ─────────────────────

ORDER_ID = uuid4()
ORDER = {
    "id": ORDER_ID, "minutes": 100, "price_cents": 1000, "currency": "usd",
    "name": "100 minutes", "package_code": "m100",
}


class _Svc:
    async def create_order(self, **_kw):
        return dict(ORDER)

    async def attach_session(self, *_a, tenant_id):
        return None


class _Billing:
    last: dict = {}
    mock_mode = True

    def __init__(self, *_a, **_k):
        pass

    async def create_topup_checkout_session(self, **kw):
        type(self).last = kw
        return {"session_id": "cs_x", "checkout_url": "https://stripe.example/x", "mock_mode": True}


def _settings():
    return lambda: SimpleNamespace(frontend_url="https://app.example", allowed_origins=["https://app.example"])


@pytest.fixture
def checkout(monkeypatch):
    monkeypatch.setattr(bt, "_service", lambda: _Svc())
    monkeypatch.setattr(bt, "BillingService", _Billing)
    monkeypatch.setattr(billing_checkout, "get_settings", _settings())
    monkeypatch.setattr(bt, "_purchase_audience", AsyncMock(return_value="subscriber"))
    _Billing.last = {}

    async def run():
        return await bt.create_topup_checkout(
            body=bt.CheckoutIn(package_code="m100"),
            request=SimpleNamespace(headers={}, client=SimpleNamespace(host="1.2.3.4")),
            current_user=SimpleNamespace(tenant_id=str(uuid4()), id=str(uuid4()), email="a@b.c"),
            db_client=object(),
            audit_logger=SimpleNamespace(log=AsyncMock()),
        )
    return run


@pytest.mark.asyncio
async def test_the_endpoint_hands_the_order_to_the_service_that_builds_the_return_address(checkout):
    """The endpoint passes the plain addresses and the order id; the service
    adds ``order_id`` (next test). ``request_id`` is the plan-purchase
    reference and must NOT appear on a top-up return: the billing page would
    read it as a plan purchase."""
    await checkout()
    success = parse_qs(urlsplit(_Billing.last["success_url"]).query)
    cancel = parse_qs(urlsplit(_Billing.last["cancel_url"]).query)
    assert success == {"topup": ["success"]}
    assert cancel == {"topup": ["cancelled"]}
    assert _Billing.last["order_id"] == str(ORDER_ID)
    assert _Billing.last["success_url"].startswith("https://app.example/billing?")


@pytest.mark.asyncio
async def test_the_address_the_customer_really_lands_on_carries_the_order(monkeypatch):
    """Real-mode path: BillingService adds order_id (our own row id) to both
    addresses and session_id={CHECKOUT_SESSION_ID} to success. The braces must
    survive unencoded."""
    monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    svc = BillingService(object())
    monkeypatch.setattr(svc, "create_or_get_customer", AsyncMock(return_value={"customer_id": "cus_x"}))
    seen = {}

    async def stripe_call(resource, method, *args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(id="cs_1", url="https://checkout.stripe.test/1")

    monkeypatch.setattr(svc, "_stripe_call", stripe_call)
    base = "https://app.example/billing"
    await svc.create_topup_checkout_session(
        tenant_id=str(uuid4()), email="a@b.c", order_id=str(ORDER_ID), minutes=100, price_cents=1000,
        currency="usd", product_name="x", success_url=f"{base}?topup=success",
        cancel_url=f"{base}?topup=cancelled",
    )
    assert "session_id={CHECKOUT_SESSION_ID}" in seen["success_url"] and "%7B" not in seen["success_url"]
    success = parse_qs(urlsplit(seen["success_url"]).query)
    cancel = parse_qs(urlsplit(seen["cancel_url"]).query)
    assert success["topup"] == ["success"] and success["order_id"] == [str(ORDER_ID)]
    assert cancel == {"topup": ["cancelled"], "order_id": [str(ORDER_ID)]}
    assert "request_id" not in success and "request_id" not in cancel


class _OrderConn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        order_id, tenant_id = args
        row = self.rows.get(str(order_id))
        return row if row is not None and str(row["tenant_id"]) == str(tenant_id) else None


@pytest.fixture
def order_conn(monkeypatch):
    mine, other = str(uuid4()), str(uuid4())
    paid = uuid4()
    rows = {
        str(paid): {"id": paid, "tenant_id": mine, "package_code": "m100", "minutes": 100,
                    "price_cents": 1000, "currency": "usd", "status": "paid",
                    "created_at": NOW, "paid_at": NOW},
        "5" * 8 + "-5555-4555-8555-" + "5" * 12: {
            "id": UUID("55555555-5555-4555-8555-555555555555"), "tenant_id": other,
            "package_code": "m100", "minutes": 100, "price_cents": 1000, "currency": "usd",
            "status": "pending", "created_at": NOW, "paid_at": None},
    }
    conn = _OrderConn(rows)

    @contextlib.asynccontextmanager
    async def acquire(pool, tenant_id, **_kw):
        yield conn

    monkeypatch.setattr(bt, "acquire_with_tenant", acquire)
    monkeypatch.setattr(bt, "get_container", lambda: SimpleNamespace(db_pool=object(), is_initialized=True))
    stripe_guard = AsyncMock(side_effect=AssertionError("no outbound call allowed"))
    monkeypatch.setattr(BillingService, "_stripe_call", stripe_guard)
    return SimpleNamespace(conn=conn, mine=mine, paid=paid, stripe=stripe_guard)


@pytest.mark.asyncio
async def test_own_order_returns_the_stored_state_with_no_outbound_call(order_conn):
    user = SimpleNamespace(tenant_id=order_conn.mine)
    out = await bt.get_topup_order(order_id=order_conn.paid, current_user=user)
    assert out["id"] == str(order_conn.paid) and out["status"] == "paid"
    assert out["minutes"] == 100 and out["currency_exponent"] == 2
    assert out["paid_at"] == NOW.isoformat()
    sql, args = order_conn.conn.calls[0]
    assert "tenant_id = $2" in sql and args == (str(order_conn.paid), order_conn.mine)
    order_conn.stripe.assert_not_called()


@pytest.mark.asyncio
async def test_another_tenants_order_and_an_unknown_order_are_both_404(order_conn):
    user = SimpleNamespace(tenant_id=order_conn.mine)
    for order_id in (UUID("55555555-5555-4555-8555-555555555555"), uuid4()):
        with pytest.raises(HTTPException) as err:
            await bt.get_topup_order(order_id=order_id, current_user=user)
        assert err.value.status_code == 404 and err.value.detail == "Order not found"


def test_the_order_route_needs_the_billing_read_permission():
    route = next(r for r in bt.router.routes if r.path == "/billing/topups/{order_id}")
    assert route.methods == {"GET"}
    assert any(bt.Permission.BILLING_READ in [c.cell_contents for c in (d.dependency.__closure__ or ())]
               for d in route.dependencies)


# ── D5-19: audit rows carry the actor's name ───────────────────────────────

TENANT = uuid4()
OTHER_TENANT = uuid4()
ALICE, BOB, GONE = uuid4(), uuid4(), uuid4()


def _entry(actor_id, actor_type="user", tenant_id=TENANT):
    return AuditLogEntry(
        event_id=uuid4(), event_time=NOW, event_type=AuditEvent.RECORD_VIEWED,
        event_category=EventCategory.DATA_ACCESS, severity=Severity.INFO,
        actor_id=actor_id, actor_type=actor_type, actor_role=None, tenant_id=tenant_id,
        resource_type=None, resource_id=None, ip_address=None, user_agent=None,
        session_id=None, device_fingerprint=None, country_code=None, action="x",
        description=None, before_state=None, after_state=None, metadata=None,
        previous_hash=None, entry_hash="h", signature=None, retention_until=NOW, created_at=NOW,
    )


PROFILES = {ALICE: ("Alice Doe", TENANT), BOB: ("Bob Other", OTHER_TENANT)}


class _ProfileConn:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        ids = {UUID(str(i)) for i in args[0]}
        tenant = UUID(str(args[1])) if len(args) > 1 else None
        return [{"id": i, "name": PROFILES[i][0]} for i in ids
                if i in PROFILES and (tenant is None or PROFILES[i][1] == tenant)]


class _Logger:
    def __init__(self, entries):
        self.entries, self.db_pool = entries, object()

    async def query(self, **_kw):
        return self.entries

    async def log(self, **_kw):
        return None


@pytest.fixture
def profiles(monkeypatch):
    conn = _ProfileConn()

    @contextlib.asynccontextmanager
    async def acquire(pool, tenant_id, **_kw):
        yield conn

    monkeypatch.setattr(al, "acquire_with_tenant", acquire)
    return conn


async def _list(entries, role="admin", tenant_id=TENANT):
    return await al.query_audit_logs(
        request=SimpleNamespace(), start_date=None, end_date=None, event_type=None, actor_id=None,
        tenant_id=None, resource_type=None, severity=None, limit=100, offset=0, event_group=None,
        user_query=None, partner_id=None, before_time=None, before_id=None,
        current_user={"id": str(uuid4()), "role": role, "tenant_id": str(tenant_id)},
        audit_logger=_Logger(entries),
    )


@pytest.mark.asyncio
async def test_rows_carry_the_name_system_and_deleted_labels(profiles):
    rows = await _list([_entry(ALICE), _entry(None, "system"), _entry(GONE), _entry(None, "service"),
                        _entry(uuid4(), "api_key")], role="platform_admin")
    assert [r.actor_name for r in rows] == ["Alice Doe", "System", "Deleted user", "Service", "API key"]
    assert rows[0].actor_id == ALICE and rows[0].actor_type == "user"  # existing fields unchanged


@pytest.mark.asyncio
async def test_one_lookup_for_the_whole_page_and_none_when_no_user_actor(profiles):
    await _list([_entry(ALICE), _entry(ALICE), _entry(GONE), _entry(ALICE)])
    assert len(profiles.calls) == 1
    assert sorted(str(i) for i in profiles.calls[0][1][0]) == sorted([str(ALICE), str(GONE)])
    profiles.calls.clear()
    await _list([_entry(None, "system")])
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_a_tenant_reader_never_gets_a_name_from_another_tenant(profiles):
    rows = await _list([_entry(ALICE), _entry(BOB)], role="admin")
    sql, args = profiles.calls[0]
    assert "tenant_id = $2" in sql and str(args[1]) == str(TENANT)
    assert rows[0].actor_name == "Alice Doe"
    assert "Bob" not in (rows[1].actor_name or "") and rows[1].actor_name == "Unknown user"


@pytest.mark.asyncio
async def test_a_platform_reader_resolves_across_tenants(profiles):
    rows = await _list([_entry(ALICE), _entry(BOB, tenant_id=OTHER_TENANT)], role="platform_admin")
    assert [r.actor_name for r in rows] == ["Alice Doe", "Bob Other"]
    assert len(profiles.calls) == 1 and len(profiles.calls[0][1]) == 1


@pytest.mark.asyncio
async def test_single_entry_route_also_carries_the_name(profiles):
    entry = _entry(ALICE)
    out = await al.get_audit_log(
        event_id=entry.event_id,
        current_user={"id": str(uuid4()), "role": "admin", "tenant_id": str(TENANT)},
        audit_logger=_Logger([entry]),
    )
    assert out.actor_name == "Alice Doe" and len(profiles.calls) == 1


def test_who_can_read_audit_logs_is_unchanged():
    from app.api.v1.endpoints.audit_logs import router

    reads = {r.path: r for r in router.routes if r.path in {"/admin/audit/logs", "/admin/audit/logs/{event_id}"}}
    assert set(reads) == {"/admin/audit/logs", "/admin/audit/logs/{event_id}"}
    source = (BACKEND / "app/api/v1/endpoints/audit_logs.py").read_text(encoding="utf-8")
    assert source.count('require_permissions(["audit:read"])') >= 2


# ── D5-24: a short, readable audit reference ───────────────────────────────

def test_short_reference_is_derived_from_the_full_id_by_one_function():
    full = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
    assert admin_tenants.audit_reference_short(full) == "AUD-1A2B3C4D"
    assert admin_tenants.audit_reference_short(UUID(full)) == "AUD-1A2B3C4D"
    assert admin_tenants.audit_reference_short(None) is None


def test_mutation_receipt_adds_the_short_reference_and_keeps_the_full_id():
    row = {"id": uuid4(), "subscription_status": "active", "plan_id": None, "updated_at": NOW,
           "minutes_allocated": 5, "calling_rules": "{}"}
    event = uuid4()
    receipt = admin_tenants._mutation_receipt(row, detail="d", changed=True, audit_event_id=event)
    assert receipt["audit_event_id"] == str(event)
    assert receipt["audit_reference_short"] == f"AUD-{str(event)[:8].upper()}"
    unchanged = admin_tenants._mutation_receipt(row, detail="d", changed=False)
    assert unchanged["audit_event_id"] is None and unchanged["audit_reference_short"] is None


# ── D5-3: the plan-limits document covers every stored plan line ───────────

def _migration(name):
    return importlib.import_module(f"Alembic.versions.{name}")


def _stored_plan_lines():
    """Lines `GET /plans` and `GET /plans/payg` can show, from the migrations'
    known values, after 0097 removes the campaign wizard line."""
    remove = _migration("0097_remove_campaign_wizard_plan_line").without_line
    m71, m73, m83 = (_migration(n) for n in (
        "0071_paid_only_billing", "0073_payment_test_plan", "0083_pay_as_you_go"))
    lists = [m71.BASIC_FEATURES, m71.BASIC_NOT_INCLUDED, m71.BUSINESS_FEATURES, m73.FEATURES, m83.FEATURES]
    return [line for raw in lists for line in remove(json.loads(raw))]


def test_the_plan_limits_document_lists_every_stored_plan_line():
    doc = (BACKEND / "docs" / "plan-limits-enforcement.md").read_text(encoding="utf-8")
    lines = _stored_plan_lines()
    assert len(lines) >= 20
    missing = [line for line in lines if line not in doc]
    assert not missing, f"plan lines missing from docs/plan-limits-enforcement.md: {missing}"
    assert "Automatic campaign wizard" not in "".join(lines)
    assert "For the owner" in doc and "NOT ENFORCED" in doc
