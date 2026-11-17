"""PayPal top-up service: the decisions that turn a PayPal payment into minutes.

The fakes run the service's real control flow. What they cannot prove is what
PostgreSQL enforces (row lock, unique ``provider_event_id``); ``FakeTopups``
models the once-only rule the way ``TopupService.credit_paid_order`` states it.
"""
from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.domain.services import paypal_topups as pt
from app.domain.services.paypal_client import PayPalError
from app.domain.services.paypal_topups import PayPalTopupError, PayPalTopupService
from app.domain.services.topup_service import TopupError

TENANT = str(uuid4())
OTHER_TENANT = str(uuid4())
# Built at run time from pieces: not a literal that looks like a real id.
PP_ORDER = "".join(["PPORD", "-", "test", "0001"])
CAPTURE = "".join(["CAP", "-", "test", "0001"])


class FakeConn:
    def __init__(self, store):
        self.store = store

    def _find(self, pred):
        for row in self.store["orders"].values():
            if pred(row):
                return row
        return None

    async def execute(self, sql, *args):
        self.store["sql"].append(sql)
        order_id, tenant = args[0], args[1]
        row = self.store["orders"].get(order_id)
        ok = row is not None and row["tenant_id"] == tenant and row["status"] == "pending"
        if "SET provider = 'paypal'" in sql:
            if self.store.get("provider_update_zero") or not (ok and row["provider_session_id"] is None):
                return "UPDATE 0"
            row["provider"] = "paypal"
            return "UPDATE 1"
        if "SET status = 'failed'" in sql:
            if not ok:
                return "UPDATE 0"
            row["status"] = "failed"
            return "UPDATE 1"
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetchrow(self, sql, *args):
        self.store["sql"].append(sql)
        if "WHERE id = $1::uuid AND tenant_id = $2::uuid" in sql:
            row = self._find(lambda r: r["id"] == args[0] and r["tenant_id"] == args[1])
        elif "provider_session_id = $1 AND provider = 'paypal'" in sql:
            row = self._find(lambda r: r["provider_session_id"] == args[0] and r["provider"] == "paypal")
        elif "provider_payment_id = $1 AND provider = 'paypal'" in sql:
            row = self._find(lambda r: r["provider_payment_id"] == args[0] and r["provider"] == "paypal")
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        return copy.deepcopy(row)


class FakePool:
    pass


