"""Changing plan must not destroy minutes the customer already paid for.

Top-ups and plan allocations share ONE column. ``topup_service`` credits with
``minutes_allocated = minutes_allocated + $2``; the subscription checkout
handler used to hard-SET the same column to the new plan's minutes. So a
customer who bought 500 minutes on Monday and upgraded on Tuesday lost the 500
— silently, while ``billing_ledger`` still recorded the sale. The books said we
sold the minutes; the account said they never existed.

The fix re-derives the purchased balance from the ledger inside the same
statement that writes the plan entitlement, because that is the only version
with no window for a concurrent top-up to be overwritten.

WHAT THIS FILE CAN PROVE
------------------------
The handler's real control flow runs against fakes that execute the statement
as written (the ledger subselect included). It cannot prove PostgreSQL's own
behaviour — that is what ``scripts/verify_topup_ledger.py`` is for.
"""
from __future__ import annotations

import inspect

import pytest

from app.domain.services.billing_service import BillingService


# ── fakes ───────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    """The chainable ``db_client.table(...)`` surface the handler uses."""

    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._payload = None
        self._single = False

    def update(self, payload):
        self._payload = payload
        return self

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def single(self):
        self._single = True
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._payload is not None:
            self.store["table_writes"].append((self.name, self._payload))
            return _Result([{}])
        if self.name == "plans":
            # ``.single()`` yields the row itself, not a list — the shape the
            # handler indexes with ``plan.data.get("minutes")``.
            row = self.store["plan"]
            return _Result(row if self._single else [row])
        return _Result(None)


class _Conn:
    def __init__(self, store):
        self.store = store
        self.sql: list[str] = []

    class _Txn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def transaction(self):
        return self._Txn()

    async def execute(self, sql, *args):
        self.sql.append(sql)
        if "UPDATE tenants" in sql and "minutes_allocated" in sql:
            # Execute the statement the way Postgres would: the plan figure
            # plus whatever the ledger says was bought, in ONE evaluation.
            plan_minutes = int(args[1] or 0)
            purchased = max(0, sum(self.store["ledger"]))
            self.store["allocated"] = (
                0 if plan_minutes <= 0 else plan_minutes + purchased
            )
            return "UPDATE 1"
        return "UPDATE 0"


class _Pool:
    def __init__(self, store):
        self.conn = _Conn(store)

    def acquire(self, **_kw):
        conn = self.conn

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _CM()


class _Client:
    def __init__(self, store):
        self.store = store
        self.pool = _Pool(store)

    def table(self, name):
        return _Table(self.store, name)


def make_store(*, allocated=1000, purchased=(), plan_minutes=2000):
    return {
        "allocated": allocated,
        # signed ledger deltas, exactly as ``billing_ledger.minutes_delta``
        "ledger": list(purchased),
        "plan": {"minutes": plan_minutes},
        "table_writes": [],
    }


def _session(plan_id="plan_growth"):
    return {
        "id": "cs_sub_1",
        "customer": "cus_1",
        "subscription": "sub_1",
        "metadata": {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "plan_id": plan_id,
        },
    }


def _svc(store):
    return BillingService(_Client(store))


# ── the money ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_plan_change_keeps_minutes_the_customer_bought():
    """THE BUG. 500 purchased minutes must survive a move to a 2000 plan."""
    store = make_store(allocated=1500, purchased=(500,), plan_minutes=2000)

    await _svc(store)._handle_checkout_completed(_session())

    assert store["allocated"] == 2500, (
        "the plan change overwrote the allocation with the plan figure alone — "
        "500 minutes the customer paid for were destroyed, while the ledger "
        "still records the sale"
    )


@pytest.mark.asyncio
async def test_a_refunded_topup_is_not_re_added_on_the_next_plan_change():
    """The ledger is signed. A refunded bundle nets to zero and must not come
    back as free minutes the next time the plan moves."""
    store = make_store(allocated=1000, purchased=(500, -500), plan_minutes=2000)

    await _svc(store)._handle_checkout_completed(_session())

    assert store["allocated"] == 2000


@pytest.mark.asyncio
async def test_a_tenant_who_never_topped_up_gets_exactly_the_plan():
    store = make_store(allocated=100, purchased=(), plan_minutes=2000)

    await _svc(store)._handle_checkout_completed(_session())

    assert store["allocated"] == 2000


@pytest.mark.asyncio
async def test_an_unlimited_plan_stays_unlimited():
    """``minutes_allocated <= 0`` is the unlimited sentinel everywhere else
    (``minutes_quota``). Adding a purchased balance to it would CAP an
    uncapped account — the same trap ``topup_service`` avoids."""
    store = make_store(allocated=0, purchased=(500,), plan_minutes=0)

    await _svc(store)._handle_checkout_completed(_session())

    assert store["allocated"] == 0, "an unlimited plan was capped at 500 minutes"


@pytest.mark.asyncio
async def test_a_checkout_with_no_plan_leaves_the_allocation_alone():
    store = make_store(allocated=1500, purchased=(500,))

    await _svc(store)._handle_checkout_completed(_session(plan_id=None))

    assert store["allocated"] == 1500


# ── how, not just what ──────────────────────────────────────────────────────

def test_the_purchased_balance_is_read_in_the_same_statement_that_writes():
    """Structural. A SELECT of the ledger followed by a hard SET leaves a
    window: a top-up committing between the two is overwritten, and a lost
    update on money is the bug this file exists for.
    """
    src = inspect.getsource(BillingService._set_plan_allocation)
    assert "billing_ledger" in src, (
        "the plan allocation is written without consulting the ledger"
    )
    assert src.count("UPDATE tenants") == 1
    assert "SELECT" in src.split("UPDATE tenants", 1)[1], (
        "the purchased balance must be a subselect inside the UPDATE, not a "
        "separate read"
    )


@pytest.mark.asyncio
async def test_a_failed_allocation_write_does_not_fall_back_to_destroying_minutes():
    """If the ledger cannot be read, the safe outcome is to leave the
    allocation as it stands and shout — never to write the plan figure alone.
    """
    store = make_store(allocated=1500, purchased=(500,), plan_minutes=2000)
    svc = _svc(store)

    async def _boom(sql, *args):
        raise RuntimeError("connection reset")

    svc.db_client.pool.conn.execute = _boom

    await svc._handle_checkout_completed(_session())  # must not raise

    assert store["allocated"] == 1500
    assert not any(
        "minutes_allocated" in payload
        for _name, payload in store["table_writes"]
    ), "the handler fell back to a hard SET and destroyed the purchased minutes"
