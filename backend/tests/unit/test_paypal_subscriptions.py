"""PayPal plan service: start, sync (every row of the decision table), cancel, reversal, sweep.

The fake database runs the service's real control flow and understands exactly
the statements the service writes (it matches on the service's own SQL text and
raises AssertionError on anything else). What it cannot prove is what PostgreSQL
enforces (row locks, the partial unique index); the integration test covers that.
"""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from app.domain.services import paypal_subscriptions as ps
from app.domain.services.billing_catalog import BillingCatalogUnavailable
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.paypal_client import PayPalError
from app.domain.services.subscription_status import ACTIVE, CANCELLED, PAST_DUE, SUSPENDED, UNPAID

TENANT = str(uuid4())
OTHER_TENANT = str(uuid4())
OPTION_ID = str(uuid4())
# Built from pieces: not literals that look like real ids.
PLAN_ID = "".join(["P", "-", "test", "plan01"])
SUB_PREFIX = "".join(["I", "-", "test"])
TXN_PREFIX = "".join(["TXN", "-", "test"])

OPTION = {
    "id": OPTION_ID, "plan_id": "pro", "plan_name": "Pro", "kind": "stripe", "interval": "month",
    "interval_count": 1, "amount_minor": 1900, "currency": "usd", "currency_exponent": 2,
    "provider_mode": "test", "minutes": 500,
}


def now():
    return datetime.now(UTC)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(sql):
    return " ".join(sql.split())


class FakeConn:
    def __init__(self, store):
        self.s = store

    def _live(self, tenant):
        return [r for r in self.s["subs"].values() if r["tenant_id"] == tenant and r["status"] in ps.LIVE]

    async def fetchrow(self, sql, *a):
        self.s["sql"].append(sql)
        s = self.s
        if sql == ps._Q_TENANT_LOCK:
            return copy.deepcopy(s["tenants"].get(a[0]))
        if sql in (ps._Q_ROW_BY_ID, ps._Q_ROW_LOCK):
            r = s["subs"].get(a[0])
            return copy.deepcopy(r) if r and r["tenant_id"] == a[1] else None
        if sql == ps._Q_ROW_BY_PP:
            return copy.deepcopy(next((r for r in s["subs"].values()
                                       if r["paypal_subscription_id"] == a[0]), None))
        if sql == ps._Q_PLAN_OPTION:
            return copy.deepcopy(s["plan_options"].get(a[0]))
        if sql == ps._Q_STRIPE_ATTEMPT:
            return copy.deepcopy(next((r for r in s["attempts"] if r["tenant_id"] == a[0]
                                       and r["status"] in ("creating", "ready", "unknown")), None))
        if sql == ps._Q_OTHER_LIVE:
            rows = self._live(a[0])
            return copy.deepcopy(rows[0]) if rows else None
        if sql == ps._Q_HELD:
            rows = [r for r in s["subs"].values() if r["tenant_id"] == a[0] and r["status"] in ps.HELD]
            return copy.deepcopy(rows[0]) if rows else None
        if sql == ps._Q_OPEN_ROW:
            rows = [r for r in s["subs"].values() if r["tenant_id"] == a[0] and r["status"] in ps.OPEN]
            return copy.deepcopy(rows[0]) if rows else None
        if sql == ps._Q_PAYMENT_BY_TXN:
            return copy.deepcopy(s["payments"].get(a[0]))
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetchval(self, sql, *a):
        self.s["sql"].append(sql)
        s = self.s
        found = None
        if sql == ps._Q_STRIPE_SUB_EXISTS:
            found = any(r["tenant_id"] == a[0] and r["status"] not in ("canceled", "cancelled", "incomplete_expired")
                        for r in s["stripe_subs"])
        elif sql == ps._Q_STRIPE_ATTEMPT_EXISTS:
            found = any(r["tenant_id"] == a[0] and r["status"] in ("creating", "ready", "unknown")
                        for r in s["attempts"])
        elif sql == ps._Q_OTHER_LIVE_EXISTS:
            found = any(r["id"] != a[1] for r in self._live(a[0]))
        elif sql == ps._Q_HAS_LIVE:
            found = bool(self._live(a[0]))
        elif sql == ps._Q_HELD_EXISTS:
            found = any(r["tenant_id"] == a[0] and r["status"] in ps.HELD for r in s["subs"].values())
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        return 1 if found else None  # SELECT 1 ... LIMIT 1

    async def fetch(self, sql, *a):
        self.s["sql"].append(sql)
        s = self.s
        t = now()
        if sql == ps._Q_SWEEP_DUE:
            return [copy.deepcopy(r) for r in s["subs"].values()
                    if r["status"] == ACTIVE and r["cancel_at_period_end"]
                    and r["current_period_end"] and r["current_period_end"] <= t][: a[0]]
        if sql == ps._Q_SWEEP_STALE:
            return [copy.deepcopy(r) for r in s["subs"].values()
                    if r["status"] in ps.LIVE
                    and (r["checked_at"] is None or r["checked_at"] < t - timedelta(hours=6)
                         or (r["status"] in ps.OPEN and r["checked_at"] < t - timedelta(minutes=10)))][: a[0]]
        if sql == ps._Q_SWEEP_ABANDONED:
            return [copy.deepcopy(r) for r in s["subs"].values()
                    if r["status"] in ("failed", "expired") and r["activated_at"] is None
                    and r["paypal_subscription_id"] and r["created_at"] > t - timedelta(days=3)
                    and (r["checked_at"] is None or r["checked_at"] < t - timedelta(hours=1))][: a[0]]
        if sql == ps._Q_SWEEP_STRAY:
            return [copy.deepcopy(r) for r in s["subs"].values()
                    if r["status"] == "cancelled" and r["paypal_subscription_id"]
                    and r["paypal_status"] in ("APPROVED", "ACTIVE", "SUSPENDED")
                    and (r["checked_at"] is None or r["checked_at"] < t - timedelta(hours=1))][: a[0]]
        if sql == ps._Q_SWEEP_UNCONFIRMED:
            return [copy.deepcopy(r) for r in s["subs"].values()
                    if r["status"] in ("active", "past_due") and not r["cancel_at_period_end"]
                    and r["current_period_end"] < t - timedelta(days=3)
                    and (r["checked_at"] is None or r["checked_at"] < t - timedelta(days=3))][: a[0]]
        if sql == ps._Q_PAYMENTS:
            out = []
            for p in sorted(s["payments"].values(), key=lambda p: p["paid_at"], reverse=True):
                if p["tenant_id"] != a[0] or (a[2] is not None and p["id"] != a[2]):
                    continue
                sub = s["subs"][p["subscription_id"]]
                out.append({**p, "provider_mode": sub["provider_mode"], "snapshot": sub["snapshot"]})
            return out[: a[1]]
        raise AssertionError(f"unexpected SQL: {sql}")

    async def execute(self, sql, *a):
        self.s["sql"].append(sql)
        s = self.s
        if sql in (ps._Q_SAVE_CREATED, ps._Q_MARK_UNKNOWN, ps._Q_FAIL_UNSTARTED, ps._Q_CLOSE_OPEN,
                   ps._Q_SAVE):
            r = s["subs"].get(a[0])
            if r is None or r["tenant_id"] != a[1]:
                return "UPDATE 0"
            if sql == ps._Q_SAVE:
                r.update(zip(ps._SAVED, a[2:]))
                return "UPDATE 1"
            allowed = ps.OPEN if sql == ps._Q_CLOSE_OPEN else (ps.CREATING, ps.UNKNOWN)
            if r["status"] not in allowed:
                return "UPDATE 0"
            if sql == ps._Q_SAVE_CREATED:
                r.update(paypal_subscription_id=a[2], approval_url=a[3], paypal_status=a[4],
                         status="approval_pending", checked_at=now())
            elif sql == ps._Q_MARK_UNKNOWN:
                r["status"] = "unknown"
            elif sql == ps._Q_FAIL_UNSTARTED:
                r.update(status="failed", ended_at=now())
            else:
                r.update(status=a[2], ended_at=now())
            return "UPDATE 1"
        if sql == ps._Q_INSERT:
            rid, tenant = a[0], a[1]
            if rid in s["subs"] or self._live(tenant) or s.get("insert_conflict"):
                raise asyncpg.UniqueViolationError("duplicate")
            s["subs"][rid] = make_row(
                id=rid, tenant_id=tenant, price_option_id=a[2], plan_id=a[3], provider_mode=a[4],
                paypal_plan_id=a[5], snapshot=json.loads(a[6]), status="creating", paypal_subscription_id=None)
            return "INSERT 0 1"
        if sql == ps._Q_TENANT_PROJECT:
            t = s["tenants"][a[0]]
            if t["stripe_subscription_id"] is None and t["plan_id"] == a[2]:
                t["subscription_status"] = a[1]
            return "UPDATE 1"
        if sql == ps._Q_TENANT_PLAN_ONLY:
            s["tenants"][a[0]]["plan_id"] = a[1]
            return "UPDATE 1"
        if sql == ps._Q_TENANT_GRANT:
            s["tenants"][a[0]].update(plan_id=a[1], subscription_status=a[2])
            return "UPDATE 1"
        if sql == ps._Q_PAYMENT_REVERSE:
            p = next(p for p in s["payments"].values() if p["id"] == a[0] and p["tenant_id"] == a[1])
            p["status"] = "reversed"
            return "UPDATE 1"
        if sql == ps._Q_RECHECK_SOON:
            r = s["subs"].get(a[0])
            if r is None or r["tenant_id"] != a[1]:
                return "UPDATE 0"
            if r["activated_at"] is not None and r["activated_at"] > now() - timedelta(days=2):
                r["checked_at"] = None
                return "UPDATE 1"
            return "UPDATE 0"
        if sql in (ps._Q_PAYMENT_UPDATE, ps._Q_PAYMENT_INSERT) and s.get("payments_fail"):
            raise RuntimeError("db down")
        if sql == ps._Q_PAYMENT_UPDATE:
            cur = s["payments"].get(a[2])
            if (cur is None or cur["tenant_id"] != a[0] or cur["subscription_id"] != a[1]
                    or cur["status"] == "reversed"):
                return "UPDATE 0"
            cur["status"] = a[3]
            return "UPDATE 1"
        if sql == ps._Q_PAYMENT_INSERT:
            if a[2] in s["payments"]:
                return "INSERT 0 0"  # ON CONFLICT DO NOTHING
            s["payments"][a[2]] = dict(id=str(uuid4()), tenant_id=a[0], subscription_id=a[1],
                                       paypal_transaction_id=a[2], status=a[3], amount_minor=a[4],
                                       currency=a[5], paid_at=a[6], created_at=now())
            return "INSERT 0 1"
        if sql == ps._Q_REVIEW:
            if s.get("review_fails"):
                raise RuntimeError("db down")
            row = s["reviews"].setdefault(a[0], {"attempts": 0, "detail": a[3]})
            row["attempts"] += 1
            return "INSERT 0 1"
        raise AssertionError(f"unexpected SQL: {sql}")


