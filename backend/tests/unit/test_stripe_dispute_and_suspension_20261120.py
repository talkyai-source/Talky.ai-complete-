"""Plan disputes that do not end in "won", and Stripe lifting an admin suspension."""
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import billing_state_events as bse
from app.domain.services import plan_end
from app.domain.services.billing_checkout import CheckoutAttempts, CheckoutError

TENANT, SUB, CUS = "11111111-1111-1111-1111-111111111111", "sub_1", "cus_1"


class State:
    def __init__(self, tenant_status="active", dispute=None):
        self.tenant = tenant_status
        self.meta = {"dispute": dispute} if dispute else {}
        self.sub_id = SUB
        self.customer = CUS
        self.plan = "pro"


def _norm(sql):
    return " ".join(sql.split())


def _predicate_holds(pred, st, a):
    """One WHERE predicate, evaluated against the tenant row and bound args."""
    if pred == "id=$1::uuid":
        return a[0] == TENANT
    if pred == "stripe_subscription_id=$2":
        return st.sub_id == a[1]
    if pred == "(stripe_subscription_id=$2 OR stripe_subscription_id IS NULL)":
        return st.sub_id == a[1] or st.sub_id is None
    if pred == "(stripe_subscription_id=$2 OR (stripe_subscription_id IS NULL AND plan_id=$4))":
        return st.sub_id == a[1] or (st.sub_id is None and st.plan == a[3])
    if pred == "stripe_customer_id=$3":
        return st.customer == a[2]
    if pred == "subscription_status=$4":
        return st.tenant == a[3]
    if pred == "(subscription_status IS DISTINCT FROM 'suspended' OR $4='suspended')":
        return st.tenant != "suspended" or a[3] == "suspended"
    raise AssertionError(f"fake does not understand predicate: {pred!r}")


class Conn:
    """Interprets the few statements _apply_dispute / _apply_subscription issue.

    An UPDATE tenants statement applies only if every WHERE predicate holds for
    the held row and the bound arguments; anything unrecognised fails loudly.
    """

    def __init__(self, st):
        self.st = st
        self.fetchval = AsyncMock(return_value=True)

    async def fetchrow(self, sql, *a):
        if "FROM invoices" in sql:
            return {"tenant_id": TENANT, "stripe_subscription_id": SUB}
        if "FROM subscriptions" in sql:
            return {"metadata": dict(self.st.meta)}
        if sql == plan_end._Q_TENANT:
            return {"subscription_status": self.st.tenant, "plan_id": self.st.plan,
                    "stripe_subscription_id": self.st.sub_id, "stripe_customer_id": self.st.customer}
        raise AssertionError(sql)

    def _update_tenants(self, sql, a):
        m = re.fullmatch(r"UPDATE tenants SET (.+?) WHERE (.+)", _norm(sql))
        if not m:
            raise AssertionError(f"fake does not understand: {sql!r}")
        set_clause, where = m.groups()
        compound = "(stripe_subscription_id=$2 OR (stripe_subscription_id IS NULL AND plan_id=$4))"
        predicates = [compound] if compound in where else []
        predicates += where.replace(compound, "").split(" AND ")
        if not all(_predicate_holds(p, self.st, a) for p in predicates if p.strip()):
            return "UPDATE 0"
        if set_clause in ("subscription_status=$3", "subscription_status=$4"):
            self.st.tenant = a[int(set_clause[-1]) - 1]
        else:
            raise AssertionError(f"fake does not understand SET: {set_clause!r}")
        return "UPDATE 1"

    async def execute(self, sql, *a):
        if sql.lstrip().startswith("INSERT INTO subscriptions"):
            return "INSERT 0 1"
        if "UPDATE subscriptions SET metadata" in sql:
            status = "open" if "'open'" in sql else ("lost" if "'lost'" in sql else ("won" if "'won'" in sql else a[3]))
            self.st.meta["dispute"] = {"id": a[2], "status": status}
            return "UPDATE 1"
        if sql == plan_end._Q_TO_PAYG:
            if self.st.sub_id != a[3]:
                return "UPDATE 0"
            self.st.plan, self.st.tenant, self.st.sub_id = a[1], a[2], None
            return "UPDATE 1"
        if sql.lstrip().startswith("UPDATE tenants SET"):
            return self._update_tenants(sql, a)
        raise AssertionError(f"fake does not understand: {sql!r}")


