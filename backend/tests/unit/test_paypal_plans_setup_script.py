"""scripts/paypal_plans_setup.py against a fake PayPal client and a fake store."""
from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from app.domain.services import billing_mode
from app.domain.services.paypal_client import PayPalError
from app.domain.services.paypal_mode import PAYPAL_WEBHOOK_EVENTS

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("paypal_plans_setup_under_test", _SCRIPTS / "paypal_plans_setup.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

HOOK = "https://api.example.com" + ps.WEBHOOK_PATH
SITE = "https://app.example.com"
CLIENT_ID = "client-id-for-tests"
CLIENT_SECRET = "client-secret-for-tests"
PRODUCT_ID = "".join(["PROD", "-", "test", "0001"])
_ALL = (
    "PAYPAL_ENV", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET",
    "PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET", "PAYPAL_PLANS_ENABLED",
)
OPT_BASIC = "11111111-1111-1111-1111-111111111111"
OPT_BIZ = "22222222-2222-2222-2222-222222222222"


def option(oid, plan_name, interval, amount_minor, currency="usd", exponent=2):
    return {
        "id": oid, "plan_id": plan_name.lower(), "plan_name": plan_name, "interval": interval,
        "amount_minor": amount_minor, "currency": currency, "currency_exponent": exponent,
    }


def plan_body(plan_id, *, name, interval, value, currency="USD", product_id=PRODUCT_ID, status="ACTIVE"):
    return {
        "id": plan_id, "product_id": product_id, "name": name, "status": status,
        "billing_cycles": [{
            "tenure_type": "REGULAR", "sequence": 1, "total_cycles": 0,
            "frequency": {"interval_unit": interval.upper(), "interval_count": 1},
            "pricing_scheme": {"fixed_price": {"value": value, "currency_code": currency}},
        }],
    }


class FakeStore:
    def __init__(self, options, rows=None):
        self.options = options
        self.rows = rows or {}
        self.saved: list[dict] = []
        self.modes: list[str] = []

    async def price_options(self, mode):
        self.modes.append(mode)
        return self.options

    async def plan_rows(self):
        return self.rows

    async def save(self, **kw):
        self.saved.append(kw)
        self.rows[str(kw["price_option_id"])] = dict(kw)


class FakeClient:
    def __init__(self, *, products=None, plans=None, hooks=None, fail_on=None):
        self.products = products if products is not None else []
        self.plans = plans if plans is not None else {}
        self.hooks = hooks if hooks is not None else [
            {"url": HOOK, "event_types": [{"name": n} for n in PAYPAL_WEBHOOK_EVENTS]}
        ]
        self.fail_on = fail_on
        self.calls: list[tuple] = []
        self.created_plans = 0

    def _enter(self, name, *detail):
        self.calls.append((name, *detail))
        if self.fail_on == name:
            raise PayPalError(status=400, name="BAD", issue="NOPE", debug_id="dbg1")

    async def list_products(self, *, page=1, page_size=20):
        self._enter("list_products")
        return self.products if page == 1 else []

    async def create_product(self, *, request_id, name, description, home_url=None):
        self._enter("create_product", request_id)
        self.products.append({"id": PRODUCT_ID, "name": name})
        return {"id": PRODUCT_ID, "name": name}

    async def list_plans(self, *, product_id, page=1, page_size=20):
        self._enter("list_plans")
        return list(self.plans.values()) if page == 1 else []

    async def get_plan(self, plan_id):
        self._enter("get_plan", plan_id)
        return self.plans[plan_id]

    async def create_plan(self, *, request_id, product_id, name, description, interval, currency, value,
                          payment_failure_threshold=2):
        self._enter("create_plan", request_id)
        self.created_plans += 1
        plan_id = "".join(["P", "-", "test", f"{self.created_plans:04d}"])
        body = plan_body(plan_id, name=name, interval=interval, value=value, currency=currency,
                         product_id=product_id)
        self.plans[plan_id] = body
        return body

    async def create_order(self, **kw):
        self._enter("create_order")
        return {
            "id": "ORD-test", "status": "PAYER_ACTION_REQUIRED",
            "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/x"}],
        }

    async def get_order(self, order_id):
        self._enter("get_order")
        return {"id": order_id}

    async def create_subscription(self, *, plan_id, **kw):
        self._enter("create_subscription", plan_id)
        return {
            "id": "I-test", "status": "APPROVAL_PENDING", "plan_id": plan_id,
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/y"}],
        }

    async def get_subscription(self, sub_id):
        self._enter("get_subscription")
        return {"id": sub_id, "plan_id": self.calls[[c[0] for c in self.calls].index("create_subscription")][1]}

    async def list_subscription_transactions(self, sub_id, *, start_time, end_time):
        self._enter("transactions")
        return []

    async def list_webhooks(self):
        self._enter("list_webhooks")
        return self.hooks

    @property
    def writes(self):
        return [c for c in self.calls if c[0] in ("create_product", "create_plan", "create_order",
                                                  "create_subscription")]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PAYPAL_SANDBOX_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setattr(billing_mode, "get_billing_mode", lambda **kw: "test")


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "app.env"
    path.write_text("FIRST=1\n", encoding="utf-8")
    return path


