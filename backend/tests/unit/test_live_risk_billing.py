"""Live-risk billing findings 1-7 (audit 2026-10-20)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import minutes_quota
from app.domain.services.call_guard import CallGuard, TenantCallLimits
from app.domain.services.topup_service import TopupError, TopupService
from tests.unit.test_op01_metering_unavailable import Meter, Pool
from tests.unit.test_topup_service import FakeConn, make_store

TENANT = "11111111-1111-4111-8111-111111111111"


# billing-1: expired purchased minutes stop counting ---------------------------
class _ExpiryConn(Meter):
    def __init__(self, expired, **kw):
        super().__init__(**kw)
        self.expired = expired

    async def fetchrow(self, *_):
        return {"minutes_allocated": self.allocated, "plan_minutes": self.plan_minutes,
                "expired_topup_minutes": self.expired}


@pytest.mark.asyncio
async def test_bought_minutes_never_expire():
    """Owner decision 2026-10-27: top-up minutes carry over until used (and
    expiring packages are refused at sale), so the meter has no expiry term.
    The recurring-top-up bug expiry once worked around is fixed by
    topup_usage recording each month's use once."""
    s = await minutes_quota.compute_minutes_status(_ExpiryConn(500, allocated=530, used=0), TENANT)
    assert (s.allocated, s.remaining_minutes) == (530, 530)


@pytest.mark.asyncio
async def test_a_finite_plan_with_nothing_left_is_exhausted_not_unlimited():
    s = await minutes_quota.compute_minutes_status(_ExpiryConn(0, allocated=30, used=30 * 60), TENANT)
    assert s.allocated == 30 and s.unlimited is False and s.exhausted is True


@pytest.mark.asyncio
async def test_unlimited_tenant_ignores_expiry_term():
    s = await minutes_quota.compute_minutes_status(
        _ExpiryConn(500, allocated=0, plan_minutes=0, used=0), TENANT)
    assert s.unlimited is True


# billing-2: spend cap sees real month spend -----------------------------------
def _guard(meter):
    return SimpleNamespace(_db_pool=Pool(meter))


@pytest.mark.asyncio
async def test_spend_cap_trips_on_metered_month_usage_even_when_stored_used_is_zero():
    limits = TenantCallLimits(monthly_spend_cap=5.0, monthly_spend_used=0.0)
    # 6000 min * $0.05 = $300 of metered spend vs a $5 cap
    r = await CallGuard._check_spend_limit(
        _guard(Meter(allocated=0, plan_minutes=0, used=6000 * 60)),
        tenant_id=TENANT, tenant_limits=limits, estimated_duration_seconds=60)
    assert r.passed is False and "spend_cap_exceeded" in r.reason


@pytest.mark.asyncio
async def test_spend_cap_passes_when_metered_usage_is_under_cap():
    limits = TenantCallLimits(monthly_spend_cap=100.0)
    r = await CallGuard._check_spend_limit(
        _guard(Meter(allocated=0, plan_minutes=0, used=60 * 60)),
        tenant_id=TENANT, tenant_limits=limits, estimated_duration_seconds=60)
    assert r.passed is True


@pytest.mark.asyncio
async def test_spend_cap_metering_failure_raises_so_the_guard_fails_closed():
    limits = TenantCallLimits(monthly_spend_cap=100.0)
    with pytest.raises(Exception):
        await CallGuard._check_spend_limit(
            _guard(Meter(missing=True)), tenant_id=TENANT, tenant_limits=limits)


# billing-3: in-flight outbound calls reserve minutes ---------------------------
@pytest.mark.asyncio
async def test_inflight_calls_reserve_minutes_against_the_allowance():
    # 1 minute left, 10 calls already in flight: an 11th must be refused.
    r = await CallGuard._check_minutes_quota(
        _guard(Meter(allocated=100, used=99 * 60, inflight=10)),
        tenant_id=TENANT, tenant_limits=TenantCallLimits(), estimated_duration_seconds=60)
    assert r.passed is False and r.details["inflight_calls"] == 10


@pytest.mark.asyncio
async def test_no_inflight_calls_still_admits_the_last_minute():
    r = await CallGuard._check_minutes_quota(
        _guard(Meter(allocated=100, used=99 * 60, inflight=0)),
        tenant_id=TENANT, tenant_limits=TenantCallLimits(), estimated_duration_seconds=60)
    assert r.passed is True


@pytest.mark.asyncio
async def test_unlimited_tenant_is_not_reserved_against():
    r = await CallGuard._check_minutes_quota(
        _guard(Meter(allocated=0, plan_minutes=0, inflight=500)),
        tenant_id=TENANT, tenant_limits=TenantCallLimits())
    assert r.passed is True


# billing-4: no top-up sale to an unlimited tenant ------------------------------
def _service(store, monkeypatch):
    from app.domain.services import topup_service

    conn = FakeConn(store)

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id):
        yield conn

    monkeypatch.setattr(topup_service, "acquire_with_tenant", fake_acquire)
    return TopupService(None), conn


_PKG = {"code": "p", "name": "P", "minutes": 100, "price_cents": 1000, "currency": "GBP"}


