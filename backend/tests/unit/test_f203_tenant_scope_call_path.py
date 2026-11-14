"""F2-03 (call-path slice): the dialer worker, the call settlement service and the
telephony teardown helpers name their tenant explicitly.

Production enforces row-level security, but the project rule is an explicit
``tenant_id`` predicate in the SQL itself: a role that bypasses RLS (the prod
application role did, and the dialer worker still opens every connection with
``acquire_with_tenant(pool, None)``) must not be able to read or write another
tenant's row. Every test runs real code on a fake connection that records
``(sql, args)``; where the statement is plain enough it is also executed on
SQLite beside a second tenant's rows.

Section 1 - statements that lacked a predicate and now carry one:

* the dialer worker's compliance and batch lookups (campaign calling config,
  in-flight count, lead cooldown, lead timezone, repeat-dial cap) read a lead or
  campaign by id alone although every caller holds the job's tenant;
* ``CallService`` settlement wrote the call row (terminal facts, the retry
  outbox reset, the settled marker, the outbox acknowledgement) by id alone
  although the locked row's own tenant is read one statement earlier;
* the termination helpers locked, fenced and read call state by reference alone.

Section 2 - the claims the baseline exceptions for this slice rest on. These
statements already bind the tenant through the parent call, discover the tenant
of a provider channel before it is known, or are platform sweeps by design; the
F2-03 scanner cannot prove them (``call_legs`` / ``call_events`` carry no
``tenant_id`` column, LEFT JOIN, EXISTS, ``ON CONFLICT``, dynamic fragments, an
optional platform tenant). They pass before and after the change; they exist so
a later edit that drops the binding fails here, not only in the ratchet.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.core.security.tenant_isolation import (
    clear_tenant_context,
    set_bypass_rls,
    set_current_tenant_id,
)
from app.domain.models.calling_rules import CallingRules
from app.domain.models.dialer_job import CallOutcome, DialerJob
from app.domain.services.call_service import CallService
from app.domain.services.telephony import lifecycle, termination, transfer_provider_identity
from app.workers.dialer_worker import DialerCallIntent, DialerWorker
from tests.unit.test_call_service_pooled_teardown import _FakeConn, _service
from tests.unit.test_dnc_settlement_truth import SuppressionConn

TENANT_A = UUID("a1111111-1111-4111-8111-111111111111")
TENANT_B = UUID("b2222222-2222-4222-8222-222222222222")
CAMPAIGN = "c3333333-3333-4333-8333-333333333333"
CALL = "d4444444-4444-4444-8444-444444444444"
LEAD = "e5555555-5555-4555-8555-555555555555"
JOB = "f6666666-6666-4666-8666-666666666666"
LEG = "a7777777-7777-4777-8777-777777777777"
PBX_CHANNEL = "talky-out-da0f496d-1234-4a2b-9c3d-abcdef012345"
JOB_TENANT = str(TENANT_A)
AMBIENT_TENANT = "00000000-0000-0000-0000-000000000000"


# ── shared helpers ─────────────────────────────────────────────────────────


def squash(sql: str) -> str:
    return " ".join(sql.split())


def bound_tenants(sql: str, args: tuple) -> list:
    """Every value the statement compares ``tenant_id`` with, read from its bind slots."""
    return [args[int(n) - 1] for n in re.findall(r"\btenant_id\s*=\s*\$(\d+)", sql, re.I)]


def insert_binding(sql: str, column: str, args: tuple):
    """The value an ``INSERT INTO t (cols) VALUES (slots)`` binds to ``column``."""
    columns, values = re.search(r"\(([^)]*)\)\s*VALUES\s*\((.*)\)", sql, re.I | re.S).groups()
    names = [name.strip() for name in columns.split(",")]
    slots = [slot.strip() for slot in re.split(r",\s*(?![^()]*\))", values)]
    slot = slots[names.index(column)]
    return args[int(re.match(r"\$(\d+)", slot).group(1)) - 1]


class NoTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class ScriptConn:
    """Fake asyncpg connection: records ``(sql, args)``; answers by the first matching marker."""

    def __init__(self, script=(), *, execute_result="UPDATE 1"):
        self.script = list(script)
        self.calls: list[tuple[str, tuple]] = []
        self.execute_result = execute_result

    def _answer(self, sql, args, default):
        for marker, value in self.script:
            if marker in sql:
                return value(*args) if callable(value) else value
        return default

    async def fetchrow(self, sql, *args):
        self.calls.append((squash(sql), args))
        return self._answer(squash(sql), args, None)

    async def fetch(self, sql, *args):
        self.calls.append((squash(sql), args))
        return self._answer(squash(sql), args, [])

    async def fetchval(self, sql, *args):
        self.calls.append((squash(sql), args))
        return self._answer(squash(sql), args, None)

    async def execute(self, sql, *args):
        self.calls.append((squash(sql), args))
        return self._answer(squash(sql), args, self.execute_result)

    def transaction(self):
        return NoTransaction()

    def statements(self, marker: str) -> list[tuple[str, tuple]]:
        return [(sql, args) for sql, args in self.calls if marker in sql]


class Ctx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def run_on_sqlite(db, sql: str, args: tuple):
    """Execute the recorded statement as sent: ``$n`` slots become named binds."""
    statement = re.sub(r"::[a-z_]+(?:\[\])?", "", sql, flags=re.I)
    statement = re.sub(r"\bFOR\s+(?:UPDATE|SHARE)\b", "", statement, flags=re.I)
    statement = statement.replace("NOW()", "CURRENT_TIMESTAMP")
    binds = {f"p{n}": str(a) if isinstance(a, UUID) else a for n, a in enumerate(args, 1)}
    return db.execute(re.sub(r"\$(\d+)", r":p\1", statement), binds)


def sqlite_with(schema: str, insert: str = "", rows=()):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_function("BTRIM", 1, lambda value: None if value is None else value.strip())
    db.executescript(schema)
    if insert:
        db.executemany(insert, rows)
    return db


@pytest.fixture(autouse=True)
def teardown_context():
    """Every real settlement caller sets bypass_rls (and, when known, a tenant that
    is NOT necessarily this call's) before calling ``handle_call_status``."""
    set_bypass_rls(True)
    set_current_tenant_id(AMBIENT_TENANT)
    yield
    clear_tenant_context()


# ═══════════════════════════════════════════════════════════════════════════
# 1a. dialer worker: lookups by lead / campaign id carry the job's tenant
# ═══════════════════════════════════════════════════════════════════════════


def worker_on(conn) -> DialerWorker:
    worker = DialerWorker()

    @asynccontextmanager
    async def acquire():
        yield conn

    worker._acquire_db = acquire  # type: ignore[assignment]
    return worker


def job_of(tenant=JOB_TENANT, **kw) -> DialerJob:
    base = dict(job_id=JOB, campaign_id=CAMPAIGN, lead_id=LEAD, tenant_id=str(tenant), phone_number="+447700900123")
    base.update(kw)
    return DialerJob(**base)


LOOKUPS = [
    pytest.param("_get_campaign_calling_config", CAMPAIGN, "FROM campaigns", id="campaign-calling-config"),
    pytest.param("_get_lead_last_called", LEAD, "FROM leads", id="lead-last-called"),
    pytest.param("_get_lead_timezone", LEAD, "FROM leads", id="lead-timezone"),
    pytest.param("_campaign_inflight_calls", CAMPAIGN, "FROM calls", id="campaign-inflight"),
    pytest.param("_get_lead_attempts_today", LEAD, "JOIN leads l", id="lead-attempts-today"),
]


@pytest.mark.parametrize(("method", "object_id", "marker"), LOOKUPS)
async def test_dialer_lookup_binds_the_jobs_tenant(method, object_id, marker):
    conn = ScriptConn()
    await getattr(worker_on(conn), method)(object_id, JOB_TENANT)
    ((sql, args),) = conn.calls
    assert marker in sql
    assert args[0] == object_id
    assert bound_tenants(sql, args) == [JOB_TENANT], f"no tenant predicate bound to the job's tenant\n{sql}\n{args}"


async def test_repeat_dial_count_follows_the_leads_tenant_not_just_its_number():
    conn = ScriptConn()
    await worker_on(conn)._get_lead_attempts_today(LEAD, JOB_TENANT)
    ((sql, args),) = conn.calls
    # the lead is looked up WITHIN the tenant, and the calls are counted for that same tenant
    assert re.search(r"JOIN leads l ON l\.id = \$1 AND l\.tenant_id = \$2", sql)
    assert "c.tenant_id = l.tenant_id" in sql and "c.phone_number = l.phone_number" in sql
    assert args == (LEAD, JOB_TENANT)


@pytest.mark.parametrize(
    ("method", "column", "stored"),
    [
        ("_get_lead_last_called", "last_called_at", "2026-11-01T10:00:00"),
        ("_get_lead_timezone", "timezone", "Europe/London"),
    ],
)
async def test_a_lead_of_another_tenant_is_invisible_to_the_lookup(method, column, stored):
    conn = ScriptConn()
    await getattr(worker_on(conn), method)(LEAD, JOB_TENANT)
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE leads (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, last_called_at TEXT, timezone TEXT);",
        "INSERT INTO leads VALUES (?,?,?,?)",
        [(LEAD, str(TENANT_B), stored, stored)],  # the lead id belongs to tenant B
    )
    assert run_on_sqlite(db, sql, args).fetchone() is None
    assert run_on_sqlite(db, sql, (LEAD, str(TENANT_B))).fetchone()[0] == stored


