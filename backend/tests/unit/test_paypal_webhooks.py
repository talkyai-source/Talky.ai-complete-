"""PayPal webhook handler: verify, receipt, dispatch. No database, no network."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.services import paypal_webhooks as pw
from app.domain.services.billing_checkout import CheckoutError
from app.domain.services.paypal_client import PayPalError, PayPalNotConfigured
from app.domain.services.paypal_topups import PayPalTopupError

ORDER = str(uuid4())
CAPTURE = "CAP" + "-" + "synthetic1"
PP_ORDER = "PPO" + "-" + "synthetic1"
PP_SUB = "".join(["I", "-", "synthetic", "0001"])
SALE = "".join(["SALE", "-", "synthetic", "1"])
PLAN_ROW = str(uuid4())
SIG_VALUE = "".join(["sig", "-", "synthetic", "-", "value"])
HEADERS = {
    "paypal-transmission-sig": SIG_VALUE,
    "paypal-transmission-id": "tid-1",
    "paypal-transmission-time": "2026-11-18T10:00:00Z",
    "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "paypal-auth-algo": "SHA256withRSA",
}


class FakeClient:
    def __init__(self, result=True, error=None):
        self.result, self.error, self.calls = result, error, []

    async def verify_webhook_signature(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result


class FakeService:
    def __init__(self):
        self.settle_answer = SimpleNamespace(order_id=ORDER, status="paid")
        self.reverse_answers = "reversed"
        self.error = None
        self.settled = []
        self.reversed = []

    async def settle_paypal_order(self, *, paypal_order_id):
        self.settled.append(paypal_order_id)
        if self.error:
            raise self.error
        return self.settle_answer

    async def reverse_capture(self, *, capture_id, kind, event_id):
        self.reversed.append(dict(capture_id=capture_id, kind=kind, event_id=event_id))
        if self.error:
            raise self.error
        if isinstance(self.reverse_answers, list):
            return self.reverse_answers.pop(0)
        return self.reverse_answers


class FakePlans:
    def __init__(self):
        self.outcome = SimpleNamespace(
            subscription_id=PLAN_ROW, tenant_id="T", status="active", activated=True, review=False)
        self.end_answer = "not_ours"
        self.error = None
        self.synced = []
        self.ended = []

    async def sync(self, paypal_subscription_id):
        self.synced.append(paypal_subscription_id)
        if self.error:
            raise self.error
        return self.outcome

    async def end_for_reversal(self, *, transaction_id, kind, event_id):
        self.ended.append(dict(transaction_id=transaction_id, kind=kind, event_id=event_id))
        if self.error:
            raise self.error
        return self.end_answer


class Store:
    def __init__(self):
        self.rows = {}
        self.sql = []
        self.orders = {CAPTURE: ORDER}
        self.sales = {}
        self.opens = 0


class FakeConn:
    def __init__(self, store):
        self.s = store

    async def fetchval(self, sql, *a):
        self.s.sql.append(sql)
        assert sql.startswith("INSERT INTO paypal_webhook_events")
        event_id, event_type, rtype, rid, mode, digest = a
        if event_id in self.s.rows:
            return None
        self.s.rows[event_id] = dict(
            event_type=event_type, resource_type=rtype, resource_id=rid, provider_mode=mode,
            state="received", payload_hash=digest, attempt_count=1, detail=None,
            order_id=None, completed_at=None)
        return event_id

    async def fetchrow(self, sql, *a):
        self.s.sql.append(sql)
        if "FROM paypal_webhook_events" in sql:
            row = self.s.rows.get(a[0])
            return None if row is None else {"state": row["state"], "payload_hash": row["payload_hash"]}
        if "FROM paypal_subscription_payments" in sql:
            sub = self.s.sales.get(a[0])
            return None if sub is None else {"paypal_subscription_id": sub}
        if "FROM topup_orders" in sql:
            oid = self.s.orders.get(a[0])
            return None if oid is None else {"id": oid}
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        self.s.sql.append(sql)
        row = self.s.rows[a[0]]
        if "attempt_count = attempt_count + 1" in sql:
            row["attempt_count"] += 1
        elif "SET state = 'failed'" in sql:
            row.update(state="failed", detail=a[1])
        elif "SET state = $2" in sql:
            row.update(state=a[1], detail=a[2], completed_at="now")
            if a[3]:
                row["order_id"] = a[3]
        else:
            raise AssertionError(sql)
        return "UPDATE 1"


@pytest.fixture
def world(monkeypatch):
    store = Store()

    @asynccontextmanager
    async def fake_acquire(pool, tenant, **kw):
        assert tenant is None, "webhook SQL must run on the platform-level connection"
        store.opens += 1
        yield FakeConn(store)

    monkeypatch.setattr(pw, "acquire_with_tenant", fake_acquire)
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "".join(["wh", "-", "synthetic"]))
    return SimpleNamespace(store=store, client=FakeClient(), svc=FakeService(), plans=FakePlans())


def body(event_type="PAYMENT.CAPTURE.COMPLETED", resource=None, event_id="WH-EVT-1", resource_type="capture"):
    resource = resource if resource is not None else {
        "id": CAPTURE, "supplementary_data": {"related_ids": {"order_id": PP_ORDER}},
        "payer": {"name": "Jane Payer", "email": "jane@example.com"}}
    return json.dumps({"id": event_id, "event_type": event_type, "resource_type": resource_type,
                       "resource": resource}).encode()


async def run(world, raw, **kw):
    return await pw.handle_paypal_webhook(
        object(), headers=HEADERS, raw_body=raw,
        client=kw.get("client", world.client), service=kw.get("service", world.svc),
        plans=kw.get("plans", world.plans))


def row(world, event_id="WH-EVT-1"):
    return world.store.rows[event_id]


# -- envelope -----------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    b"not json", b"[]", b'"x"', json.dumps({"event_type": "A", "resource": {}}).encode(),
    json.dumps({"id": "", "event_type": "A", "resource": {}}).encode(),
    json.dumps({"id": 5, "event_type": "A", "resource": {}}).encode(),
    json.dumps({"id": "x" * 256, "event_type": "A", "resource": {}}).encode(),
    json.dumps({"id": "E", "event_type": 3, "resource": {}}).encode(),
    json.dumps({"id": "E", "event_type": "A", "resource": []}).encode(),
    json.dumps({"id": "E", "event_type": "A"}).encode(),
])
async def test_malformed_bodies_are_rejected_before_any_call(world, raw):
    with pytest.raises(pw.PayPalWebhookRejected):
        await run(world, raw)
    assert world.client.calls == [] and world.store.opens == 0


async def test_missing_webhook_id_or_mode_retries_without_calls(world, monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID")
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body())
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "wh-x")
    monkeypatch.delenv("PAYPAL_ENV")
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body())
    assert world.client.calls == [] and world.store.opens == 0


async def test_unconfigured_client_retries(world, monkeypatch):
    def boom():
        raise PayPalNotConfigured("x")
    monkeypatch.setattr(pw, "get_paypal_client", boom)
    with pytest.raises(pw.PayPalWebhookRetry):
        await pw.handle_paypal_webhook(object(), headers=HEADERS, raw_body=body(), service=world.svc)
    assert world.store.opens == 0


async def test_bad_signature_is_rejected_with_no_database_write(world):
    world.client.result = False
    with pytest.raises(pw.PayPalWebhookRejected):
        await run(world, body())
    assert world.store.opens == 0 and world.store.rows == {} and world.svc.settled == []


async def test_verify_error_retries_with_no_database_write(world):
    world.client.error = PayPalError(status=500, retryable=True)
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body())
    assert world.store.opens == 0


async def test_verify_gets_the_raw_bytes_headers_and_webhook_id(world):
    raw = body()
    await run(world, raw)
    call = world.client.calls[0]
    assert call["raw_body"] is raw and call["headers"] == HEADERS and call["webhook_id"] == "wh-synthetic"


# -- dispatch -----------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", [
    "PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DECLINED", "PAYMENT.CAPTURE.DENIED"])
async def test_capture_events_settle_the_paypal_order(world, event_type):
    result = await run(world, body(event_type))
    assert result == {"status": "completed"}
    assert world.svc.settled == [PP_ORDER]
    r = row(world)
    assert r["state"] == "completed" and r["order_id"] == ORDER and r["completed_at"]
    assert r["resource_id"] == CAPTURE and r["provider_mode"] == "test"


async def test_foreign_payment_is_ignored(world):
    world.svc.settle_answer = None
    assert await run(world, body()) == {"status": "ignored"}
    assert row(world)["state"] == "ignored" and row(world)["order_id"] is None


async def test_capture_without_order_id_is_ignored(world):
    assert await run(world, body(resource={"id": CAPTURE})) == {"status": "ignored"}
    assert world.svc.settled == []


def refund_resource(rid="REF-1", href=None):
    href = href or f"https://api.sandbox.paypal.com/v2/payments/captures/{CAPTURE}"
    res = {"links": [{"rel": "self", "href": "https://x/v2/payments/refunds/REF-1"},
                     {"rel": "up", "href": href}]}
    if rid:
        res["id"] = rid
    return res


async def test_refund_uses_the_up_link_capture_and_refund_id(world):
    assert await run(world, body("PAYMENT.CAPTURE.REFUNDED", refund_resource(), resource_type="refund")) \
        == {"status": "completed"}
    assert world.svc.reversed == [dict(capture_id=CAPTURE, kind="refund", event_id="paypal:refund:REF-1")]


async def test_refund_without_resource_id_falls_back_to_the_event_id(world):
    await run(world, body("PAYMENT.CAPTURE.REFUNDED", refund_resource(rid=""), resource_type="refund"))
    assert world.svc.reversed[0]["event_id"] == "paypal:refund:WH-EVT-1"


@pytest.mark.parametrize("res", [
    {"id": "R"}, {"links": [{"rel": "self", "href": "https://x/a/B"}]},
    refund_resource(href="https://x/captures/bad id!"), refund_resource(href="https://x/captures/" + "a" * 65),
])
async def test_refund_without_a_valid_capture_is_ignored(world, res):
    assert await run(world, body("PAYMENT.CAPTURE.REFUNDED", res)) == {"status": "ignored"}
    assert world.svc.reversed == []


async def test_reversed_capture_resource_uses_its_own_id(world):
    await run(world, body("PAYMENT.CAPTURE.REVERSED", {"id": CAPTURE}, resource_type="capture"))
    assert world.svc.reversed == [dict(capture_id=CAPTURE, kind="dispute", event_id="paypal:reversal:WH-EVT-1")]


async def test_reversed_other_resource_uses_the_up_link(world):
    await run(world, body("PAYMENT.CAPTURE.REVERSED", refund_resource(), resource_type="refund"))
    assert world.svc.reversed[0]["capture_id"] == CAPTURE


def dispute(*ids, dispute_id="PP-D-1"):
    res = {"disputed_transactions": [{"seller_transaction_id": i} for i in ids] + [{"x": 1}]}
    if dispute_id:
        res["dispute_id"] = dispute_id
    return res


async def test_dispute_created_reverses_each_transaction(world):
    await run(world, body("CUSTOMER.DISPUTE.CREATED", dispute("C1", "C2"), resource_type="dispute"))
    assert world.svc.reversed == [
        dict(capture_id="C1", kind="dispute", event_id="paypal:dispute:PP-D-1:C1"),
        dict(capture_id="C2", kind="dispute", event_id="paypal:dispute:PP-D-1:C2")]


async def test_dispute_created_without_dispute_id_uses_the_event_id(world):
    await run(world, body("CUSTOMER.DISPUTE.CREATED", dispute("C1", dispute_id=""), resource_type="dispute"))
    assert world.svc.reversed[0]["event_id"] == "paypal:dispute:WH-EVT-1:C1"


@pytest.mark.parametrize("answer,state", [
    ("reversed", "completed"), ("already", "completed"), ("unknown", "ignored"), ("review", "needs_review")])
async def test_every_reverse_answer_maps(world, answer, state):
    world.svc.reverse_answers = answer
    assert await run(world, body("PAYMENT.CAPTURE.REFUNDED", refund_resource())) == {"status": state}
    assert row(world)["state"] == state


@pytest.mark.parametrize("answers,state", [
    (["unknown", "reversed"], "completed"), (["reversed", "review"], "needs_review"),
    (["unknown", "review", "reversed"], "needs_review"), (["unknown", "unknown"], "ignored")])
async def test_several_transactions_take_the_worst_answer(world, answers, state):
    world.svc.reverse_answers = list(answers)
    ids = [f"C{i}" for i in range(len(answers))]
    assert await run(world, body("CUSTOMER.DISPUTE.CREATED", dispute(*ids))) == {"status": state}
    assert len(world.svc.reversed) == len(answers)


@pytest.mark.parametrize("event_type", ["CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"])
async def test_dispute_update_on_our_order_needs_review_and_never_reverses(world, event_type):
    assert await run(world, body(event_type, dispute("OTHER", CAPTURE))) == {"status": "needs_review"}
    assert world.svc.reversed == [] and row(world)["detail"] == "dispute_update"
    assert row(world)["order_id"] == ORDER


async def test_dispute_update_on_foreign_payment_is_ignored(world):
    assert await run(world, body("CUSTOMER.DISPUTE.UPDATED", dispute("OTHER"))) == {"status": "ignored"}


@pytest.mark.parametrize("event_type", ["CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.PENDING", "SOMETHING.ELSE"])
async def test_other_events_are_ignored(world, event_type):
    assert await run(world, body(event_type)) == {"status": "ignored"}
    assert world.svc.settled == [] and world.svc.reversed == []


# -- receipts -----------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["completed", "ignored", "needs_review"])
async def test_finished_duplicate_calls_nothing(world, state):
    raw = body()
    await run(world, raw)
    row(world)["state"] = state
    world.svc.settled.clear()
    assert await run(world, raw) == {"status": "duplicate"}
    assert world.svc.settled == [] and row(world)["attempt_count"] == 1


@pytest.mark.parametrize("state", ["received", "failed"])
async def test_unfinished_event_is_processed_again_with_attempt_raised(world, state):
    raw = body()
    await run(world, raw)
    row(world).update(state=state)
    world.svc.settled.clear()
    assert await run(world, raw) == {"status": "completed"}
    assert world.svc.settled == [PP_ORDER]
    assert row(world)["attempt_count"] == 2 and row(world)["state"] == "completed"


async def test_changed_payload_goes_to_review_without_work(world):
    await run(world, body())
    world.svc.settled.clear()
    changed = body(resource={"id": CAPTURE, "supplementary_data": {"related_ids": {"order_id": "OTHER"}}})
    assert await run(world, changed) == {"status": "needs_review"}
    assert world.svc.settled == [] and row(world)["detail"] == "payload_changed"


async def test_service_review_gives_needs_review(world):
    world.svc.error = PayPalTopupError("review", "amount_mismatch")
    assert await run(world, body()) == {"status": "needs_review"}
    assert row(world)["state"] == "needs_review"


async def test_other_topup_error_marks_failed_and_propagates(world):
    world.svc.error = PayPalTopupError("provider_failed")
    with pytest.raises(PayPalTopupError):
        await run(world, body())
    assert row(world)["state"] == "failed"


@pytest.mark.parametrize("retryable", [True, False])
async def test_paypal_error_marks_failed_and_retries(world, retryable):
    world.svc.error = PayPalError(status=503, retryable=retryable)
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body())
    assert row(world)["state"] == "failed" and row(world)["detail"] == "paypal_error_503"


async def test_unexpected_error_marks_failed_and_propagates(world):
    world.svc.error = ValueError("boom")
    with pytest.raises(ValueError):
        await run(world, body())
    assert row(world)["state"] == "failed"


# -- privacy ------------------------------------------------------------------------------


async def test_stored_row_and_sql_never_contain_the_body(world):
    raw = body()
    await run(world, raw)
    r = row(world)
    assert r["payload_hash"] == hashlib.sha256(raw).hexdigest()
    flat = json.dumps(r, default=str)
    for secret in ("Jane Payer", "jane@example.com", "payer"):
        assert secret not in flat
    assert not any("tenant_id" in s for s in world.store.sql if "paypal_webhook_events" in s)


async def test_logs_never_contain_the_signature_or_the_body(world, caplog):
    caplog.set_level(logging.DEBUG)
    world.svc.error = PayPalError(status=500)
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body())
    world.svc.error = None
    await run(world, body(event_id="WH-EVT-2"))
    world.client.result = False
    with pytest.raises(pw.PayPalWebhookRejected):
        await run(world, body(event_id="WH-EVT-3"))
    text = caplog.text
    assert "WH-EVT-1" in text
    for forbidden in (SIG_VALUE, "tid-1", "Jane Payer", "jane@example.com", "certs/CERT-1"):
        assert forbidden not in text


async def test_an_unverified_caller_cannot_forge_log_lines(world, caplog):
    """The route is public: the event id and type of a refused notice are the
    caller's own text, so a log line must never carry their line breaks or spaces."""
    caplog.set_level(logging.DEBUG)
    world.client.result = False
    forged_id = "WH-1" + chr(10) + "paypal_webhook event=WH-REAL state=completed"
    forged_type = "X" + chr(13) + chr(10) + "ERROR fake line"
    with pytest.raises(pw.PayPalWebhookRejected):
        await run(world, body(event_id=forged_id, event_type=forged_type))
    lines = [record.getMessage() for record in caplog.records if record.name == pw.logger.name]
    assert len(lines) == 1
    line = lines[0]
    assert chr(10) not in line and chr(13) not in line
    # Line breaks, spaces and "=" are all replaced, so nothing forged reads as a field or a line.
    assert line == ("paypal_webhook_rejected event=WH-1?paypal_webhook?event?WH-REAL?state?completed "
                    "type=X??ERROR?fake?line")
    assert len(line) < 200


