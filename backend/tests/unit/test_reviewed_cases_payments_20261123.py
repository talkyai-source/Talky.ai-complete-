"""Cases the reviews of the payment-review panel named, now pinned.

1. a tenant's own admin is refused on both routes (real dependency, real HTTP stack);
2. the command-line dismiss still closes a young stuck receipt (failed AND pending);
3. a PayPal review that became `completed` between the list and the close is refused, nothing logged.
Case 2's panel-side rule (7 hours refused with the Stripe text, 5 days closable) is already
pinned by test_payment_reviews.py::test_the_stripe_close_refuses_a_young_stuck_row_before_dismiss_receipt.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_client
from app.api.v1.endpoints.admin import payment_reviews as ep
from tests.unit.test_payment_reviews import PG, REASON, close, pp_receipt

_TOUCHED = []


class _Pool:
    def __getattr__(self, name):
        _TOUCHED.append(f"pool.{name}")
        raise AssertionError("the database must not be touched for a non-staff caller")


def _app(role):
    app = FastAPI()
    app.include_router(ep.router, prefix="/admin")
    user = CurrentUser(id="30000000-0000-4000-8000-000000000001", email="owner@tenant.example",
                       tenant_id="20000000-0000-4000-8000-000000000001", role=role)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=_Pool())

    class Audit:
        def __getattr__(self, name):
            _TOUCHED.append(f"audit.{name}")
            raise AssertionError("audit must not be touched for a non-staff caller")

    app.dependency_overrides[get_audit_logger] = lambda: Audit()
    return app


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _TOUCHED.clear()

    def no_acquire(*a, **k):
        _TOUCHED.append("acquire_with_tenant")
        raise AssertionError("no connection may be opened")

    async def no_service(*a, **k):
        _TOUCHED.append("service")
        raise AssertionError("no service may run")

    monkeypatch.setattr(ep, "acquire_with_tenant", no_acquire)
    monkeypatch.setattr(ep, "list_payment_reviews", no_service)
    monkeypatch.setattr(ep, "close_payment_review", no_service)


@pytest.mark.parametrize("role", ["admin", "user", "member", "tenant_admin"])
async def test_a_tenants_own_admin_is_refused_on_the_list_route_and_nothing_is_read(role):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(role)), base_url="http://t.invalid") as c:
        r = await c.get("/admin/billing/payment-reviews")
    assert r.status_code == 403, r.text
    assert _TOUCHED == []


@pytest.mark.parametrize("role", ["admin", "user", "member", "tenant_admin"])
async def test_a_tenants_own_admin_is_refused_on_the_close_route_and_nothing_is_written(role):
    body = {"provider": "paypal", "event_id": "e1", "seen_attempt_count": 1, "reason": REASON}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(role)), base_url="http://t.invalid") as c:
        r = await c.post("/admin/billing/payment-reviews/close", json=body)
    assert r.status_code == 403, r.text
    assert _TOUCHED == []


# -- 2: the command-line path is looser than the panel, on purpose -----------------------


@pytest.mark.parametrize("state", ["failed", "pending"])
async def test_the_command_line_dismiss_allows_a_young_stuck_receipt_the_panel_refuses(state):
    from app.domain.services.billing_webhook_reconciliation import dismiss_receipt
    db = PG()
    db.st["e1"] = dict(state=state, attempt_count=1, age_h=7)
    with pytest.raises(ValueError, match="Stripe may still send this message again"):
        await close(db, provider="stripe")
    assert db.st["e1"]["state"] == state and db.st_log == []
    assert (await dismiss_receipt(db, "e1", operator="op", reason=REASON))["changed"] is True
    assert db.st["e1"]["state"] == "dismissed"


# -- 3: the stored state moved between the list and the close ----------------------------


@pytest.mark.parametrize("attempts_now", [1, 2])
async def test_a_paypal_review_completed_since_the_list_is_refused_and_nothing_is_logged(attempts_now):
    db = PG()
    db.pp["e1"] = pp_receipt(state="completed", attempts=attempts_now)
    with pytest.raises(ValueError):
        await close(db, seen=1)
    assert db.pp_log == [] and db.pp["e1"]["state"] == "completed"