async def test_a_campaign_of_another_tenant_has_no_calling_config_for_the_job():
    conn = ScriptConn()
    await worker_on(conn)._get_campaign_calling_config(CAMPAIGN, JOB_TENANT)
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE campaigns (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, calling_config TEXT);",
        "INSERT INTO campaigns VALUES (?,?,?)",
        [(CAMPAIGN, str(TENANT_B), '{"batch_size": 99}')],
    )
    assert run_on_sqlite(db, sql, args).fetchone() is None
    assert run_on_sqlite(db, sql, (CAMPAIGN, str(TENANT_B))).fetchone()[0] == '{"batch_size": 99}'


# ═══════════════════════════════════════════════════════════════════════════
# 1b. dialer worker: every caller hands those lookups the job's tenant
# ═══════════════════════════════════════════════════════════════════════════


def harness_worker(**campaign):
    from tests.unit.test_live_risk_dialer import _OPEN_ALWAYS, _worker

    return _worker(rules=CallingRules(**_OPEN_ALWAYS), **campaign)


async def test_process_job_hands_the_jobs_tenant_to_every_lookup():
    job = job_of(phone_number="+447700900123")
    w = harness_worker()
    w._resolve_batch_size = MagicMock(return_value=3)  # run the in-flight batch gate too
    await w.process_job(job)
    w._get_campaign_calling_config.assert_awaited_once_with(CAMPAIGN, JOB_TENANT)
    w._get_lead_last_called.assert_awaited_once_with(LEAD, JOB_TENANT)
    w._get_lead_timezone.assert_awaited_once_with(LEAD, JOB_TENANT)
    w._get_lead_attempts_today.assert_awaited_once_with(LEAD, JOB_TENANT)
    w._campaign_inflight_calls.assert_awaited_once_with(CAMPAIGN, JOB_TENANT)


async def test_a_replayed_attempt_hands_the_jobs_tenant_to_its_lookups():
    job = job_of()
    w = harness_worker()
    intent = DialerCallIntent(CALL, "TK-1", LEG, "initiated", None, False)
    w._load_existing_call_intent = AsyncMock(return_value=intent)
    w._make_call = AsyncMock(return_value=w._ORIGINATION_UNCERTAIN)
    w._park_uncertain_origination = AsyncMock()
    await w.process_job(job)
    w._get_campaign_calling_config.assert_awaited_once_with(CAMPAIGN, JOB_TENANT)
    w._get_lead_timezone.assert_awaited_once_with(LEAD, JOB_TENANT)
    w._park_uncertain_origination.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# 1c. CallService settlement: every write to the call row names the call's own tenant
# ═══════════════════════════════════════════════════════════════════════════

CALL_TENANT = "20000000-0000-4000-8000-000000000017"


def call_updates(statements) -> list[tuple[str, tuple]]:
    return [
        (sql, args)
        for sql, args in statements
        if sql.startswith("UPDATE calls SET") and "campaign_alert_processed_at" not in sql
    ]


def assert_names_only_the_call_tenant(statements, expected: int):
    updates = call_updates(statements)
    assert len(updates) == expected, [sql for sql, _ in updates]
    for sql, args in updates:
        assert bound_tenants(sql, args) == [CALL_TENANT], (
            f"call row written without the call's own tenant (ambient context is {AMBIENT_TENANT})\n{sql}\n{args}"
        )


