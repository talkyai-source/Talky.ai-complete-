"""QA F13-15: an operator can dismiss one needs_review payment receipt, audited.

Offline: a small in-memory store answers the exact SQL the code issues. The
real-PostgreSQL migration test is tests/integration/test_webhook_receipt_dismissed_pg.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.services import billing_webhook_reconciliation as recon_mod
from app.domain.services import billing_webhooks as hooks_mod
from app.domain.services.billing_webhook_reconciliation import BillingReconciliation
from app.domain.services.billing_webhooks import BillingWebhookProcessor, BillingWebhookReviewRequired

BACKEND = Path(__file__).resolve().parents[2]
REASON = "QA test receipt, reviewed; no replay wanted."


class Store:
    def __init__(self, states):
        self.rows = {eid: {"event_id": eid, "state": st, "event_type": "invoice.paid",
                           "provider_mode": "test", "payload_hash": None, "last_error_code": None,
                           "attempt_count": 0, "tenant_id": None, "legacy_claim": False}
                     for eid, st in states.items()}
        self.log = []
        self.updates = 0

    def snapshot(self):
        return json.dumps(self.rows, sort_keys=True), list(self.log)


class Conn:
    def __init__(self, store, fail_update=False):
        self.s, self.fail_update = store, fail_update

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_xact_lock" in sql:
            return True
        if "state='needs_review'" in sql and "count(*)" in sql:  # healthwatch's exact query
            return sum(1 for r in self.s.rows.values() if r["state"] == "needs_review")
        raise AssertionError(sql)

    async def fetchrow(self, sql, eid):
        assert "FOR UPDATE" in sql
        row = self.s.rows.get(eid)
        return dict(row) if row else None

    async def fetch(self, sql, *args):
        if "RETURNING event_id" in sql and "state='dismissed'" in sql:
            eid = args[0]
            row = self.s.rows.get(eid)
            if self.fail_update or not row or row["state"] != "needs_review":
                return []
            row["state"] = "dismissed"
            self.s.updates += 1
            return [{"event_id": eid}]
        if "FROM tenants" in sql:
            return []
        if "FROM processed_webhook_events" in sql and "NOT IN ('completed','dismissed')" in sql:
            return [dict(r) for r in self.s.rows.values() if r["state"] not in ("completed", "dismissed")]
        if "billing_webhook_notifications" in sql:
            return []
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "INSERT INTO billing_webhook_review_log" in sql:
            assert "'dismiss'" in sql
            self.s.log.append({"event_id": args[0], "operator": args[1], "reason": args[2]})
        elif "INSERT INTO processed_webhook_events" in sql:
            pass  # ON CONFLICT DO NOTHING: the receipt already exists
        elif sql.startswith("SET LOCAL"):
            pass
        else:
            raise AssertionError(sql)


@pytest.fixture
def store(monkeypatch):
    s = Store({"evt_a": "needs_review", "evt_b": "needs_review", "evt_done": "completed",
               "evt_failed": "failed"})

    @asynccontextmanager
    async def acquire(pool, tenant, **kw):
        assert tenant is None
        yield Conn(s, fail_update=getattr(s, "fail", False))

    monkeypatch.setattr(recon_mod, "acquire_with_tenant", acquire)
    monkeypatch.setattr(hooks_mod, "acquire_with_tenant", acquire)
    return s


def recon():
    return BillingReconciliation(object(), object())


async def test_list_shows_only_unresolved_and_never_dismissed_or_sensitive_fields(store):
    await recon().dismiss("evt_a", operator="op", reason=REASON)
    listed = await recon().list_unresolved()
    ids = {r["event_id"] for r in listed["events"]}
    assert ids == {"evt_b", "evt_failed"}
    assert not ({"event_payload", "recipient", "email"} & set().union(*(r.keys() for r in listed["events"])))


async def test_dismiss_changes_exactly_one_row_and_writes_the_audit_record(store):
    result = await recon().dismiss("evt_a", operator=" ops-1 ", reason=REASON)
    assert result == {"event_id": "evt_a", "state": "dismissed", "changed": True,
                      "financial_effects_applied": False}
    assert store.rows["evt_a"]["state"] == "dismissed"
    assert store.rows["evt_b"]["state"] == "needs_review" and store.updates == 1
    assert store.log == [{"event_id": "evt_a", "operator": "ops-1", "reason": REASON}]


@pytest.mark.parametrize("operator,reason", [("", REASON), ("  ", REASON), ("op", ""), ("op", "short"),
                                             ("op", "elevenchars"), ("x" * 201, REASON)])
async def test_missing_operator_or_short_reason_is_refused_and_changes_nothing(store, operator, reason):
    before = store.snapshot()
    with pytest.raises(ValueError, match="operator and substantive"):
        await recon().dismiss("evt_a", operator=operator, reason=reason)
    assert store.snapshot() == before


@pytest.mark.parametrize("event_id", ["evt_done", "evt_failed", "evt_missing"])
async def test_a_receipt_that_is_not_needs_review_is_refused(store, event_id):
    before = store.snapshot()
    with pytest.raises(ValueError):
        await recon().dismiss(event_id, operator="op", reason=REASON)
    assert store.snapshot() == before


async def test_second_run_is_a_noop_and_adds_no_audit_row(store):
    await recon().dismiss("evt_a", operator="op", reason=REASON)
    after_first = store.snapshot()
    again = await recon().dismiss("evt_a", operator="op", reason=REASON)
    assert again["changed"] is False and again["state"] == "dismissed"
    assert store.snapshot() == after_first


async def test_zero_rows_changed_fails_after_the_audit_insert_so_the_transaction_rolls_back(store):
    store.fail = True
    with pytest.raises(ValueError, match="Exactly one receipt"):
        await recon().dismiss("evt_a", operator="op", reason=REASON)
    # The real connection is inside acquire_with_tenant's transaction, so the
    # raised error rolls the audit insert back; the receipt itself is untouched.
    assert store.rows["evt_a"]["state"] == "needs_review"


async def test_healthwatch_query_no_longer_counts_a_dismissed_receipt(store):
    spec = importlib.util.spec_from_file_location("qa_hw_receipts", BACKEND / "scripts/healthwatch.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)

    @asynccontextmanager
    async def acquire(pool, tenant, **kw):
        yield Conn(store)

    watch.acquire_with_tenant = acquire
    assert (await watch.check_database(object(), None, now=None))["payments"] == {"status": "alert", "needs_review": 2}
    await recon().dismiss("evt_a", operator="op", reason=REASON)
    assert (await watch.check_database(object(), None, now=None))["payments"] == {"status": "alert", "needs_review": 1}
    await recon().dismiss("evt_b", operator="op", reason=REASON)
    assert (await watch.check_database(object(), None, now=None))["payments"] == {"status": "ok", "needs_review": 0}


def _event():
    return {"id": "evt_a", "type": "invoice.paid", "livemode": False, "created": 1700000000,
            "api_version": "2025-09-30.clover", "data": {"object": {"id": "in_1", "object": "invoice"}}}


async def test_redelivery_for_a_dismissed_receipt_is_acknowledged_not_processed(store):
    await recon().dismiss("evt_a", operator="op", reason=REASON)
    handler = AsyncMock()
    before = store.snapshot()
    result = await BillingWebhookProcessor(object()).process(_event(), handler)
    assert result["status"] == "duplicate" and result["event_id"] == "evt_a"
    handler.assert_not_awaited()
    assert store.snapshot() == before and store.rows["evt_a"]["state"] == "dismissed"


async def test_redelivery_for_a_needs_review_receipt_still_requires_review(store):
    handler = AsyncMock()
    with pytest.raises(BillingWebhookReviewRequired):
        await BillingWebhookProcessor(object()).process(_event() | {"id": "evt_b"}, handler)
    handler.assert_not_awaited()


def test_failure_recording_can_never_overwrite_a_dismissed_receipt():
    source = (BACKEND / "app/domain/services/billing_webhooks.py").read_text(encoding="utf-8")
    assert "state NOT IN ('completed','dismissed')" in source


def test_cli_exposes_dismiss_with_required_operator_and_reason_and_no_bulk_mode(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("qa_cli", BACKEND / "scripts/reconcile_billing_webhooks.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    seen = []
    monkeypatch.setattr(cli.asyncio, "run", lambda coro: (coro.close(), seen.append(1)))
    monkeypatch.setattr("sys.argv", ["x", "dismiss", "--event-id", "evt_a", "--operator", "o", "--reason", REASON])
    cli.main()
    assert seen
    for argv in (["dismiss", "--event-id", "evt_a"], ["dismiss", "--all", "--operator", "o", "--reason", REASON]):
        monkeypatch.setattr("sys.argv", ["x", *argv])
        with pytest.raises(SystemExit):
            cli.main()


def test_migration_0095_upgrade_adds_dismissed_and_downgrade_restores_the_old_check(monkeypatch):
    migration = importlib.import_module("Alembic.versions.0095_webhook_receipt_dismissed_state")
    assert migration.down_revision == "0094_harden_legacy_db_objects" and len(migration.revision) <= 32
    for operation in ("upgrade", "downgrade"):
        statements = []
        monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
        getattr(migration, operation)()
        sql = " ".join(statements)
        assert "pg_get_constraintdef(oid) LIKE '%legacy_unverified%'" in sql
        if operation == "upgrade":
            assert "'needs_review','dismissed'))" in sql and "UPDATE" not in sql
        else:
            assert sql.index("SET state='needs_review' WHERE state='dismissed'") < sql.index("DROP CONSTRAINT")
            assert "dismissed'))" not in sql and "'needs_review'))" in sql
