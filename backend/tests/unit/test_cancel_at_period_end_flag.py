"""G9: cancel_at_period_end is Stripe's own flag, not inferred from cancel_at.

GET /billing/subscription answered ``bool(cancel_at)``: a subscription Stripe
marks cancel_at_period_end=true without a cancel_at read false, and one with a
cancel_at but cancel_at_period_end=false read true. Stripe's flag is now kept
in subscriptions.metadata by the webhook sync and read back; rows synced
before that keep the old cancel_at reading.
"""
import json
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import billing as billing_ep
from app.domain.services import billing_state_events as events


class Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return "INSERT 0 1" if sql.lstrip().upper().startswith("INSERT INTO SUBSCRIPTIONS") else "UPDATE 1"


def _subscription(**overrides):
    subscription = {"id": "sub_1", "status": "active", "current_period_start": 1_700_000_000,
                    "current_period_end": 1_702_592_000, "cancel_at": None, "canceled_at": None,
                    "cancel_at_period_end": True}
    subscription.update(overrides)
    return subscription


def _binding():
    return {"tenant_id": "t-1", "customer_id": "cus_1", "current": True, "attempt": None,
            "recorded": {}, "tenant": {"plan_id": "starter"}}


@pytest.mark.parametrize("flag", [True, False])
async def test_the_webhook_sync_stores_stripes_own_flag_without_replacing_metadata(flag):
    conn = Conn()
    await events._apply_subscription(conn, None, _subscription(cancel_at_period_end=flag), _binding())
    sql, args = next(call for call in conn.calls if call[0].startswith("INSERT INTO subscriptions"))
    assert args[-1] is flag
    compact = sql.replace(" ", "")
    assert "metadata=COALESCE(subscriptions.metadata,'{}'::jsonb)||EXCLUDED.metadata" in compact, (
        "the stored purchase details in metadata must be merged, never replaced")


async def test_a_missing_provider_flag_is_stored_as_false_not_guessed():
    conn = Conn()
    subscription = _subscription()
    del subscription["cancel_at_period_end"]
    await events._apply_subscription(conn, None, subscription, _binding())
    _sql, args = next(call for call in conn.calls if call[0].startswith("INSERT INTO subscriptions"))
    assert args[-1] is False


@pytest.mark.parametrize("subscription,expected", [
    ({"metadata": {"cancel_at_period_end": True}, "cancel_at": None}, True),
    ({"metadata": {"cancel_at_period_end": False}, "cancel_at": "2026-12-01T00:00:00+00:00"}, False),
    ({"metadata": json.dumps({"cancel_at_period_end": True}), "cancel_at": None}, True),
    ({"metadata": {"purchase": {}}, "cancel_at": "2026-12-01T00:00:00+00:00"}, True),
    ({"metadata": None, "cancel_at": None}, False),
], ids=["flag-true", "flag-false-wins", "json-text", "legacy-row", "nothing"])
def test_the_read_uses_stripes_flag_and_falls_back_only_for_old_rows(subscription, expected):
    assert billing_ep._cancel_at_period_end(subscription) is expected


async def test_the_subscription_endpoint_reports_the_stored_flag(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield object()

    async def minutes(_conn, _tenant):
        return SimpleNamespace(require_available=lambda: SimpleNamespace(
            used_minutes=0, allocated=100, remaining_minutes=100, state="known"))

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.minutes_quota.compute_minutes_status", minutes)
    billing = SimpleNamespace(get_subscription=lambda _tenant: None)

    async def get_subscription(_tenant):
        return {"status": "active", "plan_id": "starter", "plans": {"name": "Starter"},
                "cancel_at": None, "metadata": {"cancel_at_period_end": True}}

    billing.get_subscription = get_subscription
    user = SimpleNamespace(id="u", tenant_id="t-1", role="tenant_admin", email="a@example.com")
    response = await billing_ep.get_subscription(current_user=user, billing=billing, db_pool=object())
    assert response.cancel_at_period_end is True