def retry_settlement_conn() -> _FakeConn:
    return _FakeConn(
        calls_row={
            "id": "call-1", "tenant_id": CALL_TENANT, "lead_id": "lead-1", "campaign_id": "camp-1",
            "dialer_job_id": "job-1", "dialer_attempt_number": 1, "status": "initiated",
        },
        leads_row={"id": "lead-1", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={
            "id": "job-1", "status": "processing", "attempt_number": 1, "tenant_id": CALL_TENANT,
            "priority": 5, "phone_number": "+15550002222",
        },
    )


def settled_service(conn) -> CallService:
    service = _service(conn)
    service._schedule_retry = AsyncMock(return_value=True)  # Redis is not under test
    service._schedule_crm_sync = lambda *_a, **_k: None
    return service


async def test_a_settled_call_with_a_retry_writes_its_row_under_its_own_tenant():
    conn = retry_settlement_conn()
    result = await settled_service(conn).handle_call_status("call-1", CallOutcome.BUSY, duration=0)
    assert result.durable, result.error
    assert conn.calls_row["terminal_retry_enqueued_at"] == "NOW", "the outbox acknowledgement did not land"
    # terminal facts, the settled marker (with its retry payload) and the outbox acknowledgement
    assert_names_only_the_call_tenant(conn.executed, expected=3)


async def test_a_settled_call_without_a_retry_writes_its_row_under_its_own_tenant():
    conn = retry_settlement_conn()
    result = await settled_service(conn).handle_call_status("call-1", CallOutcome.ANSWERED, duration=42)
    assert result.durable, result.error
    assert_names_only_the_call_tenant(conn.executed, expected=2)


@pytest.mark.parametrize("settled", [False, True], ids=["ended-by-operator-first", "settled-with-facts-missing"])
async def test_filling_the_missing_facts_of_an_already_terminal_call_names_its_tenant(settled):
    conn = retry_settlement_conn()
    conn.calls_row.update(status="ended", terminal_settled_at="NOW" if settled else None)
    result = await settled_service(conn).handle_call_status("call-1", CallOutcome.ANSWERED, duration=42)
    assert result.durable, result.error
    assert conn.calls_row["outcome"] == "answered"
    updates = call_updates(conn.executed)
    assert any("outcome = COALESCE(outcome" in sql for sql, _ in updates), "the fill-in update did not run"
    assert_names_only_the_call_tenant(conn.executed, expected=1 if settled else 2)


class RecordedSuppressionConn(SuppressionConn):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.statements: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.statements.append((squash(sql), args))
        return await super().execute(sql, *args)

    async def fetchrow(self, sql, *args):
        self.statements.append((squash(sql), args))
        return await super().fetchrow(sql, *args)


async def test_an_opt_out_that_cancels_a_pending_retry_resets_the_outbox_under_the_calls_tenant():
    conn = RecordedSuppressionConn(settled=True)
    assert conn.calls_row["tenant_id"] == CALL_TENANT
    execution = await _service(conn)._handle_call_status_pooled("call-1", CallOutcome.FAILED, "failed", 42)
    assert execution.retry_args is None
    assert conn.calls_row["terminal_retry_payload"] is None, "the outbox reset did not run"
    resets = [(s, a) for s, a in conn.statements if "terminal_retry_payload = NULL" in s]
    assert len(resets) == 1
    assert bound_tenants(*resets[0]) == [CALL_TENANT], resets


async def test_the_retry_acknowledgement_cannot_touch_another_tenants_call():
    conn = retry_settlement_conn()
    await settled_service(conn).handle_call_status("call-1", CallOutcome.BUSY, duration=0)
    sql, args = next((s, a) for s, a in conn.executed if "terminal_retry_enqueued_at = COALESCE" in s)
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, terminal_retry_payload TEXT,"
        " terminal_retry_enqueued_at TEXT, updated_at TEXT);",
        "INSERT INTO calls VALUES (?,?,?,NULL,NULL)",
        [("call-1", str(TENANT_B), "{}")],  # the call id belongs to tenant B
    )
    sql = re.sub(r"\s+RETURNING\b.*$", "", sql, flags=re.I | re.S)
    assert run_on_sqlite(db, sql, args).rowcount == 0
    assert run_on_sqlite(db, sql, ("call-1", str(TENANT_B))).rowcount == 1


# ═══════════════════════════════════════════════════════════════════════════
# 1d. termination helpers: the lock, the fence and the leg reads name the tenant
# ═══════════════════════════════════════════════════════════════════════════

LOCKED_ROW = {
    "call_id": CALL, "tenant_id": str(TENANT_A), "provider_call_id": "chan-1", "status": "in_call",
    "provider": "asterisk", "direction": "outbound", "campaign_id": None, "answered_at": None,
}


def patch_termination_acquire(monkeypatch, conn) -> list:
    """Replace ``acquire_with_tenant`` in termination.py; the list keeps each tenant it was given."""
    seen: list = []

    @asynccontextmanager
    async def fake(_pool, tenant_id, **_kwargs):
        seen.append(tenant_id)
        yield conn

    monkeypatch.setattr(termination, "acquire_with_tenant", fake)
    return seen


async def lock_call(monkeypatch, caller_tenant):
    conn = ScriptConn([("FOR UPDATE", dict(LOCKED_ROW))])
    acquired = patch_termination_acquire(monkeypatch, conn)
    context = await termination.mark_termination_pending_and_load_context(
        object(), call_reference="chan-1", tenant_id=caller_tenant
    )
    return conn, acquired, context


async def test_termination_lock_binds_the_callers_tenant(monkeypatch):
    conn, acquired, _ = await lock_call(monkeypatch, JOB_TENANT)
    lock_sql, lock_args = conn.calls[0]
    assert "FROM calls" in lock_sql and "FOR UPDATE" in lock_sql
    assert lock_args[0] == "chan-1"
    assert bound_tenants(lock_sql, lock_args) == [JOB_TENANT], f"the lock names no tenant\n{lock_sql}\n{lock_args}"
    assert acquired == [JOB_TENANT]


@pytest.mark.parametrize("caller_tenant", [JOB_TENANT, None], ids=["tenant-caller", "platform-caller"])
async def test_termination_fence_names_the_tenant_of_the_row_it_locked(monkeypatch, caller_tenant):
    conn, _, _ = await lock_call(monkeypatch, caller_tenant)
    ((sql, args),) = conn.statements("SET status='termination_pending'")
    assert args[0] == CALL
    assert bound_tenants(sql, args) == [str(TENANT_A)], f"the fence names no tenant\n{sql}\n{args}"


async def test_a_platform_termination_still_locks_any_tenants_call(monkeypatch):
    conn, acquired, context = await lock_call(monkeypatch, None)
    lock_sql, lock_args = conn.calls[0]
    assert bound_tenants(lock_sql, lock_args) == [None], "platform callers pass an explicit NULL tenant"
    assert acquired == [None]
    assert context.tenant_id == str(TENANT_A) and context.call_id == CALL


async def test_a_tenant_caller_cannot_lock_another_tenants_call_by_its_provider_id(monkeypatch):
    conn, _, _ = await lock_call(monkeypatch, JOB_TENANT)
    lock_sql, lock_args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, talklee_call_id TEXT, provider_call_id TEXT,"
        " external_call_uuid TEXT, status TEXT, provider TEXT, direction TEXT, campaign_id TEXT, answered_at TEXT,"
        " route_snapshot TEXT, created_at TEXT);",
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(CALL, str(TENANT_B), None, "chan-1", None, "in_call", "asterisk", "outbound", None, None, None, "2026-11-01")],
    )
    assert run_on_sqlite(db, lock_sql, lock_args).fetchone() is None
    assert run_on_sqlite(db, lock_sql, ("chan-1", str(TENANT_B))).fetchone()["call_id"] == CALL
    assert run_on_sqlite(db, lock_sql, ("chan-1", None)).fetchone()["call_id"] == CALL  # platform caller