def make_row(**over):
    row = {
        "id": str(uuid4()), "tenant_id": TENANT, "price_option_id": OPTION_ID, "plan_id": "pro",
        "provider_mode": "test", "paypal_plan_id": PLAN_ID, "paypal_subscription_id": None,
        "snapshot": {"option": dict(OPTION), "paypal_plan_id": PLAN_ID,
                     "return_url": "https://app.example/billing?checkout=success",
                     "cancel_url": "https://app.example/billing?checkout=cancelled"},
        "status": "approval_pending", "paypal_status": None, "approval_url": None,
        "activated_at": None, "current_period_start": None, "current_period_end": None,
        "cancel_at_period_end": False, "cancelled_at": None, "ended_at": None,
        "last_payment_at": None, "failed_payments": 0, "checked_at": None, "approved_at": None,
        "created_at": now() - timedelta(minutes=5), "updated_at": now(),
    }
    row.update(over)
    return row


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.remote: dict[str, dict] = {}
        self.txns: dict[str, list] = {}
        self.create_error: Exception | None = None
        self.create_answer: dict | None = None
        self.get_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.list_error: Exception | None = None
        self.on_create = None
        self._by_request: dict[str, str] = {}

    async def create_subscription(self, **kw):
        self.calls.append(("create", kw))
        if self.on_create:
            self.on_create()
        if self.create_error:
            raise self.create_error
        if self.create_answer is not None:
            return copy.deepcopy(self.create_answer)
        sid = self._by_request.setdefault(kw["request_id"], f"{SUB_PREFIX}{len(self._by_request) + 1:04d}")
        sub = {"id": sid, "status": "APPROVAL_PENDING", "plan_id": kw["plan_id"], "custom_id": kw["custom_id"],
               "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/subscribe?x=1"}]}
        self.remote[sid] = sub
        return copy.deepcopy(sub)

    async def get_subscription(self, sid):
        self.calls.append(("get", sid))
        if self.get_error:
            raise self.get_error
        return copy.deepcopy(self.remote[sid])

    async def cancel_subscription(self, sid, *, reason):
        self.calls.append(("cancel", sid, reason))
        if self.cancel_error:
            raise self.cancel_error
        if sid in self.remote:
            self.remote[sid]["status"] = "CANCELLED"

    async def list_subscription_transactions(self, sid, *, start_time, end_time):
        self.calls.append(("list", sid, start_time, end_time))
        if self.list_error:
            raise self.list_error
        return copy.deepcopy(self.txns.get(sid, []))

    def names(self):
        return [c[0] for c in self.calls]


def _fake_approval(sub, *, env):
    for link in sub.get("links") or []:
        if link.get("rel") == "approve":
            return link["href"]
    return None


@pytest.fixture
def env(monkeypatch):
    store = {
        "sql": [], "subs": {}, "payments": {}, "reviews": {}, "stripe_subs": [], "attempts": [],
        "tenants": {TENANT: {"id": TENANT, "status": "active", "subscription_status": "inactive",
                             "stripe_subscription_id": None, "plan_id": "free"}},
        "plan_options": {OPTION_ID: {"paypal_plan_id": PLAN_ID, "provider_mode": "test", "interval": "month",
                                     "amount_minor": 1900, "currency": "usd", "active": True,
                                     "verified_at": now()}},
    }
    grants: list = []

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kw):
        saved = {k: copy.deepcopy(v) for k, v in store.items() if k != "sql"}
        try:
            yield FakeConn(store)
        except BaseException:
            store.update(saved)
            raise

    async def fake_allocation(conn, tenant_id, minutes):
        grants.append((tenant_id, minutes))
        store["tenants"][tenant_id]["minutes_allocated"] = minutes

    async def fake_resolve(pool, option_id, *, allow_team_only=False):
        if store.get("offer_error"):
            raise BillingCatalogUnavailable("Unknown price option.")
        return dict(OPTION)

    monkeypatch.setattr(ps, "acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(ps, "apply_plan_allocation", fake_allocation)
    monkeypatch.setattr(ps, "resolve_checkout_option", fake_resolve)
    monkeypatch.setattr(ps, "billing_return_urls", lambda: "https://app.example/billing")
    monkeypatch.setattr(ps, "paypal_provider_mode", lambda: "test")
    monkeypatch.setattr(ps, "paypal_env", lambda: "sandbox")
    monkeypatch.setattr(ps._pc, "subscription_approval_url", _fake_approval, raising=False)
    client = FakeClient()
    svc = ps.PayPalSubscriptionService(object(), client=client)
    return SimpleNamespace(svc=svc, client=client, store=store, grants=grants, mp=monkeypatch)


def add_row(env, **over):
    over.setdefault("paypal_subscription_id", f"{SUB_PREFIX}{len(env.store['subs']) + 1:04d}x")
    row = make_row(**over)
    env.store["subs"][row["id"]] = row
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "APPROVAL_PENDING", paid=False)
    return row


def add_active(env, **over):
    base = dict(status="active", activated_at=now() - timedelta(days=3), paypal_status="ACTIVE",
                current_period_start=now() - timedelta(days=3), current_period_end=now() + timedelta(days=27),
                last_payment_at=now() - timedelta(days=3))
    base.update(over)
    env.store["tenants"][TENANT].update(plan_id="pro", subscription_status="active")
    return add_row(env, **base)


def pp_sub(row, status="ACTIVE", *, paid=True, failed=0, value="19.00", currency="USD",
           paid_time=None, next_billing=None, **over):
    info = {"failed_payments_count": failed,
            "next_billing_time": next_billing or iso(now() + timedelta(days=30))}
    if paid:
        info["last_payment"] = {"amount": {"currency_code": currency, "value": value},
                                "time": paid_time or iso(now())}
    sub = {"id": row["paypal_subscription_id"], "status": status, "plan_id": row["paypal_plan_id"],
           "custom_id": str(row["id"]), "billing_info": info}
    sub.update(over)
    return sub


def stored(env, row):
    return env.store["subs"][row["id"]]


async def apply(env, row, status="ACTIVE", **kw):
    sub = pp_sub(row, status, **kw)
    return await env.svc._apply(env.store["subs"][row["id"]], sub)


def tenant(env):
    return env.store["tenants"][TENANT]


async def start(env, rid=None, option=OPTION_ID, tenant_id=TENANT):
    return await env.svc.start_checkout(tenant_id=tenant_id, request_id=rid or str(uuid4()),
                                        price_option_id=option)


# -- constants -----------------------------------------------------------------------


def test_status_groups():
    assert ps.LIVE == ("creating", "unknown", "approval_pending", "active", "past_due", "suspended")
    assert ps.OPEN == ("creating", "unknown", "approval_pending")
    assert ps.HELD == ("active", "past_due", "suspended")


def test_literal_sql_lists_equal_the_tuples():
    for literal, values in ((ps._LIVE_SQL, ps.LIVE), (ps._OPEN_SQL, ps.OPEN), (ps._HELD_SQL, ps.HELD)):
        assert literal == "(" + ", ".join(f"'{v}'" for v in values) + ")"


# -- start_checkout ------------------------------------------------------------------


async def test_happy_start(env):
    rid = str(uuid4())
    out = await start(env, rid)
    row = stored(env, {"id": rid})
    assert row["status"] == "approval_pending" and row["paypal_subscription_id"] == f"{SUB_PREFIX}0001"
    assert row["paypal_status"] == "APPROVAL_PENDING" and row["checked_at"] is not None
    assert out == {
        "request_id": rid, "state": "open", "session_id": row["paypal_subscription_id"],
        "checkout_url": "https://www.sandbox.paypal.com/subscribe?x=1",
        "price_option": ps.public_option(OPTION), "mock_mode": False, "message": None}
    kw = env.client.calls[0][1]
    assert kw["request_id"] == f"plan-sub-{rid}" and kw["custom_id"] == rid and kw["plan_id"] == PLAN_ID
    assert kw["brand_name"] == "Talk-Lee"
    assert kw["return_url"] == f"https://app.example/billing?checkout=success&provider=paypal&request_id={rid}"
    assert kw["cancel_url"] == f"https://app.example/billing?checkout=cancelled&provider=paypal&request_id={rid}"
    snap = row["snapshot"]
    assert snap["paypal_plan_id"] == PLAN_ID and snap["option"]["minutes"] == 500
    assert snap["option"]["id"] == OPTION_ID and snap["return_url"] == kw["return_url"]


async def test_replay_does_not_insert_twice(env):
    rid = str(uuid4())
    first = await start(env, rid)
    second = await start(env, rid)
    assert first["session_id"] == second["session_id"] and second["state"] == "open"
    assert len(env.store["subs"]) == 1
    assert env.client.names().count("create") == 1
    assert sum(1 for s in env.store["sql"] if s == ps._Q_INSERT) == 1


async def test_a_different_offer_on_the_same_id_conflicts(env):
    rid = str(uuid4())
    await start(env, rid)
    with pytest.raises(CheckoutError) as raised:
        await start(env, rid, option=str(uuid4()))
    assert raised.value.code == "checkout_payload_conflict"


async def test_invalid_request_id(env):
    with pytest.raises(CheckoutError) as raised:
        await start(env, "not-a-uuid")
    assert raised.value.code == "checkout_invalid" and raised.value.request_not_started


async def test_paypal_mode_off(env):
    env.mp.setattr(ps, "paypal_provider_mode", lambda: None)
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert (raised.value.code, raised.value.status_code, raised.value.request_not_started) == (
        "paypal_unavailable", 503, True)
    assert not env.store["subs"]


async def test_client_not_configured(env):
    from app.domain.services.paypal_client import PayPalNotConfigured

    def boom():
        raise PayPalNotConfigured

    env.mp.setattr(ps, "get_paypal_client", boom)
    svc = ps.PayPalSubscriptionService(object())
    with pytest.raises(CheckoutError) as raised:
        await svc.start_checkout(tenant_id=TENANT, request_id=str(uuid4()), price_option_id=OPTION_ID)
    assert raised.value.code == "paypal_unavailable" and raised.value.status_code == 503


async def test_offer_rejected_by_catalog(env):
    env.store["offer_error"] = True
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert (raised.value.code, raised.value.status_code, raised.value.request_not_started) == (
        "billing_offer_unavailable", 400, True)


@pytest.mark.parametrize("change", [
    "missing", {"active": False}, {"verified_at": None}, {"provider_mode": "live"},
    {"interval": "year"}, {"amount_minor": 1800}, {"currency": "eur"},
])
async def test_paypal_plan_must_match_the_offer(env, change):
    if change == "missing":
        env.store["plan_options"].clear()
    else:
        env.store["plan_options"][OPTION_ID].update(change)
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "paypal_offer_unavailable" and raised.value.request_not_started
    assert not env.store["subs"] and not env.client.calls


async def test_unsupported_currency_is_refused(env, monkeypatch):
    async def resolve(pool, option_id, *, allow_team_only=False):
        return {**OPTION, "currency": "jpy"}

    env.mp.setattr(ps, "resolve_checkout_option", resolve)
    env.store["plan_options"][OPTION_ID]["currency"] = "jpy"
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "paypal_offer_unavailable"


@pytest.mark.parametrize("case", [
    "no_tenant", "inactive", "suspended", "stripe_customer_sub", "stripe_row", "held_paypal"])
