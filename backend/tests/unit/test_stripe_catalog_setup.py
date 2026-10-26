"""scripts/stripe_catalog_setup.py: the Stripe side of paid-only billing.

A fake client stands in for Stripe; no network call is made.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import stripe_catalog_setup as setup


def test_env_upsert_keeps_other_lines_updates_adds_and_removes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nA=1\nSTRIPE_BILLING_DISABLED=1\nexport B=2\n", encoding="utf-8")
    changes = setup.upsert_env(env, {"A": "9", "C": "3", "B": "8"}, {"STRIPE_BILLING_DISABLED"})
    assert env.read_text(encoding="utf-8") == "# comment\nA=9\nB=8\nC=3\n"
    assert sorted(changes) == ["added C", "removed STRIPE_BILLING_DISABLED", "updated A", "updated B"]
    assert len(list(tmp_path.glob(".env.bak-stripe-*"))) == 1


def test_env_upsert_refuses_a_value_that_would_inject_a_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        setup.upsert_env(env, {"A": "x\nJWT_SECRET=y"})
    assert env.read_text(encoding="utf-8") == "A=1\n"


class FakeStripe:
    def __init__(self, prices=(), products=(), endpoints=()):
        self.created = []
        self._prices, self._products, self._endpoints = list(prices), list(products), list(endpoints)
        self.deleted = []
        fake = self

        class Listing(list):
            @property
            def data(self):
                return list(self)

            def auto_paging_iter(self):
                return iter(self)

        class Prices:
            def list(self, params):
                return Listing(p for p in fake._prices if p["lookup_key"] in params["lookup_keys"])

            def create(self, params, options):
                price = {"id": f"price_{len(fake._prices) + 1}", "object": "price", "active": True,
                         "type": "recurring", "billing_scheme": "per_unit", "livemode": False,
                         "unit_amount": params["unit_amount"], "currency": params["currency"],
                         "product": params["product"], "lookup_key": params["lookup_key"],
                         "recurring": dict(params["recurring"])}
                fake._prices.append(price)
                fake.created.append(("price", params, options))
                return price

        class Products:
            def list(self, params):
                return Listing(fake._products)

            def create(self, params, options):
                product = {"id": f"prod_{params['metadata']['talky_plan_id']}", "metadata": params["metadata"]}
                fake._products.append(product)
                fake.created.append(("product", params, options))
                return product

        class Endpoints:
            def list(self, params):
                return Listing(fake._endpoints)

            def create(self, params):
                fake.created.append(("webhook", params, None))
                return {"id": "we_new", "secret": "whsec_created_once"}

            def update(self, identity, params):
                fake.created.append(("webhook_update", params, identity))

            def delete(self, identity):
                fake.deleted.append(identity)

        class Portal:
            def create(self, params):
                fake.created.append(("portal", params, None))
                return {"id": "bpc_new"}

            def update(self, identity, params):
                fake.created.append(("portal_update", params, identity))

        self.v1 = SimpleNamespace(prices=Prices(), products=Products(), webhook_endpoints=Endpoints(),
                                  billing_portal=SimpleNamespace(configurations=Portal()))


def test_a_first_run_creates_one_product_and_both_prices_per_plan():
    client = FakeStripe()
    options = setup.ensure_prices(client, "test", apply=True)
    prices = {(o["plan_id"], o["interval"]): o["amount_minor"] for o in options}
    assert prices == {("basic", "month"): 9900, ("basic", "year"): 98600,
                      ("business", "month"): 29900, ("business", "year"): 297800}
    assert all(o["provider_mode"] == "test" and o["stripe_price_id"].startswith("price_") for o in options)
    kinds = [kind for kind, _, _ in client.created]
    assert kinds.count("product") == 2 and kinds.count("price") == 4
    assert all(options_["idempotency_key"] for _, _, options_ in client.created)


def test_a_second_run_reuses_what_exists():
    client = FakeStripe()
    setup.ensure_prices(client, "test", apply=True)
    again = FakeStripe(prices=client._prices, products=client._products)
    setup.ensure_prices(again, "test", apply=True)
    assert again.created == []


def test_a_dry_run_changes_nothing():
    client = FakeStripe()
    assert setup.ensure_prices(client, "test", apply=False) == []
    assert client.created == []


def test_a_new_webhook_secret_goes_to_the_env_file_only(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    client = FakeStripe(endpoints=[{"id": "we_old", "url": "https://api.example/api/v1/billing/webhooks"}])
    setup.ensure_webhook(client, "https://api.example/api/v1/billing/webhooks", env, apply=True)
    assert client.deleted == ["we_old"], "an endpoint whose secret is unknown can never be verified"
    _, params, _ = next(item for item in client.created if item[0] == "webhook")
    assert set(params["enabled_events"]) >= {"checkout.session.completed", "invoice.paid", "charge.refunded",
                                             "customer.subscription.deleted", "charge.dispute.created"}
    assert "STRIPE_WEBHOOK_SECRET=whsec_created_once" in env.read_text(encoding="utf-8")
    assert "whsec_created_once" not in capsys.readouterr().out


def test_a_configured_webhook_is_updated_not_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_configured")
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    client = FakeStripe(endpoints=[{"id": "we_live", "url": "https://api.example/hook"}])
    setup.ensure_webhook(client, "https://api.example/hook", env, apply=True)
    assert client.deleted == [] and client.created[0][0] == "webhook_update"
    assert env.read_text(encoding="utf-8") == "A=1\n"


def test_the_portal_never_offers_a_plan_change(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_PORTAL_CONFIGURATION_ID", raising=False)
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    client = FakeStripe()
    setup.ensure_portal(client, "https://app.example/billing", env, apply=True)
    _, params, _ = client.created[0]
    assert params["features"]["subscription_update"] == {"enabled": False}
    assert params["features"]["subscription_cancel"]["mode"] == "at_period_end"
    assert "STRIPE_PORTAL_CONFIGURATION_ID=bpc_new" in env.read_text(encoding="utf-8")


async def test_the_billing_portal_session_uses_the_configured_portal(monkeypatch):
    from app.domain.services.billing_service import BillingService

    calls = []
    billing = BillingService.__new__(BillingService)
    billing.mock_mode = False
    billing.billing_mode = "test"

    async def stripe_call(resource, method, *args, **kwargs):
        calls.append((resource, kwargs))
        return SimpleNamespace(url="https://billing.stripe.com/p/session")

    async def verified(*_a):
        return True

    billing._stripe_call = stripe_call
    billing._portal_customer_verified = verified
    billing.db_client = SimpleNamespace(table=lambda name: SimpleNamespace(
        select=lambda *a: SimpleNamespace(eq=lambda *a: SimpleNamespace(single=lambda: SimpleNamespace(
            execute=lambda: SimpleNamespace(data={"stripe_customer_id": "cus_1"}))))))
    monkeypatch.setenv("STRIPE_PORTAL_CONFIGURATION_ID", "bpc_configured")
    await billing.create_portal_session("11111111-1111-1111-1111-111111111111", "https://app.example/billing")
    assert calls[-1] == ("billing_portal.Session",
                         {"customer": "cus_1", "return_url": "https://app.example/billing",
                          "configuration": "bpc_configured"})