# -- not-yet-completed order, and the verification bound --------------------------------------


async def test_completed_notice_for_an_unfinished_order_retries_and_stays_received(world):
    world.svc.settle_answer = SimpleNamespace(order_id=ORDER, status="not_approved")
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body())
    assert row(world)["state"] == "received" and row(world)["completed_at"] is None
    world.svc.settle_answer = SimpleNamespace(order_id=ORDER, status="paid")
    assert await run(world, body()) == {"status": "completed"}
    assert row(world)["state"] == "completed" and row(world)["attempt_count"] == 2
    assert len(world.svc.settled) == 2


@pytest.mark.parametrize("event_type", ["PAYMENT.CAPTURE.DECLINED", "PAYMENT.CAPTURE.DENIED"])
async def test_declined_and_denied_notices_keep_their_behaviour(world, event_type):
    world.svc.settle_answer = SimpleNamespace(order_id=ORDER, status="not_approved")
    assert await run(world, body(event_type=event_type)) == {"status": "completed"}
    assert row(world)["detail"] == "capture_not_approved"


class HeldClient:
    def __init__(self):
        self.gate = asyncio.Event()
        self.calls = 0
        self.error = None

    async def verify_webhook_signature(self, **kw):
        self.calls += 1
        await self.gate.wait()
        if self.error:
            raise self.error
        return True


