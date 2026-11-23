"""Payments that need a person: the list, the close, the redelivery rule and the endpoint.

No database: a small in-memory store answers the exact module constants. The
Postgres proof is tests/integration/test_payment_reviews_pg.py (CI only).
"""
from __future__ import annotations

import ast
import importlib
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tests.unit.test_paypal_webhooks import body, row as webhook_row, run as run_webhook, world  # noqa: F401

BACKEND = Path(__file__).resolve().parents[2]
REASON = "Checked at the provider and settled by hand."
T1, T2 = str(uuid4()), str(uuid4())
NOW = datetime.now(timezone.utc)


def pr():
    return importlib.import_module("app.domain.services.payment_reviews")


def _norm(text):
    return " ".join(text.split())


class PG:
    """Answers the module's constants exactly; anything else fails the test."""

    def __init__(self):
        self.stripe, self.paypal, self.tenants = [], [], []
        self.pp = {}      # event_id -> receipt state for the PayPal close
        self.st = {}      # event_id -> receipt for the Stripe close
        self.pp_log, self.st_log, self.calls, self.busy = [], [], [], False

    async def fetch(self, sql, *a):
        m = pr()
        self.calls.append("fetch")
        if sql == m._Q_STRIPE:
            return self.stripe[: a[0]]
        if sql == m._Q_PAYPAL:
            return self.paypal[: a[0]]
        if sql == m._Q_NAMES:
            return [t for t in self.tenants if str(t["id"]) in a[0] or t["stripe_customer_id"] in a[1]]
        if sql == m._Q_PP_CLOSE:
            r = self.pp[a[0]]
            if r["state"] != a[1] or r["attempt_count"] != a[2]:
                return []
            r["state"] = "dismissed"
            return [{"event_id": a[0]}]
        if "UPDATE processed_webhook_events SET state='dismissed'" in sql:
            r = self.st[a[0]]
            if r["state"] not in ("needs_review", "failed", "pending"):
                return []
            r["state"] = "dismissed"
            return [{"event_id": a[0]}]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        m = pr()
        if sql == m._Q_PP_LOCK:
            r = self.pp.get(a[0])
            return None if r is None else dict(r)
        if sql == m._Q_STRIPE_LOCK:
            r = self.st.get(a[0])
            return None if r is None else {"state": r["state"], "attempt_count": r["attempt_count"],
                                           "past_redelivery": r["age_h"] >= 96}
        if "FROM processed_webhook_events WHERE event_id=$1 FOR UPDATE" in sql:
            r = self.st.get(a[0])
            return None if r is None else {"state": r["state"], "stuck": r["age_h"] >= 6}
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        if "pg_try_advisory_xact_lock" in sql:
            return not self.busy
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        m = pr()
        if sql == m._Q_PP_LOG:
            self.pp_log.append(a)
        elif "INSERT INTO billing_webhook_review_log" in sql:
            self.st_log.append(a)
        else:
            raise AssertionError(sql)


def pp_receipt(state="needs_review", attempts=1, past=False, detail="amount_mismatch"):
    return {"state": state, "attempt_count": attempts, "past_redelivery": past, "detail": detail}


def pp_row(**kw):
    base = dict(event_id="local:review:o1:amount_mismatch", event_type="LOCAL.CAPTURE.REVIEW", state="needs_review",
                detail="amount_mismatch", attempt_count=1, received_at=NOW - timedelta(hours=5),
                resource_id="PPO-1", waiting_hours=5, past_redelivery=False, order_tenant_id=T1,
                price_cents=2500, order_currency="USD", plan_tenant_id=None, plan_amount_text=None,
                plan_currency=None)
    base.update(kw)
    return base


def st_row(**kw):
    base = dict(event_id="evt_1", event_type="checkout.session.completed", state="needs_review", attempt_count=1,
                last_error_code="credit_not_confirmed", received_at=NOW - timedelta(hours=30),
                waiting_hours=30, past_redelivery=False, object_id="cs_1", metadata_tenant_id=T2, purpose="minute_topup",
                checkout_mode="payment", customer_id="cus_ABC123", amount_text="1900", currency="usd")
    base.update(kw)
    return base