async def test_termination_leg_reads_are_keyed_by_the_locked_calls_own_id(monkeypatch):
    conn, _, _ = await lock_call(monkeypatch, JOB_TENANT)
    legs = conn.statements("FROM call_legs")
    assert len(legs) == 2, "expected the active-leg read and the full inventory read"
    for sql, args in legs:
        # call_legs has no tenant column; ownership is the parent's, and the parent was just locked
        assert "WHERE call_id=$1::uuid" in sql and "tenant_id" not in sql
        assert args[0] == CALL


async def test_active_leg_lookup_binds_the_callers_tenant():
    conn = ScriptConn([("FROM call_legs l", [{"provider_leg_id": "leg-1", "provider": "asterisk"}])])
    legs = await termination.fetch_active_provider_leg_ids(
        conn, call_reference=CALL, expected_provider="asterisk", tenant_id=JOB_TENANT
    )
    assert legs == ("leg-1",)
    ((sql, args),) = conn.calls
    assert args == (CALL, JOB_TENANT)
    assert bound_tenants(sql, args) == [JOB_TENANT], f"the leg lookup names no tenant\n{sql}\n{args}"


async def test_active_leg_lookup_never_returns_another_tenants_legs():
    conn = ScriptConn()
    await termination.fetch_active_provider_leg_ids(
        conn, call_reference=CALL, expected_provider="asterisk", tenant_id=JOB_TENANT
    )
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, talklee_call_id TEXT, provider_call_id TEXT,"
        " external_call_uuid TEXT);"
        "CREATE TABLE call_legs (id TEXT PRIMARY KEY, call_id TEXT, provider_leg_id TEXT, provider TEXT, status TEXT,"
        " created_at TEXT);"
    )
    db.execute("INSERT INTO calls VALUES (?,?,NULL,NULL,NULL)", (CALL, str(TENANT_B)))
    db.execute("INSERT INTO call_legs VALUES ('leg-row', ?, 'leg-1', 'asterisk', 'active', '2026-11-01')", (CALL,))
    assert run_on_sqlite(db, sql, args).fetchall() == []
    assert len(run_on_sqlite(db, sql, (CALL, str(TENANT_B))).fetchall()) == 1


async def test_the_pool_level_leg_loader_hands_its_tenant_to_the_query(monkeypatch):
    conn = ScriptConn([("FROM call_legs l", [{"provider_leg_id": "leg-1", "provider": "asterisk"}])])
    acquired = patch_termination_acquire(monkeypatch, conn)
    legs = await termination.load_active_provider_leg_ids(
        object(), call_reference=CALL, expected_provider="asterisk", tenant_id=JOB_TENANT
    )
    assert legs == ("leg-1",) and acquired == [JOB_TENANT]
    ((sql, args),) = conn.calls
    assert bound_tenants(sql, args) == [JOB_TENANT]


TERMINAL_FACTS = {
    "answered_at": datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    "provider_terminated_at": datetime(2026, 9, 2, 12, 0, 5, tzinfo=timezone.utc),
    "duration_seconds": 5,
    "reserved_seconds": 60,
}


async def test_persisted_terminal_duration_read_binds_the_callers_tenant(monkeypatch):
    conn = ScriptConn([("FROM calls", dict(TERMINAL_FACTS))])
    acquired = patch_termination_acquire(monkeypatch, conn)
    duration = await termination._load_persisted_inbound_terminal_duration(
        object(), durable_call_id=CALL, provider_call_id="chan-1", tenant_id=JOB_TENANT
    )
    assert duration == 5 and acquired == [JOB_TENANT]
    ((sql, args),) = conn.calls
    assert args[:2] == (CALL, "chan-1")
    assert bound_tenants(sql, args) == [JOB_TENANT], f"the duration read names no tenant\n{sql}\n{args}"


async def test_persisted_terminal_duration_never_reads_another_tenants_call(monkeypatch):
    conn = ScriptConn([("FROM calls", dict(TERMINAL_FACTS))])
    patch_termination_acquire(monkeypatch, conn)
    await termination._load_persisted_inbound_terminal_duration(
        object(), durable_call_id=CALL, provider_call_id="chan-1", tenant_id=JOB_TENANT
    )
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, direction TEXT, provider_call_id TEXT,"
        " external_call_uuid TEXT, answered_at TEXT, provider_terminated_at TEXT, duration_seconds INTEGER,"
        " reserved_seconds INTEGER);",
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?)",
        [(CALL, str(TENANT_B), "inbound", "chan-1", None, "t0", "t1", 5, 60)],
    )
    assert run_on_sqlite(db, sql, args).fetchone() is None
    assert run_on_sqlite(db, sql, (CALL, "chan-1", str(TENANT_B))).fetchone()["duration_seconds"] == 5


# ═══════════════════════════════════════════════════════════════════════════
# 1e. restart recovery: the leg lookup is scoped to the tenant of the call it located
# ═══════════════════════════════════════════════════════════════════════════


def install_db(monkeypatch, conn):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    monkeypatch.setattr(
        container_module, "get_container", lambda: SimpleNamespace(is_initialized=True, db_pool=object())
    )
    monkeypatch.setattr(db_utils, "acquire_with_tenant", lambda *_a, **_k: Ctx(conn))


def recovery_row(**over):
    from tests.unit.test_outbound_recovery_duration_billing import _row

    return _row(id=CALL, tenant_id=str(TENANT_A), **over)


async def test_orphan_hydration_scopes_its_leg_lookup_to_the_located_calls_tenant(monkeypatch):
    conn = ScriptConn([("FROM calls c", recovery_row()), ("FROM call_legs", [])])
    install_db(monkeypatch, conn)
    await lifecycle._hydrate_orphan_recovery_context("pbx-out", {"state": "active", "provider": "asterisk"})
    ((sql, args),) = conn.statements("FROM call_legs")
    assert args == (CALL, str(TENANT_A))
    assert bound_tenants(sql, args) == [str(TENANT_A)], f"the recovery leg lookup names no tenant\n{sql}\n{args}"


# ═══════════════════════════════════════════════════════════════════════════
# 2a. dialer worker: statements that already bind the job's tenant, and the one
#     discovery sweep that is cross-tenant by design
# ═══════════════════════════════════════════════════════════════════════════


async def test_the_dialer_discovers_tenants_with_running_campaigns_across_all_tenants_by_design():
    conn = ScriptConn([("SELECT DISTINCT tenant_id FROM campaigns", [{"tenant_id": TENANT_A}, {"tenant_id": TENANT_B}])])
    assert await worker_on(conn)._get_active_tenant_ids() == [str(TENANT_A), str(TENANT_B)]
    ((sql, args),) = conn.calls
    assert not re.search(r"tenant_id\s*=", sql) and args == (), "the discovery sweep must stay platform-wide"