async def test_guards_refuse_before_any_row_or_paypal_call(env, case):
    t = tenant(env)
    if case == "no_tenant":
        env.store["tenants"].clear()
    elif case == "inactive":
        t["status"] = "suspended"
    elif case == "suspended":
        t["subscription_status"] = SUSPENDED
    elif case == "stripe_customer_sub":
        t["stripe_subscription_id"] = "".join(["sub", "_", "test01"])
    elif case == "stripe_row":
        env.store["stripe_subs"].append({"tenant_id": TENANT, "status": "active"})
    elif case == "held_paypal":
        add_active(env)
    before = len(env.store["subs"])
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    code = {"no_tenant": "tenant_unavailable", "inactive": "tenant_unavailable",
            "suspended": "tenant_unavailable"}.get(case, "subscription_exists")
    assert raised.value.code == code and raised.value.request_not_started
    assert len(env.store["subs"]) == before and env.client.names().count("create") == 0


async def test_ended_stripe_rows_do_not_block(env):
    env.store["stripe_subs"].append({"tenant_id": TENANT, "status": "canceled"})
    assert (await start(env))["state"] == "open"


async def test_outstanding_stripe_attempt_is_reported(env):
    attempt = {"id": str(uuid4()), "tenant_id": TENANT, "status": "ready", "stripe_session_id": "cs_x",
               "checkout_url": "https://checkout.stripe.com/x", "snapshot": {"option": dict(OPTION)}}
    env.store["attempts"].append(attempt)
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    err = raised.value
    assert err.code == "checkout_outstanding" and err.request_not_started
    assert err.existing_attempt["request_id"] == attempt["id"] and err.existing_attempt["state"] == "open"
    assert not env.store["subs"]


async def test_abandoned_open_attempt_is_released_when_paypal_still_waits(env):
    other = add_row(env, status="approval_pending")
    out = await start(env)
    assert out["state"] == "open"
    assert stored(env, other)["status"] == "failed" and stored(env, other)["ended_at"] is not None
    assert len(env.store["subs"]) == 2


async def test_open_attempt_that_paypal_says_is_paid_blocks_and_is_granted_once(env):
    other = add_row(env, status="approval_pending")
    env.client.remote[other["paypal_subscription_id"]] = pp_sub(other, "ACTIVE")
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "subscription_exists"
    assert stored(env, other)["status"] == "active" and len(env.grants) == 1
    assert env.client.names().count("create") == 0


async def test_open_attempt_active_but_unpaid_still_blocks(env):
    other = add_row(env, status="approval_pending")
    env.client.remote[other["paypal_subscription_id"]] = pp_sub(other, "ACTIVE", paid=False)
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    err = raised.value
    assert err.code == "checkout_outstanding" and err.existing_attempt["request_id"] == other["id"]
    assert err.existing_attempt["state"] == "pending"
    assert not env.grants


async def test_fresh_open_row_without_a_paypal_id_is_left_alone(env):
    other = make_row(status="creating", created_at=now() - timedelta(seconds=30))
    env.store["subs"][other["id"]] = other
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "checkout_outstanding"
    assert env.client.calls == []


async def test_old_open_row_without_a_paypal_id_learns_its_id_then_is_released(env):
    other = make_row(status="unknown", created_at=now() - timedelta(minutes=10))
    env.store["subs"][other["id"]] = other
    out = await start(env)
    assert out["state"] == "open"
    assert stored(env, other)["status"] == "failed"
    assert env.client.calls[0][1]["request_id"] == f"plan-sub-{other['id']}"


async def test_old_open_row_whose_create_still_fails_propagates(env):
    other = make_row(status="creating", created_at=now() - timedelta(minutes=10))
    env.store["subs"][other["id"]] = other
    env.client.create_error = PayPalError(status=503, name="X", retryable=True)
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "checkout_unconfirmed"


async def test_release_cannot_reach_paypal(env):
    add_row(env, status="approval_pending")
    env.client.get_error = PayPalError(status=500, name="X", retryable=True)
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "checkout_unconfirmed" and raised.value.status_code == 503


async def test_retryable_paypal_error_leaves_unknown(env):
    env.client.create_error = PayPalError(status=503, name="X", retryable=True)
    rid = str(uuid4())
    with pytest.raises(CheckoutError) as raised:
        await start(env, rid)
    assert raised.value.code == "checkout_unconfirmed" and raised.value.status_code == 503
    assert not raised.value.request_not_started
    assert stored(env, {"id": rid})["status"] == "unknown"


async def test_unknown_row_is_retried_with_the_same_paypal_request_id(env):
    env.client.create_error = PayPalError(status=503, name="X", retryable=True)
    rid = str(uuid4())
    with pytest.raises(CheckoutError):
        await start(env, rid)
    env.client.create_error = None
    out = await start(env, rid)
    assert out["state"] == "open" and stored(env, {"id": rid})["status"] == "approval_pending"
    assert [c[1]["request_id"] for c in env.client.calls] == [f"plan-sub-{rid}"] * 2
    assert len(env.store["subs"]) == 1


async def test_refusal_fails_the_row(env):
    env.client.create_error = PayPalError(status=422, name="UNPROCESSABLE", issue="X", retryable=False)
    rid = str(uuid4())
    with pytest.raises(CheckoutError) as raised:
        await start(env, rid)
    assert raised.value.code == "paypal_refused" and raised.value.status_code == 502
    row = stored(env, {"id": rid})
    assert row["status"] == "failed" and row["ended_at"] is not None


async def test_other_exception_leaves_unknown(env):
    env.client.create_error = RuntimeError("socket closed")
    rid = str(uuid4())
    with pytest.raises(CheckoutError) as raised:
        await start(env, rid)
    assert raised.value.code == "checkout_unconfirmed"
    assert stored(env, {"id": rid})["status"] == "unknown"


async def test_cancellation_marks_unknown_and_is_reraised(env):
    env.client.create_error = asyncio.CancelledError()
    rid = str(uuid4())
    with pytest.raises(asyncio.CancelledError):
        await start(env, rid)
    assert stored(env, {"id": rid})["status"] == "unknown"


@pytest.mark.parametrize("answer", [
    {"id": "bad id!", "status": "APPROVAL_PENDING"},
    {"id": 7, "status": "APPROVAL_PENDING"},
    {"status": "APPROVAL_PENDING"},
    {"id": "".join([SUB_PREFIX, "9"]), "status": "CANCELLED"},
    {"id": "".join([SUB_PREFIX, "9"]), "status": "APPROVAL_PENDING", "plan_id": "other"},
    {"id": "".join([SUB_PREFIX, "9"]), "status": "APPROVAL_PENDING", "custom_id": "other"},
    {"id": "".join([SUB_PREFIX, "9"]), "status": "APPROVAL_PENDING"},  # no approval link
])
async def test_untrustworthy_answer_leaves_unknown(env, answer):
    answer = dict(answer)
    if answer.get("plan_id") is None:
        answer["plan_id"] = PLAN_ID
    env.client.create_answer = answer
    rid = str(uuid4())
    with pytest.raises(CheckoutError) as raised:
        await start(env, rid)
    assert raised.value.code == "checkout_unconfirmed"
    row = stored(env, {"id": rid})
    assert row["status"] == "unknown" and row["paypal_subscription_id"] is None


async def test_non_pending_answer_needs_no_link(env):
    env.client.create_answer = {"id": "".join([SUB_PREFIX, "9"]), "status": "APPROVED", "plan_id": PLAN_ID}
    out = await start(env)
    assert out["state"] == "pending" and out["checkout_url"] is None and out["message"] == ps.MSG_CONFIRMING


async def test_save_that_updates_no_row_is_unconfirmed(env):
    rid = str(uuid4())

    def flip():
        stored(env, {"id": rid})["status"] = "failed"

    env.client.on_create = flip
    with pytest.raises(CheckoutError) as raised:
        await start(env, rid)
    assert raised.value.code == "checkout_unconfirmed"


async def test_insert_that_loses_on_the_unique_index_is_a_conflict(env):
    env.store["insert_conflict"] = True  # another request id won the race to the partial index
    with pytest.raises(CheckoutError) as raised:
        await start(env)
    assert raised.value.code == "checkout_conflict" and not env.store["subs"]


# -- get_attempt / response states -------------------------------------------------


async def test_get_attempt_not_found(env):
    with pytest.raises(CheckoutError) as raised:
        await env.svc.get_attempt(tenant_id=TENANT, request_id=str(uuid4()))
    assert raised.value.status_code == 404 and raised.value.code == "checkout_not_found"


async def test_get_attempt_is_tenant_bound(env):
    row = add_row(env)
    with pytest.raises(CheckoutError):
        await env.svc.get_attempt(tenant_id=OTHER_TENANT, request_id=row["id"])


async def test_get_attempt_activates_once_paid(env):
    row = add_row(env, approval_url="https://www.sandbox.paypal.com/x", paypal_status="APPROVAL_PENDING")
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    out = await env.svc.get_attempt(tenant_id=TENANT, request_id=row["id"])
    assert out["state"] == "activated" and out["checkout_url"] is None and out["message"] is None
    assert len(env.grants) == 1


async def test_get_attempt_swallows_paypal_errors(env):
    row = add_row(env, approval_url="https://www.sandbox.paypal.com/x", paypal_status="APPROVAL_PENDING")
    env.client.get_error = PayPalError(status=500, name="X", retryable=True)
    out = await env.svc.get_attempt(tenant_id=TENANT, request_id=row["id"])
    assert out["state"] == "open" and out["checkout_url"] == "https://www.sandbox.paypal.com/x"


async def test_get_attempt_swallows_unconfigured_client(env):
    row = add_row(env, status="creating", paypal_subscription_id=None)
    row2 = add_row(env, tenant_id=OTHER_TENANT, status="approval_pending")
    env.svc._client_override = None

    def boom():
        raise ps.PayPalNotConfigured

    env.mp.setattr(ps, "get_paypal_client", boom)
    out = await env.svc.get_attempt(tenant_id=OTHER_TENANT, request_id=row2["id"])
    assert out["state"] == "pending"
    assert (await env.svc.get_attempt(tenant_id=TENANT, request_id=row["id"]))["state"] == "pending"


@pytest.mark.parametrize("over,state,message", [
    ({"activated_at": now()}, "activated", None),
    ({"status": "expired"}, "expired", None),
    ({"status": "failed"}, "failed", None),
    ({"status": "cancelled"}, "failed", None),
    ({"status": "approval_pending", "paypal_status": "APPROVAL_PENDING", "approval_url": "u"}, "open", None),
    ({"status": "approval_pending", "paypal_status": None, "approval_url": "u"}, "open", None),
    ({"status": "approval_pending", "paypal_status": "APPROVAL_PENDING", "approval_url": None},
     "pending", ps.MSG_UNCONFIRMED),
    ({"status": "approval_pending", "paypal_status": "APPROVED", "approval_url": "u"},
     "pending", ps.MSG_CONFIRMING),
    ({"status": "approval_pending", "paypal_status": "ACTIVE"}, "pending", ps.MSG_CONFIRMING),
    ({"status": "creating", "paypal_status": None}, "pending", ps.MSG_UNCONFIRMED),
    ({"status": "unknown"}, "pending", ps.MSG_UNCONFIRMED),
])
def test_response_states(over, state, message):
    out = ps._response(make_row(**over))
    assert out["state"] == state and out["message"] == message
    assert (out["checkout_url"] is not None) == (state == "open")


# -- _apply: the decision table ---------------------------------------------------