class FakeTopups:
    """Same observable contract as TopupService for the calls the service makes."""

    def __init__(self, store, currency="USD"):
        self.store = store
        self.currency = currency
        self.credits: list[dict] = []
        self.ledger_events: set[str] = set()
        self.failed: list[str] = []
        self.reversals: list[dict] = []
        self.attach_error: Exception | None = None
        self.create_error: Exception | None = None

    async def packages(self, audience):
        return [{"code": "m100", "name": "100 minutes", "minutes": 100,
                 "price_cents": 1000, "currency": self.currency, "audience": audience}]

    async def create_order(self, *, tenant_id, user_id, package_code, audience):
        if self.create_error:
            raise self.create_error
        oid = str(uuid4())
        self.store["orders"][oid] = dict(
            id=oid, tenant_id=tenant_id, minutes=100, price_cents=1000,
            currency=self.currency.lower(), status="pending", provider="stripe",
            provider_session_id=None, provider_payment_id=None)
        return {"id": oid, "package_code": package_code, "minutes": 100,
                "price_cents": 1000, "currency": self.currency.lower(),
                "status": "pending", "name": "100 minutes"}

    async def attach_session(self, order_id, session_id, *, tenant_id):
        if self.attach_error:
            raise self.attach_error
        self.store["orders"][order_id]["provider_session_id"] = session_id

    def _by_session(self, session_id):
        for row in self.store["orders"].values():
            if row["provider_session_id"] == session_id:
                return row
        return None

    async def credit_paid_order(self, *, session_id, event_id, payment_id=None, conn=None):
        row = self._by_session(session_id)
        if row is None or row["status"] in {"paid", "refunded", "disputed"} or event_id in self.ledger_events:
            return False
        self.ledger_events.add(event_id)
        row["status"] = "paid"
        row["provider_payment_id"] = payment_id
        self.credits.append({"order": row["id"], "minutes": row["minutes"], "event": event_id})
        return True

    async def mark_failed(self, *, session_id, status, conn=None):
        row = self._by_session(session_id)
        self.failed.append(session_id)
        if row and row["status"] == "pending":
            row["status"] = status

    async def reverse(self, *, event_id, kind="refund", payment_id=None, session_id=None, conn=None):
        for row in self.store["orders"].values():
            if row["provider_payment_id"] == payment_id and row["status"] == "paid":
                row["status"] = "refunded" if kind == "refund" else "disputed"
                self.reversals.append({"event": event_id, "kind": kind, "payment": payment_id})
                return True
        return False


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.create_answer = None
        self.create_error: Exception | None = None
        self.capture_answer = None
        self.capture_error: Exception | None = None
        self.order_answer = None
        self.order_error: Exception | None = None
        self.capture_object = {"id": CAPTURE, "status": "COMPLETED"}

    async def create_order(self, **kw):
        self.calls.append(("create_order", kw))
        if self.create_error:
            raise self.create_error
        if self.create_answer is not None:
            return self.create_answer
        return {"id": PP_ORDER, "status": "PAYER_ACTION_REQUIRED", "links": [
            {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=" + PP_ORDER}]}

    async def capture_order(self, paypal_order_id, *, request_id):
        self.calls.append(("capture_order", paypal_order_id, request_id))
        if self.capture_error:
            raise self.capture_error
        return self.capture_answer

    async def get_order(self, paypal_order_id):
        self.calls.append(("get_order", paypal_order_id))
        if self.order_error:
            raise self.order_error
        return self.order_answer

    async def get_capture(self, capture_id):
        self.calls.append(("get_capture", capture_id))
        return self.capture_object

    def names(self):
        return [c[0] for c in self.calls]


def pp_order(order, *, status="COMPLETED", capture_status="COMPLETED", value=None,
             currency=None, paypal_id=PP_ORDER, units=None, capture_extra=None,
             captures=None, unit_extra=None):
    capture = {"id": CAPTURE, "status": capture_status,
               "amount": {"currency_code": currency or order["currency"].upper(),
                          "value": value or f"{order['price_cents'] // 100}.{order['price_cents'] % 100:02d}"},
               "custom_id": order["id"]}
    capture.update(capture_extra or {})
    unit = {"reference_id": order["id"], "custom_id": order["id"],
            "payments": {"captures": [capture] if captures is None else captures}}
    unit.update(unit_extra or {})
    return {"id": paypal_id, "status": status, "purchase_units": units if units is not None else [unit]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    store = {"orders": {}, "sql": []}

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kw):
        store["acquired"] = store.get("acquired", []) + [tenant_id]
        yield FakeConn(store)

    monkeypatch.setattr(pt, "acquire_with_tenant", fake_acquire)
    topups = FakeTopups(store)
    client = FakeClient()
    svc = PayPalTopupService(FakePool(), client=client, topups=topups)
    return svc, client, topups, store


def add_order(store, *, provider="paypal", status="pending", session=PP_ORDER, tenant=TENANT,
              payment=None, currency="USD"):
    oid = str(uuid4())
    store["orders"][oid] = dict(
        id=oid, tenant_id=tenant, minutes=100, price_cents=1000, currency=currency,
        status=status, provider=provider, provider_session_id=session, provider_payment_id=payment)
    return store["orders"][oid]


# -- start_checkout ---------------------------------------------------------------


async def start(svc):
    return await svc.start_checkout(tenant_id=TENANT, user_id=str(uuid4()), package_code="m100",
                                    audience="subscriber", billing_base="https://app.example/billing")


async def test_start_checkout_happy_path(env):
    svc, client, topups, store = env
    out = await start(svc)
    row = store["orders"][out.order_id]
    assert row["provider"] == "paypal" and row["provider_session_id"] == PP_ORDER
    assert row["status"] == "pending"
    assert out.paypal_order_id == PP_ORDER and out.checkout_url.startswith("https://www.sandbox.paypal.com/")
    assert (out.minutes, out.price_cents, out.currency) == (100, 1000, "USD")
    kw = client.calls[0][1]
    assert kw["request_id"] == f"topup-{out.order_id}"
    assert kw["reference_id"] == kw["custom_id"] == out.order_id
    assert kw["value"] == "10.00" and kw["currency"] == "USD"
    assert kw["description"] == "Talk-Lee: 100 minutes" and kw["brand_name"] == "Talk-Lee"
    assert kw["return_url"] == f"https://app.example/billing?topup=success&provider=paypal&order_id={out.order_id}"
    assert kw["cancel_url"] == f"https://app.example/billing?topup=cancelled&provider=paypal&order_id={out.order_id}"
    # every SQL here carries the tenant predicate
    assert all("tenant_id" in s for s in store["sql"])


async def test_description_is_cut_to_127_characters(env):
    svc, client, topups, store = env

    async def packages(audience):
        return [{"code": "m100", "name": "x" * 300, "minutes": 100, "price_cents": 1000, "currency": "USD"}]

    orig = topups.create_order

    async def create(**kw):
        out = await orig(**kw)
        out["name"] = "x" * 300
        return out

    topups.packages, topups.create_order = packages, create
    await start(svc)
    assert len(client.calls[0][1]["description"]) == 127


async def test_unsupported_currency_creates_no_order(env):
    svc, client, topups, store = env
    topups.currency = "JPY"
    with pytest.raises(PayPalTopupError) as e:
        await start(svc)
    assert e.value.code == "unsupported_currency"
    assert store["orders"] == {} and client.calls == []


async def test_unknown_package_is_refused_by_create_order(env):
    svc, client, topups, store = env
    topups.create_error = TopupError("Unknown or inactive top-up package 'zz'")
    with pytest.raises(TopupError):
        await svc.start_checkout(tenant_id=TENANT, user_id=None, package_code="zz",
                                 audience="subscriber", billing_base="https://app.example/billing")
    assert client.calls == []


async def test_provider_update_touching_zero_rows_raises_and_never_calls_paypal(env):
    svc, client, topups, store = env
    store["provider_update_zero"] = True
    with pytest.raises(PayPalTopupError) as e:
        await start(svc)
    assert e.value.code == "provider_failed"
    assert client.calls == []


@pytest.mark.parametrize("failure", ["error", "no_id", "no_url", "wrong_host", "bad_value"])
async def test_provider_failure_marks_the_order_failed(env, failure, caplog):
    svc, client, topups, store = env
    if failure == "error":
        client.create_error = PayPalError(status=422, name="UNPROCESSABLE", debug_id="dbg123")
    elif failure == "no_id":
        client.create_answer = {"links": []}
    elif failure == "no_url":
        client.create_answer = {"id": PP_ORDER, "links": []}
    elif failure == "wrong_host":
        client.create_answer = {"id": PP_ORDER, "links": [
            {"rel": "approve", "href": "https://evil.example/checkoutnow"}]}
    else:
        client.create_error = ValueError("bad")
    with caplog.at_level("ERROR"):
        with pytest.raises(PayPalTopupError) as e:
            await start(svc)
    assert e.value.code == "provider_failed"
    (row,) = store["orders"].values()
    assert row["status"] == "failed" and row["provider_session_id"] is None
    assert "paypal_topup_create_failed" in caplog.text


async def test_provider_error_log_carries_debug_id_only(env, caplog):
    svc, client, topups, store = env
    client.create_error = PayPalError(status=500, name="INTERNAL", debug_id="dbg-77", retryable=True)
    with caplog.at_level("ERROR"):
        with pytest.raises(PayPalTopupError):
            await start(svc)
    assert "dbg-77" in caplog.text


async def test_attach_failure_fails_the_order(env):
    svc, client, topups, store = env
    topups.attach_error = TopupError("vanished")
    with pytest.raises(PayPalTopupError) as e:
        await start(svc)
    assert e.value.code == "provider_failed"
    (row,) = store["orders"].values()
    assert row["status"] == "failed"


# -- capture_for_tenant -------------------------------------------------------------


async def test_capture_happy_path_credits_once(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_answer = pp_order(row)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert (out.order_id, out.status, out.minutes) == (row["id"], "paid", 100)
    assert client.calls == [("capture_order", PP_ORDER, f"capture-{row['id']}")]
    assert topups.credits == [{"order": row["id"], "minutes": 100, "event": f"paypal:capture:{CAPTURE}"}]
    assert row["provider_payment_id"] == CAPTURE


async def test_capture_twice_credits_once(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_answer = pp_order(row)
    first = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    second = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert first.status == second.status == "paid"
    assert len(topups.credits) == 1
    assert client.names().count("capture_order") == 1  # the second answered from our row


async def test_a_replayed_credit_still_answers_paid_but_moves_nothing(env):
    svc, client, topups, store = env
    row = add_order(store)
    topups.ledger_events.add(f"paypal:capture:{CAPTURE}")
    client.capture_answer = pp_order(row)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "paid" and topups.credits == []


@pytest.mark.parametrize("status", ["paid", "refunded", "disputed"])
async def test_capture_after_payment_or_reversal_answers_paid_without_calling_paypal(env, status):
    svc, client, topups, store = env
    row = add_order(store, status=status)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert (out.status, out.minutes) == ("paid", 100)
    assert client.calls == [] and topups.credits == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_capture_of_a_dead_order_answers_failed(env, status):
    svc, client, topups, store = env
    row = add_order(store, status=status)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "failed" and client.calls == []


async def test_order_without_paypal_order_is_not_approved(env):
    svc, client, topups, store = env
    row = add_order(store, session=None)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "not_approved" and client.calls == []


async def test_another_tenants_order_is_not_found(env):
    svc, client, topups, store = env
    row = add_order(store, tenant=OTHER_TENANT)
    with pytest.raises(PayPalTopupError) as e:
        await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert e.value.code == "not_found" and client.calls == []


async def test_a_stripe_order_is_not_found(env):
    svc, client, topups, store = env
    row = add_order(store, provider="stripe", session="cs_" + "x" * 8)
    with pytest.raises(PayPalTopupError) as e:
        await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert e.value.code == "not_found" and client.calls == []


@pytest.mark.parametrize("order_id", ["nope", str(uuid4())])
async def test_unknown_or_malformed_order_id_is_not_found(env, order_id):
    svc, client, topups, store = env
    with pytest.raises(PayPalTopupError) as e:
        await svc.capture_for_tenant(tenant_id=TENANT, order_id=order_id)
    assert e.value.code == "not_found"


async def test_retryable_error_reraises_and_leaves_the_order_pending(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_error = PayPalError(status=503, name="SERVICE_UNAVAILABLE", retryable=True)
    with pytest.raises(PayPalError):
        await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert row["status"] == "pending" and topups.credits == []
    assert client.names() == ["capture_order"]


async def test_non_retryable_error_then_completed_credits(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_error = PayPalError(status=422, name="UNPROCESSABLE_ENTITY", issue="ORDER_ALREADY_CAPTURED")
    client.order_answer = pp_order(row)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "paid" and len(topups.credits) == 1
    assert client.names() == ["capture_order", "get_order"]


async def test_non_retryable_error_then_voided_fails_the_order(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_error = PayPalError(status=422, name="X")
    client.order_answer = {"id": PP_ORDER, "status": "VOIDED"}
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "failed" and row["status"] == "failed" and topups.credits == []


@pytest.mark.parametrize("state", ["CREATED", "SAVED", "APPROVED", "PAYER_ACTION_REQUIRED"])
async def test_non_retryable_error_then_unfinished_order_is_not_approved(env, state):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_error = PayPalError(status=422, name="X")
    client.order_answer = {"id": PP_ORDER, "status": state}
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "not_approved" and row["status"] == "pending" and topups.credits == []


async def test_get_order_failing_after_a_capture_error_reraises(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_error = PayPalError(status=422, name="X")
    client.order_error = PayPalError(status=503, retryable=True)
    with pytest.raises(PayPalError):
        await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert row["status"] == "pending"


async def test_pending_capture_credits_nothing(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_answer = pp_order(row, capture_status="PENDING")
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert (out.status, out.minutes) == ("pending", None)
    assert topups.credits == [] and row["status"] == "pending"


@pytest.mark.parametrize("state", ["DECLINED", "FAILED"])
async def test_declined_capture_fails_the_order(env, state):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_answer = pp_order(row, capture_status=state)
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "failed" and row["status"] == "failed" and topups.credits == []


# -- verify and credit: every rule, failing in turn ----------------------------------

_RULES = {
    "paypal_order_id": lambda o: dict(paypal_id="OTHER-ORDER"),
    "no_units": lambda o: dict(units=[]),
    "two_units": lambda o: dict(units=[{"custom_id": o["id"], "payments": {"captures": []}}] * 2),
    "unit_custom_id": lambda o: dict(unit_extra={"custom_id": str(uuid4())}),
    "unit_reference_id": lambda o: dict(unit_extra={"reference_id": str(uuid4())}),
    "no_captures": lambda o: dict(captures=[]),
    "two_captures": lambda o: dict(captures=[{"id": "A", "status": "COMPLETED"}, {"id": "B", "status": "COMPLETED"}]),
    "capture_custom_id": lambda o: dict(capture_extra={"custom_id": str(uuid4())}),
    "currency": lambda o: dict(currency="GBP"),
    "amount_less": lambda o: dict(value="9.99"),
    "amount_more": lambda o: dict(value="10.01"),
    "amount_unreadable": lambda o: dict(value="ten"),
    "amount_missing": lambda o: dict(capture_extra={"amount": None}),
    "refunded": lambda o: dict(capture_status="REFUNDED"),
    "partially_refunded": lambda o: dict(capture_status="PARTIALLY_REFUNDED"),
}


@pytest.mark.parametrize("rule", sorted(_RULES))
async def test_every_verification_rule_failing_gives_review_and_no_credit(env, rule, caplog):
    svc, client, topups, store = env
    row = add_order(store)
    client.capture_answer = pp_order(row, **_RULES[rule](row))
    with caplog.at_level("ERROR"):
        with pytest.raises(PayPalTopupError) as e:
            await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert e.value.code == "review"
    assert topups.credits == [] and row["status"] == "pending"
    assert "paypal_topup_review" in caplog.text and row["id"][:8] in caplog.text


async def test_missing_custom_and_reference_ids_are_allowed_when_the_rest_matches(env):
    svc, client, topups, store = env
    row = add_order(store)
    answer = pp_order(row)
    unit = answer["purchase_units"][0]
    unit.pop("custom_id"), unit.pop("reference_id")
    unit["payments"]["captures"][0].pop("custom_id")
    client.capture_answer = answer
    out = await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert out.status == "paid"


async def test_credited_amount_comes_from_our_row_not_paypal(env):
    svc, client, topups, store = env
    row = add_order(store)
    answer = pp_order(row)
    answer["purchase_units"][0]["payments"]["captures"][0]["minutes"] = 99999
    client.capture_answer = answer
    await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert topups.credits[0]["minutes"] == 100


async def test_lowercase_order_currency_matches(env):
    svc, client, topups, store = env
    row = add_order(store, currency="usd")
    client.capture_answer = pp_order(row)
    assert (await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])).status == "paid"


# -- settle_paypal_order (webhook) ----------------------------------------------------


async def test_settle_credits_a_completed_order(env):
    svc, client, topups, store = env
    row = add_order(store, tenant=OTHER_TENANT)
    client.order_answer = pp_order(row)
    out = await svc.settle_paypal_order(paypal_order_id=PP_ORDER)
    assert (out.order_id, out.status, out.minutes) == (row["id"], "paid", 100)
    assert len(topups.credits) == 1
    assert store["acquired"] == [None]  # no tenant is known on this path
    assert any("provider = 'paypal'" in s for s in store["sql"])


async def test_settle_unknown_order_returns_none(env):
    svc, client, topups, store = env
    add_order(store, provider="stripe")
    assert await svc.settle_paypal_order(paypal_order_id=PP_ORDER) is None
    assert client.calls == []


async def test_settle_then_capture_credits_once(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.order_answer = pp_order(row)
    client.capture_answer = pp_order(row)
    await svc.settle_paypal_order(paypal_order_id=PP_ORDER)
    await svc.capture_for_tenant(tenant_id=TENANT, order_id=row["id"])
    assert len(topups.credits) == 1


@pytest.mark.parametrize("status", ["paid", "refunded", "disputed"])
async def test_settle_of_a_finished_order_makes_no_paypal_call(env, status):
    svc, client, topups, store = env
    row = add_order(store, status=status)
    out = await svc.settle_paypal_order(paypal_order_id=PP_ORDER)
    assert out.status == "paid" and client.calls == []


async def test_settle_of_an_unapproved_paypal_order_is_not_approved(env):
    svc, client, topups, store = env
    add_order(store)
    client.order_answer = {"id": PP_ORDER, "status": "APPROVED"}
    out = await svc.settle_paypal_order(paypal_order_id=PP_ORDER)
    assert out.status == "not_approved" and topups.credits == []


async def test_settle_with_a_mismatching_amount_is_review(env):
    svc, client, topups, store = env
    row = add_order(store)
    client.order_answer = pp_order(row, value="1.00")
    with pytest.raises(PayPalTopupError) as e:
        await svc.settle_paypal_order(paypal_order_id=PP_ORDER)
    assert e.value.code == "review" and topups.credits == []


# -- reverse_capture ---------------------------------------------------------------------


async def paid_order(env):
    svc, client, topups, store = env
    row = add_order(store, status="paid", payment=CAPTURE)
    return row


async def test_full_refund_reverses_once(env):
    svc, client, topups, store = env
    row = await paid_order(env)
    client.capture_object = {"id": CAPTURE, "status": "REFUNDED", "custom_id": row["id"]}
    first = await svc.reverse_capture(capture_id=CAPTURE, kind="refund", event_id="evt-1")
    second = await svc.reverse_capture(capture_id=CAPTURE, kind="refund", event_id="evt-1")
    assert (first, second) == ("reversed", "already")
    assert row["status"] == "refunded" and len(topups.reversals) == 1


async def test_partial_refund_is_review_and_reverses_nothing(env, caplog):
    svc, client, topups, store = env
    row = await paid_order(env)
    client.capture_object = {"id": CAPTURE, "status": "PARTIALLY_REFUNDED"}
    with caplog.at_level("ERROR"):
        out = await svc.reverse_capture(capture_id=CAPTURE, kind="refund", event_id="evt-2")
    assert out == "review" and topups.reversals == [] and row["status"] == "paid"
    assert "paypal_topup_review" in caplog.text


async def test_refund_event_for_a_capture_that_is_not_refunded_is_review(env):
    svc, client, topups, store = env
    await paid_order(env)
    client.capture_object = {"id": CAPTURE, "status": "COMPLETED"}
    assert await svc.reverse_capture(capture_id=CAPTURE, kind="refund", event_id="evt-3") == "review"
    assert topups.reversals == []


async def test_dispute_reverses_whatever_the_capture_status(env):
    svc, client, topups, store = env
    row = await paid_order(env)
    client.capture_object = {"id": CAPTURE, "status": "COMPLETED"}
    out = await svc.reverse_capture(capture_id=CAPTURE, kind="dispute", event_id="evt-4")
    assert out == "reversed" and row["status"] == "disputed"
    assert topups.reversals[0]["kind"] == "dispute"


async def test_unknown_capture_is_unknown(env):
    svc, client, topups, store = env
    add_order(store, provider="stripe", status="paid", payment=CAPTURE)  # a Stripe row never matches
    assert await svc.reverse_capture(capture_id=CAPTURE, kind="refund", event_id="evt-5") == "unknown"
    assert await svc.reverse_capture(capture_id="nothing", kind="dispute", event_id="evt-6") == "unknown"
    assert topups.reversals == []


async def test_refund_of_a_capture_that_belongs_to_another_order_is_review(env):
    svc, client, topups, store = env
    await paid_order(env)
    client.capture_object = {"id": CAPTURE, "status": "REFUNDED", "custom_id": str(uuid4())}
    assert await svc.reverse_capture(capture_id=CAPTURE, kind="refund", event_id="evt-7") == "review"
    assert topups.reversals == []