async def test_at_most_four_verifications_are_in_flight(world):
    assert pw._MAX_VERIFICATIONS_IN_FLIGHT == 4
    held = HeldClient()
    tasks = [asyncio.create_task(run(world, body(event_id=f"WH-{i}"), client=held)) for i in range(4)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert held.calls == 4 and pw._verifications_in_flight == 4
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body(event_id="WH-5"), client=held)
    assert held.calls == 4
    held.gate.set()
    assert [r["status"] for r in await asyncio.gather(*tasks)] == ["completed"] * 4
    assert pw._verifications_in_flight == 0
    assert await run(world, body(event_id="WH-6"), client=held) == {"status": "completed"}
    assert held.calls == 5


async def test_the_counter_returns_to_zero_after_a_verification_error(world):
    held = HeldClient()
    held.gate.set()
    held.error = PayPalError(status=500, retryable=True)
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body(), client=held)
    assert pw._verifications_in_flight == 0
    held.error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await run(world, body(), client=held)
    assert pw._verifications_in_flight == 0


# -- plans bought with a PayPal subscription -----------------------------------------------

PLAN_EVENTS = [
    "BILLING.SUBSCRIPTION.ACTIVATED", "BILLING.SUBSCRIPTION.UPDATED", "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.SUSPENDED", "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
]