def args_for(path, **kw):
    base = dict(env_file=path, webhook_url=None, site_url=None, enable=None, self_check=False, apply=False)
    base.update(kw)
    return Namespace(**base)


def two_options():
    return [option(OPT_BASIC, "Basic", "month", 9900), option(OPT_BIZ, "Business", "year", 99000)]


# ---- plan_matches -------------------------------------------------------------

GOOD = plan_body("P-x", name="n", interval="month", value="99.00")


def _mut(fn):
    plan = plan_body("P-x", name="n", interval="month", value="99.00")
    fn(plan)
    return plan


@pytest.mark.parametrize(
    "plan,expected",
    [
        (GOOD, True),
        (_mut(lambda p: p.update(status="INACTIVE")), False),
        (_mut(lambda p: p.update(product_id="OTHER")), False),
        (_mut(lambda p: p.update(billing_cycles=[])), False),
        (_mut(lambda p: p.update(billing_cycles=p["billing_cycles"] * 2)), False),
        (_mut(lambda p: p.update(billing_cycles="x")), False),
        (_mut(lambda p: p["billing_cycles"][0].update(tenure_type="TRIAL")), False),
        (_mut(lambda p: p["billing_cycles"][0]["frequency"].update(interval_unit="YEAR")), False),
        (_mut(lambda p: p["billing_cycles"][0]["frequency"].update(interval_count=2)), False),
        (_mut(lambda p: p["billing_cycles"][0]["frequency"].update(interval_count=True)), False),
        (_mut(lambda p: p["billing_cycles"][0]["pricing_scheme"]["fixed_price"].update(currency_code="GBP")), False),
        (_mut(lambda p: p["billing_cycles"][0]["pricing_scheme"]["fixed_price"].update(value="98.00")), False),
        (_mut(lambda p: p["billing_cycles"][0]["pricing_scheme"]["fixed_price"].update(value=99)), False),
        (_mut(lambda p: p["billing_cycles"][0]["pricing_scheme"].pop("fixed_price")), False),
        (_mut(lambda p: p["billing_cycles"][0].pop("frequency")), False),
        ({}, False),
        ("x", False),
        (None, False),
    ],
)
def test_plan_matches_table(plan, expected):
    assert ps.plan_matches(plan, product_id=PRODUCT_ID, interval="month", value="99.00", currency="usd") is expected


def test_plan_matches_value_compared_in_minor_units():
    plan = plan_body("P-x", name="n", interval="month", value="99.0")
    assert ps.plan_matches(plan, product_id=PRODUCT_ID, interval="month", value="99.00", currency="USD")