async def test_stale_payload_check_reads_the_job_and_the_lead_within_the_jobs_tenant():
    live = {"job_status": "pending", "lead_row_id": LEAD, "lead_status": "pending", "lead_do_not_call": False}
    conn = ScriptConn([("FROM (SELECT 1) AS base", live)])
    assert await worker_on(conn)._stale_payload_reason(job_of()) is None
    ((sql, args),) = conn.calls
    assert args == (JOB, JOB_TENANT, LEAD)
    assert sql.count("tenant_id = $2::uuid") == 2, "both the dialer_jobs join and the leads join must bind the tenant"
    db = sqlite_with(
        "CREATE TABLE dialer_jobs (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT);"
        "CREATE TABLE leads (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT, do_not_call INTEGER);"
    )
    db.execute("INSERT INTO dialer_jobs VALUES (?,?,'pending')", (JOB, str(TENANT_B)))
    db.execute("INSERT INTO leads VALUES (?,?,'pending',0)", (LEAD, str(TENANT_B)))
    foreign = run_on_sqlite(db, sql, args).fetchone()
    assert foreign["job_status"] is None and foreign["lead_row_id"] is None
    owned = run_on_sqlite(db, sql, (JOB, str(TENANT_B), LEAD)).fetchone()
    assert (owned["job_status"], owned["lead_row_id"]) == ("pending", LEAD)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"job_status": None, "lead_row_id": None, "lead_status": None, "lead_do_not_call": None}, "job_not_active"),
        ({"job_status": "pending", "lead_row_id": None, "lead_status": None, "lead_do_not_call": None}, "lead_not_dialable"),
    ],
    ids=["no-job-in-tenant", "no-lead-in-tenant"],
)
async def test_a_job_or_lead_outside_the_jobs_tenant_makes_the_payload_stale(row, reason):
    conn = ScriptConn([("FROM (SELECT 1) AS base", row)])
    assert await worker_on(conn)._stale_payload_reason(job_of()) == reason


async def test_attempt_replay_lookup_is_bound_to_the_jobs_tenant_and_reads_the_leg_through_the_call():
    conn = ScriptConn()
    assert await worker_on(conn)._load_existing_call_intent(job_of()) is None
    ((sql, args),) = conn.calls
    assert args == (JOB_TENANT, JOB, 1)
    assert "WHERE c.tenant_id = $1::uuid" in sql
    assert "FROM call_legs WHERE call_id = c.id" in sql


async def test_a_replayed_attempt_of_another_tenant_is_rejected_not_adopted():
    foreign = {
        "id": CALL, "tenant_id": TENANT_B, "campaign_id": CAMPAIGN, "lead_id": LEAD, "phone_number": "+447700900123",
        "direction": "outbound", "talklee_call_id": "TK-1", "status": "initiated", "provider_call_id": None, "leg_id": LEG,
    }
    conn = ScriptConn([("FROM calls AS c", foreign)])
    with pytest.raises(RuntimeError, match="mismatched tenant_id"):
        await worker_on(conn)._load_existing_call_intent(job_of())


async def test_call_intent_rows_are_written_for_the_jobs_tenant_and_the_children_hang_off_that_call(monkeypatch):
    inserted = {"id": CALL, "talklee_call_id": "TK-1", "status": "initiated", "provider_call_id": None}
    conn = ScriptConn([("INSERT INTO calls", inserted)])
    monkeypatch.setattr(DialerWorker, "_new_call_identity", staticmethod(lambda: (CALL, "TK-1", LEG)))
    intent = await worker_on(conn)._create_call_intent(job_of())
    assert intent.created and intent.call_id == CALL and intent.leg_id == LEG
    ((calls_sql, calls_args),) = conn.statements("INSERT INTO calls")
    assert insert_binding(calls_sql, "tenant_id", calls_args) == JOB_TENANT
    assert insert_binding(calls_sql, "id", calls_args) == CALL
    ((leg_sql, leg_args),) = conn.statements("INSERT INTO call_legs")
    assert insert_binding(leg_sql, "call_id", leg_args) == CALL, "the leg must hang off the call inserted above"
    events = conn.statements("INSERT INTO call_events")
    assert events and all(insert_binding(sql, "call_id", args) == CALL for sql, args in events)


async def test_the_children_of_a_call_intent_are_never_written_when_the_call_insert_lost_the_race(monkeypatch):
    winner = {
        "id": CALL, "tenant_id": TENANT_A, "campaign_id": CAMPAIGN, "lead_id": LEAD, "phone_number": "+447700900123",
        "direction": "outbound", "talklee_call_id": "TK-1", "status": "initiated", "provider_call_id": None, "leg_id": LEG,
    }
    conn = ScriptConn([("INSERT INTO calls", None), ("FROM calls AS c", winner)])  # ON CONFLICT DO NOTHING -> no row
    monkeypatch.setattr(DialerWorker, "_new_call_identity", staticmethod(lambda: ("1" * 8 + "-1111-4111-8111-" + "1" * 12, "TK-2", LEG)))
    intent = await worker_on(conn)._create_call_intent(job_of())
    assert intent.created is False and intent.call_id == CALL
    assert not conn.statements("INSERT INTO call_legs") and not conn.statements("INSERT INTO call_events")
    ((winner_sql, winner_args),) = conn.statements("FROM calls AS c")
    assert winner_args[0] == JOB_TENANT and "WHERE c.tenant_id = $1::uuid" in winner_sql


async def test_attempt_settled_probe_is_bound_to_the_jobs_tenant_through_every_join():
    conn = ScriptConn()
    assert await worker_on(conn)._attempt_settled_failed_unanswered(job_of()) is False
    ((sql, args),) = conn.calls
    assert args[:3] == (JOB_TENANT, JOB, 1)
    assert "WHERE c.tenant_id = $1::uuid" in sql
    assert "j.tenant_id = c.tenant_id" in sql and "other_job.tenant_id = c.tenant_id" in sql


async def test_binding_the_provider_names_the_tenant_and_touches_the_leg_only_after_the_call_matched():
    job = job_of()
    intent = DialerCallIntent(CALL, "TK-1", LEG, "initiated", None, True)
    conn = ScriptConn()
    await worker_on(conn)._bind_call_intent(job, intent, "chan-1")
    (calls_sql, calls_args), (leg_sql, leg_args) = conn.calls
    assert calls_sql.startswith("UPDATE calls") and bound_tenants(calls_sql, calls_args) == [JOB_TENANT]
    assert leg_sql.startswith("UPDATE call_legs") and "tenant_id" not in leg_sql
    assert leg_args[:2] == (LEG, CALL), "the leg is keyed by the call whose tenant the first update just proved"
    refusing = ScriptConn(execute_result="UPDATE 0")  # the call does not belong to this job's tenant
    with pytest.raises(RuntimeError, match="durable call bind affected UPDATE 0"):
        await worker_on(refusing)._bind_call_intent(job, intent, "chan-1")
    assert [sql.split()[1] for sql, _ in refusing.calls] == ["calls"], "no leg may be touched after a missed match"