@pytest.mark.parametrize("event_type", PLAN_EVENTS)
async def test_subscription_events_sync_the_subscription_by_resource_id(world, event_type):
    world.plans.outcome.status = "cancelled"
    assert await run(world, body(event_type, {"id": PP_SUB}, resource_type="subscription")) == {"status": "completed"}
    assert world.plans.synced == [PP_SUB]
    assert row(world)["detail"] == "plan_cancelled" and row(world)["order_id"] == PLAN_ROW
    assert world.svc.settled == [] and world.svc.reversed == []


@pytest.mark.parametrize("resource", [{}, {"id": 5}, {"id": "bad id!"}, {"id": "a" * 65}])
async def test_subscription_event_without_a_valid_id_is_ignored(world, resource):
    assert await run(world, body(PLAN_EVENTS[0], resource)) == {"status": "ignored"}
    assert row(world)["detail"] == "no_subscription_id" and world.plans.synced == []


async def test_subscription_that_is_not_ours_is_ignored(world):
    world.plans.outcome = None
    assert await run(world, body(PLAN_EVENTS[0], {"id": PP_SUB})) == {"status": "ignored"}
    assert row(world)["detail"] == "not_our_subscription"


async def test_subscription_needing_review_gives_needs_review(world):
    world.plans.outcome.review = True
    assert await run(world, body(PLAN_EVENTS[1], {"id": PP_SUB})) == {"status": "needs_review"}
    assert row(world)["detail"] == "plan_review" and row(world)["order_id"] == PLAN_ROW