@pytest.mark.asyncio
async def test_create_order_refuses_unlimited_tenant_before_any_order_row(monkeypatch):
    svc, conn = _service(make_store(allocated=0, enforced=0, package=_PKG), monkeypatch)
    with pytest.raises(TopupError):
        await svc.create_order(tenant_id=TENANT, user_id=None, package_code="p")
    assert not any("INSERT INTO topup_orders" in q for q in conn.sql)


@pytest.mark.asyncio
async def test_create_order_still_works_for_a_capped_tenant(monkeypatch):
    svc, conn = _service(make_store(allocated=500, enforced=500, package=_PKG), monkeypatch)
    out = await svc.create_order(tenant_id=TENANT, user_id=None, package_code="p")
    assert out["minutes"] == 100


# billing-5: Test Agent gate ----------------------------------------------------
@pytest.mark.asyncio
async def test_suspended_tenant_is_refused_a_test_session(monkeypatch):
    from app.api.v1.endpoints import campaign_test_ws as ws
    from app.domain.services.call_guard import CheckResult, GuardCheck

    monkeypatch.setattr(
        CallGuard, "_check_tenant_active",
        AsyncMock(return_value=CheckResult(GuardCheck.TENANT_ACTIVE, False, reason="tenant_suspended")))
    assert await ws._tenant_test_refusal(object(), TENANT) == "tenant_suspended"


@pytest.mark.asyncio
async def test_cancelled_subscription_is_refused_a_test_session(monkeypatch):
    from app.api.v1.endpoints import campaign_test_ws as ws
    from app.domain.services.call_guard import CheckResult, GuardCheck

    monkeypatch.setattr(
        CallGuard, "_check_tenant_active",
        AsyncMock(return_value=CheckResult(GuardCheck.TENANT_ACTIVE, True)))
    monkeypatch.setattr(
        CallGuard, "_check_subscription_uncached",
        AsyncMock(return_value=CheckResult(GuardCheck.SUBSCRIPTION_VALID, False,
                                           reason="subscription_canceled")))
    assert await ws._tenant_test_refusal(object(), TENANT) == "subscription_canceled"


def test_one_tenant_cannot_hold_every_global_slot():
    from app.api.v1.endpoints import campaign_test_ws as ws

    assert ws._MAX_CONCURRENT_TEST_PER_TENANT < ws._MAX_CONCURRENT_TEST
    sem = ws._get_tenant_semaphore("tenant-cap-test")
    assert ws._get_tenant_semaphore("tenant-cap-test") is sem

    async def hold():
        for _ in range(ws._MAX_CONCURRENT_TEST_PER_TENANT):
            await sem.acquire()
        locked = sem.locked()
        for _ in range(ws._MAX_CONCURRENT_TEST_PER_TENANT):
            sem.release()
        return locked

    assert asyncio.run(hold()) is True


# billing-6: no Stripe read while the tenants row is locked --------------------
@pytest.mark.asyncio
async def test_charge_is_retrieved_before_the_tenants_row_lock():
    from tests.unit.test_billing_topup_events import apply, fixture_data

    fixture = fixture_data()
    seen = {}
    inner = fixture.provider._stripe_call
    real_fetchrow = fixture.conn.fetchrow

    async def record(sql, *a):
        fixture.conn.sql.append(sql)
        return await real_fetchrow(sql, *a)

    async def spy(resource, method, identity):
        seen[resource] = any("FROM tenants" in q and "FOR UPDATE" in q for q in fixture.conn.sql)
        return await inner(resource, method, identity)

    fixture.conn.fetchrow = record
    fixture.provider._stripe_call = spy
    result = await apply(fixture)
    assert result["status"] == "handled"
    assert seen["Charge"] is False


# billing-7: cancel_subscription ------------------------------------------------
class _Q:
    def __init__(self, rec, table):
        self.rec, self.table, self.payload, self.filters, self.is_update = rec, table, None, [], False

    def select(self, *_):
        return self

    def update(self, payload):
        self.payload, self.is_update = payload, True
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def single(self):
        return self

    def execute(self):
        if self.is_update:
            self.rec.updates.append((self.table, self.payload, self.filters))
        return SimpleNamespace(data={"stripe_subscription_id": "sub_1"})


class _DB:
    def __init__(self):
        self.updates = []

    def table(self, name):
        return _Q(self, name)


@pytest.mark.asyncio
async def test_cancel_at_period_end_uses_threaded_stripe_call_and_no_canceled_at(monkeypatch):
    from app.domain.services.billing_service import BillingService

    db = _DB()
    svc = BillingService(db)
    monkeypatch.setattr(svc, "billing_mode", "test", raising=False)
    monkeypatch.setattr(svc, "mock_mode", False, raising=False)
    monkeypatch.setattr(svc, "_require_billing_enabled", lambda: None, raising=False)
    calls = []

    async def fake_call(resource, method, *args, **kw):
        calls.append((resource, method, args, kw))
        return SimpleNamespace(status="active", cancel_at=1900000000, canceled_at=None,
                               cancel_at_period_end=True)

    monkeypatch.setattr(svc, "_stripe_call", fake_call)
    await svc.cancel_subscription("t1", cancel_at_period_end=True)
    assert calls == [("Subscription", "update", ("sub_1",), {"cancel_at_period_end": True})]
    (_, payload, filters) = next(u for u in db.updates if u[0] == "subscriptions")
    assert payload["canceled_at"] is None
    assert ("tenant_id", "t1") in filters