@pytest.mark.parametrize(
    ("reason", "literal"),
    [
        ("telephony_unavailable", "telephony_unavailable"),
        ("line_not_ready", "line_not_ready"),
        ("x'; DROP TABLE calls; --", "line_not_ready"),
    ],
    ids=["telephony", "line", "hostile-text-is-never-interpolated"],
)
async def test_line_not_ready_release_names_the_tenant_and_interpolates_only_a_whitelisted_literal(reason, literal):
    conn = ScriptConn()
    intent = DialerCallIntent(CALL, "TK-1", LEG, "initiated", None, True)
    assert await worker_on(conn)._release_intent_line_not_ready(job_of(), intent, reason=reason) is True
    ((sql, args),) = conn.calls
    assert f"failure_reason = '{literal}'" in sql and "DROP" not in sql
    assert args[0] == CALL and bound_tenants(sql, args) == [JOB_TENANT]


@pytest.mark.parametrize("status", ["calling", "failed"])
async def test_lead_status_write_names_the_jobs_tenant_whatever_the_dynamic_fragment(status):
    conn = ScriptConn()
    await worker_on(conn)._update_lead_status(job_of(), status)
    ((sql, args),) = conn.calls
    assert bound_tenants(sql, args) == [JOB_TENANT] and args[1] == LEAD
    assert ("last_called_at" in sql) is (status != "calling"), "the only dynamic fragment is the timestamp"


async def test_lead_status_recheck_after_a_missed_write_is_tenant_bound_too():
    conn = ScriptConn([("SELECT status FROM leads", "dnc")], execute_result="UPDATE 0")
    await worker_on(conn)._update_lead_status(job_of(), "calling")  # a concurrent opt-out owns the lead: skipped
    ((sql, args),) = conn.statements("SELECT status FROM leads")
    assert bound_tenants(sql, args) == [JOB_TENANT]


async def test_live_call_probe_names_the_jobs_tenant():
    conn = ScriptConn([("SELECT EXISTS", True)])
    assert await worker_on(conn)._lead_has_live_or_answered_call(job_of(), 2.0) is True
    ((sql, args),) = conn.calls
    assert bound_tenants(sql, args) == [JOB_TENANT] and args[1] == LEAD


# ═══════════════════════════════════════════════════════════════════════════
# 2b. CallService: the statements that already bind the locked call's tenant
# ═══════════════════════════════════════════════════════════════════════════


def bare_service() -> CallService:
    return CallService(db_client=AsyncMock(), call_repo=AsyncMock(), lead_repo=AsyncMock(), db_pool=object())


async def test_settlement_discovers_the_calls_tenant_from_the_row_it_locks_and_never_from_the_ambient_context():
    conn = retry_settlement_conn()
    await settled_service(conn).handle_call_status("call-1", CallOutcome.ANSWERED, duration=42)
    first_sql, first_args = next((s, a) for s, a in conn.executed if "FROM calls WHERE id" in s)
    assert first_args == ("call-1",) and "FOR UPDATE" in first_sql
    assert "tenant_id =" not in first_sql and "tenant_id=" not in first_sql
    # every statement after it that names a tenant names THIS call's, not the ambient teardown context
    named = [a for s, a in conn.executed if bound_tenants(s, a)]
    assert named, "no statement carried a tenant"
    for sql, args in conn.executed:
        assert AMBIENT_TENANT not in [str(a) for a in args], f"ambient tenant leaked into\n{sql}\n{args}"


@pytest.mark.parametrize(
    ("outcome", "counter"),
    [(CallOutcome.ANSWERED, "calls_completed"), (CallOutcome.INVALID, "calls_failed")],
)
async def test_campaign_counter_bump_names_the_settled_calls_tenant(outcome, counter):
    conn = ScriptConn()
    await bare_service()._update_campaign_counters_pooled(conn, "camp-1", outcome, scope_tenant_id=CALL_TENANT)
    ((sql, args),) = conn.calls
    assert f"SET {counter} = COALESCE({counter}, 0) + 1" in sql, "the only dynamic part is one of two counter names"
    assert args == ("camp-1", CALL_TENANT) and bound_tenants(sql, args) == [CALL_TENANT]


async def test_the_retry_identity_read_reaches_the_call_leg_only_through_the_calls_tenant():
    job_row = {
        "id": "job-1", "tenant_id": CALL_TENANT, "attempt_number": 1, "priority": 5, "phone_number": "+15550002222",
        "status": "processing",
    }
    leg_meta = json.dumps({"first_speaker": "agent", "agent_name": "Tessa"})
    conn = ScriptConn([
        ("SELECT * FROM dialer_jobs", job_row),
        ("UPDATE dialer_jobs", "job-1"),
        ("SELECT cl.metadata FROM call_legs", {"metadata": leg_meta}),
    ])
    lead_is_terminal, retry_args = await bare_service()._handle_job_completion_pooled(
        conn, "job-1", CallOutcome.BUSY, "camp-1", "lead-1",
        scope_tenant_id=CALL_TENANT, call_attempt_number=1, call_id="call-1",
    )
    assert lead_is_terminal is False and retry_args[1]["first_speaker"] == "agent"
    ((sql, args),) = conn.statements("FROM call_legs")
    assert "JOIN calls c ON c.id = cl.call_id" in sql and "tenant_id" in sql
    assert args == ("call-1", CALL_TENANT) and bound_tenants(sql, args) == [CALL_TENANT]


def opt_out_call_row(**over):
    row = {
        "id": "call-1", "tenant_id": CALL_TENANT, "phone_number": "+14155552671", "direction": "outbound",
        "is_test": False, "lead_id": None, "dialer_job_id": "job-1", "terminal_retry_payload": None,
    }
    row.update(over)
    return row


async def test_the_opt_out_probe_reads_the_calls_tenant_plus_the_global_list_only():
    conn = ScriptConn([("FROM dnc_entries", False)])
    assert await bare_service()._active_caller_opt_out_pooled(conn, opt_out_call_row()) is False
    ((sql, args),) = conn.calls
    assert args[0] == CALL_TENANT
    assert "(tenant_id=$1::uuid OR tenant_id IS NULL)" in sql
    number = args[1]
    db = sqlite_with(
        "CREATE TABLE dnc_entries (id INTEGER PRIMARY KEY, tenant_id TEXT, normalized_number TEXT, source TEXT,"
        " expires_at TEXT);"
    )
    insert = "INSERT INTO dnc_entries (tenant_id, normalized_number, source) VALUES (?,?, 'caller_opt_out')"
    db.execute(insert, (str(TENANT_B), number))  # another tenant's suppression of the same number
    assert run_on_sqlite(db, sql, args).fetchone()[0] == 0, "another tenant's opt-out must not suppress this call"
    db.execute(insert, (None, number))  # the platform-wide list applies to everyone
    assert run_on_sqlite(db, sql, args).fetchone()[0] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2c. transfer provider identity: children are reached only through the