def _billing(dispute_status="x", subscription_status="active"):
    objs = {
        "Dispute": {"id": "dp_1", "livemode": False, "charge": "ch_1", "status": dispute_status},
        "Charge": {"id": "ch_1", "livemode": False},
        "Subscription": {"id": SUB, "livemode": False, "status": subscription_status},
    }

    async def call(resource, method, identity):
        return objs[resource]

    return SimpleNamespace(billing_mode="test", _stripe_call=call)


ALLOCATIONS = []


@pytest.fixture(autouse=True)
def _invoice(monkeypatch):
    monkeypatch.setattr(bse, "_invoice_for_adjustment", AsyncMock(return_value="in_1"))
    ALLOCATIONS.clear()

    async def allocate(conn, tenant_id, minutes):
        ALLOCATIONS.append((tenant_id, minutes))

    monkeypatch.setattr(plan_end, "apply_plan_allocation", allocate)


async def _run(event, dispute_status, st):
    return await bse.apply_billing_event(
        Conn(st), _billing(dispute_status), event, {"id": "dp_1"}, source_reference="evt")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["needs_response", "under_review", "lost"])
async def test_created_money_dispute_suspends(status):
    st = State()
    r = await _run("charge.dispute.created", status, st)
    assert (st.tenant, st.meta["dispute"]["status"], r["status"]) == ("suspended", "open", "handled")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["warning_needs_response", "warning_under_review", "something_new"])
async def test_created_inquiry_does_not_suspend(status):
    st = State()
    r = await _run("charge.dispute.created", status, st)
    assert st.tenant == "active" and "dispute" not in st.meta
    assert (r["status"], r["reason"]) == ("ignored", "dispute_inquiry")


@pytest.mark.asyncio
async def test_won_restores():
    st = State("suspended", {"id": "dp_1", "status": "open"})
    await _run("charge.dispute.closed", "won", st)
    assert (st.tenant, st.meta["dispute"]["status"]) == ("active", "won")


@pytest.mark.asyncio
async def test_warning_closed_restores_an_account_the_old_code_suspended():
    st = State("suspended", {"id": "dp_1", "status": "open"})
    r = await _run("charge.dispute.closed", "warning_closed", st)
    assert (st.tenant, st.meta["dispute"]["status"], r["status"]) == ("active", "closed", "handled")


@pytest.mark.asyncio
async def test_warning_closed_with_nothing_stored_is_ignored():
    st = State("active")
    r = await _run("charge.dispute.closed", "warning_closed", st)
    assert (st.tenant, r["status"]) == ("active", "ignored") and "dispute" not in st.meta


@pytest.mark.asyncio
async def test_lost_stays_suspended_and_is_stored_lost_committed():
    st = State("suspended", {"id": "dp_1", "status": "open"})
    r = await _run("charge.dispute.closed", "lost", st)
    assert (st.tenant, st.meta["dispute"]["status"]) == ("suspended", "lost")
    assert (r["status"], r["reason"]) == ("handled", "subscription_dispute_lost")


def test_dispute_open_is_true_only_for_open():
    def mk(s):
        return {"metadata": {"dispute": {"id": "d", "status": s}}}
    assert bse._dispute_open(mk("open")) is True
    assert not any(bse._dispute_open(mk(s)) for s in ("lost", "won", "closed"))


# ---- Stripe subscription events never lift a suspension --------------------

def _binding_current():
    return {"tenant_id": TENANT, "customer_id": CUS, "current": True, "attempt": None,
            "recorded": {"plan_id": "p1", "metadata": {}}, "tenant": {"plan_id": "p1"}}


def _sub(status):
    return {"id": SUB, "customer": CUS, "status": status, "current_period_start": 1000, "current_period_end": 2000}