async def test_approval_pending_young_stays(env):
    row = add_row(env, status="creating")
    out = await apply(env, row, "APPROVAL_PENDING", paid=False)
    s = stored(env, row)
    assert s["status"] == "approval_pending" and s["paypal_status"] == "APPROVAL_PENDING"
    assert s["checked_at"] is not None and not out.activated and not out.review


async def test_approval_pending_old_expires(env):
    row = add_row(env, created_at=now() - timedelta(hours=4))
    out = await apply(env, row, "APPROVAL_PENDING", paid=False)
    s = stored(env, row)
    assert s["status"] == "expired" and s["ended_at"] is not None and out.status == "expired"


async def test_approved_keeps_waiting_even_when_old(env):
    row = add_row(env, created_at=now() - timedelta(hours=4))
    await apply(env, row, "APPROVED", paid=False)
    s = stored(env, row)
    assert s["status"] == "approval_pending" and s["paypal_status"] == "APPROVED"


@pytest.mark.parametrize("start_status", ["approval_pending", "failed", "expired"])
async def test_active_without_payment_grants_nothing(env, start_status):
    row = add_row(env, status=start_status, ended_at=now() if start_status != "approval_pending" else None)
    out = await apply(env, row, "ACTIVE", paid=False)
    s = stored(env, row)
    # an abandoned purchase that PayPal still runs is followed again, never shown as failed
    assert s["status"] == "approval_pending" and s["ended_at"] is None and not out.activated
    assert not env.grants and tenant(env)["plan_id"] == "free"
    assert s["paypal_status"] == "ACTIVE"


async def test_active_with_matching_payment_grants_exactly_once(env):
    row = add_row(env)
    paid_at = iso(now() - timedelta(minutes=1))
    next_billing = iso(now() + timedelta(days=30))
    out = await apply(env, row, "ACTIVE", paid_time=paid_at, next_billing=next_billing)
    s = stored(env, row)
    assert out.activated and out.status == "active" and out.subscription_id == row["id"]
    assert s["status"] == "active" and s["activated_at"] is not None
    assert s["last_payment_at"] == ps._parse_time(paid_at) == s["current_period_start"]
    assert s["current_period_end"] == ps._parse_time(next_billing)
    assert not s["cancel_at_period_end"] and s["ended_at"] is None
    assert tenant(env)["plan_id"] == "pro" and tenant(env)["subscription_status"] == ACTIVE
    assert env.grants == [(TENANT, 500)]
    again = await apply(env, row, "ACTIVE", paid_time=paid_at, next_billing=next_billing)
    assert again.activated and len(env.grants) == 1


async def test_activation_without_next_billing_uses_the_interval(env):
    row = add_row(env)
    sub = pp_sub(row, "ACTIVE", paid_time="2026-01-31T10:00:00Z")
    sub["billing_info"].pop("next_billing_time")
    await env.svc._apply(stored(env, row), sub)
    assert stored(env, row)["current_period_end"] == datetime(2026, 2, 28, 10, 0, tzinfo=UTC)


async def test_activation_without_any_payment_time_starts_now(env):
    row = add_row(env)
    sub = pp_sub(row, "ACTIVE")
    sub["billing_info"]["last_payment"].pop("time")
    sub["billing_info"].pop("next_billing_time")
    await env.svc._apply(stored(env, row), sub)
    s = stored(env, row)
    assert s["last_payment_at"] is None and abs(s["current_period_start"] - now()) < timedelta(seconds=5)


def test_add_period():
    assert ps._add_period(datetime(2026, 12, 15, tzinfo=UTC), "month") == datetime(2027, 1, 15, tzinfo=UTC)
    assert ps._add_period(datetime(2028, 2, 29, tzinfo=UTC), "year") == datetime(2029, 2, 28, tzinfo=UTC)


@pytest.mark.parametrize("kw", [
    {"value": "18.99"}, {"value": "19.01"}, {"currency": "EUR"}, {"value": "nineteen"}, {"currency": "XXX"},
])
async def test_wrong_amount_or_currency_grants_nothing(env, kw):
    row = add_row(env)
    out = await apply(env, row, "ACTIVE", **kw)
    assert not out.activated and not env.grants and stored(env, row)["status"] == "approval_pending"


async def test_last_payment_not_an_object_grants_nothing(env):
    row = add_row(env)
    sub = pp_sub(row, "ACTIVE", paid=False)
    sub["billing_info"]["last_payment"] = "19.00"
    out = await env.svc._apply(stored(env, row), sub)
    assert not out.activated and not env.grants


async def test_missing_billing_info_grants_nothing(env):
    row = add_row(env)
    sub = pp_sub(row, "ACTIVE")
    del sub["billing_info"]
    out = await env.svc._apply(stored(env, row), sub)
    assert not out.activated


def txn(tid, *, status="COMPLETED", value="19.00", currency="USD", when=None):
    return {"id": tid, "status": status, "time": when or iso(now()),
            "amount_with_breakdown": {"gross_amount": {"currency_code": currency, "value": value}}}


async def test_payment_found_through_the_transactions_list(env):
    row = add_row(env)
    env.client.txns[row["paypal_subscription_id"]] = [
        txn(f"{TXN_PREFIX}1", status="DECLINED"), txn(f"{TXN_PREFIX}2", value="5.00"),
        txn(f"{TXN_PREFIX}3")]
    out = await apply(env, row, "ACTIVE", paid=False)
    assert out.activated and len(env.grants) == 1
    start, end = env.client.calls[-2][2], env.client.calls[-2][3]
    assert env.client.names().count("list") == 2  # the one look, then the payment record
    assert start < row["created_at"] and end > now()


@pytest.mark.parametrize("bad", [
    [txn(f"{TXN_PREFIX}1", status="DECLINED")], [txn(f"{TXN_PREFIX}1", value="18.00")],
    [txn(f"{TXN_PREFIX}1", currency="EUR")], ["junk"], [],
])
async def test_transactions_that_do_not_prove_payment(env, bad):
    row = add_row(env)
    env.client.txns[row["paypal_subscription_id"]] = bad
    out = await apply(env, row, "ACTIVE", paid=False)
    assert not out.activated and not env.grants


async def test_transactions_list_failure_counts_as_not_paid(env):
    row = add_row(env)
    env.client.list_error = PayPalError(status=500, name="X", retryable=True)
    out = await apply(env, row, "ACTIVE", paid=False)
    assert not out.activated and not env.grants


@pytest.mark.parametrize("start_status", ["failed", "expired"])
async def test_an_abandoned_row_that_was_paid_later_is_activated(env, start_status):
    row = add_row(env, status=start_status, ended_at=now())
    out = await apply(env, row, "ACTIVE")
    s = stored(env, row)
    assert out.activated and s["status"] == "active" and s["ended_at"] is None and len(env.grants) == 1


# -- late approval of an abandoned purchase ---------------------------------------


@pytest.mark.parametrize("old", ["failed", "expired"])
async def test_late_approval_of_an_abandoned_purchase_is_followed_again(env, old):
    row = add_row(env, status=old, ended_at=now())
    out = await apply(env, row, "APPROVED", paid=False)
    s = stored(env, row)
    assert s["status"] == "approval_pending" and s["ended_at"] is None and s["paypal_status"] == "APPROVED"
    assert not out.review and not out.activated and not env.grants
    assert "cancel" not in env.client.names() and not env.store["reviews"]


@pytest.mark.parametrize("pp", ["APPROVED", "ACTIVE"])
@pytest.mark.parametrize("case", ["tenant_stripe", "stripe_row", "attempt", "other_paypal"])
async def test_late_approval_with_a_conflict_is_superseded(env, pp, case):
    row = add_row(env, status="failed", ended_at=now())
    t = tenant(env)
    if case == "tenant_stripe":
        t["stripe_subscription_id"] = "".join(["sub", "_", "test01"])
    elif case == "stripe_row":
        env.store["stripe_subs"].append({"tenant_id": TENANT, "status": "active"})
    elif case == "attempt":
        env.store["attempts"].append({"tenant_id": TENANT, "status": "creating"})
    else:
        add_active(env)
        t.update(plan_id="free", subscription_status="inactive")
    out = await apply(env, row, pp, paid=(pp == "ACTIVE"))
    s = stored(env, row)
    assert out.review and not out.activated and s["status"] == "cancelled" and s["ended_at"] is not None
    assert ("cancel", row["paypal_subscription_id"], "Superseded by another purchase") in env.client.calls
    assert f"local:review:plan:{row['id']}:superseded" in env.store["reviews"]
    assert not env.grants


async def test_get_attempt_asks_paypal_about_an_abandoned_row(env):
    row = add_row(env, status="failed", ended_at=now())
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    out = await env.svc.get_attempt(tenant_id=TENANT, request_id=row["id"])
    assert out["state"] == "activated" and len(env.grants) == 1
    assert env.client.names().count("get") == 1


async def test_get_attempt_does_not_ask_about_an_activated_or_id_less_row(env):
    done = add_active(env, status="cancelled", ended_at=now())
    await env.svc.get_attempt(tenant_id=TENANT, request_id=done["id"])
    idless = make_row(status="failed", ended_at=now(), tenant_id=OTHER_TENANT)
    env.store["subs"][idless["id"]] = idless
    await env.svc.get_attempt(tenant_id=OTHER_TENANT, request_id=idless["id"])
    assert env.client.calls == []


@pytest.mark.parametrize("activated", [False, True])
@pytest.mark.parametrize("pp", ["APPROVED", "ACTIVE", "SUSPENDED"])
async def test_a_cancelled_row_that_paypal_still_runs_is_cancelled_again_and_reviewed(env, pp, activated):
    extra = dict(activated_at=now() - timedelta(days=3)) if activated else {}
    row = add_row(env, status="cancelled", ended_at=now(), **extra)
    before = copy.deepcopy(stored(env, row))
    out = await apply(env, row, pp, failed=2)
    after = stored(env, row)
    assert out.review and out.status == "cancelled" and out.activated is activated and not env.grants
    assert ("cancel", row["paypal_subscription_id"], "Plan ended") in env.client.calls
    assert f"local:review:plan:{row['id']}:still_running_at_paypal" in env.store["reviews"]
    stamped = {"paypal_status", "failed_payments", "checked_at"}
    assert {k: v for k, v in after.items() if k not in stamped} == {
        k: v for k, v in before.items() if k not in stamped}
    assert after["paypal_status"] == pp and after["failed_payments"] == 2 and after["checked_at"]


async def test_a_cancelled_row_whose_paypal_subscription_is_cancelled_is_only_stamped(env):
    row = add_active(env, status="cancelled", ended_at=now())
    out = await apply(env, row, "CANCELLED", paid=False)
    assert not out.review and stored(env, row)["paypal_status"] == "CANCELLED"
    assert "cancel" not in env.client.names() and not env.store["reviews"]


@pytest.mark.parametrize("over", [
    {"id": "other"}, {"plan_id": "other"}, {"custom_id": "other"}])
async def test_binding_mismatch_is_a_review_and_changes_nothing(env, over):
    row = add_row(env)
    before = copy.deepcopy(stored(env, row))
    out = await env.svc._apply(stored(env, row), pp_sub(row, "ACTIVE", **over))
    assert out.review and not out.activated
    assert stored(env, row) == before and not env.grants
    assert f"local:review:plan:{row['id']}:binding" in env.store["reviews"]