#     tenant-verified parent; alias probes search every tenant on purpose
# ═══════════════════════════════════════════════════════════════════════════

PARENT_CHANNEL = "talky-in-parent-1"
PLANNED = "talky-xfer-planned-1"
ACTUAL = "talky-xfer-actual-1"


def transfer_conn(*, parent=True, call_alias=None, leg_alias=None) -> ScriptConn:
    parent_row = {
        "id": CALL, "tenant_id": str(TENANT_A), "talklee_call_id": "TK-1", "status": "in_call",
        "processing_status": "active", "billing_status": "reserved", "ended_at": None,
    }
    leg_row = {
        "id": LEG, "call_id": CALL, "provider": "asterisk", "provider_leg_id": PLANNED, "status": "initiated",
        "billing_status": "reserved", "reserved_seconds": 60, "metadata": {},
        "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
    }
    rebound = json.dumps({
        "provider_leg_id_original": PLANNED, "provider_leg_id_authoritative": ACTUAL, "provider_leg_id_rebound": True,
    })
    return ScriptConn([
        ("SELECT id, tenant_id, talklee_call_id, status,", parent_row if parent else None),
        ("SELECT id, call_id, provider, provider_leg_id, status,", [leg_row]),
        ("SELECT id FROM calls WHERE provider_call_id=$1", call_alias),
        ("SELECT id FROM call_legs WHERE id<>$1::uuid", leg_alias),
        ("UPDATE call_legs", {"id": LEG, "call_id": CALL, "provider_leg_id": ACTUAL, "metadata": rebound}),
    ])


async def rebind(monkeypatch, conn):
    monkeypatch.setattr(transfer_provider_identity, "acquire_with_tenant", lambda *_a, **_k: Ctx(conn))
    return await transfer_provider_identity.persist_asterisk_transfer_provider_identity(
        object(), tenant_id=JOB_TENANT, durable_call_id=CALL, parent_provider_call_id=PARENT_CHANNEL,
        planned_provider_leg_id=PLANNED, actual_provider_leg_id=ACTUAL,
    )


async def test_a_transfer_rebind_touches_its_children_only_through_the_tenant_verified_parent(monkeypatch):
    conn = transfer_conn()
    identity = await rebind(monkeypatch, conn)
    assert identity.rebound and identity.provider_leg_id == ACTUAL and identity.tenant_id == JOB_TENANT
    parent_sql, parent_args = conn.calls[0]
    assert "FROM calls" in parent_sql and "FOR UPDATE" in parent_sql
    assert parent_args == (CALL, JOB_TENANT, PARENT_CHANNEL)
    assert bound_tenants(parent_sql, parent_args) == [JOB_TENANT]
    ((leg_sql, leg_args),) = conn.statements("FROM call_legs WHERE call_id=$1::uuid")
    assert leg_args[0] == CALL and "tenant_id" not in leg_sql  # call_legs has no tenant column
    ((update_sql, update_args),) = conn.statements("UPDATE call_legs")
    assert update_args[0] == LEG and update_args[1] == CALL
    ((event_sql, event_args),) = conn.statements("INSERT INTO call_events")
    assert event_args[0] == CALL and event_args[2] == LEG


async def test_a_rebind_for_the_wrong_tenant_never_reaches_a_child_row(monkeypatch):
    conn = transfer_conn(parent=False)  # no parent with this id, tenant and provider id
    with pytest.raises(transfer_provider_identity.TransferProviderIdentityError) as refused:
        await rebind(monkeypatch, conn)
    assert refused.value.code == "transfer_provider_identity_parent_scope_mismatch"
    assert len(conn.calls) == 1, [sql for sql, _ in conn.calls]


async def test_the_alias_probes_deliberately_search_every_tenant(monkeypatch):
    conn = transfer_conn()
    await rebind(monkeypatch, conn)
    ((call_sql, call_args),) = conn.statements("SELECT id FROM calls WHERE provider_call_id=$1")
    ((leg_sql, leg_args),) = conn.statements("SELECT id FROM call_legs WHERE id<>$1::uuid")
    assert "tenant_id" not in call_sql and "tenant_id" not in leg_sql, (
        "a channel id already held by ANY tenant must be refused; a tenant filter would hide the conflict"
    )
    assert call_args == (ACTUAL,) and leg_args == (LEG, ACTUAL)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"call_alias": {"id": "another-tenants-call"}}, "transfer_provider_identity_call_alias"),
        ({"leg_alias": {"id": "another-tenants-leg"}}, "transfer_provider_identity_leg_alias"),
    ],
    ids=["call-alias", "leg-alias"],
)
async def test_an_alias_held_by_another_tenant_is_refused_before_any_write(monkeypatch, kwargs, code):
    conn = transfer_conn(**kwargs)
    with pytest.raises(transfer_provider_identity.TransferProviderIdentityError) as refused:
        await rebind(monkeypatch, conn)
    assert refused.value.code == code
    assert not conn.statements("UPDATE call_legs") and not conn.statements("INSERT INTO call_events")


async def test_loading_a_persisted_transfer_identity_joins_the_leg_to_the_tenants_call(monkeypatch):
    row = {"id": LEG, "call_id": CALL, "provider_leg_id": PLANNED, "metadata": {}}
    conn = ScriptConn([("FROM call_legs l", [row])])
    monkeypatch.setattr(transfer_provider_identity, "acquire_with_tenant", lambda *_a, **_k: Ctx(conn))
    identity = await transfer_provider_identity.load_persisted_transfer_provider_identity(
        object(), tenant_id=JOB_TENANT, durable_call_id=CALL, leg_id=None,
        expected_original_provider_leg_id=PLANNED, parent_provider_call_id=PARENT_CHANNEL,
    )
    assert identity.tenant_id == JOB_TENANT and identity.leg_id == LEG and identity.rebound is False
    ((sql, args),) = conn.calls
    assert "JOIN calls c ON c.id=l.call_id" in sql
    assert args[:2] == (CALL, JOB_TENANT) and bound_tenants(sql, args) == [JOB_TENANT]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, provider_call_id TEXT, external_call_uuid TEXT);"
        "CREATE TABLE call_legs (id TEXT PRIMARY KEY, call_id TEXT, leg_type TEXT, provider_leg_id TEXT,"
        " metadata TEXT, created_at TEXT);"
    )
    db.execute("INSERT INTO calls VALUES (?,?,?,NULL)", (CALL, str(TENANT_B), PARENT_CHANNEL))
    db.execute("INSERT INTO call_legs VALUES (?,?,'transfer',?,'{}','2026-11-01')", (LEG, CALL, PLANNED))
    assert run_on_sqlite(db, sql, args).fetchall() == []
    assert len(run_on_sqlite(db, sql, (CALL, str(TENANT_B), None, PLANNED, PARENT_CHANNEL)).fetchall()) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2d. lifecycle: discovery of a provider channel's tenant, and the platform sweeps