async def test_subscription_event_does_not_wait_for_a_payment(world):
    world.plans.outcome.status, world.plans.outcome.activated = "approval_pending", False
    assert await run(world, body(PLAN_EVENTS[0], {"id": PP_SUB})) == {"status": "completed"}


async def test_sale_completed_syncs_by_billing_agreement_id(world):
    resource = {"id": SALE, "billing_agreement_id": PP_SUB}
    assert await run(world, body("PAYMENT.SALE.COMPLETED", resource, resource_type="sale")) == {"status": "completed"}
    assert world.plans.synced == [PP_SUB]


@pytest.mark.parametrize("resource", [{"id": SALE}, {"id": SALE, "billing_agreement_id": "bad id!"}])
async def test_sale_completed_without_a_subscription_is_ignored(world, resource):
    assert await run(world, body("PAYMENT.SALE.COMPLETED", resource)) == {"status": "ignored"}
    assert row(world)["detail"] == "no_subscription_id" and world.plans.synced == []


async def test_sale_completed_while_approval_pending_retries_and_stays_received(world):
    world.plans.outcome.status, world.plans.outcome.activated = "approval_pending", False
    raw = body("PAYMENT.SALE.COMPLETED", {"id": SALE, "billing_agreement_id": PP_SUB})
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, raw)
    assert row(world)["state"] == "received" and row(world)["completed_at"] is None
    world.plans.outcome.status, world.plans.outcome.activated = "active", True
    assert await run(world, raw) == {"status": "completed"}
    assert row(world)["attempt_count"] == 2