# -- the list ----------------------------------------------------------------------------

KEYS = {"provider", "event_id", "event_type", "state", "received_at", "waiting_hours", "kind", "tenant_id",
        "business_name", "amount_minor", "currency", "currency_exponent", "reason_code", "reason_text",
        "provider_reference", "attempt_count", "can_close", "close_blocked_reason"}


async def test_paypal_minutes_review_shows_account_price_and_words():
    db = PG()
    db.paypal = [pp_row()]
    db.tenants = [{"id": T1, "business_name": "Acme Dental", "stripe_customer_id": None}]
    out = await pr().list_payment_reviews(db)
    (item,) = out["items"]
    assert set(item) == KEYS and out["truncated"] is False and out["limit"] == 100
    assert (item["provider"], item["kind"], item["state"]) == ("paypal", "minutes", "needs_review")
    assert (item["tenant_id"], item["business_name"]) == (T1, "Acme Dental")
    assert (item["amount_minor"], item["currency"], item["currency_exponent"]) == (2500, "usd", 2)
    assert item["reason_text"].startswith("PayPal shows a payment for this order that does not match it")
    assert item["can_close"] is True and item["close_blocked_reason"] is None
    assert item["provider_reference"] == "PPO-1"


async def test_stripe_review_shows_hints_and_never_payload_customer_or_email():
    db = PG()
    db.stripe = [st_row(metadata_tenant_id=None, customer_id="cus_ABC123")]
    db.tenants = [{"id": T2, "business_name": "Beta Ltd", "stripe_customer_id": "cus_ABC123"}]
    (item,) = (await pr().list_payment_reviews(db))["items"]
    assert set(item) == KEYS
    assert item["tenant_id"] == T2 and item["business_name"] == "Beta Ltd" and item["kind"] == "minutes"
    assert item["amount_minor"] == 1900 and item["currency"] == "usd"
    flat = json.dumps(item, default=str)
    assert "cus_ABC123" not in flat and "@" not in flat and "event_payload" not in flat


async def test_a_customer_shared_by_two_accounts_is_not_guessed():
    db = PG()
    db.stripe = [st_row(metadata_tenant_id=None)]
    db.tenants = [{"id": T1, "business_name": "A", "stripe_customer_id": "cus_ABC123"},
                  {"id": T2, "business_name": "B", "stripe_customer_id": "cus_ABC123"}]
    (item,) = (await pr().list_payment_reviews(db))["items"]
    assert item["tenant_id"] is None and item["business_name"] is None


async def test_bad_amount_and_bad_tenant_id_are_dropped_not_passed_on():
    db = PG()
    db.stripe = [st_row(metadata_tenant_id="not-a-uuid", amount_text="12.50", customer_id="x; DROP")]
    (item,) = (await pr().list_payment_reviews(db))["items"]
    assert item["tenant_id"] is None and item["amount_minor"] is None


async def test_stuck_rows_and_the_four_day_rule_for_paypal():
    db = PG()
    db.paypal = [pp_row(event_id="a", state="failed", detail="topup_x", past_redelivery=False),
                 pp_row(event_id="b", state="received", detail=None, past_redelivery=True)]
    db.stripe = [st_row(state="failed", past_redelivery=True)]
    items = {i["event_id"]: i for i in (await pr().list_payment_reviews(db))["items"]}
    assert items["a"]["state"] == "stuck" and items["a"]["can_close"] is False
    assert items["a"]["close_blocked_reason"].startswith("PayPal may still send this message again")
    assert items["b"]["can_close"] is True
    assert items["evt_1"]["can_close"] is True
    assert "more than 6 hours" in items["evt_1"]["reason_text"] and "an hour" in items["a"]["reason_text"]


async def test_truncation_and_order_are_by_arrival():
    db = PG()
    db.paypal = [pp_row(event_id=f"p{i}", received_at=NOW - timedelta(hours=i)) for i in range(3)]
    out = await pr().list_payment_reviews(db, limit=2)
    assert out["truncated"] is True and len(out["items"]) == 2 and out["limit"] == 2
    assert [i["received_at"] for i in out["items"]] == sorted(i["received_at"] for i in out["items"])


