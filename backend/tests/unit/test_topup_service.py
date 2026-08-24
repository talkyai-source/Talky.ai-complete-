"""Minute top-ups: the rules that decide whether money turns into minutes.

goals.md §9 asks for a top-up flow. The part worth testing is not that a row can
be inserted — it is the set of decisions around the insert, because each one has
a failure mode that costs somebody real money:

  * a redelivered webhook must not credit twice,
  * an unpaid or abandoned checkout must not credit at all,
  * a top-up must not be mistaken for a subscription checkout,
  * an unlimited tenant must not be silently converted to a capped one,
  * a refund must not push an allocation negative (which reads as unlimited).

WHAT THIS FILE CAN AND CANNOT PROVE
------------------------------------
The fake connection below runs the service's REAL control flow — every branch
decision in ``credit_paid_order`` executes against it. What it cannot prove is
the behaviour PostgreSQL enforces: the UNIQUE index on ``provider_event_id``,
the row-level security policies, and the transaction boundary. Those are proven
against a real database by ``scripts/verify_topup_ledger.py``; the assertions
here that reference SQL text are marked as structural, and they exist because a
guard that quietly stops being present in the statement is exactly how the trust
rule in lead capture turned decorative.
"""
from __future__ import annotations

import inspect

import pytest

from app.domain.services import topup_service as mod
from app.domain.services.topup_service import (
    MAX_OPEN_ORDERS,
    TopupError,
    TopupService,
    _rows_affected,
)


# ── a connection that behaves the way Postgres does on the paths we branch on ──

class FakeConn:
    """Enough of asyncpg to run the service's real branching.

    ``seen_events`` models the UNIQUE index: a second insert of the same event
    id returns no row, which is what ``ON CONFLICT DO NOTHING ... RETURNING``
    does.
    """

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
        if "UPDATE tenants" in sql and "minutes_allocated + $2" in sql:
            self.store["allocated"] += args[1]
            return "UPDATE 1"
        if "UPDATE tenants" in sql and "GREATEST(0" in sql:
            if self.store["allocated"] > 0:
                self.store["allocated"] = max(0, self.store["allocated"] - args[1])
            return "UPDATE 1"
        if "UPDATE topup_orders" in sql and "status='paid'" in sql:
            self.store["order"]["status"] = "paid"
            return "UPDATE 1"
        if "UPDATE topup_orders" in sql and "SET status=$2" in sql:
            self.store["order"]["status"] = args[1]
            return "UPDATE 1"
        if "UPDATE topup_orders" in sql and "provider_session_id = $2" in sql:
            if self.store.get("order") is None:
                return "UPDATE 0"
            self.store["order"]["provider_session_id"] = args[1]
            return "UPDATE 1"
        return "UPDATE 0"

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if "FROM topup_packages" in sql:
            return self.store.get("package")
        if "FROM topup_orders" in sql:
            return self.store.get("order")
        if "INSERT INTO billing_ledger" in sql:
            # The credit path hardcodes kind='topup' in the statement and so
            # binds one fewer parameter than the reversal path. Getting this
            # offset wrong is how a fake ends up dedup'ing on the note text.
            if "'topup'" in sql:
                kind, delta, event_id = "topup", args[2], args[5]
            else:
                kind, delta, event_id = args[2], args[3], args[6]
            if event_id in self.store["seen_events"]:
                return None  # the UNIQUE index refuses it
            self.store["seen_events"].add(event_id)
            self.store["ledger"].append({"kind": kind, "minutes_delta": delta})
            return {"id": len(self.store["ledger"])}
        if "INSERT INTO topup_orders" in sql:
            return {
                "id": "order-1", "package_code": args[2], "minutes": args[3],
                "price_cents": args[4], "currency": args[5], "status": "pending",
            }
        return None

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if "minutes_allocated" in sql:
            return self.store["allocated"]
        if "count(*)" in sql:
            return self.store.get("open_orders", 0)
        if "SUM(minutes_delta)" in sql:
            return sum(e["minutes_delta"] for e in self.store["ledger"])
        return None

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return []


class FakePool:
    def __init__(self, store):
        self.conn = FakeConn(store)

    def acquire(self, **kwargs):
        conn = self.conn

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _CM()


def make_store(*, allocated=1000, order=None, package=None):
    return {
        "allocated": allocated,
        "order": order,
        "package": package,
        "seen_events": set(),
        "ledger": [],
    }


def paid_order(status="pending", minutes=250):
    return {
        "id": "order-1", "tenant_id": "11111111-1111-1111-1111-111111111111",
        "minutes": minutes, "price_cents": 2500, "currency": "GBP",
        "status": status,
    }


TENANT = "11111111-1111-1111-1111-111111111111"