async def test_non_object_answer_is_a_review(env):
    row = add_row(env)
    out = await env.svc._apply(stored(env, row), None)
    assert out.review


async def test_missing_custom_id_is_accepted(env):
    row = add_row(env)
    sub = pp_sub(row, "ACTIVE")
    del sub["custom_id"]
    out = await env.svc._apply(stored(env, row), sub)
    assert out.activated and not out.review


@pytest.mark.parametrize("case", ["tenant_stripe", "stripe_row", "attempt", "other_paypal"])
async def test_activation_refused_when_another_purchase_appeared(env, case):
    row = add_row(env, status="failed")
    t = tenant(env)
    if case == "tenant_stripe":
        t["stripe_subscription_id"] = "".join(["sub", "_", "test01"])
    elif case == "stripe_row":
        env.store["stripe_subs"].append({"tenant_id": TENANT, "status": "active"})
    elif case == "attempt":
        env.store["attempts"].append({"tenant_id": TENANT, "status": "creating"})
    else:
        add_active(env)
        t.update(plan_id="free", subscription_status="inactive")
    out = await apply(env, row, "ACTIVE")
    s = stored(env, row)
    assert out.review and not out.activated and out.status == "cancelled"
    assert s["status"] == "cancelled" and s["ended_at"] is not None and s["activated_at"] is None
    assert ("cancel", row["paypal_subscription_id"], "Superseded by another purchase") in env.client.calls
    assert f"local:review:plan:{row['id']}:superseded" in env.store["reviews"]
    assert not env.grants and t["plan_id"] == "free"


async def test_superseded_swallows_a_failed_paypal_cancel(env):
    row = add_row(env, status="failed")
    tenant(env)["stripe_subscription_id"] = "".join(["sub", "_", "test01"])
    env.client.cancel_error = PayPalError(status=500, name="X", retryable=True)
    out = await apply(env, row, "ACTIVE")
    assert out.review and stored(env, row)["status"] == "cancelled"


async def test_review_write_failure_is_swallowed(env):
    row = add_row(env)
    env.store["review_fails"] = True
    out = await env.svc._apply(stored(env, row), pp_sub(row, "ACTIVE", id="other"))
    assert out.review


async def test_admin_suspended_tenant_keeps_its_status(env):
    tenant(env).update(subscription_status=SUSPENDED)
    row = add_row(env)
    out = await apply(env, row, "ACTIVE")
    assert out.activated and tenant(env)["subscription_status"] == SUSPENDED
    assert tenant(env)["plan_id"] == "pro" and len(env.grants) == 1
    # and a later problem never overwrites it either
    await apply(env, row, "ACTIVE", failed=1)
    assert tenant(env)["subscription_status"] == SUSPENDED
    assert stored(env, row)["status"] == "past_due"


async def test_failed_payments_then_recovery(env):
    row = add_active(env)
    out = await apply(env, row, "ACTIVE", failed=2, paid=False)
    assert out.status == PAST_DUE and stored(env, row)["failed_payments"] == 2
    assert tenant(env)["subscription_status"] == PAST_DUE
    newer = iso(now() + timedelta(minutes=1))
    nb = iso(now() + timedelta(days=31))
    out = await apply(env, row, "ACTIVE", failed=0, paid_time=newer, next_billing=nb)
    s = stored(env, row)
    assert out.status == "active" and tenant(env)["subscription_status"] == ACTIVE
    assert s["last_payment_at"] == ps._parse_time(newer) == s["current_period_start"]
    assert s["current_period_end"] == ps._parse_time(nb) and s["failed_payments"] == 0
    assert not env.grants


async def test_an_older_payment_never_moves_the_period_back(env):
    last = now() + timedelta(days=1)
    row = add_active(env, last_payment_at=last, current_period_start=last)
    await apply(env, row, "ACTIVE", paid_time=iso(now()))
    assert stored(env, row)["last_payment_at"] == last


async def test_a_renewal_with_a_wrong_amount_does_not_advance_the_period(env):
    row = add_active(env)
    before = stored(env, row)["last_payment_at"]
    await apply(env, row, "ACTIVE", value="1.00", paid_time=iso(now() + timedelta(days=1)))
    assert stored(env, row)["last_payment_at"] == before


async def test_suspended_before_activation_fails_the_row(env):
    row = add_row(env)
    out = await apply(env, row, "SUSPENDED", paid=False)
    s = stored(env, row)
    assert s["status"] == "failed" and s["ended_at"] is not None and not env.grants and not out.activated


async def test_suspended_after_activation(env):
    row = add_active(env)
    out = await apply(env, row, "SUSPENDED", paid=False)
    assert out.status == SUSPENDED and stored(env, row)["status"] == SUSPENDED
    assert tenant(env)["subscription_status"] == UNPAID


@pytest.mark.parametrize("pp,expected", [("CANCELLED", "cancelled"), ("EXPIRED", "expired")])
async def test_cancelled_or_expired_before_activation(env, pp, expected):
    row = add_row(env)
    out = await apply(env, row, pp, paid=False)
    s = stored(env, row)
    assert s["status"] == expected and s["ended_at"] is not None and out.status == expected
    assert tenant(env)["plan_id"] == "free"


@pytest.mark.parametrize("pp", ["CANCELLED", "EXPIRED"])
async def test_cancelled_with_paid_time_left_keeps_access(env, pp):
    row = add_active(env)
    out = await apply(env, row, pp, paid=False)
    s = stored(env, row)
    assert out.status == "active" and s["status"] == "active" and s["cancel_at_period_end"]
    assert s["cancelled_at"] is not None and s["ended_at"] is None
    assert tenant(env)["subscription_status"] == ACTIVE
    first = s["cancelled_at"]
    await apply(env, row, pp, paid=False)
    assert stored(env, row)["cancelled_at"] == first


@pytest.mark.parametrize("over", [
    {"current_period_end": None}, {"status": "past_due"}, {"status": "suspended"}])
async def test_cancelled_with_no_time_left_ends(env, over):
    over = {"current_period_end": now() - timedelta(minutes=1), **over}
    row = add_active(env, **over)
    out = await apply(env, row, "CANCELLED", paid=False)
    s = stored(env, row)
    assert out.status == CANCELLED and s["status"] == CANCELLED and s["ended_at"] is not None
    assert not s["cancel_at_period_end"] and tenant(env)["subscription_status"] == CANCELLED
    assert tenant(env)["plan_id"] == "pro"  # the plan is not cleared


async def test_end_never_overwrites_an_admin_suspension(env):
    row = add_active(env, current_period_end=now() - timedelta(minutes=1))
    tenant(env)["subscription_status"] = SUSPENDED
    await apply(env, row, "CANCELLED", paid=False)
    assert tenant(env)["subscription_status"] == SUSPENDED


async def test_projection_needs_the_same_plan_and_no_stripe_subscription(env):
    row = add_active(env, current_period_end=now() - timedelta(minutes=1))
    tenant(env)["plan_id"] = "other"
    await apply(env, row, "CANCELLED", paid=False)
    assert tenant(env)["subscription_status"] == ACTIVE


@pytest.mark.parametrize("pp", ["FOO", ""])
async def test_unknown_paypal_status_only_stamps(env, pp):
    row = add_row(env)
    await env.svc._apply(stored(env, row), pp_sub(row, pp))
    s = stored(env, row)
    assert s["status"] == "approval_pending" and s["paypal_status"] == pp and s["checked_at"]


async def test_failed_payment_count_is_never_a_bool_or_negative(env):
    row = add_active(env)
    await env.svc._apply(stored(env, row), pp_sub(row, "ACTIVE", failed=True))
    assert stored(env, row)["status"] == "active" and stored(env, row)["failed_payments"] == 0
    await env.svc._apply(stored(env, row), pp_sub(row, "ACTIVE", failed=-3))
    assert stored(env, row)["failed_payments"] == 0


async def test_unknown_row_is_a_404(env):
    ghost = make_row(paypal_subscription_id="".join([SUB_PREFIX, "ghost"]))
    with pytest.raises(CheckoutError) as raised:
        await env.svc._apply(ghost, pp_sub(ghost, "ACTIVE"))
    assert raised.value.status_code == 404


async def test_the_decision_uses_the_locked_row_not_the_copy(env):
    row = add_row(env)
    stale = copy.deepcopy(stored(env, row))
    await apply(env, row, "ACTIVE")  # another request activated it meanwhile
    out = await env.svc._apply(stale, pp_sub(stale, "ACTIVE"))
    assert out.activated and len(env.grants) == 1


async def test_a_failed_grant_rolls_the_whole_transaction_back(env):
    row = add_row(env)

    async def boom(conn, tenant_id, minutes):
        raise RuntimeError("db down")

    env.mp.setattr(ps, "apply_plan_allocation", boom)
    with pytest.raises(RuntimeError):
        await apply(env, row, "ACTIVE")
    s = stored(env, row)
    assert s["activated_at"] is None and s["status"] == "approval_pending"
    assert tenant(env)["plan_id"] == "free"


# -- payments record ---------------------------------------------------------------


async def test_payments_are_recorded_and_updated(env):
    row = add_row(env)
    sid = row["paypal_subscription_id"]
    env.client.txns[sid] = [
        txn(f"{TXN_PREFIX}1"), txn(f"{TXN_PREFIX}2", status="PENDING"),
        txn("bad id", ), txn(f"{TXN_PREFIX}3", status="WEIRD"), txn(f"{TXN_PREFIX}4", value="x"),
        {"id": f"{TXN_PREFIX}5", "status": "COMPLETED"}, txn(f"{TXN_PREFIX}6", when="nonsense"), "junk"]
    await apply(env, row, "ACTIVE")
    pays = env.store["payments"]
    assert set(pays) == {f"{TXN_PREFIX}1", f"{TXN_PREFIX}2"}
    p = pays[f"{TXN_PREFIX}1"]
    assert (p["status"], p["amount_minor"], p["currency"], p["tenant_id"]) == ("completed", 1900, "usd", TENANT)
    env.client.txns[sid][0]["status"] = "REFUNDED"
    await apply(env, row, "ACTIVE")
    assert pays[f"{TXN_PREFIX}1"]["status"] == "refunded"
    pays[f"{TXN_PREFIX}1"]["status"] = "reversed"
    env.client.txns[sid][0]["status"] = "COMPLETED"
    await apply(env, row, "ACTIVE")
    assert pays[f"{TXN_PREFIX}1"]["status"] == "reversed"


async def test_payment_update_then_insert(env):
    row = add_row(env)
    sid = row["paypal_subscription_id"]
    env.client.txns[sid] = [txn(f"{TXN_PREFIX}1")]
    await apply(env, row, "ACTIVE")
    pays = env.store["payments"]
    sqls = [q for q in env.store["sql"] if q in (ps._Q_PAYMENT_UPDATE, ps._Q_PAYMENT_INSERT)]
    assert sqls == [ps._Q_PAYMENT_UPDATE, ps._Q_PAYMENT_INSERT]
    assert pays[f"{TXN_PREFIX}1"]["status"] == "completed"
    env.store["sql"].clear()
    env.client.txns[sid][0]["status"] = "REFUNDED"
    await apply(env, row, "ACTIVE")
    assert pays[f"{TXN_PREFIX}1"]["status"] == "refunded"
    assert ps._Q_PAYMENT_INSERT not in env.store["sql"]  # the update found it
    pays[f"{TXN_PREFIX}1"]["status"] = "reversed"
    env.client.txns[sid][0]["status"] = "COMPLETED"
    await apply(env, row, "ACTIVE")
    assert pays[f"{TXN_PREFIX}1"]["status"] == "reversed" and len(pays) == 1  # skipped, insert did nothing