def test_the_list_where_clauses_equal_the_healthwatch_predicates():
    m = pr()
    health = _norm((BACKEND / "scripts" / "healthwatch.py").read_text(encoding="utf-8"))
    assert "state='needs_review'" in health and "state = 'needs_review'" in health
    for fragment in ("state IN ('failed', 'received') AND received_at < NOW() - INTERVAL '1 hour'",
                     "state IN ('failed', 'pending') AND received_at < NOW() - INTERVAL '6 hours'"):
        assert fragment.replace("state IN", "state IN") in health
    assert "WHERE state = 'needs_review' OR (state IN ('failed', 'pending') AND received_at < NOW() - INTERVAL '6 hours')" in m._Q_STRIPE
    assert ("WHERE e.state = 'needs_review' OR (e.state IN ('failed', 'received') "
            "AND e.received_at < NOW() - INTERVAL '1 hour')") in m._Q_PAYPAL


def test_every_statement_is_a_plain_literal_constant_without_the_payload():
    source = (BACKEND / "app" / "domain" / "services" / "payment_reviews.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    queries = [n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id.startswith("_Q_")]
    assert len(queries) == 7
    for node in queries:
        assert not [n for n in ast.walk(node) if isinstance(n, (ast.JoinedStr, ast.BinOp, ast.Call))], "plain literals"
    m = pr()
    assert not re.search(r"event_payload(?!->)", m._Q_STRIPE), "only named fields leave the stored event"
    assert "payload_hash" not in m._Q_PAYPAL


# -- PayPal close ------------------------------------------------------------------------


async def close(db, provider="paypal", event_id="e1", seen=1, operator="a@b.c (id)", reason=REASON):
    return await pr().close_payment_review(
        db, provider=provider, event_id=event_id, seen_attempt_count=seen,
        operator=operator, operator_id=str(uuid4()), reason=reason)


async def test_paypal_close_changes_one_row_and_logs_who_why_and_the_seen_count():
    db = PG()
    db.pp["e1"] = pp_receipt(attempts=3)
    out = await close(db, seen=3)
    assert out == {"provider": "paypal", "event_id": "e1", "state": "dismissed", "changed": True,
                   "prior_state": "needs_review", "financial_effects_applied": False}
    (log,) = db.pp_log
    assert log[0] == "e1" and log[1] == "a@b.c (id)" and log[3] == REASON and log[4] == 3
    assert db.pp["e1"]["state"] == "dismissed"


async def test_a_second_close_is_a_no_op_with_no_second_log_row():
    db = PG()
    db.pp["e1"] = pp_receipt()
    await close(db)
    again = await close(db)
    assert again["changed"] is False and again["prior_state"] == "dismissed" and len(db.pp_log) == 1


async def test_close_is_refused_when_the_attempt_count_moved():
    db = PG()
    db.pp["e1"] = pp_receipt(attempts=2)
    with pytest.raises(ValueError, match="changed after the list was read"):
        await close(db, seen=1)
    assert db.pp_log == [] and db.pp["e1"]["state"] == "needs_review"


@pytest.mark.parametrize("state", ["completed", "ignored"])
async def test_close_is_refused_for_finished_receipts(state):
    db = PG()
    db.pp["e1"] = pp_receipt(state=state)
    with pytest.raises(ValueError, match="Only a payment that needs review can be closed"):
        await close(db)
    assert db.pp_log == []


@pytest.mark.parametrize("state", ["failed", "received"])
async def test_young_stuck_rows_are_refused_and_old_ones_are_allowed(state):
    db = PG()
    db.pp["e1"] = pp_receipt(state=state, past=False)
    with pytest.raises(ValueError, match="can be closed 4 days after it arrived"):
        await close(db)
    db.pp["e1"] = pp_receipt(state=state, past=True)
    assert (await close(db))["prior_state"] == state and len(db.pp_log) == 1


async def test_a_missing_paypal_receipt_is_refused():
    with pytest.raises(ValueError, match="No such payment message"):
        await close(PG(), event_id="nope")


@pytest.mark.parametrize("provider", ["paypal", "stripe"])
@pytest.mark.parametrize("kw", [dict(operator=""), dict(operator="  "), dict(operator="x" * 201),
                                dict(reason="too short"), dict(reason=" " * 40), dict(reason="y" * 2001)])
async def test_missing_operator_or_short_reason_is_refused_before_any_write(provider, kw):
    db = PG()
    db.pp["e1"] = pp_receipt()
    db.st["e1"] = dict(state="needs_review", attempt_count=1, age_h=1)
    with pytest.raises(ValueError, match="An identified operator and substantive reconciliation reason"):
        await close(db, provider=provider, **kw)
    assert db.pp_log == [] and db.st_log == [] and db.calls == []


# -- Stripe close follows the dismiss_receipt rules --------------------------------------


async def test_stripe_close_dismisses_a_needs_review_receipt_and_logs_it():
    db = PG()
    db.st["e1"] = dict(state="needs_review", attempt_count=2, age_h=1)
    out = await close(db, provider="stripe", seen=2)
    assert out["changed"] is True and out["prior_state"] == "needs_review"
    assert out["financial_effects_applied"] is False and db.st["e1"]["state"] == "dismissed"
    assert db.st_log == [("e1", "a@b.c (id)", REASON)]


async def test_stripe_close_rules_young_failed_completed_moved_and_busy():
    db = PG()
    db.st["e1"] = dict(state="failed", attempt_count=1, age_h=100)
    assert (await close(db, provider="stripe"))["changed"] is True
    assert (await close(db, provider="stripe"))["changed"] is False
    db.st["e2"] = dict(state="completed", attempt_count=1, age_h=99)
    with pytest.raises(ValueError, match="Only a needs_review receipt"):
        await close(db, provider="stripe", event_id="e2")
    db.st["e3"] = dict(state="needs_review", attempt_count=4, age_h=1)
    with pytest.raises(ValueError, match="changed after the list was read"):
        await close(db, provider="stripe", event_id="e3", seen=1)
    db.busy = True
    from app.domain.services.billing_webhooks import BillingWebhookRetryable
    with pytest.raises(BillingWebhookRetryable):
        await close(db, provider="stripe", event_id="e3", seen=4)
    assert len(db.st_log) == 1


async def test_a_stuck_stripe_row_is_not_closable_while_stripe_may_resend_it():
    db = PG()
    db.stripe = [st_row(event_id="young", state="failed", received_at=NOW - timedelta(hours=7), waiting_hours=7),
                 st_row(event_id="old", state="pending", past_redelivery=True, waiting_hours=120),
                 st_row(event_id="review", state="needs_review", waiting_hours=1000, past_redelivery=True),
                 st_row(event_id="review_young", state="needs_review", waiting_hours=1)]
    items = {i["event_id"]: i for i in (await pr().list_payment_reviews(db))["items"]}
    assert items["young"]["state"] == "stuck" and items["young"]["can_close"] is False
    assert items["young"]["close_blocked_reason"] == (
        "Stripe may still send this message again (it keeps trying for up to 3 days). "
        "It can be closed 4 days after it arrived.")
    assert items["old"]["can_close"] is True and items["old"]["close_blocked_reason"] is None
    assert items["review"]["can_close"] is True and items["review_young"]["can_close"] is True


@pytest.mark.parametrize("state", ["failed", "pending"])
async def test_the_stripe_close_refuses_a_young_stuck_row_before_dismiss_receipt(state):
    db = PG()
    db.st["e1"] = dict(state=state, attempt_count=1, age_h=7)
    with pytest.raises(ValueError, match="Stripe may still send this message again"):
        await close(db, provider="stripe")
    assert db.st_log == [] and db.st["e1"]["state"] == state
    db.st["e1"] = dict(state=state, attempt_count=1, age_h=120)
    assert (await close(db, provider="stripe"))["prior_state"] == state and len(db.st_log) == 1
    db.st["e2"] = dict(state="needs_review", attempt_count=1, age_h=1)
    assert (await close(db, provider="stripe", event_id="e2"))["changed"] is True


async def test_the_command_line_dismiss_still_allows_a_failed_receipt_after_six_hours():
    db = PG()
    db.st["e1"] = dict(state="failed", attempt_count=1, age_h=7)
    from app.domain.services.billing_webhook_reconciliation import dismiss_receipt
    assert (await dismiss_receipt(db, "e1", operator="op", reason=REASON))["changed"] is True


@pytest.mark.parametrize("rule", ["binding", "not_confirmed_with_paypal"])
async def test_a_recurring_plan_rule_closes_even_though_its_count_moved(rule):
    db = PG()
    db.pp["e1"] = pp_receipt(attempts=9, detail=rule)
    assert (await close(db, seen=2))["changed"] is True
    assert db.pp_log[0][4] == 9 and db.pp["e1"]["state"] == "dismissed"
    assert pr()._RECURRING_RULES == {"binding", "not_confirmed_with_paypal"}


async def test_another_plan_rule_whose_count_moved_is_still_refused():
    db = PG()
    db.pp["e1"] = pp_receipt(attempts=9, detail="superseded")
    with pytest.raises(ValueError, match="changed after the list was read"):
        await close(db, seen=2)
    assert db.pp_log == []


# -- redelivery and recurrence -----------------------------------------------------------


async def test_a_redelivered_dismissed_receipt_answers_duplicate_and_dispatches_nothing(world):  # noqa: F811
    raw = body()
    await run_webhook(world, raw)
    webhook_row(world)["state"] = "dismissed"
    world.svc.settled.clear()
    before = webhook_row(world)["attempt_count"]
    assert await run_webhook(world, raw) == {"status": "duplicate"}
    assert world.svc.settled == [] and world.svc.reversed == [] and world.plans.synced == []
    assert webhook_row(world)["attempt_count"] == before and webhook_row(world)["state"] == "dismissed"


async def test_a_changed_body_for_a_dismissed_event_reopens_it(world):  # noqa: F811
    await run_webhook(world, body())
    webhook_row(world)["state"] = "dismissed"
    assert await run_webhook(world, body(resource={"id": "OTHER"})) == {"status": "needs_review"}
    assert webhook_row(world)["state"] == "needs_review" and webhook_row(world)["detail"] == "payload_changed"


async def test_a_changed_body_counts_as_a_delivery_attempt(world):  # noqa: F811
    raw = body()
    await run_webhook(world, raw)
    webhook_row(world)["state"] = "needs_review"
    assert webhook_row(world)["attempt_count"] == 1
    changed = body(resource={"id": "OTHER"})
    assert await run_webhook(world, changed) == {"status": "needs_review"}
    assert webhook_row(world)["attempt_count"] == 2 and webhook_row(world)["detail"] == "payload_changed"
    assert len(world.svc.settled) == 1  # only the first delivery was dispatched


async def test_a_changed_body_after_the_list_was_read_is_not_closed_unseen(world):  # noqa: F811
    await run_webhook(world, body())
    stored = webhook_row(world)
    stored.update(state="needs_review", attempt_count=2, detail="amount_mismatch", past_redelivery=False)
    db = PG()
    db.pp["WH-EVT-1"] = stored  # the same row the webhook handler writes
    db.paypal = [pp_row(event_id="WH-EVT-1", attempt_count=stored["attempt_count"])]
    db.tenants = [{"id": T1, "business_name": "Acme Dental", "stripe_customer_id": None}]
    (item,) = (await pr().list_payment_reviews(db))["items"]
    seen = item["attempt_count"]
    assert seen == 2
    assert await run_webhook(world, body(resource={"id": "OTHER"})) == {"status": "needs_review"}
    assert stored["detail"] == "payload_changed"
    with pytest.raises(ValueError, match="changed after the list was read"):
        await close(db, event_id="WH-EVT-1", seen=seen)
    assert db.pp_log == [] and stored["state"] == "needs_review"


def _local_review_sql(path):
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    sql = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and "LOCAL." in n.value and "ON CONFLICT (event_id)" in n.value]
    assert len(sql) == 1
    return _norm(sql[0])