async def test_sale_completed_for_a_foreign_subscription_is_ignored(world):
    world.plans.outcome = None
    resource = {"id": SALE, "billing_agreement_id": PP_SUB}
    assert await run(world, body("PAYMENT.SALE.COMPLETED", resource)) == {"status": "ignored"}


async def test_refund_of_a_known_sale_resyncs_and_records_the_refund(world):
    world.store.sales[SALE] = PP_SUB
    result = await run(world, body("PAYMENT.SALE.REFUNDED", {"id": "REF-1", "sale_id": SALE}))
    assert result == {"status": "completed"}
    assert world.plans.synced == [PP_SUB] and row(world)["detail"] == "plan_refund_recorded"
    assert world.plans.ended == []
    sql = [q for q in world.store.sql if "paypal_subscription_payments" in q]
    assert len(sql) == 1 and "s.tenant_id = p.tenant_id" in sql[0]


async def test_refund_of_an_unknown_sale_is_ignored(world):
    assert await run(world, body("PAYMENT.SALE.REFUNDED", {"sale_id": SALE})) == {"status": "ignored"}
    assert row(world)["detail"] == "not_our_payment" and world.plans.synced == []


@pytest.mark.parametrize("resource", [{}, {"sale_id": 5}, {"sale_id": "bad id!"}])
async def test_refund_without_a_valid_sale_id_is_ignored(world, resource):
    assert await run(world, body("PAYMENT.SALE.REFUNDED", resource)) == {"status": "ignored"}
    assert row(world)["detail"] == "no_sale_id"