async def test_record_payments_failure_is_swallowed(env):
    row = add_row(env)
    env.client.txns[row["paypal_subscription_id"]] = [txn(f"{TXN_PREFIX}1")]
    env.store["payments_fail"] = True
    out = await apply(env, row, "ACTIVE")
    assert out.activated and len(env.grants) == 1 and not env.store["payments"]


async def test_record_payments_window_is_at_most_95_days(env):
    row = add_active(env, created_at=now() - timedelta(days=400))
    await apply(env, row, "ACTIVE")
    call = next(c for c in env.client.calls if c[0] == "list")
    assert call[2] >= now() - timedelta(days=95, minutes=1)


async def test_record_payments_listing_failure_is_swallowed(env):
    row = add_active(env)
    env.client.list_error = PayPalError(status=500, name="X", retryable=True)
    out = await apply(env, row, "ACTIVE")
    assert out.status == "active"


# -- sync ---------------------------------------------------------------------------


async def test_sync_not_ours(env):
    assert await env.svc.sync("".join([SUB_PREFIX, "nope"])) is None
    assert await env.svc.sync("bad id!") is None
    assert await env.svc.sync(None) is None
    assert env.client.calls == []


async def test_sync_applies_paypal_truth(env):
    row = add_row(env)
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    out = await env.svc.sync(row["paypal_subscription_id"])
    assert out.activated and out.tenant_id == TENANT and len(env.grants) == 1


async def test_sync_lets_paypal_errors_propagate(env):
    row = add_row(env)
    env.client.get_error = PayPalError(status=500, name="X", retryable=True)
    with pytest.raises(PayPalError):
        await env.svc.sync(row["paypal_subscription_id"])


# -- cancel_for_tenant ---------------------------------------------------------------


async def test_cancel_without_a_paypal_plan(env):
    assert await env.svc.cancel_for_tenant(TENANT) is None
    add_row(env, status="approval_pending")  # an unfinished purchase is not a plan
    assert await env.svc.cancel_for_tenant(TENANT) is None


async def test_cancel_keeps_access_until_the_period_ends(env):
    row = add_active(env)
    out = await env.svc.cancel_for_tenant(TENANT)
    s = stored(env, row)
    assert ("cancel", row["paypal_subscription_id"], "Cancelled by the customer") in env.client.calls
    assert s["status"] == "active" and s["cancel_at_period_end"]
    assert out == {"status": ACTIVE, "cancel_at_period_end": True,
                   "current_period_end": s["current_period_end"].isoformat(), "mock_mode": False,
                   "message": "Your plan stays active until the end of the paid period."}


async def test_cancel_twice_does_not_call_paypal_again(env):
    add_active(env)
    await env.svc.cancel_for_tenant(TENANT)
    calls = len(env.client.calls)
    again = await env.svc.cancel_for_tenant(TENANT)
    assert again["cancel_at_period_end"] and len(env.client.calls) == calls


async def test_cancel_when_no_time_is_left_ends_the_plan(env):
    row = add_active(env, current_period_end=now() - timedelta(minutes=1))
    out = await env.svc.cancel_for_tenant(TENANT)
    assert out["status"] == CANCELLED and out["message"] == "Your plan has ended."
    assert stored(env, row)["status"] == "cancelled"


async def test_cancel_paypal_error_is_a_503(env):
    row = add_active(env)
    env.client.cancel_error = PayPalError(status=500, name="X", retryable=True)
    with pytest.raises(CheckoutError) as raised:
        await env.svc.cancel_for_tenant(TENANT)
    assert (raised.value.code, raised.value.status_code) == ("paypal_unavailable", 503)
    assert not stored(env, row)["cancel_at_period_end"]


async def test_cancel_tolerates_already_ended_at_paypal(env):
    row = add_active(env)
    env.client.cancel_error = PayPalError(status=422, name="X", issue="SUBSCRIPTION_STATUS_INVALID")
    env.client.remote[row["paypal_subscription_id"]]["status"] = "CANCELLED"
    out = await env.svc.cancel_for_tenant(TENANT)
    assert out["cancel_at_period_end"] and stored(env, row)["cancel_at_period_end"]


async def test_accepted_cancel_with_a_lagging_read_back_still_cancels(env):
    row = add_active(env)

    async def accepted(sid, *, reason):
        env.client.calls.append(("cancel", sid, reason))  # PayPal says yes, the read still shows ACTIVE

    env.client.cancel_subscription = accepted
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    out = await env.svc.cancel_for_tenant(TENANT)
    s = stored(env, row)
    assert s["status"] == "active" and s["cancel_at_period_end"] and s["cancelled_at"] is not None
    assert out["status"] == ACTIVE and out["cancel_at_period_end"] is True


async def test_a_cancelled_plan_paypal_still_runs_is_never_extended_and_is_cancelled_again(env):
    end = now() + timedelta(days=3)
    row = add_active(env, cancel_at_period_end=True, cancelled_at=now() - timedelta(hours=7),
                     current_period_end=end)
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    out = await apply(env, row, "ACTIVE", paid=False)
    s = stored(env, row)
    assert s["status"] == "active" and s["cancel_at_period_end"] and s["current_period_end"] == end
    assert out.activated and not out.review
    assert [c for c in env.client.calls if c[0] == "cancel"] == [
        ("cancel", row["paypal_subscription_id"], "Cancelled by the customer")]
    assert not [k for k in env.store["reviews"] if k.endswith(":charged_after_cancel")]


async def test_a_payment_after_the_customer_cancelled_goes_to_a_person(env):
    end = now() + timedelta(days=3)
    row = add_active(env, cancel_at_period_end=True, cancelled_at=now() - timedelta(days=2),
                     current_period_end=end, last_payment_at=now() - timedelta(days=20))
    out = await apply(env, row, "ACTIVE", paid=True)  # a payment dated now, after the cancel
    s = stored(env, row)
    assert s["current_period_end"] == end and s["cancel_at_period_end"]
    assert out.review and out.activated
    assert [c[0] for c in env.client.calls].count("cancel") == 1
    assert [k for k in env.store["reviews"] if k.endswith(":charged_after_cancel")]


async def test_accepted_cancel_with_a_lagging_read_back_and_no_time_left_ends_the_plan(env):
    row = add_active(env, current_period_end=now() - timedelta(minutes=1))

    async def accepted(sid, *, reason):
        env.client.calls.append(("cancel", sid, reason))

    env.client.cancel_subscription = accepted
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(
        row, "ACTIVE", next_billing=iso(now() - timedelta(minutes=1)))
    out = await env.svc.cancel_for_tenant(TENANT)
    assert out["status"] == CANCELLED and stored(env, row)["status"] == "cancelled"
    assert tenant(env)["subscription_status"] == CANCELLED


async def test_accepted_cancel_with_a_failing_read_back_still_cancels(env):
    row = add_active(env)

    async def flaky(sid):
        raise PayPalError(status=500, name="X", retryable=True)

    env.client.get_subscription = flaky
    out = await env.svc.cancel_for_tenant(TENANT)
    assert stored(env, row)["cancel_at_period_end"] and out["cancel_at_period_end"] is True
    assert out["message"] == "Your plan stays active until the end of the paid period."


async def test_refused_cancel_with_the_subscription_still_running_is_a_503(env):
    row = add_active(env)
    env.client.cancel_error = PayPalError(status=422, name="X", issue="SUBSCRIPTION_STATUS_INVALID")
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    with pytest.raises(CheckoutError) as raised:
        await env.svc.cancel_for_tenant(TENANT)
    assert (raised.value.code, raised.value.status_code) == ("paypal_unavailable", 503)
    assert not stored(env, row)["cancel_at_period_end"]


async def test_refused_cancel_with_a_failing_read_back_is_a_503(env):
    row = add_active(env)
    env.client.cancel_error = PayPalError(status=422, name="X", issue="SUBSCRIPTION_STATUS_INVALID")

    async def flaky(sid):
        raise PayPalError(status=500, name="X", retryable=True)

    env.client.get_subscription = flaky
    with pytest.raises(CheckoutError) as raised:
        await env.svc.cancel_for_tenant(TENANT)
    assert raised.value.status_code == 503 and not stored(env, row)["cancel_at_period_end"]


# -- end_for_reversal -----------------------------------------------------------------


async def seed_payment(env, row, tid=f"{TXN_PREFIX}R1"):
    env.store["payments"][tid] = dict(
        id=str(uuid4()), tenant_id=row["tenant_id"], subscription_id=row["id"], paypal_transaction_id=tid,
        status="completed", amount_minor=1900, currency="usd", paid_at=now(), created_at=now())
    return tid


async def test_reversal_not_ours(env):
    assert await env.svc.end_for_reversal(transaction_id="nope", kind="dispute", event_id="e1") == "not_ours"


async def test_reversal_ends_the_plan_once(env):
    row = add_active(env)
    tid = await seed_payment(env, row)
    assert await env.svc.end_for_reversal(transaction_id=tid, kind="dispute", event_id="e1") == "ended"
    s = stored(env, row)
    assert s["status"] == "cancelled" and s["ended_at"] is not None
    assert env.store["payments"][tid]["status"] == "reversed"
    assert tenant(env)["subscription_status"] == CANCELLED
    assert ("cancel", row["paypal_subscription_id"], "Payment reversed") in env.client.calls
    assert f"local:review:plan:{row['id']}:dispute" in env.store["reviews"]
    assert await env.svc.end_for_reversal(transaction_id=tid, kind="dispute", event_id="e2") == "already"
    assert env.client.names().count("cancel") == 1
    assert env.store["reviews"][f"local:review:plan:{row['id']}:dispute"]["attempts"] == 2


async def test_reversal_kind_reversal_and_failed_paypal_cancel(env):
    row = add_active(env)
    tid = await seed_payment(env, row)
    env.client.cancel_error = PayPalError(status=500, name="X", retryable=True)
    assert await env.svc.end_for_reversal(transaction_id=tid, kind="reversal", event_id="e1") == "ended"
    assert f"local:review:plan:{row['id']}:reversal" in env.store["reviews"]


async def test_reversal_bad_kind(env):
    with pytest.raises(ValueError):
        await env.svc.end_for_reversal(transaction_id="x", kind="refund", event_id="e1")


# -- sweep -------------------------------------------------------------------------


async def test_sweep_ends_a_due_row_once(env):
    row = add_active(env, cancel_at_period_end=True, current_period_end=now() - timedelta(minutes=1),
                     checked_at=now())
    assert await env.svc.sweep() == {"ended": 1, "checked": 0, "failed": 0, "unconfirmed": 0}
    assert stored(env, row)["status"] == "cancelled" and tenant(env)["subscription_status"] == CANCELLED
    assert await env.svc.sweep() == {"ended": 0, "checked": 0, "failed": 0, "unconfirmed": 0}