def test_a_recurring_minutes_review_reopens_the_row():
    assert _local_review_sql("app/domain/services/paypal_topups.py").endswith(
        "ON CONFLICT (event_id) DO UPDATE SET attempt_count = paypal_webhook_events.attempt_count + 1, "
        "state = 'needs_review'")


def test_a_recurring_plan_review_reopens_except_the_two_rules_the_sweep_rewrites_every_tick():
    assert _local_review_sql("app/domain/services/paypal_subscriptions.py").endswith(
        "ON CONFLICT (event_id) DO UPDATE SET attempt_count = paypal_webhook_events.attempt_count + 1, "
        "state = CASE WHEN paypal_webhook_events.state = 'dismissed' AND paypal_webhook_events.detail "
        "IN ('binding', 'not_confirmed_with_paypal') THEN 'dismissed' ELSE 'needs_review' END")


# -- every PayPal rule literal has specific words ----------------------------------------


def _rule_literals():
    rules = set()
    topups = (BACKEND / "app/domain/services/paypal_topups.py").read_text(encoding="utf-8")
    rules |= set(re.findall(r'_review\([^,]+,[^,]+,\s*"([a-z_]+)"', topups))
    subs = (BACKEND / "app/domain/services/paypal_subscriptions.py").read_text(encoding="utf-8")
    rules |= set(re.findall(r'_record_review\([^,]+,\s*"([a-z_]+)"', subs))
    hooks = (BACKEND / "app/domain/services/paypal_webhooks.py").read_text(encoding="utf-8")
    rules |= set(re.findall(r'"needs_review", "([a-z_]+)"', hooks)) - {"dismissed"}  # _FINISHED lists states
    rules |= {"dispute_created", "dispute_escalated", "dispute_inquiry", "dispute_update",
              "refund_review", "reversal_review", "payload_changed", "dispute", "reversal"}
    return rules