# ═══════════════════════════════════════════════════════════════════════════


async def test_inbound_admission_discovery_is_by_provider_identity_and_refuses_ambiguity(monkeypatch):
    row = {
        "id": CALL, "tenant_id": TENANT_A, "campaign_id": None, "provider": "asterisk", "provider_call_id": "chan-9",
        "billing_status": "reserved", "identity_match_count": 1,
    }
    conn = ScriptConn([("FROM calls WHERE direction='inbound'", dict(row))])
    install_db(monkeypatch, conn)
    container = SimpleNamespace(is_initialized=True, db_pool=object())
    found = await lifecycle._find_inbound_admission_by_provider_identity(
        container, provider=" Asterisk ", provider_call_id="chan-9"
    )
    assert found["tenant_id"] == str(TENANT_A) and found["call_id"] == CALL
    ((sql, args),) = conn.calls
    assert args == ("asterisk", "chan-9")
    assert not re.search(r"tenant_id\s*=", sql), "the tenant is what this lookup discovers"
    conn.script[0] = ("FROM calls WHERE direction='inbound'", {**row, "identity_match_count": 2})
    with pytest.raises(RuntimeError, match="ambiguous inbound provider identity"):
        await lifecycle._find_inbound_admission_by_provider_identity(
            container, provider="asterisk", provider_call_id="chan-9"
        )


@pytest.mark.parametrize("ledger_tenant", [str(TENANT_A), None], ids=["ledger-names-a-tenant", "legacy-ledger-entry"])
async def test_orphan_hydration_binds_the_ledger_tenant_when_the_ledger_has_one(monkeypatch, ledger_tenant):
    conn = ScriptConn([("FROM calls c", recovery_row()), ("FROM call_legs", [])])
    install_db(monkeypatch, conn)
    ledger = {"state": "active", "provider": "asterisk", "durable_call_id": CALL}
    if ledger_tenant:
        ledger["tenant_id"] = ledger_tenant
    await lifecycle._hydrate_orphan_recovery_context("pbx-out", ledger)
    ((sql, args),) = conn.statements("FROM calls c")
    assert args == ("pbx-out", CALL, "asterisk", ledger_tenant)
    assert "($4::uuid IS NULL OR c.tenant_id=$4::uuid)" in sql


async def test_orphan_hydration_refuses_an_ambiguous_provider_session(monkeypatch):
    conn = ScriptConn([("FROM calls c", recovery_row(recovery_match_count=2))])
    install_db(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="ambiguous"):
        await lifecycle._hydrate_orphan_recovery_context("pbx-out", {"state": "active", "provider": "asterisk"})
    assert not conn.statements("FROM call_legs"), "an ambiguous identity must not be followed into any tenant's legs"


async def test_pending_termination_sweep_is_platform_wide_and_each_candidate_carries_its_own_tenant(monkeypatch):
    import app.domain.services.telephony.legacy_pending_identity as legacy

    other_call = "99999999-9999-4999-8999-999999999999"
    rows = [
        {"id": CALL, "tenant_id": TENANT_A, "provider": "asterisk", "provider_call_id": "chan-a",
         "external_call_uuid": None, "direction": "outbound", "status": "termination_pending", "billing_status": None},
        {"id": other_call, "tenant_id": TENANT_B, "provider": "asterisk", "provider_call_id": "chan-b",
         "external_call_uuid": None, "direction": "inbound", "status": "completed", "billing_status": "reserved"},
    ]
    conn = ScriptConn([("FROM calls WHERE", rows)])
    install_db(monkeypatch, conn)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace(name="asterisk"))
    monkeypatch.setattr(legacy, "reconcile_pending_identity", AsyncMock())
    candidates = await lifecycle._load_termination_pending_candidates()
    assert [(c["call_id"], c["tenant_id"], c["durable_call_id"]) for c in candidates] == [
        ("chan-a", str(TENANT_A), CALL), ("chan-b", str(TENANT_B), other_call),
    ]
    ((sql, args),) = conn.statements("FROM calls WHERE")
    assert args == ("asterisk",) and not re.search(r"\btenant_id\s*=", sql)


async def test_retry_rotation_only_bumps_the_ordering_timestamp_of_one_call(monkeypatch):
    conn = ScriptConn()
    install_db(monkeypatch, conn)
    await lifecycle._rotate_deferred_termination_candidate(CALL)
    ((sql, args),) = conn.calls
    assert args == (CALL,)
    assert re.search(r"^UPDATE calls SET updated_at=NOW\(\) WHERE id=\$1::uuid AND \(", sql), sql


async def test_the_account_authority_sweep_judges_each_call_by_its_own_tenants_policy(monkeypatch):
    from tests.unit.test_account_hold_telephony import call_row, install_authority_db
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row, hold

    free_tenant = "00000000-0000-0000-0000-000000000777"
    held_call = call_row()  # TENANT, which carries an active hold
    free_call = call_row(id="00000000-0000-0000-0000-000000000602", tenant_id=free_tenant)
    fixture = install_authority_db(
        monkeypatch, [held_call, free_call],
        {TENANT: authority_row(active_holds=[hold()]), free_tenant: authority_row(id=free_tenant)},
    )
    dispatched = []

    async def terminate(row, **_kwargs):
        dispatched.append((row["id"], str(row["tenant_id"])))
        return False

    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", terminate)
    assert await lifecycle._reconcile_account_call_authority() == 1
    await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
    assert dispatched == [(held_call["id"], TENANT)], "only the held tenant's call may be dispatched"
    candidate_sql = next(sql for sql, _ in fixture.queries if "FROM calls c" in sql)
    assert "c.tenant_id" in candidate_sql and not re.search(r"\btenant_id\s*=", candidate_sql)


def test_guard_the_preanswer_teardown_installs_the_discovered_tenant_before_settling():
    """Source guard, not behaviour proof: ``_on_call_ended`` is too large to drive here, and
    tests/unit/test_outbound_answered_duration.py already runs this lookup end to end."""
    source = Path(lifecycle.__file__).read_text(encoding="utf-8")
    start = source.index("SELECT id, tenant_id, answered_at, NOW() AS db_now FROM calls")
    assert "Provider callback does not carry a tenant" in source[start - 600:start]
    after = source[start:]
    assert after.index('set_current_tenant_id(str(_row["tenant_id"]))') < after.index("call_service.handle_call_status(")
