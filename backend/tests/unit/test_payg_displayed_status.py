"""G6: a paid pay-as-you-go account no longer reads as "inactive".

Signup stores subscription_status='inactive' and the first paid prepaid bundle
only sets plan_id='payg' (a prepaid account never has a subscription).
GET /billing/subscription therefore answered "inactive" with no plan, and the
Admin console offered Resume instead of Suspend for a paying customer. The
displayed status is projected; the stored value never changes.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.services.billing_service import BillingService
from app.domain.services.payg import displayed_subscription_status
from tests.unit.test_admin_tenant_lifecycle import make_client, tenant_row

PAYG_PLAN = {"name": "Pay as you go", "price": 0, "minutes": 0, "agents": None}


class _Query:
    def __init__(self, row):
        self.row = row

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=deepcopy(self.row))


def _service(monkeypatch, row):
    service = BillingService.__new__(BillingService)
    service.db_client = SimpleNamespace(table=lambda _name: _Query(row), pool=object())
    service.audit_logger = None

    async def passthrough(self, tenant_id, subscription):
        return subscription

    monkeypatch.setattr(BillingService, "_purchase_projection", passthrough)
    return service


def _tenant(status, plan_id):
    return {"subscription_status": status, "stripe_subscription_id": None, "stripe_customer_id": None,
            "plan_id": plan_id, "plans": PAYG_PLAN if plan_id == "payg" else None}


async def test_a_paid_payg_account_reads_active_on_the_payg_plan(monkeypatch):
    subscription = await _service(monkeypatch, _tenant("inactive", "payg")).get_subscription("t-1")
    assert subscription["status"] == "active"
    assert subscription["plan_id"] == "payg"
    assert subscription["plan"]["name"] == "Pay as you go"


async def test_an_account_that_never_paid_still_has_no_subscription(monkeypatch):
    assert await _service(monkeypatch, _tenant("inactive", None)).get_subscription("t-1") is None


@pytest.mark.parametrize("stored,plan_id,expected", [
    ("inactive", "payg", "active"),
    (None, "payg", "active"),
    ("", "payg", "active"),
    ("suspended", "payg", "suspended"),
    ("past_due", "payg", "past_due"),
    ("canceled", "payg", "canceled"),
    ("inactive", None, "inactive"),
    ("inactive", "free", "inactive"),
    (None, None, None),
])
def test_only_an_unpaid_looking_payg_status_is_projected(stored, plan_id, expected):
    assert displayed_subscription_status(stored, plan_id) == expected


def _admin_client(row):
    def respond(method, sql, args):
        query = " ".join(sql.lower().split())
        if method == "fetchval" and "count(*)" in query:
            return 1
        if method == "fetch":
            return [deepcopy(row)]
        if method == "fetchrow" and "from tenants" in query:
            return deepcopy(row)
        raise AssertionError(f"Unexpected synthetic DB operation: {method} {query}")

    client, _conn, _audit = make_client(respond)
    return client


@pytest.mark.parametrize("plan_id,expected", [("payg", "active"), ("free", "inactive")])
def test_admin_shows_a_paid_payg_tenant_as_active_so_it_offers_suspend(plan_id, expected):
    row = {**tenant_row(1, status="inactive"), "plan_id": plan_id}
    client = _admin_client(row)
    with client:
        listed = client.get("/api/v1/admin/tenants", params={"include_archived": True})
        detail = client.get(f"/api/v1/admin/tenants/{row['id']}")
    assert listed.status_code == detail.status_code == 200, (listed.text, detail.text)
    assert listed.json()[0]["status"] == expected
    assert detail.json()["status"] == expected
    assert row["subscription_status"] == "inactive", "the stored value is never rewritten"