def test_every_paypal_rule_literal_has_specific_words():
    rules = _rule_literals()
    assert {"binding", "superseded", "still_running_at_paypal", "active_without_payment",
            "charged_after_cancel", "not_confirmed_with_paypal", "service_review", "plan_review",
            "amount_mismatch", "captures_not_one"} <= rules
    for rule in sorted(rules):
        text = pr().reason_text("paypal", "needs_review", rule)
        assert text != pr()._FALLBACK, rule
    for dynamic in ("unit_currency_mismatch", "capture_status_DECLINED", "reverse_on_DENIED",
                    "refund_status_FAILED", "refund_custom_id_mismatch"):
        assert pr().reason_text("paypal", "needs_review", dynamic) != pr()._FALLBACK, dynamic
    assert pr().reason_text("paypal", "needs_review", "something_new") == pr()._FALLBACK
    assert "different contents" in pr().reason_text("stripe", "needs_review", "event_identity_conflict")
    assert "another Stripe account" in pr().reason_text("stripe", "needs_review", "provider_account_or_mode_mismatch")


# -- the endpoint ------------------------------------------------------------------------


def _endpoint(monkeypatch, db, order):
    ep = importlib.import_module("app.api.v1.endpoints.admin.payment_reviews")

    @asynccontextmanager
    async def acquire(pool, tenant, **kw):
        assert tenant is None and kw.get("user_id") == "u1"
        yield db

    async def guard(conn, actor):
        order.append("mutation_guard")

    async def session(conn, request, user):
        order.append("session")

    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(ep, "require_platform_admin_mutation", guard)
    monkeypatch.setattr(ep, "_session", session)

    async def fake_close(conn, **kw):
        order.append("write")
        if kw["reason"] == "short":
            raise ValueError("An identified operator and substantive reconciliation reason are required")
        return {"provider": kw["provider"], "event_id": kw["event_id"], "state": "dismissed",
                "changed": True, "prior_state": "needs_review", "financial_effects_applied": False}

    monkeypatch.setattr(ep, "close_payment_review", fake_close)
    audit = SimpleNamespace(calls=[])

    async def lock(conn):
        order.append("lock")

    async def log(conn, **kw):
        order.append("audit_log")
        audit.calls.append(kw)

    audit.lock_transaction, audit.log_in_transaction = lock, log
    return ep, audit