@pytest.mark.asyncio
@pytest.mark.parametrize("stripe_status", ["active", "past_due", "canceled"])
async def test_subscription_event_does_not_lift_admin_suspension(stripe_status):
    st = State("suspended")
    r = await bse._apply_subscription(Conn(st), _billing(), _sub(stripe_status), _binding_current())
    assert st.tenant == "suspended"
    assert r["status"] == "handled"
    if stripe_status == "canceled":
        assert st.sub_id is None  # the subscription link is still cleared
        assert st.plan == "payg" and ALLOCATIONS == [(TENANT, 0)]  # and the plan's minutes end


@pytest.mark.asyncio
async def test_subscription_event_still_updates_a_normal_tenant():
    st = State("active")
    await bse._apply_subscription(Conn(st), _billing(), _sub("past_due"), _binding_current())
    assert st.tenant != "active"


@pytest.mark.asyncio
async def test_open_dispute_still_writes_suspended_through_subscription_event():
    st = State("active")
    binding = _binding_current()
    binding["recorded"]["metadata"] = {"dispute": {"id": "dp_1", "status": "open"}}
    await bse._apply_subscription(Conn(st), _billing(), _sub("active"), binding)
    assert st.tenant == "suspended"


# ---- checkout claim and cancel ---------------------------------------------

@pytest.mark.asyncio
async def test_checkout_claim_refuses_a_suspended_tenant(monkeypatch):
    import app.domain.services.billing_checkout as bc

    class C:
        async def fetchrow(self, sql, *a):
            if "FROM tenants" in sql:
                return {"id": TENANT, "status": "active", "subscription_status": "suspended",
                        "stripe_customer_id": None, "stripe_subscription_id": None}
            return None

        async def fetchval(self, *a):
            return None

        async def execute(self, *a):
            raise AssertionError("a suspended tenant must not get an attempt row")

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kw):
        yield C()

    monkeypatch.setattr(bc, "acquire_with_tenant", acquire)
    attempts = CheckoutAttempts(None, SimpleNamespace(billing_mode="test"))
    with pytest.raises(CheckoutError) as ei:
        await attempts._claim(TENANT, "22222222-2222-2222-2222-222222222222",
                              {"id": "opt", "minutes": 1, "plan_id": "p"}, "x@example.com", None)
    assert str(ei.value) == "This account is suspended and cannot buy a plan. Contact support."
    assert ei.value.request_not_started is True


@pytest.mark.asyncio
async def test_cancel_does_not_write_status_over_a_suspension(monkeypatch):
    from app.domain.services.billing_service import BillingService

    writes = []

    class Q:
        def __init__(self, t):
            self.t, self.p = t, None

        def select(self, *_):
            return self

        def update(self, p):
            self.p = p
            return self

        def eq(self, *_):
            return self

        def single(self):
            return self

        def execute(self):
            if self.p is not None:
                writes.append((self.t, self.p))
            return SimpleNamespace(data={"stripe_subscription_id": "sub_1", "subscription_status": "suspended"})

    svc = BillingService(SimpleNamespace(table=lambda t: Q(t)))
    monkeypatch.setattr(svc, "mock_mode", False, raising=False)
    monkeypatch.setattr(svc, "_require_billing_enabled", lambda: None, raising=False)

    async def call(*a, **k):
        return SimpleNamespace(status="canceled", cancel_at=None, canceled_at=None, cancel_at_period_end=False)

    monkeypatch.setattr(svc, "_stripe_call", call)
    await svc.cancel_subscription("t1")
    assert not [w for w in writes if w[0] == "tenants"]


# ---- review fixes 20261120 -------------------------------------------------

@pytest.mark.asyncio
async def test_won_dispute_restores_a_plan_cancelled_while_the_dispute_was_open():
    st = State("active")
    await _run("charge.dispute.created", "needs_response", st)
    assert st.tenant == "suspended"
    await bse._apply_subscription(Conn(st), _billing(), _sub("canceled"), _binding_current())
    assert (st.tenant, st.sub_id) == ("suspended", None)
    r = await bse.apply_billing_event(
        Conn(st), _billing("won", subscription_status="canceled"), "charge.dispute.closed",
        {"id": "dp_1"}, source_reference="evt")
    # The plan ended while the dispute was open: it comes back as pay as you go, with no plan minutes.
    assert (st.tenant, r["reason"]) == ("inactive", "subscription_dispute_won")
    assert st.plan == "payg" and st.sub_id is None and ALLOCATIONS and all(m == 0 for _, m in ALLOCATIONS)


