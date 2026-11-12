"""F2-03 (billing slice): top-up statements name their tenant explicitly.

Production has run with an application role that bypasses row-level security,
so a statement that does not name its tenant is not isolated at all. Each test
runs a real ``TopupService`` method on a connection that records ``(sql, args)``
and checks two things about the statement that follows the order lookup:

* the tenant predicate is in the SQL, and it is bound to the tenant of the
  order the method has just read (not to a constant, not to nothing);
* executed on SQLite beside a second tenant's rows, an order or ledger row of
  another tenant matches nothing, while the order's own tenant still matches.

The first read of ``credit_paid_order``, ``reverse``, ``reinstate_dispute`` and
``mark_failed`` is not tested here: a signed Stripe event carries no tenant, so
that read is what discovers it. Those are recorded as reviewed exceptions in
``tenant_sql_baseline.json``.
"""
from __future__ import annotations

import re
import sqlite3
from uuid import UUID

import pytest

from app.domain.services.payg import PAYG
from app.domain.services.topup_service import TopupService

TENANT_A = UUID("a1111111-1111-4111-8111-111111111111")
TENANT_B = UUID("b2222222-2222-4222-8222-222222222222")
ORDER_A = UUID("a3333333-3333-4333-8333-333333333333")
ORDER_B = UUID("b4444444-4444-4444-8444-444444444444")


def order_row(tenant, status):
    return {"id": ORDER_A, "tenant_id": tenant, "minutes": 250, "price_cents": 2500,
            "currency": "GBP", "status": status}


class RecordingConn:
    """Answers the few reads the methods branch on and records every statement."""

    def __init__(self, order, *, ledger_hit):
        self.order, self.ledger_hit = order, ledger_hit
        self.calls: list[tuple[str, tuple]] = []

    def is_in_transaction(self):
        return True

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "AS plan_minutes" in sql:  # _unlimited(): a capped subscription tenant
            return {"minutes_allocated": 100, "plan_minutes": 100, "billing_model": None}
        if "INSERT INTO billing_ledger" in sql:
            return {"id": 1}
        if "FROM topup_orders" in sql:
            return self.order
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "INSERT INTO billing_ledger" in sql:
            return 1
        if "FROM billing_ledger" in sql:
            return self.ledger_hit
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


async def run_credit(tenant):
    conn = RecordingConn(order_row(tenant, "pending"), ledger_hit=False)
    credited = await TopupService(None).credit_paid_order(
        session_id="cs_1", event_id="evt_1", payment_id="pi_1", conn=conn)
    assert credited is True
    return conn


async def run_reverse(tenant):
    conn = RecordingConn(order_row(tenant, "paid"), ledger_hit=False)
    reversed_ = await TopupService(None).reverse(
        event_id="evt_2", kind="refund", payment_id="pi_1", session_id="cs_1", conn=conn)
    assert reversed_ is True
    return conn


async def run_reinstate(tenant):
    conn = RecordingConn(order_row(tenant, "disputed"), ledger_hit=True)
    restored = await TopupService(None).reinstate_dispute(
        conn, payment_id="pi_1", dispute_id="dp_1", event_id="evt_3")
    assert restored is True
    return conn


# label, flow, text found in the statement, position of its order id and of its tenant argument
CASES = [
    ("credit: already-credited guard", run_credit, "FROM billing_ledger WHERE order_id=$1", 0, 1),
    ("credit: pay-as-you-go activation", run_credit, "UPDATE tenants SET plan_id", 2, 0),
    ("credit: order marked paid", run_credit, "UPDATE topup_orders SET status='paid'", 0, 2),
    ("reverse: already-reversed guard", run_reverse, "FROM billing_ledger WHERE order_id=$1", 0, 1),
    ("reverse: order marked refunded", run_reverse, "UPDATE topup_orders SET status=$2", 0, 2),
    ("reinstate: dispute-recorded guard", run_reinstate, "FROM billing_ledger WHERE order_id=$1", 0, 1),
    ("reinstate: order marked paid", run_reinstate, "UPDATE topup_orders SET status='paid'", 0, 1),
]
IDS = [case[0] for case in CASES]


