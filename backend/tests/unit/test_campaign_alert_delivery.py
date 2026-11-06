"""F3-08: durable, tenant-owned alerts without real mail or browser activity.

The adapter executes SQL against SQLite, translating only PostgreSQL parameters,
casts, row-lock suffixes, timestamps and validated transaction-local GUCs. It is
not PostgreSQL locking evidence. Unknown business SQL is executed, never accepted
by a blanket fake. Provider delivery is always injected.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import FastAPI
import httpx
import pytest

from app.domain.services.minutes_quota import MinutesStatus


TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
OTHER = "bbeeffaa-1234-4abc-8def-0123456789ab"
CAMPAIGN = "ccddeeff-1234-4abc-8def-0123456789ab"
FOREIGN = "ddeeffaa-1234-4abc-8def-0123456789ab"
LEAD = "eeffbbcc-1234-4abc-8def-0123456789ab"
NOW = "2026-11-06T12:00:00+00:00"


@pytest.fixture
def alerts():
    name = "app.domain.services.campaign_alerts"
    assert importlib.util.find_spec(name) is not None, (
        "F3-08 has no durable campaign/low-minute/lead alert and email outbox service"
    )
    return importlib.import_module(name)


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("gen_random_uuid", 0, lambda: str(uuid4()))
        self.db.executescript("""
        CREATE TABLE tenants(id TEXT PRIMARY KEY);
        CREATE TABLE campaigns(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,name TEXT,status TEXT);
        CREATE TABLE calls(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,campaign_id TEXT,
          lead_id TEXT,status TEXT,outcome TEXT,direction TEXT DEFAULT 'outbound',
          is_test INTEGER DEFAULT 0,terminal_settled_at TEXT,campaign_alert_processed_at TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,ended_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE leads(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,campaign_id TEXT,
          is_lead INTEGER DEFAULT 0,qualified_call_id TEXT,qualified_at TEXT,
          first_name TEXT,last_name TEXT,phone_number TEXT,follow_up_note TEXT);
        CREATE TABLE user_profiles(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,email TEXT,
          role TEXT,is_active INTEGER DEFAULT 1);
        CREATE TABLE campaign_alert_state(tenant_id TEXT NOT NULL,campaign_id TEXT NOT NULL,
          failure_count INTEGER NOT NULL DEFAULT 0,failure_episode TEXT,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(tenant_id,campaign_id));
        CREATE TABLE tenant_alert_state(tenant_id TEXT PRIMARY KEY,
          low_minutes_active INTEGER NOT NULL DEFAULT 0,low_minutes_episode TEXT,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE security_events(event_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
          event_type TEXT,severity TEXT,status TEXT DEFAULT 'open',title TEXT,
          description TEXT,evidence TEXT,alert_type TEXT,detection_source TEXT,
          assigned_to TEXT,resolved_at TEXT,resolution_notes TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE campaign_alert_outbox(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
          event_id TEXT NOT NULL REFERENCES security_events(event_id),delivery_key TEXT NOT NULL,
          kind TEXT NOT NULL,recipient TEXT,subject TEXT,body TEXT,status TEXT DEFAULT 'pending',
          attempt_count INTEGER DEFAULT 0,claim_token TEXT,started_at TEXT,accepted_at TEXT,
          last_error_code TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(tenant_id,delivery_key));
        """)
        self.db.executemany("INSERT INTO tenants VALUES(?)", [(TENANT,), (OTHER,)])
        self.db.executemany("INSERT INTO campaigns VALUES(?,?,?,?)", [
            (CAMPAIGN, TENANT, "Own campaign", "running"),
            (FOREIGN, OTHER, "Foreign campaign", "running"),
        ])
        self.db.executemany("INSERT INTO user_profiles VALUES(?,?,?,?,?)", [
            (str(uuid4()), TENANT, "own@example.invalid", "tenant_admin", 1),
            (str(uuid4()), OTHER, "foreign@example.invalid", "tenant_admin", 1),
        ])
        self.sql = []
        self.fail_on = None
        self.lock = asyncio.Lock()
        self.in_transaction = False

    def call(self, number, outcome="failed", *, tenant=TENANT, campaign=CAMPAIGN,
             direction="outbound", is_test=False, settled=True):
        identity = str(uuid5(NAMESPACE_URL, f"campaign-alert-fixture:{number}"))
        self.db.execute("INSERT INTO calls(id,tenant_id,campaign_id,status,outcome,direction,is_test,terminal_settled_at) VALUES(?,?,?,?,?,?,?,?)",
                        (identity, tenant, campaign, "completed", outcome, direction, is_test, NOW if settled else None))
        return identity

    def qualified(self, call_id, *, tenant=TENANT, campaign=CAMPAIGN, qualified=True):
        self.db.execute("INSERT INTO leads VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (LEAD, tenant, campaign, qualified, call_id, NOW,
                         "PRIVATE CONTACT", "PERSON", "+15555550111", "PRIVATE FOLLOWUP"))
        self.db.execute("UPDATE calls SET lead_id=? WHERE id=?", (LEAD, call_id))

    def rows(self, table):
        assert table in {"calls", "security_events", "campaign_alert_outbox", "campaign_alert_state", "tenant_alert_state"}
        return [dict(row) for row in self.db.execute(f"SELECT * FROM {table}")]

    def _query(self, sql, args):
        flat = " ".join(sql.split())
        self.sql.append((flat, args))
        if self.fail_on and self.fail_on in flat:
            raise RuntimeError("synthetic durable write failure")
        if re.fullmatch(r"SET LOCAL app\.(?:current_tenant_id|bypass_rls) = '[a-z0-9-]+'", flat):
            return None
        if re.fullmatch(r"SET LOCAL (?:statement_timeout|lock_timeout) = '[1-9][0-9]*ms'", flat):
            return None
        if flat == "SELECT set_config('statement_timeout', $1, true)" or flat == "SELECT set_config('lock_timeout', $1, true)":
            assert len(args) == 1 and int(str(args[0]).removesuffix("ms")) > 0
            return None
        # Exact lock statement is transaction serialization in the adapter.
        if flat.startswith("SELECT pg_advisory_xact_lock("):
            assert self.in_transaction
            return None
        sql = re.sub(r"::(?:uuid|jsonb|text|timestamptz|integer|boolean|int)\b", "", sql)
        sql = re.sub(r"\bFOR UPDATE(?: SKIP LOCKED)?\b", "", sql, flags=re.I)
        sql = re.sub(r"now\(\)\s*-\s*interval '5 minutes'", "datetime(CURRENT_TIMESTAMP, '-5 minutes')", sql, flags=re.I)
        sql = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
        bound = []
        def parameter(match):
            value = args[int(match[1]) - 1]
            if isinstance(value, (UUID, datetime)):
                value = str(value)
            elif isinstance(value, (dict, list)):
                value = json.dumps(value)
            bound.append(value)
            return "?"
        sql = re.sub(r"\$(\d+)", parameter, sql)
        return self.db.execute(sql, bound)

    async def execute(self, sql, *args):
        cursor = self._query(sql, args)
        return f"UPDATE {cursor.rowcount}" if cursor is not None else "SELECT 1"

    async def fetch(self, sql, *args):
        cursor = self._query(sql, args)
        result = []
        for raw in ([] if cursor is None else cursor.fetchall()):
            row = dict(raw)
            for name in ("created_at", "updated_at", "started_at", "resolved_at", "accepted_at", "qualified_at", "ended_at", "terminal_settled_at"):
                if row.get(name):
                    row[name] = datetime.fromisoformat(row[name]).replace(tzinfo=timezone.utc)
            result.append(row)
        return result

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        row = await self.fetchrow(sql, *args)
        return next(iter(row.values())) if row else None

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            self.db.execute("BEGIN")
            self.in_transaction = True
            try:
                yield self
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")
            finally:
                self.in_transaction = False

    @asynccontextmanager
    async def acquire(self):
        yield self


@pytest.fixture
def store():
    result = Store()
    yield result
    result.db.close()


async def terminal(alerts, store, call_id, *, tenant=TENANT, threshold=5):
    async with store.transaction():
        await alerts.record_terminal_call(store, tenant, call_id, threshold=threshold)


async def low(alerts, store, remaining, allocated=200, *, state="known", tenant=TENANT):
    value = MinutesStatus(allocated, None if remaining is None else allocated - remaining,
                          remaining, state == "unlimited", remaining == 0, state)
    async with store.transaction():
        await alerts.record_low_minutes(store, tenant, value)


async def seed_alert(alerts, store):
    await low(alerts, store, 19)
    assert len(store.rows("campaign_alert_outbox")) == 1


@pytest.mark.asyncio
async def test_failure_threshold_episode_reset_and_nonadjacent_duplicate(alerts, store):
    ids = [store.call(n) for n in range(12)]
    for identity in ids[:4]:
        await terminal(alerts, store, identity)
    assert not store.rows("security_events")
    await terminal(alerts, store, ids[4])
    await terminal(alerts, store, ids[0])  # not merely last-call deduplication
    await terminal(alerts, store, ids[5])
    assert len(store.rows("security_events")) == len(store.rows("campaign_alert_outbox")) == 1
    assert store.rows("campaign_alert_state")[0]["failure_count"] == 6
    await terminal(alerts, store, store.call("success", "answered"))
    for identity in ids[6:11]:
        await terminal(alerts, store, identity)
    assert len(store.rows("security_events")) == 2
    assert len({row["delivery_key"] for row in store.rows("campaign_alert_outbox")}) == 2


@pytest.mark.asyncio
async def test_duplicate_terminal_workers_only_increment_once(alerts, store):
    call_id = store.call(1)
    await asyncio.gather(*(terminal(alerts, store, call_id, threshold=1) for _ in range(5)))
    assert len(store.rows("security_events")) == 1
    assert store.rows("campaign_alert_state")[0]["failure_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["inbound", "test", "foreign", "missing", "not_terminal", "campaign_mismatch"])
async def test_non_campaign_or_unowned_work_cannot_count(alerts, store, kind):
    kwargs = {"direction": "inbound"} if kind == "inbound" else {}
    if kind == "test":
        kwargs["is_test"] = True
    if kind == "foreign":
        kwargs.update(tenant=OTHER, campaign=FOREIGN)
    if kind == "campaign_mismatch":
        kwargs["campaign"] = FOREIGN
    call_id = store.call(kind, **kwargs) if kind != "missing" else str(uuid4())
    if kind == "not_terminal":
        store.db.execute("UPDATE calls SET status='initiated',outcome=NULL,terminal_settled_at=NULL WHERE id=?", (call_id,))
    await terminal(alerts, store, call_id, threshold=1)
    assert store.rows("security_events") == []
    assert store.rows("campaign_alert_state") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["answered", "goal_achieved", "goal_not_achieved", "voicemail"])
async def test_connected_outcome_resets_failure_streak(alerts, store, outcome):
    await terminal(alerts, store, store.call(1), threshold=2)
    await terminal(alerts, store, store.call(2, outcome), threshold=2)
    await terminal(alerts, store, store.call(3), threshold=2)
    assert store.rows("security_events") == []
    assert store.rows("campaign_alert_state")[0]["failure_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining,allocated,expected", [(19, 200, 1), (20, 200, 0), (21, 300, 1), (30, 300, 0), (0, 0, 1)])
async def test_low_minutes_uses_strict_or_boundaries(alerts, store, remaining, allocated, expected):
    await low(alerts, store, remaining, allocated)
    assert len(store.rows("security_events")) == expected
    assert len(store.rows("campaign_alert_outbox")) == expected


@pytest.mark.asyncio
async def test_low_minutes_unknown_does_not_rearm_but_known_replenishment_does(alerts, store):
    await low(alerts, store, 19)
    await low(alerts, store, None, state="unavailable")
    await low(alerts, store, 18)
    assert len(store.rows("security_events")) == 1
    await low(alerts, store, 100)
    await low(alerts, store, 19)
    assert len(store.rows("security_events")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unlimited", "unavailable"])
async def test_nonfinite_meter_cannot_fabricate_low_alert(alerts, store, state):
    await low(alerts, store, None, state=state)
    assert store.rows("security_events") == []
    assert store.rows("campaign_alert_outbox") == []


@pytest.mark.asyncio
async def test_qualified_lead_is_durable_private_minimal_and_deduplicated(alerts, store):
    call_id = store.call(1, "answered")
    store.qualified(call_id)
    for _ in range(2):
        async with store.transaction():
            await alerts.record_qualified_lead(store, TENANT, LEAD, call_id)
    assert len(store.rows("security_events")) == len(store.rows("campaign_alert_outbox")) == 1
    serialized = json.dumps(store.rows("campaign_alert_outbox") + store.rows("security_events"))
    assert "PRIVATE CONTACT" not in serialized and "+15555550111" not in serialized
    assert "PRIVATE FOLLOWUP" not in serialized
    assert store.rows("campaign_alert_outbox")[0]["recipient"] == "own@example.invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["foreign", "unqualified", "wrong_call"])
async def test_qualified_lead_requires_owned_qualification_identity(alerts, store, kind):
    call_id = store.call(1, "answered", tenant=OTHER if kind == "foreign" else TENANT,
                         campaign=FOREIGN if kind == "foreign" else CAMPAIGN)
    store.qualified(call_id, tenant=OTHER if kind == "foreign" else TENANT,
                    campaign=FOREIGN if kind == "foreign" else CAMPAIGN,
                    qualified=kind != "unqualified")
    async with store.transaction():
        await alerts.record_qualified_lead(store, TENANT, LEAD, str(uuid4()) if kind == "wrong_call" else call_id)
    assert store.rows("security_events") == []


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_inapp_and_call_claim(alerts, store):
    call_id = store.call(1)
    store.fail_on = "INSERT INTO campaign_alert_outbox"
    with pytest.raises(RuntimeError, match="synthetic durable"):
        await terminal(alerts, store, call_id, threshold=1)
    assert store.rows("security_events") == []
    assert store.rows("campaign_alert_state") == []
    assert store.rows("calls")[0]["campaign_alert_processed_at"] is None


def mail_service(store, response=None, error=None):
    async def send(**kwargs):
        assert not store.in_transaction, "External mail must not hold the alert transaction"
        if error:
            raise error
        return {"status": "success"} if response is None else response
    return SimpleNamespace(email_enabled=True, email_provider="smtp", smtp_host="mail.invalid",
        smtp_user="fixture", smtp_password="synthetic-only", send_email=AsyncMock(side_effect=send))


@pytest.mark.asyncio
async def test_concurrent_delivery_accepts_once_and_never_uses_foreign_recipient(alerts, store):
    await seed_alert(alerts, store)
    service = mail_service(store)
    await asyncio.gather(*(alerts.deliver_tenant_alerts(store, TENANT, service=service, limit=10) for _ in range(3)))
    assert service.send_email.await_count == 1
    assert service.send_email.call_args.kwargs["to_email"] == "own@example.invalid"
    row = store.rows("campaign_alert_outbox")[0]
    assert row["status"] == "accepted" and row["attempt_count"] == 1
    await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    assert service.send_email.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unknown", "timeout", "cancel", "persistence_failure"])
async def test_uncertain_send_never_becomes_retryable(alerts, store, outcome):
    await seed_alert(alerts, store)
    error = asyncio.TimeoutError() if outcome == "timeout" else asyncio.CancelledError() if outcome == "cancel" else None
    service = mail_service(store, response={"status": "error"}, error=error)
    if outcome == "persistence_failure":
        original = service.send_email.side_effect
        async def fail_after_send(**kwargs):
            await original(**kwargs)
            store.fail_on = "UPDATE campaign_alert_outbox"
            return {"status": "success"}
        service.send_email.side_effect = fail_after_send
    try:
        await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    except (asyncio.CancelledError, RuntimeError):
        assert outcome in {"cancel", "persistence_failure"}
    store.fail_on = None
    assert store.rows("campaign_alert_outbox")[0]["status"] in {"sending", "unknown"}
    await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    assert service.send_email.await_count == 1, "Uncertain acceptance is never permission to resend"


@pytest.mark.asyncio
async def test_missing_recipient_is_not_delivered_or_global_admin_fallback(alerts, store):
    store.db.execute("DELETE FROM user_profiles WHERE tenant_id=?", (TENANT,))
    await seed_alert(alerts, store)
    service = mail_service(store)
    await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    assert service.send_email.await_count == 0
    assert store.rows("campaign_alert_outbox")[0]["status"] == "recipient_missing"
    assert len(store.rows("security_events")) == 1


@pytest.mark.asyncio
async def test_ambiguous_owner_does_not_guess_recipient(alerts, store):
    store.db.execute("INSERT INTO user_profiles VALUES(?,?,?,?,?)",
                     (str(uuid4()), TENANT, "second@example.invalid", "tenant_admin", 1))
    await seed_alert(alerts, store)
    service = mail_service(store)
    await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    assert service.send_email.await_count == 0
    assert store.rows("campaign_alert_outbox")[0]["status"] == "recipient_missing"


@pytest.mark.asyncio
async def test_crashed_sending_claim_is_unknown_not_requeued(alerts, store):
    await seed_alert(alerts, store)
    store.db.execute("UPDATE campaign_alert_outbox SET status='sending',started_at='2000-01-01 00:00:00',attempt_count=1")
    service = mail_service(store)
    await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    assert service.send_email.await_count == 0
    assert store.rows("campaign_alert_outbox")[0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_disabled_email_is_explicit_presend_failure(alerts, store):
    await seed_alert(alerts, store)
    service = mail_service(store)
    service.email_enabled = False
    await alerts.deliver_tenant_alerts(store, TENANT, service=service)
    assert service.send_email.await_count == 0
    assert store.rows("campaign_alert_outbox")[0]["status"] == "failed_before_send"


@pytest.mark.asyncio
async def test_existing_alert_route_exposes_only_owned_durable_alerts(alerts, store):
    from app.api.v1 import dependencies
    from app.api.v1.endpoints import alerts as api
    await low(alerts, store, 19)
    await low(alerts, store, 19, tenant=OTHER)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[dependencies.get_current_user] = lambda: SimpleNamespace(tenant_id=TENANT, id=LEAD)
    app.dependency_overrides[dependencies.get_db_client] = lambda: SimpleNamespace(pool=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture.invalid") as client:
        response = await client.get("/api/v1/alerts")
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    owned = [row["event_id"] for row in store.rows("security_events") if row["tenant_id"] == TENANT]
    assert [row["id"] for row in response.json()["items"]] == owned


@pytest.mark.asyncio
async def test_tick_uses_canonical_meter_and_bounded_directory_pages(alerts, store, monkeypatch):
    meter = AsyncMock(return_value=MinutesStatus(200, 181, 19, False, False))
    monkeypatch.setattr(alerts, "compute_minutes_status", meter)
    service = mail_service(store)
    cursor = await alerts.process_alert_tick(store, limit=1, service=service)
    assert meter.await_count == 1
    assert len(store.rows("security_events")) == 1
    assert cursor == TENANT
    cursor = await alerts.process_alert_tick(store, after_tenant=cursor, limit=1, service=service)
    assert meter.await_count == 2 and cursor == OTHER
    assert len(store.rows("security_events")) == 2
    assert {call.args[1] for call in meter.call_args_list} == {TENANT, OTHER}


@pytest.mark.asyncio
async def test_actual_qualification_emitter_writes_durable_intent_before_best_effort_stream(monkeypatch):
    from app.domain.services.call_summary import store as producer
    from app.domain.services import event_emitter
    intent = AsyncMock()
    monkeypatch.setattr(producer, "record_qualified_lead", intent, raising=False)
    stream = AsyncMock(side_effect=RuntimeError("synthetic stream unavailable"))
    monkeypatch.setattr(event_emitter, "emit_event", stream)
    conn = object()
    call_id = str(uuid4())
    await producer._emit_qualified_lead_alert(conn, TENANT, call_id,
        {"lead_id": LEAD, "campaign_id": CAMPAIGN, "first_name": "Private"}, "Private followup")
    intent.assert_awaited_once_with(conn, TENANT, LEAD, call_id)
    stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_qualification_durable_write_failure_is_not_swallowed(monkeypatch):
    from app.domain.services.call_summary import store as producer
    from app.domain.services import event_emitter
    intent = AsyncMock(side_effect=RuntimeError("synthetic durable intent unavailable"))
    monkeypatch.setattr(producer, "record_qualified_lead", intent, raising=False)
    stream = AsyncMock()
    monkeypatch.setattr(event_emitter, "emit_event", stream)
    with pytest.raises(RuntimeError, match="durable intent unavailable"):
        await producer._emit_qualified_lead_alert(object(), TENANT, str(uuid4()),
            {"lead_id": LEAD, "campaign_id": CAMPAIGN}, "Private followup")
    stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_terminal_settlement_hooks_once_before_commit_marker(monkeypatch):
    from app.domain.services import call_service as producer
    from app.core.security.tenant_isolation import clear_tenant_context, set_bypass_rls
    from app.domain.models.dialer_job import CallOutcome
    # Reuse the existing precise terminal-settlement fixture, not a fake copy of
    # the product workflow. Its fixture is evidence for invocation/order only;
    # the SQL-executing tests above cover durable state and rollback.
    path = Path(__file__).with_name("test_call_service_pooled_teardown.py")
    spec = importlib.util.spec_from_file_location("campaign_alert_terminal_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    call_id = str(uuid4())
    conn = fixture._FakeConn(calls_row={"id": call_id, "tenant_id": TENANT,
        "lead_id": None, "campaign_id": CAMPAIGN, "dialer_job_id": None,
        "phone_number": "+14155552671",
        "direction": "outbound", "is_test": False, "status": "initiated"})
    async def record(actual_conn, tenant_id, actual_call, **kwargs):
        assert actual_conn is conn and tenant_id == TENANT and actual_call == call_id
        assert conn.calls_row["terminal_settled_at"] is None
        assert conn.calls_row["status"] == "completed"
    spy = AsyncMock(side_effect=record)
    monkeypatch.setattr(producer, "record_terminal_call", spy, raising=False)
    set_bypass_rls(True)
    try:
        service = fixture._service(conn)
        await service._handle_call_status_pooled(call_id, CallOutcome.FAILED, "failed", 0)
        await service._handle_call_status_pooled(call_id, CallOutcome.FAILED, "failed", 0)
    finally:
        clear_tenant_context()
    spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_actual_reminder_worker_runs_alert_loop_without_browser(monkeypatch):
    from app.workers.reminder_worker import ReminderWorker
    worker = ReminderWorker()
    worker._db_pool = object()
    entered = asyncio.Event()
    async def alerts_loop():
        entered.set()
        await asyncio.Future()
    async def wait_forever():
        await asyncio.Future()
    async def due():
        await asyncio.sleep(0)
        worker.running = False
        return 0
    monkeypatch.setattr(worker, "initialize", AsyncMock())
    monkeypatch.setattr(worker, "shutdown", AsyncMock())
    monkeypatch.setattr(worker, "_campaign_alert_loop", alerts_loop, raising=False)
    for name in ("_heartbeat", "_crm_delivery_loop", "_voice_callback_loop", "_billing_delivery_loop", "_topup_usage_loop", "_connector_refresh_loop"):
        monkeypatch.setattr(worker, name, wait_forever)
    monkeypatch.setattr(worker, "_process_due_reminders", due)
    monkeypatch.setattr(worker, "POLL_INTERVAL", 0)
    await worker.run()
    assert entered.is_set(), "Reminder worker must start durable alert delivery while browsers are closed"