@pytest.mark.asyncio
async def test_warning_closed_restores_a_cancelled_plan_too():
    st = State("suspended", {"id": "dp_1", "status": "open"})
    st.sub_id = None
    await bse.apply_billing_event(
        Conn(st), _billing("warning_closed", subscription_status="canceled"), "charge.dispute.closed",
        {"id": "dp_1"}, source_reference="evt")
    assert st.tenant == "inactive"
    assert st.plan == "payg" and ALLOCATIONS == [(TENANT, 0)]


@pytest.mark.asyncio
async def test_won_restore_never_touches_another_subscription():
    st = State("suspended", {"id": "dp_1", "status": "open"})
    st.sub_id = "sub_other"
    await _run("charge.dispute.closed", "won", st)
    assert st.tenant == "suspended"


@pytest.mark.asyncio
async def test_inquiry_that_becomes_a_chargeback_suspends_on_closed_lost():
    st = State("active")
    r = await _run("charge.dispute.created", "warning_needs_response", st)
    assert (st.tenant, r["reason"]) == ("active", "dispute_inquiry")
    r = await _run("charge.dispute.closed", "lost", st)
    assert (st.tenant, st.meta["dispute"]["status"]) == ("suspended", "lost")
    assert (r["status"], r["reason"]) == ("handled", "subscription_dispute_lost")
    # a redelivery of the same closed event changes nothing
    r = await _run("charge.dispute.closed", "lost", st)
    assert (st.tenant, r["reason"]) == ("suspended", "dispute_not_recorded")


@pytest.mark.asyncio
async def test_closed_won_with_nothing_stored_does_not_suspend():
    st = State("active")
    r = await _run("charge.dispute.closed", "won", st)
    assert (st.tenant, r["status"]) == ("active", "ignored")


@pytest.mark.asyncio
async def test_funds_withdrawn_after_an_inquiry_suspends_then_won_restores():
    st = State("active")
    await _run("charge.dispute.created", "warning_needs_response", st)
    r = await _run("charge.dispute.funds_withdrawn", "needs_response", st)
    assert (st.tenant, st.meta["dispute"]["status"], r["status"]) == ("suspended", "open", "handled")
    await _run("charge.dispute.closed", "won", st)
    assert (st.tenant, st.meta["dispute"]["status"]) == ("active", "won")


@pytest.mark.asyncio
async def test_updated_to_a_money_status_suspends_but_an_inquiry_update_does_not():
    st = State("active")
    r = await _run("charge.dispute.updated", "warning_under_review", st)
    assert (st.tenant, r["reason"]) == ("active", "dispute_inquiry")
    await _run("charge.dispute.updated", "needs_response", st)
    assert st.tenant == "suspended"


@pytest.mark.asyncio
async def test_a_late_updated_never_reopens_a_closed_dispute():
    st = State("active", {"id": "dp_1", "status": "won"})
    r = await _run("charge.dispute.updated", "needs_response", st)
    assert (st.tenant, st.meta["dispute"]["status"], r["status"]) == ("active", "won", "ignored")


@pytest.mark.asyncio
async def test_dispute_opened_after_the_plan_ended_still_suspends():
    st = State("inactive", None)
    st.sub_id, st.plan = None, "payg"
    await _run("charge.dispute.created", "needs_response", st)
    assert st.tenant == "suspended"


@pytest.mark.asyncio
async def test_dispute_after_the_plan_ended_never_suspends_a_newer_plan():
    st = State("active", None)
    st.sub_id, st.plan = None, "pro"  # e.g. a PayPal plan bought since
    await _run("charge.dispute.created", "needs_response", st)
    assert st.tenant == "active"