async def test_endpoint_call_order_and_audit_record(monkeypatch):
    order = []
    ep, audit = _endpoint(monkeypatch, object(), order)
    user = SimpleNamespace(id="u1", email="staff@example.com", tenant_id=None)
    out = await ep.close_review(
        ep.ClosePaymentReview(provider="paypal", event_id="e1", seen_attempt_count=1, reason=REASON),
        SimpleNamespace(), user, SimpleNamespace(pool=object()), audit)
    assert order == ["lock", "mutation_guard", "session", "write", "audit_log"]
    assert ep.PaymentReviewClosed(**out).financial_effects_applied is False
    (call,) = audit.calls
    assert call["action"] == "payment_review_closed" and call["resource_type"] == "payment_review"
    assert "resource_id" not in call and call["metadata"]["financial_effects_applied"] is False
    assert set(call["metadata"]) == {"provider", "event_id", "prior_state", "financial_effects_applied"}


async def test_endpoint_refuses_a_short_reason_with_409_and_writes_no_audit_row(monkeypatch):
    order = []
    ep, audit = _endpoint(monkeypatch, object(), order)
    user = SimpleNamespace(id="u1", email="staff@example.com", tenant_id=None)
    with pytest.raises(HTTPException) as err:
        await ep.close_review(
            ep.ClosePaymentReview(provider="stripe", event_id="e1", seen_attempt_count=0, reason="short"),
            SimpleNamespace(), user, SimpleNamespace(pool=object()), audit)
    assert err.value.status_code == 409 and "substantive reconciliation reason" in err.value.detail
    assert "audit_log" not in order


