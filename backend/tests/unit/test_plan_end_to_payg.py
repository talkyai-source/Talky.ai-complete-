"""A plan that ends leaves a pay-as-you-go account (bought minutes stay usable)."""
import pytest

from app.domain.services import plan_end
from app.domain.services.subscription_status import CANCELLED, INACTIVE, SUSPENDED

TID = "00000000-0000-0000-0000-0000000000aa"


class Conn:
    def __init__(self, *, status="active", plan="pro", sub="sub_1", customer="cus_1", move="UPDATE 1"):
        self.row = {"subscription_status": status, "plan_id": plan,
                    "stripe_subscription_id": sub, "stripe_customer_id": customer}
        self.move = move
        self.writes = []
        self.allocations = []
        self.plan_seen_by_allocation = None

    async def fetchrow(self, sql, *args):
        assert sql == plan_end._Q_TENANT
        return self.row

    async def execute(self, sql, *args):
        assert sql == plan_end._Q_TO_PAYG
        self.writes.append(args)
        if self.move == "UPDATE 1":
            self.row = {**self.row, "plan_id": args[1], "subscription_status": args[2],
                        "stripe_subscription_id": None}
        return self.move


@pytest.fixture
def conn(monkeypatch):
    c = Conn()
    return _patch(monkeypatch, c)


def _patch(monkeypatch, c):
    async def allocate(conn, tenant_id, minutes):
        c.allocations.append((tenant_id, minutes))
        c.plan_seen_by_allocation = c.row["plan_id"]
    monkeypatch.setattr(plan_end, "apply_plan_allocation", allocate)
    return c


async def test_plan_end_moves_an_active_account_to_payg(conn):
    assert await plan_end.end_plan(conn, TID, stripe_subscription_id="sub_1", stripe_customer_id="cus_1")
    assert conn.writes == [(TID, "payg", INACTIVE, "sub_1")]
    assert conn.row["stripe_subscription_id"] is None
    assert conn.allocations == [(TID, 0)]


async def test_plan_end_runs_allocation_before_the_plan_moves(conn):
    await plan_end.end_plan(conn, TID, stripe_subscription_id="sub_1")
    assert conn.plan_seen_by_allocation == "pro"
    assert conn.row["plan_id"] == "payg"


async def test_plan_end_keeps_an_admin_or_dispute_suspension(monkeypatch):
    c = _patch(monkeypatch, Conn(status="suspended"))
    assert await plan_end.end_plan(c, TID, stripe_subscription_id="sub_1")
    assert c.writes == [(TID, "payg", SUSPENDED, "sub_1")]
    assert c.allocations == [(TID, 0)]


@pytest.mark.parametrize("raw", ["cancelled", "canceled"])
async def test_plan_end_keeps_an_admin_archive(monkeypatch, raw):
    c = _patch(monkeypatch, Conn(status=raw, sub=None))
    assert await plan_end.end_plan(c, TID, stripe_subscription_id=None)
    assert c.writes == [(TID, "payg", CANCELLED, None)]


async def test_plan_end_ignores_a_replaced_stripe_subscription(conn):
    assert not await plan_end.end_plan(conn, TID, stripe_subscription_id="sub_OLD")
    assert conn.writes == [] and conn.allocations == []


async def test_plan_end_ignores_a_customer_mismatch(conn):
    assert not await plan_end.end_plan(conn, TID, stripe_subscription_id="sub_1", stripe_customer_id="cus_2")
    assert conn.writes == [] and conn.allocations == []


async def test_plan_end_ignores_another_paypal_plan(monkeypatch):
    c = _patch(monkeypatch, Conn(sub=None, plan="business"))
    assert not await plan_end.end_plan(c, TID, stripe_subscription_id=None, plan_id="pro")
    assert c.writes == [] and c.allocations == []


async def test_plan_end_raises_when_the_move_misses(monkeypatch):
    c = _patch(monkeypatch, Conn(move="UPDATE 0"))
    with pytest.raises(RuntimeError, match="plan_end_not_applied"):
        await plan_end.end_plan(c, TID, stripe_subscription_id="sub_1")


@pytest.mark.parametrize("start", ["active", "past_due", "unpaid", "trialing", "inactive"])
async def test_plan_end_writes_only_canonical_values(monkeypatch, start):
    c = _patch(monkeypatch, Conn(status=start))
    await plan_end.end_plan(c, TID, stripe_subscription_id="sub_1")
    written = c.writes[0][2]
    assert written == INACTIVE
    assert written not in ("canceled", "active")


@pytest.mark.parametrize("start", ["unpaid", "past_due"])
async def test_past_due_and_unpaid_account_moves_on_plan_end(monkeypatch, start):
    c = _patch(monkeypatch, Conn(status=start))
    assert await plan_end.end_plan(c, TID, stripe_subscription_id="sub_1")
    assert c.row["subscription_status"] == INACTIVE and c.row["plan_id"] == "payg"


async def test_a_reversal_ends_with_the_cancelled_status(monkeypatch):
    c = _patch(monkeypatch, Conn(sub=None, status="active"))
    await plan_end.end_plan(c, TID, stripe_subscription_id=None, plan_id="pro", ended_status=CANCELLED)
    assert c.writes == [(TID, "payg", CANCELLED, None)]