async def test_sweep_rechecks_the_condition_on_the_locked_row(env):
    row = add_active(env, cancel_at_period_end=True, current_period_end=now() - timedelta(minutes=1),
                     checked_at=now())
    real = env.svc._lock

    async def renewed(conn, tenant_id, row_id):
        t, locked = await real(conn, tenant_id, row_id)
        locked["current_period_end"] = now() + timedelta(days=5)  # renewed meanwhile
        return t, locked

    env.mp.setattr(env.svc, "_lock", renewed)
    assert (await env.svc.sweep())["ended"] == 0
    assert stored(env, row)["status"] == "active"


async def test_sweep_expires_an_old_row_without_an_id(env):
    row = make_row(status="creating", created_at=now() - timedelta(hours=4))
    young = make_row(status="creating", created_at=now() - timedelta(minutes=10))
    env.store["subs"].update({row["id"]: row, young["id"]: young})
    out = await env.svc.sweep()
    assert out["checked"] == 2 and stored(env, row)["status"] == "expired"
    assert stored(env, row)["ended_at"] is not None and stored(env, young)["status"] == "creating"


async def test_sweep_checks_stale_rows_and_survives_one_failure(env):
    env.store["tenants"][OTHER_TENANT] = {"id": OTHER_TENANT, "status": "active", "plan_id": "free",
                                          "subscription_status": "inactive", "stripe_subscription_id": None}
    bad = add_row(env, status="approval_pending")
    good = add_row(env, status="approval_pending", tenant_id=OTHER_TENANT)
    env.client.remote[good["paypal_subscription_id"]] = pp_sub(good, "ACTIVE")
    real = env.client.get_subscription

    async def flaky(sid):
        if sid == bad["paypal_subscription_id"]:
            raise PayPalError(status=500, name="X", retryable=True)
        return await real(sid)

    env.client.get_subscription = flaky
    out = await env.svc.sweep()
    assert out == {"ended": 0, "checked": 1, "failed": 1, "unconfirmed": 0}
    assert stored(env, good)["status"] == "active" and stored(env, bad)["status"] == "approval_pending"


async def test_sweep_survives_a_failing_end(env):
    add_active(env, cancel_at_period_end=True, current_period_end=now() - timedelta(minutes=1),
               checked_at=now())

    async def boom(*a, **k):
        raise RuntimeError("db down")

    env.mp.setattr(env.svc, "_end", boom)
    assert await env.svc.sweep() == {"ended": 0, "checked": 0, "failed": 1, "unconfirmed": 0}


def test_the_stale_query_also_picks_open_rows_after_ten_minutes():
    assert "INTERVAL '6 hours'" in ps._Q_SWEEP_STALE
    assert ("OR (status IN ('creating', 'unknown', 'approval_pending') "
            "AND checked_at < NOW() - INTERVAL '10 minutes')") in ps._Q_SWEEP_STALE


async def test_sweep_refreshes_an_open_row_after_ten_minutes_only(env):
    env.store["tenants"][OTHER_TENANT] = {"id": OTHER_TENANT, "status": "active", "plan_id": "free",
                                          "subscription_status": "inactive", "stripe_subscription_id": None}
    old = add_row(env, checked_at=now() - timedelta(minutes=15))
    young = add_row(env, tenant_id=OTHER_TENANT, checked_at=now() - timedelta(minutes=5))
    env.client.remote[old["paypal_subscription_id"]] = pp_sub(old, "ACTIVE")  # paid, notice lost
    out = await env.svc.sweep()
    assert out["checked"] == 1 and stored(env, old)["status"] == "active"
    assert stored(env, young)["status"] == "approval_pending" and len(env.grants) == 1
    assert ("get", young["paypal_subscription_id"]) not in env.client.calls


async def test_sweep_skips_fresh_rows_and_honours_the_limit(env):
    add_row(env, checked_at=now())
    assert await env.svc.sweep() == {"ended": 0, "checked": 0, "failed": 0, "unconfirmed": 0}
    for _ in range(3):
        add_row(env, checked_at=now() - timedelta(hours=7))
    assert (await env.svc.sweep(limit=2))["checked"] == 2


# -- billing page reads -------------------------------------------------------------


async def test_current_for_tenant(env):
    assert await env.svc.current_for_tenant(TENANT) is None
    row = add_active(env)
    out = await env.svc.current_for_tenant(TENANT)
    assert out == {
        "provider": "paypal", "subscription_status": "active",
        "current_period_start": row["current_period_start"], "current_period_end": row["current_period_end"],
        "cancel_at_period_end": False, "purchased_price_option": ps.public_option(OPTION)}
    assert await env.svc.current_for_tenant(OTHER_TENANT) is None


async def test_payments_match_the_invoice_shape(env):
    from app.api.v1.endpoints.billing import _invoice_public

    row = add_active(env)
    tid = await seed_payment(env, row)
    out = await env.svc.payments_for_tenant(TENANT)
    assert len(out) == 1
    expected = set(_invoice_public({"id": uuid4(), "tenant_id": TENANT, "stripe_invoice_id": None,
                                    "status": "paid"}))
    assert set(out[0]) == expected
    inv = out[0]
    assert inv["source"] == "paypal" and inv["source_reference"] == tid and inv["provider_mode"] == "test"
    assert inv["status"] == "paid" and inv["currency"] == "usd" and inv["currency_exponent"] == 2
    assert inv["amount_due"] == inv["amount_paid"] == inv["total"] == inv["subtotal"] == 1900
    assert inv["amount_remaining"] == 0 and inv["stripe_invoice_id"] is None
    assert inv["line_items"] == [{"id": tid, "description": "Pro plan", "quantity": 1, "amount": 1900,
                                  "currency": "usd", "period_start": None, "period_end": None}]
    assert datetime.fromisoformat(inv["paid_at"]).utcoffset() is not None
    assert inv["errors"] == [] and inv["detail_status"] == "complete" and inv["detail_source"] == "stored_summary"
    assert await env.svc.payments_for_tenant(OTHER_TENANT) == []
    assert len(await env.svc.payments_for_tenant(TENANT, payment_id=out[0]["id"])) == 1
    assert await env.svc.payments_for_tenant(TENANT, payment_id=str(uuid4())) == []


@pytest.mark.parametrize("status,shown,paid", [
    ("completed", "paid", 1900), ("refunded", "refunded", 1900),
    ("partially_refunded", "partially_refunded", 1900), ("reversed", "reversed", 0),
    ("pending", "pending", 0), ("declined", "failed", 0), ("failed", "failed", 0)])
async def test_payment_status_mapping(env, status, shown, paid):
    row = add_active(env)
    tid = await seed_payment(env, row)
    env.store["payments"][tid]["status"] = status
    inv = (await env.svc.payments_for_tenant(TENANT))[0]
    assert inv["status"] == shown and inv["amount_paid"] == paid and inv["amount_due"] == 1900


async def test_payments_limit_is_clamped(env):
    row = add_active(env)
    for i in range(3):
        await seed_payment(env, row, f"{TXN_PREFIX}L{i}")
    assert len(await env.svc.payments_for_tenant(TENANT, limit=0)) == 1
    assert len(await env.svc.payments_for_tenant(TENANT, limit=2)) == 2


async def test_has_live_and_held(env):
    conn = FakeConn(env.store)
    assert not await ps.has_live_subscription(conn, TENANT)
    assert not await ps.held_subscription_exists(conn, TENANT)
    add_row(env, status="approval_pending")
    assert await ps.has_live_subscription(conn, TENANT)
    assert not await ps.held_subscription_exists(conn, TENANT)  # unfinished does not count
    add_row(env, tenant_id=OTHER_TENANT, status="past_due")
    assert await ps.held_subscription_exists(conn, OTHER_TENANT)
    assert not await ps.has_live_subscription(conn, str(uuid4()))
    assert "('active', 'past_due', 'suspended')" in ps._Q_HELD_EXISTS


# -- release_open_for_tenant -------------------------------------------------------


async def test_release_open_with_no_open_row_calls_nothing(env):
    add_active(env)
    await env.svc.release_open_for_tenant(TENANT)
    assert env.client.calls == []


async def test_release_open_fails_an_unfinished_purchase(env):
    row = add_row(env, status="approval_pending")
    await env.svc.release_open_for_tenant(TENANT)
    s = stored(env, row)
    assert s["status"] == "failed" and s["ended_at"] is not None
    assert env.client.names() == ["get"]


async def test_release_open_applies_what_paypal_says_otherwise(env):
    row = add_row(env, status="approval_pending")
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    await env.svc.release_open_for_tenant(TENANT)
    assert stored(env, row)["status"] == "active" and len(env.grants) == 1


async def test_release_open_swallows_an_unreachable_paypal(env):
    row = add_row(env, status="approval_pending")
    env.client.get_error = PayPalError(status=500, name="X", retryable=True)
    await env.svc.release_open_for_tenant(TENANT)
    assert stored(env, row)["status"] == "approval_pending"


# -- tenant predicate ---------------------------------------------------------------


async def test_every_statement_is_tenant_bound_except_the_listed_ones(env):
    rid = str(uuid4())
    await start(env, rid)
    row = stored(env, {"id": rid})
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    env.client.txns[row["paypal_subscription_id"]] = [txn(f"{TXN_PREFIX}1")]
    await env.svc.sync(row["paypal_subscription_id"])
    await env.svc.cancel_for_tenant(TENANT)
    await env.svc.payments_for_tenant(TENANT)
    await env.svc.current_for_tenant(TENANT)
    await env.svc.end_for_reversal(transaction_id=f"{TXN_PREFIX}1", kind="reversal", event_id="e")
    await env.svc.sweep()
    await env.svc.release_open_for_tenant(TENANT)
    await ps.has_live_subscription(FakeConn(env.store), TENANT)
    exceptions = {ps._Q_ROW_BY_PP, ps._Q_PLAN_OPTION, ps._Q_PAYMENT_BY_TXN, ps._Q_REVIEW,
                  ps._Q_SWEEP_DUE, ps._Q_SWEEP_STALE, ps._Q_SWEEP_ABANDONED,
                  ps._Q_SWEEP_STRAY, ps._Q_SWEEP_UNCONFIRMED}
    for sql in env.store["sql"]:
        if sql in exceptions:
            continue
        if sql.startswith(("SELECT id, status, subscription_status", "UPDATE tenants")):
            assert "WHERE id = $1::uuid" in sql
            continue
        assert ("tenant_id = $" in sql or sql.startswith("INSERT INTO paypal_subscriptions (id, tenant_id")
                or "tenant_id, subscription_id" in sql), sql


# -- S1: the sweep follows rows that are not live -------------------------------------


def test_sweep_discovery_queries_are_static_and_bounded():
    assert "status IN ('failed', 'expired') AND activated_at IS NULL" in ps._Q_SWEEP_ABANDONED
    assert "paypal_subscription_id IS NOT NULL AND created_at > NOW() - INTERVAL '3 days'" in ps._Q_SWEEP_ABANDONED
    assert "checked_at < NOW() - INTERVAL '1 hour'" in ps._Q_SWEEP_ABANDONED
    assert "status = 'cancelled'" in ps._Q_SWEEP_STRAY
    assert "paypal_status IN ('APPROVED', 'ACTIVE', 'SUSPENDED')" in ps._Q_SWEEP_STRAY
    assert ps._Q_SWEEP_UNCONFIRMED == (
        "SELECT * FROM paypal_subscriptions WHERE status IN ('active', 'past_due') "
        "AND NOT cancel_at_period_end AND current_period_end < NOW() - INTERVAL '3 days' "
        "AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL '3 days') LIMIT $1")
    for query in (ps._Q_SWEEP_ABANDONED, ps._Q_SWEEP_STRAY, ps._Q_SWEEP_UNCONFIRMED):
        assert query.endswith("LIMIT $1")


