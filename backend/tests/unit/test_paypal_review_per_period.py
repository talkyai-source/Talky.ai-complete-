"""A recurring PayPal plan review is per billing period; a closed one cannot hide a later episode."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.domain.services import paypal_subscriptions as ps

PLAN = str(uuid4())
OLD = f"local:review:plan:{PLAN}:not_confirmed_with_paypal"


class _Conn:
    def __init__(self, states):
        self.states = states  # event_id -> state
        self.writes: list[tuple] = []
        self.reads: list[str] = []

    async def fetchval(self, sql, *args):
        # Recorded before raising: the caller swallows errors, so the tests read `reads`.
        self.reads.append(sql)
        raise AssertionError("recording a review must not read anything")

    async def fetch(self, sql, *args):
        self.reads.append(sql)
        raise AssertionError("recording a review must not read anything")

    async def fetchrow(self, sql, *args):
        self.reads.append(sql)
        raise AssertionError("recording a review must not read anything")

    async def execute(self, sql, *args):
        assert sql == ps._Q_REVIEW
        self.writes.append(args)
        self.states.setdefault(args[0], "needs_review")


def _svc(states):
    conn = _Conn(states)

    @asynccontextmanager
    async def acquire(_pool, tenant):
        assert tenant is None
        yield conn

    return conn, acquire


@pytest.fixture
def run(monkeypatch):
    async def _run(rule, period_end, states=None):
        conn, acquire = _svc({} if states is None else states)
        monkeypatch.setattr(ps, "acquire_with_tenant", acquire)
        svc = ps.PayPalSubscriptionService.__new__(ps.PayPalSubscriptionService)
        svc._pool = object()
        row = {"id": PLAN, "paypal_subscription_id": "I-planreview1", "provider_mode": "test",
               "current_period_end": period_end}
        await svc._record_review(row, rule)
        return conn

    return _run


END = datetime(2026, 12, 5, 23, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_recurring_rule_with_period_end_gets_the_dated_id(run):
    conn = await run("not_confirmed_with_paypal", END)
    assert conn.writes[0][0] == f"{OLD}:2026-12-05"
    assert conn.writes[0][4] == hashlib.sha256(f"{OLD}:2026-12-05".encode()).hexdigest()
    assert conn.writes[0][3] == "not_confirmed_with_paypal"


@pytest.mark.asyncio
async def test_binding_rule_is_recurring_too(run):
    conn = await run("binding", END)
    assert conn.writes[0][0] == f"local:review:plan:{PLAN}:binding:2026-12-05"


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", ["superseded", "charged_after_cancel", "active_without_payment",
                                  "still_running_at_paypal", "reversal", "dispute"])
async def test_non_recurring_rule_keeps_todays_id_and_reads_nothing(run, rule):
    conn = await run(rule, END)
    assert conn.writes[0][0] == f"local:review:plan:{PLAN}:{rule}"
    assert conn.reads == []


@pytest.mark.asyncio
async def test_a_review_open_under_the_old_id_gets_a_companion_under_the_dated_id(run):
    """No read is made, so the old row is left as it is and the period gets its own row."""
    conn = await run("not_confirmed_with_paypal", END, {OLD: "needs_review"})
    assert conn.writes[0][0] == f"{OLD}:2026-12-05" and conn.reads == []
    assert conn.states[OLD] == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["dismissed", "failed", "processed"])
async def test_finished_old_id_row_moves_to_the_dated_id(run, state):
    conn = await run("not_confirmed_with_paypal", END, {OLD: state})
    assert conn.writes[0][0] == f"{OLD}:2026-12-05"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "", "not a date", 12345])
async def test_missing_or_unreadable_period_end_keeps_todays_id(run, missing):
    conn = await run("not_confirmed_with_paypal", missing)
    assert conn.writes[0][0] == OLD
    assert conn.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [END, END.isoformat(), "2026-12-05", date(2026, 12, 5),
                                   datetime(2026, 12, 5, 1, 0)])
async def test_period_end_may_be_datetime_date_or_string(run, value):
    conn = await run("binding", value)
    assert conn.writes[0][0].endswith(":binding:2026-12-05")


@pytest.mark.asyncio
async def test_two_sweeps_in_one_period_use_the_same_id_and_a_new_period_a_new_one(run):
    states = {OLD: "dismissed"}
    first = await run("not_confirmed_with_paypal", END, states)
    second = await run("not_confirmed_with_paypal", END, states)
    later = await run("not_confirmed_with_paypal", datetime(2027, 1, 5, tzinfo=UTC), states)
    assert first.writes[0][0] == second.writes[0][0] == f"{OLD}:2026-12-05"
    assert later.writes[0][0] == f"{OLD}:2027-01-05"