# ── the redelivery problem ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_same_event_credits_once_no_matter_how_often_it_arrives():
    """THE INVARIANT THIS FEATURE EXISTS TO HOLD.

    Stripe redelivers a webhook whose response was slow. Delivering it three
    times must add 250 minutes, not 750.
    """
    store = make_store(allocated=1000, order=paid_order())
    svc = TopupService(FakePool(store))

    first = await svc.credit_paid_order(session_id="cs_1", event_id="evt_1")
    assert first is True
    assert store["allocated"] == 1250

    # Redelivery 2 and 3. The order is 'paid' now, so it short-circuits before
    # the ledger — but the ledger would refuse it anyway (next test).
    for _ in range(2):
        assert await svc.credit_paid_order(session_id="cs_1", event_id="evt_1") is False
    assert store["allocated"] == 1250, "a redelivered webhook added minutes again"


@pytest.mark.asyncio
async def test_the_ledger_refuses_a_repeat_event_even_if_the_order_looks_unpaid():
    """The order-status check is a fast path, not the guard.

    Two deliveries processed concurrently both read status='pending' before
    either writes. Only the uniqueness constraint separates them, so the credit
    must depend on the ledger insert returning a row — not on the status read.
    """
    store = make_store(allocated=1000, order=paid_order())
    store["seen_events"].add("evt_dup")  # a concurrent delivery got there first
    svc = TopupService(FakePool(store))

    credited = await svc.credit_paid_order(session_id="cs_1", event_id="evt_dup")

    assert credited is False
    assert store["allocated"] == 1000, (
        "minutes were added despite the ledger refusing the entry — the credit "
        "is not actually gated on the uniqueness constraint"
    )


def test_the_ledger_insert_is_conditional_on_the_constraint():
    """Structural: the ON CONFLICT clause is what makes the guard atomic.

    Rewriting this as a SELECT-then-INSERT would still pass the behavioural
    tests above and still double-credit under a real race.
    """
    src = inspect.getsource(TopupService.credit_paid_order)
    assert "ON CONFLICT (provider_event_id) DO NOTHING" in src
    assert "RETURNING id" in src, (
        "the insert must report whether it actually wrote, or the conflict is "
        "indistinguishable from success"
    )


# ── paying is not the same as starting to pay ───────────────────────────────

@pytest.mark.asyncio
async def test_a_payment_for_an_order_we_never_created_credits_nothing():
    store = make_store(allocated=1000, order=None)
    svc = TopupService(FakePool(store))

    assert await svc.credit_paid_order(session_id="cs_ghost", event_id="e") is False
    assert store["allocated"] == 1000


