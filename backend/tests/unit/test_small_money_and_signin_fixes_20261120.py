"""Stuck Stripe receipts alert, refunds after use go negative, password change keeps this device."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import fakeredis.aioredis
import pytest
from fastapi import Response

from app.domain.services import topup_usage

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


# --- item 1: a Stripe receipt that stays failed/pending alerts --------------
@pytest.fixture
def watch():
    spec = importlib.util.spec_from_file_location("small_fixes_healthwatch", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _payments(watch, monkeypatch, receipts):
    """receipts: [(state, age_hours)]; the fake evaluates the stuck statement's own states/interval."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    class Conn:
        async def fetchval(self, query, *args):
            if "to_regclass" in query:
                return False
            if "information_schema.columns" in query:
                return 2
            assert "processed_webhook_events" in query
            if "needs_review" in query:
                return sum(state == "needs_review" for state, _ in receipts)
            states = set(re.search(r"state IN \(([^)]*)\)", query).group(1).replace("'", "").replace(" ", "").split(","))
            hours = int(re.search(r"INTERVAL '(\d+) hours?'", query).group(1))
            return sum(state in states and age > hours for state, age in receipts)

        async def fetch(self, query, *args):
            return []

    @asynccontextmanager
    async def acquire(pool, tenant_id, **kwargs):
        yield Conn()

    monkeypatch.setattr(watch, "acquire_with_tenant", acquire)
    try:
        return (await watch.check_database(object(), redis, now=NOW, max_tenants=10))["payments"]
    finally:
        await redis.aclose()


async def test_stripe_receipt_failed_for_seven_hours_alerts(watch, monkeypatch):
    payments = await _payments(watch, monkeypatch, [("failed", 7)])
    assert payments["status"] == "alert" and payments["stuck"] == 1 and payments["needs_review"] == 0


async def test_stripe_receipt_pending_for_seven_hours_alerts(watch, monkeypatch):
    payments = await _payments(watch, monkeypatch, [("pending", 7)])
    assert payments["status"] == "alert" and payments["stuck"] == 1


async def test_stripe_receipt_failed_one_hour_ago_is_not_counted(watch, monkeypatch):
    payments = await _payments(watch, monkeypatch, [("failed", 1), ("completed", 30)])
    assert payments["status"] == "ok" and payments["stuck"] == 0


# --- item 2: a refund in the month of use must not give the minutes away ----
OCT = datetime(2026, 10, 1, tzinfo=timezone.utc)
NOV = datetime(2026, 11, 1, tzinfo=timezone.utc)
DEC = datetime(2026, 12, 1, tzinfo=timezone.utc)


def _window(start, end):
    return {"key": start.strftime("%Y-%m-%d"), "precise_key": start.isoformat(), "start": start, "end": end}


class LedgerConn:
    def __init__(self, ledger, used_by_window):
        self.ledger, self.used = ledger, used_by_window

    async def fetchval(self, query, *args):
        return None  # no allowance anchor: calendar months

    async def fetch(self, query, *args):
        if "FROM billing_ledger" in query:
            return [{"minutes_delta": d, "created_at": at, "provider_event_id": None, "kind": kind}
                    for kind, d, at in self.ledger]
        if "generate_series" in query:
            return [_window(OCT, NOV), _window(NOV, DEC)]
        keys = args[1]
        return [{"key": k, "seconds": self.used.get(k, 0) * 60} for k in keys]

    async def fetchrow(self, query, *args):
        return None


BUY = ("topup", 100, datetime(2026, 10, 5, tzinfo=timezone.utc))
REFUND = ("refund", -100, datetime(2026, 10, 20, tzinfo=timezone.utc))


async def test_refund_in_the_month_of_use_leaves_a_negative_balance():
    conn = LedgerConn([BUY, REFUND], {"2026-10-01": 80})
    assert await topup_usage.topup_balance(conn, "t", 0) == -80


async def test_refund_with_no_use_is_zero():
    assert await topup_usage.topup_balance(LedgerConn([BUY, REFUND], {}), "t", 0) == 0


async def test_refund_then_later_overage_does_not_double_charge():
    conn = LedgerConn([BUY, REFUND], {"2026-11-01": 50})
    assert await topup_usage.topup_balance(conn, "t", 0) == 0


async def test_purchase_without_refund_is_unchanged():
    assert await topup_usage.topup_balance(LedgerConn([BUY], {"2026-10-01": 80}), "t", 0) == 20
    # base 30 covers 30 of the 80 used minutes; only 50 come from the bought ones
    assert await topup_usage.topup_balance(LedgerConn([BUY], {"2026-10-01": 80}), "t", 30) == 50
    assert await topup_usage.topup_balance(LedgerConn([BUY], {}), "t", 0) == 100


# --- item 3: password change keeps THIS device's refresh family -------------
async def _change(monkeypatch, talky_rt, family_row):
    change = importlib.import_module("app.api.v1.endpoints.auth.password")
    from app.api.v1.endpoints.auth.schemas import ChangePasswordRequest
    seen = {"hashes": []}

    class Conn:
        async def fetchrow(self, query, *args):
            if "refresh_tokens" in query:
                seen["hashes"].append(args[0])
                return family_row
            return {"password_hash": "stored"}

        async def execute(self, *args):
            return "UPDATE 1"

        @asynccontextmanager
        async def transaction(self):
            yield

    @asynccontextmanager
    async def acquire(*args, **kwargs):
        yield Conn()

    async def revoke_refresh(conn, user_id, **kwargs):
        seen["refresh"] = kwargs
        return 1

    async def revoke_sessions(conn, user_id, **kwargs):
        return 1

    async def log(**kwargs):
        return None

    monkeypatch.setattr(change, "acquire_with_tenant", acquire)
    monkeypatch.setattr(change, "verify_password", lambda *a: True)
    monkeypatch.setattr(change, "hash_password", lambda p: "new-hash")
    monkeypatch.setattr(change, "validate_password_strength", lambda p: None)
    monkeypatch.setattr(change, "revoke_all_user_refresh_tokens", revoke_refresh)
    monkeypatch.setattr(change, "revoke_all_user_sessions", revoke_sessions)

    async def lock(conn, request, current_user):
        return {"password_hash": "stored"}

    monkeypatch.setattr(change, "lock_authenticated_credential_change", lock)
    request = NS(headers={}, client=NS(host="203.0.113.9"))
    await change.change_password(
        request, ChangePasswordRequest(old_password="old", new_password="new-secret"), Response(),
        NS(tenant_id=None, id="u"), NS(pool=None), NS(log=log), talky_sid=None, talky_rt=talky_rt)
    return seen


async def test_password_change_keeps_the_current_refresh_family(monkeypatch):
    seen = await _change(monkeypatch, "raw-rt", {"family_id": "F"})
    assert seen["refresh"]["exclude_family_id"] == "F"
    assert seen["hashes"] == [hashlib.sha256(b"raw-rt").hexdigest()]


async def test_password_change_without_a_family_revokes_everything(monkeypatch):
    assert (await _change(monkeypatch, None, None))["refresh"]["exclude_family_id"] is None
    assert (await _change(monkeypatch, "unknown-rt", None))["refresh"]["exclude_family_id"] is None