async def test_sweep_rereads_an_expired_row_and_activates_it_when_paid(env):
    row = add_row(env, status="expired", ended_at=now() - timedelta(hours=1),
                  created_at=now() - timedelta(hours=2))
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    env.client.txns[row["paypal_subscription_id"]] = [txn(f"{TXN_PREFIX}1")]
    out = await env.svc.sweep()
    assert out["checked"] == 1 and stored(env, row)["status"] == "active" and len(env.grants) == 1
    assert (await env.svc.sweep())["checked"] == 0 and len(env.grants) == 1


async def test_sweep_does_not_pick_an_old_abandoned_row_or_one_without_an_id(env):
    old = add_row(env, status="expired", ended_at=now(), created_at=now() - timedelta(days=4))
    idless = make_row(status="failed", ended_at=now(), tenant_id=OTHER_TENANT)
    env.store["subs"][idless["id"]] = idless
    out = await env.svc.sweep()
    assert out["checked"] == 0 and ("get", old["paypal_subscription_id"]) not in env.client.calls


async def test_sweep_cancels_again_a_cancelled_row_that_paypal_still_shows_running(env):
    row = add_row(env, status="cancelled", ended_at=now(), paypal_status="ACTIVE",
                  checked_at=now() - timedelta(hours=2))
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE", paid=False)
    out = await env.svc.sweep()
    assert out["checked"] == 1
    assert ("cancel", row["paypal_subscription_id"], "Plan ended") in env.client.calls
    assert f"local:review:plan:{row['id']}:still_running_at_paypal" in env.store["reviews"]
    assert (await env.svc.sweep())["checked"] == 0  # stamped: not read again within the hour


async def test_a_cancelled_row_drops_out_once_paypal_reports_cancelled(env):
    row = add_row(env, status="cancelled", ended_at=now(), paypal_status="ACTIVE",
                  checked_at=now() - timedelta(hours=2))
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "CANCELLED", paid=False)
    await env.svc.sweep()
    assert stored(env, row)["paypal_status"] == "CANCELLED" and "cancel" not in env.client.names()
    stored(env, row)["checked_at"] = now() - timedelta(hours=2)
    assert (await env.svc.sweep())["checked"] == 0


async def test_unconfirmed_plan_is_reported_and_left_alone(env):
    row = add_active(env, current_period_end=now() - timedelta(days=4), checked_at=now() - timedelta(days=4))
    env.client.get_error = PayPalError(status=500, name="X", retryable=True)
    before = copy.deepcopy(stored(env, row))
    out = await env.svc.sweep()
    assert out == {"ended": 0, "checked": 0, "failed": 1, "unconfirmed": 1}
    assert f"local:review:plan:{row['id']}:not_confirmed_with_paypal" in env.store["reviews"]
    assert stored(env, row) == before and tenant(env)["subscription_status"] == ACTIVE


async def test_a_plan_that_could_be_read_is_not_unconfirmed(env):
    row = add_active(env, current_period_end=now() - timedelta(days=4), checked_at=now() - timedelta(days=4))
    env.client.remote[row["paypal_subscription_id"]] = pp_sub(row, "ACTIVE")
    out = await env.svc.sweep()
    assert out["unconfirmed"] == 0 and out["checked"] == 1 and not env.store["reviews"]


# -- S2: ACTIVE at PayPal but no payment -------------------------------------------


async def test_approved_at_is_set_once_and_never_moved(env):
    row = add_row(env)
    await apply(env, row, "APPROVED", paid=False)
    first = stored(env, row)["approved_at"]
    assert first is not None and abs(first - now()) < timedelta(seconds=5)
    await apply(env, row, "ACTIVE", paid=False)
    await apply(env, row, "APPROVED", paid=False)
    assert stored(env, row)["approved_at"] == first


async def test_approved_at_is_not_set_by_approval_pending(env):
    row = add_row(env)
    await apply(env, row, "APPROVAL_PENDING", paid=False)
    assert stored(env, row)["approved_at"] is None


async def test_a_revived_row_gets_approved_at(env):
    row = add_row(env, status="failed", ended_at=now())
    await apply(env, row, "APPROVED", paid=False)
    assert stored(env, row)["approved_at"] is not None and stored(env, row)["status"] == "approval_pending"


async def test_active_without_payment_shortly_after_approval_waits(env):
    row = add_row(env, approved_at=now() - timedelta(minutes=10))
    out = await apply(env, row, "ACTIVE", paid=False)
    assert stored(env, row)["status"] == "approval_pending" and not out.review and not env.store["reviews"]
    assert "cancel" not in env.client.names()


async def test_active_without_payment_two_hours_after_approval_is_cancelled_and_reviewed(env):
    row = add_row(env, approved_at=now() - timedelta(hours=2, minutes=1))
    out = await apply(env, row, "ACTIVE", paid=False)
    s = stored(env, row)
    assert out.review and not out.activated and out.status == "cancelled"
    assert s["status"] == "cancelled" and s["ended_at"] is not None and s["activated_at"] is None
    assert ("cancel", row["paypal_subscription_id"], "No payment was confirmed") in env.client.calls
    assert f"local:review:plan:{row['id']}:active_without_payment" in env.store["reviews"]
    assert not env.grants and tenant(env)["plan_id"] == "free"


async def test_a_payment_that_shows_up_before_two_hours_activates(env):
    row = add_row(env, approved_at=now() - timedelta(hours=1, minutes=50))
    out = await apply(env, row, "ACTIVE", paid=True)
    assert out.activated and stored(env, row)["status"] == "active" and len(env.grants) == 1


async def test_a_slow_approval_is_not_cancelled_at_its_first_active_read(env):
    row = add_row(env, created_at=now() - timedelta(hours=2, minutes=50))
    out = await apply(env, row, "ACTIVE", paid=False)
    assert not out.review and stored(env, row)["status"] == "approval_pending"
    assert stored(env, row)["approved_at"] is not None and "cancel" not in env.client.names()


async def test_a_cancelled_unpaid_purchase_tells_the_customer(env):
    row = add_row(env, approved_at=now() - timedelta(hours=3))
    await apply(env, row, "ACTIVE", paid=False)
    out = await env.svc.get_attempt(tenant_id=TENANT, request_id=row["id"])
    assert out["state"] == "failed" and out["message"] == ps.MSG_NOT_USED


# -- S3: payments recorded soon after activation ------------------------------------


async def test_no_transactions_at_activation_asks_for_a_recheck(env):
    row = add_row(env)
    await apply(env, row, "ACTIVE")
    assert ps._Q_RECHECK_SOON in env.store["sql"] and stored(env, row)["checked_at"] is None


async def test_transactions_seen_means_no_recheck(env):
    row = add_row(env)
    env.client.txns[row["paypal_subscription_id"]] = [txn(f"{TXN_PREFIX}1")]
    await apply(env, row, "ACTIVE")
    assert ps._Q_RECHECK_SOON not in env.store["sql"] and stored(env, row)["checked_at"] is not None


async def test_only_invalid_transactions_count_as_none(env):
    row = add_row(env)
    env.client.txns[row["paypal_subscription_id"]] = [txn("bad id"), "junk"]
    await apply(env, row, "ACTIVE")
    assert ps._Q_RECHECK_SOON in env.store["sql"]


async def test_a_failing_payment_list_asks_for_a_recheck(env):
    row = add_active(env)
    env.client.list_error = PayPalError(status=500, name="X", retryable=True)
    await apply(env, row, "ACTIVE")
    assert ps._Q_RECHECK_SOON in env.store["sql"]


async def test_the_recheck_is_bounded_by_the_activation_date(env):
    row = add_active(env, activated_at=now() - timedelta(days=3))
    await apply(env, row, "ACTIVE")
    assert ps._Q_RECHECK_SOON in env.store["sql"] and stored(env, row)["checked_at"] is not None
    assert "activated_at > NOW() - INTERVAL '2 days'" in ps._Q_RECHECK_SOON


async def test_a_failing_recheck_statement_is_swallowed(env):
    row = add_row(env)
    real = FakeConn.execute

    async def failing(self, sql, *a):
        if sql == ps._Q_RECHECK_SOON:
            raise RuntimeError("db down")
        return await real(self, sql, *a)

    env.mp.setattr(FakeConn, "execute", failing)
    out = await apply(env, row, "ACTIVE")
    assert out.activated


# -- S4: message --------------------------------------------------------------------


@pytest.mark.parametrize("pp,message", [
    ("APPROVED", ps.MSG_NOT_USED), ("ACTIVE", ps.MSG_NOT_USED), ("CANCELLED", None), (None, None)])
def test_not_used_message_only_for_a_purchase_paypal_had_approved(pp, message):
    out = ps._response(make_row(status="cancelled", paypal_status=pp))
    assert out["state"] == "failed" and out["message"] == message


@pytest.mark.parametrize("over", [
    {"status": "failed", "paypal_status": "ACTIVE"}, {"status": "failed"},
    {"status": "cancelled", "paypal_status": "ACTIVE", "activated_at": now()}])
def test_other_rows_keep_their_message(over):
    out = ps._response(make_row(**over))
    assert out["message"] is None


# -- S5: ending a plan by PayPal's subscription id ------------------------------------


async def test_end_by_subscription_not_ours(env):
    assert await env.svc.end_subscription_for_reversal(
        paypal_subscription_id="".join([SUB_PREFIX, "nope"]), kind="reversal", event_id="e") == "not_ours"
    assert await env.svc.end_subscription_for_reversal(
        paypal_subscription_id="bad id!", kind="reversal", event_id="e") == "not_ours"
    assert await env.svc.end_subscription_for_reversal(
        paypal_subscription_id=None, kind="reversal", event_id="e") == "not_ours"
    assert env.client.calls == []


async def test_end_by_subscription_ends_once_and_touches_no_payment(env):
    row = add_active(env)
    tid = await seed_payment(env, row)
    sid = row["paypal_subscription_id"]
    assert await env.svc.end_subscription_for_reversal(
        paypal_subscription_id=sid, kind="reversal", event_id="e1") == "ended"
    s = stored(env, row)
    assert s["status"] == "cancelled" and s["ended_at"] is not None
    assert tenant(env)["subscription_status"] == CANCELLED
    assert env.store["payments"][tid]["status"] == "completed"
    assert ("cancel", sid, "Payment reversed") in env.client.calls
    assert f"local:review:plan:{row['id']}:reversal" in env.store["reviews"]
    assert await env.svc.end_subscription_for_reversal(
        paypal_subscription_id=sid, kind="reversal", event_id="e2") == "already"
    assert env.client.names().count("cancel") == 1


async def test_end_by_subscription_bad_kind(env):
    with pytest.raises(ValueError):
        await env.svc.end_subscription_for_reversal(
            paypal_subscription_id="x", kind="refund", event_id="e")