# ---- run ----------------------------------------------------------------------

async def test_dry_run_creates_and_writes_nothing(settings_file, capsys):
    store = FakeStore(two_options())
    client = FakeClient()
    assert await ps.run(args_for(settings_file, enable="on"), client=client, store=store) == 0
    out = capsys.readouterr().out
    assert "would create PayPal plan: Basic month 99.00 USD" in out
    assert "would create PayPal plan: Business year 990.00 USD" in out
    assert "would set PAYPAL_PLANS_ENABLED=on" in out
    assert out.rstrip().endswith("dry run complete; nothing changed")
    assert client.writes == []
    assert store.saved == []
    assert settings_file.read_text() == "FIRST=1\n"
    assert store.modes == ["test"]


async def test_apply_creates_product_once_and_one_plan_per_option(settings_file, capsys):
    store = FakeStore(two_options())
    client = FakeClient()
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=store) == 0
    assert [c[0] for c in client.calls].count("create_product") == 1
    assert client.created_plans == 2
    assert ("create_product", "talklee-plans-product-test") in client.calls
    assert ("create_plan", f"talklee-plan-{OPT_BASIC}-9900-USD-month") in client.calls
    assert [(s["interval"], s["amount_minor"], s["paypal_product_id"], s["provider_mode"]) for s in store.saved] == [
        ("month", 9900, PRODUCT_ID, "test"), ("year", 99000, PRODUCT_ID, "test"),
    ]
    assert {s["currency"] for s in store.saved} == {"usd"}
    assert capsys.readouterr().out.rstrip().endswith("done")


async def test_second_run_with_matching_rows_creates_nothing(settings_file, capsys):
    store = FakeStore(two_options())
    client = FakeClient()
    await ps.run(args_for(settings_file, apply=True), client=client, store=store)
    client.calls.clear()
    saved_before = len(store.saved)
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=store) == 0
    out = capsys.readouterr().out
    assert "ok: Basic month" in out and "ok: Business year" in out
    assert client.writes == []
    assert len(store.saved) == saved_before


async def test_crashed_earlier_run_is_picked_up_without_creating(settings_file):
    name = "Talk-Lee Basic (monthly)"
    existing = plan_body("".join(["P", "-", "test", "9999"]), name=name, interval="month", value="99.00")
    store = FakeStore([option(OPT_BASIC, "Basic", "month", 9900)])
    client = FakeClient(products=[{"id": PRODUCT_ID, "name": ps.PRODUCT_NAME}],
                        plans={existing["id"]: existing})
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=store) == 0
    assert client.writes == []
    assert [s["paypal_plan_id"] for s in store.saved] == [existing["id"]]


async def test_changed_price_creates_new_plan_and_updates_row(settings_file):
    store = FakeStore([option(OPT_BASIC, "Basic", "month", 9900)])
    client = FakeClient()
    await ps.run(args_for(settings_file, apply=True), client=client, store=store)
    first = store.saved[0]["paypal_plan_id"]
    store.options = [option(OPT_BASIC, "Basic", "month", 12900)]
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=store) == 0
    assert client.created_plans == 2
    assert len(store.saved) == 2
    assert store.saved[1]["amount_minor"] == 12900
    assert store.saved[1]["paypal_plan_id"] != first
    assert ("create_plan", f"talklee-plan-{OPT_BASIC}-12900-USD-month") in client.calls
    assert store.rows[OPT_BASIC]["amount_minor"] == 12900


async def test_row_whose_plan_changed_at_paypal_is_replaced(settings_file):
    store = FakeStore([option(OPT_BASIC, "Basic", "month", 9900)])
    client = FakeClient()
    await ps.run(args_for(settings_file, apply=True), client=client, store=store)
    old = store.saved[0]["paypal_plan_id"]
    client.plans[old]["status"] = "INACTIVE"
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=store) == 0
    assert client.created_plans == 2