def recorded(conn, marker):
    """The one statement the flow sent that contains ``marker``."""
    hits = [(sql, args) for sql, args in conn.calls if marker in " ".join(sql.split())]
    assert len(hits) == 1, f"expected exactly one statement containing {marker!r}, got {len(hits)}"
    sql, args = hits[0]
    return " ".join(sql.split()), args


def bound_tenants(sql, args):
    """Every value the statement compares ``tenant_id`` with, read from its bind slots."""
    return [args[int(slot) - 1] for slot in re.findall(r"\btenant_id\s*=\s*\$(\d+)", sql, re.I)]


@pytest.mark.parametrize("tenant", [TENANT_A, TENANT_B], ids=["tenant-a", "tenant-b"])
@pytest.mark.parametrize("label,flow,marker,order_pos,tenant_pos", CASES, ids=IDS)
async def test_statement_names_the_tenant_of_the_order_it_just_read(
        label, flow, marker, order_pos, tenant_pos, tenant):
    conn = await flow(tenant)
    sql, args = recorded(conn, marker)
    assert bound_tenants(sql, args) == [tenant], (
        f"{label}: no tenant predicate bound to the order's tenant\n{sql}\n{args}")


SCHEMA = """
CREATE TABLE topup_orders (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, package_code TEXT,
                           status TEXT, paid_at TEXT, updated_at TEXT, provider_payment_id TEXT);
CREATE TABLE billing_ledger (id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, order_id TEXT, kind TEXT);
CREATE TABLE tenants (id TEXT PRIMARY KEY, plan_id TEXT, stripe_subscription_id TEXT);
CREATE TABLE topup_packages (code TEXT PRIMARY KEY, audience TEXT);
"""


def seeded_db():
    """Two tenants, each with its own order, ledger history and no plan."""
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO topup_packages VALUES ('bundle', ?)", (PAYG,))
    for tenant, order in ((TENANT_A, ORDER_A), (TENANT_B, ORDER_B)):
        db.execute("INSERT INTO tenants VALUES (?, NULL, NULL)", (str(tenant),))
        db.execute("INSERT INTO topup_orders VALUES (?, ?, 'bundle', 'disputed', NULL, NULL, NULL)",
                   (str(order), str(tenant)))
        for kind in ("topup", "refund", "dispute"):
            db.execute("INSERT INTO billing_ledger (tenant_id, order_id, kind) VALUES (?, ?, ?)",
                       (str(tenant), str(order), kind))
    return db


def execute_on_sqlite(db, sql, args):
    """Run the recorded statement as sent: ``$n`` slots become named binds."""
    statement = re.sub(r"::\w+", "", sql).replace("NOW()", "CURRENT_TIMESTAMP")
    binds = {f"p{n}": str(a) if isinstance(a, UUID) else a for n, a in enumerate(args, 1)}
    cursor = db.execute(re.sub(r"\$(\d+)", r":p\1", statement), binds)
    return cursor.fetchone()[0] if sql.startswith("SELECT") else cursor.rowcount


@pytest.mark.parametrize("label,flow,marker,order_pos,tenant_pos", CASES, ids=IDS)
async def test_another_tenants_order_matches_nothing(label, flow, marker, order_pos, tenant_pos):
    conn = await flow(TENANT_A)
    sql, args = recorded(conn, marker)
    assert bound_tenants(sql, args) == [TENANT_A], f"{label}: no tenant predicate\n{sql}"
    db = seeded_db()

    def run(order):
        values = list(args)
        values[order_pos], values[tenant_pos] = order, TENANT_A
        return execute_on_sqlite(db, sql, values)

    assert run(ORDER_B) == 0, f"{label}: tenant B's order was matched on tenant A's behalf"
    assert run(ORDER_A) >= 1, f"{label}: the order's own tenant no longer matches"