async def test_refund_whose_subscription_needs_review_is_needs_review(world):
    world.store.sales[SALE] = PP_SUB
    world.plans.outcome.review = True
    assert await run(world, body("PAYMENT.SALE.REFUNDED", {"sale_id": SALE})) == {"status": "needs_review"}


@pytest.mark.parametrize("answer", ["ended", "already"])
async def test_reversal_calls_end_for_reversal_once(world, answer):
    world.plans.end_answer = answer
    assert await run(world, body("PAYMENT.SALE.REVERSED", {"id": SALE})) == {"status": "completed"}
    assert world.plans.ended == [dict(transaction_id=SALE, kind="reversal", event_id="paypal:plan-reversal:WH-EVT-1")]
    assert row(world)["detail"] == f"plan_reversal_{answer}"


async def test_reversal_of_a_foreign_payment_is_ignored(world):
    assert await run(world, body("PAYMENT.SALE.REVERSED", {"id": SALE})) == {"status": "ignored"}
    assert row(world)["detail"] == "not_our_payment"


@pytest.mark.parametrize("resource", [{}, {"id": 9}, {"id": "bad id!"}])
async def test_reversal_without_a_valid_id_is_ignored(world, resource):
    assert await run(world, body("PAYMENT.SALE.REVERSED", resource)) == {"status": "ignored"}
    assert world.plans.ended == []


async def test_dispute_on_a_plan_payment_reaches_the_plan_service_after_unknown(world):
    world.svc.reverse_answers = "unknown"
    world.plans.end_answer = "ended"
    assert await run(world, body("CUSTOMER.DISPUTE.CREATED", dispute(SALE))) == {"status": "completed"}
    assert world.svc.reversed[0]["capture_id"] == SALE
    assert world.plans.ended == [
        dict(transaction_id=SALE, kind="dispute", event_id=f"paypal:plan-dispute:PP-D-1:{SALE}")]


async def test_dispute_on_a_foreign_payment_is_ignored(world):
    world.svc.reverse_answers = "unknown"
    assert await run(world, body("CUSTOMER.DISPUTE.CREATED", dispute(SALE))) == {"status": "ignored"}
    assert len(world.plans.ended) == 1


@pytest.mark.parametrize("answer", ["reversed", "already", "review"])
async def test_dispute_on_a_prepaid_minutes_payment_never_reaches_the_plan_service(world, answer):
    world.svc.reverse_answers = answer
    await run(world, body("CUSTOMER.DISPUTE.CREATED", dispute(CAPTURE)))
    assert world.plans.ended == []


async def test_plan_service_paypal_error_retries(world):
    world.plans.error = PayPalError(status=503, retryable=True)
    with pytest.raises(pw.PayPalWebhookRetry):
        await run(world, body(PLAN_EVENTS[0], {"id": PP_SUB}))
    assert row(world)["state"] == "failed" and row(world)["detail"] == "paypal_error_503"


async def test_plan_service_checkout_error_marks_failed_and_propagates(world):
    world.plans.error = CheckoutError("checkout_unconfirmed", "x", 503)
    with pytest.raises(CheckoutError):
        await run(world, body(PLAN_EVENTS[0], {"id": PP_SUB}))
    assert row(world)["state"] == "failed" and row(world)["detail"] == "plan_checkout_unconfirmed"