async def test_unsupported_currency_fails_and_blocks_enable(settings_file, capsys):
    options = [option(OPT_BASIC, "Basic", "month", 9900), option(OPT_BIZ, "Business", "year", 99000, currency="jpy")]
    store = FakeStore(options)
    client = FakeClient()
    assert await ps.run(args_for(settings_file, apply=True, enable="on"), client=client, store=store) == 1
    out = capsys.readouterr().out
    assert "skipped: currency not supported by PayPal here" in out
    assert "not enabling PayPal plans" in out
    assert "PAYPAL_PLANS_ENABLED" not in settings_file.read_text()


async def test_bad_exponent_is_unsupported(settings_file, capsys):
    store = FakeStore([option(OPT_BASIC, "Basic", "month", 9900, exponent=0)])
    assert await ps.run(args_for(settings_file, apply=True), client=FakeClient(), store=store) == 1
    assert "skipped: currency not supported by PayPal here" in capsys.readouterr().out


async def test_enable_written_after_clean_apply(settings_file, capsys):
    store = FakeStore(two_options())
    assert await ps.run(args_for(settings_file, apply=True, enable="team"), client=FakeClient(), store=store) == 0
    assert "PAYPAL_PLANS_ENABLED=team" in settings_file.read_text().splitlines()


async def test_enable_off_written_even_after_failure(settings_file):
    store = FakeStore([option(OPT_BASIC, "Basic", "month", 9900, currency="jpy")])
    assert await ps.run(args_for(settings_file, apply=True, enable="off"), client=FakeClient(), store=store) == 1
    assert "PAYPAL_PLANS_ENABLED=off" in settings_file.read_text().splitlines()


async def test_no_price_options(settings_file, capsys):
    assert await ps.run(args_for(settings_file), client=FakeClient(), store=FakeStore([])) == 1
    assert "no approved price options" in capsys.readouterr().out


async def test_mode_mismatch_stops_before_any_call(settings_file, capsys, monkeypatch):
    monkeypatch.setattr(billing_mode, "get_billing_mode", lambda **kw: "live")
    store = FakeStore(two_options())
    client = FakeClient()
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=store) == 1
    assert "PayPal and the billing mode differ; nothing changed" in capsys.readouterr().out
    assert client.calls == []
    assert store.modes == []


async def test_missing_settings_name_only_names(settings_file, capsys, monkeypatch):
    monkeypatch.delenv("PAYPAL_SANDBOX_CLIENT_SECRET")
    client = FakeClient()
    assert await ps.run(args_for(settings_file, apply=True), client=client, store=FakeStore(two_options())) == 1
    out = capsys.readouterr().out
    assert "PAYPAL_SANDBOX_CLIENT_SECRET" in out
    assert CLIENT_ID not in out
    assert client.calls == []


async def test_paypal_error_returns_1_and_enables_nothing(settings_file, capsys):
    store = FakeStore(two_options())
    client = FakeClient(fail_on="create_plan")
    assert await ps.run(args_for(settings_file, apply=True, enable="on"), client=client, store=store) == 1
    assert "PayPal error: status=400 name=BAD issue=NOPE debug_id=dbg1" in capsys.readouterr().out
    assert "PAYPAL_PLANS_ENABLED" not in settings_file.read_text()


# ---- self-check ---------------------------------------------------------------

def check_args(path, **kw):
    return args_for(path, apply=True, self_check=True, webhook_url=HOOK, site_url=SITE, **kw)


async def test_self_check_happy_path_enables(settings_file, capsys):
    store = FakeStore(two_options())
    client = FakeClient()
    assert await ps.run(check_args(settings_file, enable="on"), client=client, store=store) == 0
    out = capsys.readouterr().out
    for name in ("order", "order-read", "subscription", "subscription-read", "transactions", "webhook"):
        assert f"self-check {name}: ok" in out
    assert ("create_subscription", store.saved[0]["paypal_plan_id"]) in client.calls
    assert "PAYPAL_PLANS_ENABLED=on" in settings_file.read_text().splitlines()