@pytest.mark.asyncio
async def test_an_abandoned_checkout_records_no_ledger_entry():
    """Nothing happened financially, so nothing is recorded. A cancelled order
    that left a ledger row would show up in reconciliation as money we took."""
    store = make_store(order=paid_order())
    svc = TopupService(FakePool(store))

    await svc.mark_failed(session_id="cs_1", status="cancelled")

    assert store["ledger"] == []
    assert store["order"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_mark_failed_refuses_a_status_that_is_not_a_failure():
    svc = TopupService(FakePool(make_store()))
    with pytest.raises(TopupError):
        await svc.mark_failed(session_id="cs_1", status="paid")


# ── the unlimited sentinel ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_topping_up_an_unlimited_tenant_does_not_give_them_a_limit():
    """``minutes_allocated <= 0`` means unlimited everywhere else in the system
    (``minutes_quota``). Adding 250 to 0 would cap an uncapped account — the
    customer pays and comes away with less than they had."""
    store = make_store(allocated=0, order=paid_order())
    svc = TopupService(FakePool(store))

    await svc.credit_paid_order(session_id="cs_1", event_id="evt_1")

    assert store["allocated"] == 0, "an unlimited tenant was given a 250-minute cap"
    assert len(store["ledger"]) == 1, "the money must still be recorded"


# ── reversals ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_refund_is_a_new_negative_row_not_an_edit():
    store = make_store(allocated=1250, order=paid_order(status="paid"))
    svc = TopupService(FakePool(store))

    assert await svc.reverse(event_id="evt_r", kind="refund", payment_id="pi_1") is True

    assert len(store["ledger"]) == 1
    assert store["ledger"][0]["minutes_delta"] == -250
    assert store["allocated"] == 1000


@pytest.mark.asyncio
async def test_a_chargeback_cannot_push_an_allocation_below_zero():
    """A tenant who already SPENT the minutes then charges back. Subtracting
    freely would land on a negative number, and negative reads as UNLIMITED —
    a chargeback would buy them free calls forever."""
    store = make_store(allocated=100, order=paid_order(status="paid"))
    svc = TopupService(FakePool(store))

    await svc.reverse(event_id="evt_d", kind="dispute", payment_id="pi_1")

    assert store["allocated"] == 0
    assert store["allocated"] >= 0


@pytest.mark.asyncio
async def test_reversing_an_unpaid_order_does_nothing():
    store = make_store(allocated=1000, order=paid_order(status="pending"))
    svc = TopupService(FakePool(store))

    assert await svc.reverse(event_id="e", kind="refund", payment_id="pi_1") is False
    assert store["allocated"] == 1000
    assert store["ledger"] == []


@pytest.mark.asyncio
async def test_reverse_needs_something_to_match_on():
    svc = TopupService(FakePool(make_store()))
    with pytest.raises(TopupError):
        await svc.reverse(event_id="e", kind="refund")


@pytest.mark.asyncio
async def test_reverse_refuses_a_kind_that_is_not_a_reversal():
    svc = TopupService(FakePool(make_store()))
    with pytest.raises(TopupError):
        await svc.reverse(event_id="e", kind="topup", payment_id="pi_1")


def test_the_reversal_floor_is_in_the_statement():
    """Structural: doing the floor in Python would leave a window where a
    concurrent credit and reversal interleave into a negative allocation."""
    assert "GREATEST(0" in inspect.getsource(TopupService.reverse)


# ── what the client is allowed to name ──────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unknown_package_is_refused_before_any_order_exists():
    store = make_store(package=None)
    svc = TopupService(FakePool(store))
    with pytest.raises(TopupError):
        await svc.create_order(tenant_id=TENANT, user_id=None, package_code="free_lol")


@pytest.mark.asyncio
async def test_price_and_minutes_come_from_the_package_not_the_caller():
    """THE HOLE THIS CLOSES: an endpoint that accepts `minutes` and `price`
    from the browser sells 10,000 minutes for a penny."""
    store = make_store(package={
        "code": "mins_250", "name": "250 minutes", "minutes": 250,
        "price_cents": 2500, "currency": "GBP",
    })
    svc = TopupService(FakePool(store))

    order = await svc.create_order(
        tenant_id=TENANT, user_id=None, package_code="mins_250",
    )

    assert order["minutes"] == 250
    assert order["price_cents"] == 2500
    sig = inspect.signature(TopupService.create_order).parameters
    assert "minutes" not in sig and "price_cents" not in sig, (
        "create_order must not accept an amount from its caller"
    )


@pytest.mark.asyncio
async def test_a_flood_of_unfinished_checkouts_is_refused():
    store = make_store(package={
        "code": "mins_250", "name": "250 minutes", "minutes": 250,
        "price_cents": 2500, "currency": "GBP",
    })
    store["open_orders"] = MAX_OPEN_ORDERS
    svc = TopupService(FakePool(store))

    with pytest.raises(TopupError):
        await svc.create_order(tenant_id=TENANT, user_id=None, package_code="mins_250")


# ── the silent-zero-row trap ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_lost_session_link_raises_instead_of_passing_quietly():
    """A zero-row UPDATE is a SUCCESS in PostgreSQL. If the link between order
    and checkout session is lost, the payment can never be matched — and this
    codebase has already lost call transcripts to exactly that shape."""
    store = make_store(order=None)  # nothing to update
    svc = TopupService(FakePool(store))

    with pytest.raises(TopupError):
        await svc.attach_session("order-1", "cs_1")


def test_rows_affected_reads_the_asyncpg_tag():
    assert _rows_affected("UPDATE 0") == 0
    assert _rows_affected("UPDATE 3") == 3
    assert _rows_affected("nonsense") == -1


def test_every_write_path_uses_the_rls_aware_connection():
    """Structural. ``topup_orders`` and ``billing_ledger`` carry FORCE RLS, so a
    bare ``pool.acquire()`` sees no rows and every UPDATE silently touches
    nothing. Only the read-only catalogue (which is not tenant-scoped) may use
    a plain acquire."""
    for name in ("create_order", "attach_session", "credit_paid_order",
                 "mark_failed", "reverse", "history", "ledger", "purchased_total"):
        src = inspect.getsource(getattr(TopupService, name))
        assert "acquire_with_tenant" in src, (
            f"{name} acquires a connection without setting the RLS context"
        )


def test_the_catalogue_read_is_the_only_plain_acquire():
    assert "acquire_with_tenant" not in inspect.getsource(TopupService.packages)


# ── the sign convention ─────────────────────────────────────────────────────

def test_trust_that_a_topup_is_positive_and_a_reversal_is_negative():
    credit = inspect.getsource(TopupService.credit_paid_order)
    reverse = inspect.getsource(TopupService.reverse)
    assert "-order[\"minutes\"]" in reverse or "-order['minutes']" in reverse
    assert "'topup'" in credit