async def test_endpoint_unknown_failure_is_503_and_list_failure_never_looks_empty(monkeypatch):
    order = []
    ep, audit = _endpoint(monkeypatch, object(), order)

    async def boom(conn, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(ep, "close_payment_review", boom)
    user = SimpleNamespace(id="u1", email="staff@example.com", tenant_id=None)
    with pytest.raises(HTTPException) as err:
        await ep.close_review(
            ep.ClosePaymentReview(provider="paypal", event_id="e1", seen_attempt_count=0, reason=REASON),
            SimpleNamespace(), user, SimpleNamespace(pool=object()), audit)
    assert err.value.status_code == 503 and "not confirmed" in err.value.detail
    monkeypatch.setattr(ep, "list_payment_reviews", boom)
    with pytest.raises(HTTPException) as err:
        await ep.get_payment_reviews(SimpleNamespace(), user, SimpleNamespace(pool=object()))
    assert err.value.status_code == 503 and "does not mean there are none" in err.value.detail


@pytest.mark.parametrize("name", ["DataError", "InvalidTextRepresentationError", "UniqueViolationError"])
async def test_an_asyncpg_error_from_the_service_answers_503_not_409(monkeypatch, name):
    import asyncpg
    order = []
    ep, audit = _endpoint(monkeypatch, object(), order)

    async def boom(conn, **kw):
        raise getattr(asyncpg.exceptions, name)("invalid input syntax for type uuid: technical text")

    monkeypatch.setattr(ep, "close_payment_review", boom)
    user = SimpleNamespace(id="u1", email="staff@example.com", tenant_id=None)
    with pytest.raises(HTTPException) as err:
        await ep.close_review(
            ep.ClosePaymentReview(provider="paypal", event_id="e1", seen_attempt_count=0, reason=REASON),
            SimpleNamespace(), user, SimpleNamespace(pool=object()), audit)
    assert err.value.status_code == 503 and "technical" not in err.value.detail


async def test_endpoint_list_response_matches_the_model(monkeypatch):
    order = []
    db = PG()
    db.paypal = [pp_row()]
    ep, _ = _endpoint(monkeypatch, db, order)
    user = SimpleNamespace(id="u1", email="staff@example.com", tenant_id=None)
    out = await ep.get_payment_reviews(SimpleNamespace(), user, SimpleNamespace(pool=object()))
    model = ep.PaymentReviewList(**out)
    assert model.items[0].provider == "paypal" and set(ep.PaymentReviewItem.model_fields) == KEYS


def test_the_routes_are_mounted_under_admin_billing():
    from fastapi.routing import iter_route_contexts
    from app.api.v1.endpoints.admin import router
    paths = {(m, r.path) for r in iter_route_contexts(router.routes) for m in (r.methods or ())}
    assert ("GET", "/admin/billing/payment-reviews") in paths
    assert ("POST", "/admin/billing/payment-reviews/close") in paths