async def test_self_check_missing_webhook_event_fails_and_blocks_enable(settings_file, capsys):
    hooks = [{"url": HOOK, "event_types": [{"name": n} for n in PAYPAL_WEBHOOK_EVENTS[:-1]]}]
    store = FakeStore(two_options())
    assert await ps.run(check_args(settings_file, enable="on"), client=FakeClient(hooks=hooks), store=store) == 1
    out = capsys.readouterr().out
    assert "self-check webhook: FAILED" in out
    assert "not enabling PayPal plans" in out
    assert "PAYPAL_PLANS_ENABLED" not in settings_file.read_text()


async def test_self_check_star_webhook_counts_as_complete(settings_file, capsys):
    hooks = [{"url": HOOK, "event_types": [{"name": "*"}]}]
    assert await ps.run(check_args(settings_file), client=FakeClient(hooks=hooks), store=FakeStore(two_options())) == 0
    assert "self-check webhook: ok" in capsys.readouterr().out


async def test_self_check_transactions_error_is_informational(settings_file, capsys):
    client = FakeClient()
    original = client.list_subscription_transactions

    async def boom(sub_id, *, start_time, end_time):
        await original(sub_id, start_time=start_time, end_time=end_time)
        raise PayPalError(status=404, name="RESOURCE_NOT_FOUND")

    client.list_subscription_transactions = boom
    assert await ps.run(check_args(settings_file, enable="on"), client=client, store=FakeStore(two_options())) == 0
    out = capsys.readouterr().out
    assert "self-check transactions: not available for an unapproved subscription (informational)" in out
    assert "PAYPAL_PLANS_ENABLED=on" in settings_file.read_text().splitlines()


async def test_self_check_wrong_subscription_status_fails(settings_file, capsys):
    client = FakeClient()
    original = client.create_subscription

    async def odd(**kw):
        sub = await original(**kw)
        sub["status"] = "ACTIVE"
        return sub

    client.create_subscription = odd
    assert await ps.run(check_args(settings_file), client=client, store=FakeStore(two_options())) == 1
    assert "self-check subscription: FAILED" in capsys.readouterr().out


async def test_self_check_paypal_error_text_is_printed(settings_file, capsys):
    client = FakeClient(fail_on="create_order")
    assert await ps.run(check_args(settings_file), client=client, store=FakeStore(two_options())) == 1
    assert "self-check order: FAILED (PayPal error: status=400 name=BAD issue=NOPE debug_id=dbg1)" in (
        capsys.readouterr().out)


async def test_self_check_without_apply_returns_1(settings_file, capsys):
    client = FakeClient()
    args = args_for(settings_file, self_check=True, webhook_url=HOOK, site_url=SITE)
    assert await ps.run(args, client=client, store=FakeStore(two_options())) == 1
    assert "self-check needs --apply" in capsys.readouterr().out
    assert client.calls == []


@pytest.mark.parametrize("override", [
    {"webhook_url": None},
    {"webhook_url": "http://api.example.com" + ps.WEBHOOK_PATH},
    {"webhook_url": "https://api.example.com/other"},
    {"site_url": None},
    {"site_url": "http://app.example.com"},
    {"site_url": "https://app.example.com/billing"},
])
async def test_self_check_bad_urls_stop_before_any_call(settings_file, override):
    client = FakeClient()
    args = check_args(settings_file)
    for key, value in override.items():
        setattr(args, key, value)
    assert await ps.run(args, client=client, store=FakeStore(two_options())) == 1
    assert client.calls == []


async def test_output_never_contains_credentials(settings_file, capsys):
    await ps.run(check_args(settings_file, enable="on"), client=FakeClient(), store=FakeStore(two_options()))
    out = capsys.readouterr().out
    assert CLIENT_ID not in out and CLIENT_SECRET not in out
