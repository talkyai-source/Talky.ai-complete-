"""F2-03 (tail A: API, security, billing and notification files): statements name their tenant.

Production has run with an application role that bypasses row-level security,
so a statement that does not name its tenant is not isolated at all. Each test
runs the real function on a connection that records ``(sql, args)`` and checks
two things about the statement:

* the tenant predicate is in the SQL, and it is bound to the tenant the caller
  already holds (the authenticated tenant, or the tenant of the row the function
  has just read), not to a constant and not to nothing;
* where the SQL is portable it is also executed on SQLite beside a second
  tenant's rows: another tenant's row matches nothing, the caller's own matches.

The statements that stay as reviewed exceptions in ``tenant_sql_baseline.json``
are not re-tested here, except for the few claims their reasons rest on (see the
second half of the file); those pass before and after the change.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

TENANT_A = "a1111111-1111-4111-8111-111111111111"
TENANT_B = "b2222222-2222-4222-8222-222222222222"
USER = "c3333333-3333-4333-8333-333333333333"
TENANTS = [TENANT_A, TENANT_B]
IDS = ["tenant-a", "tenant-b"]


def flat(sql):
    return " ".join(sql.split())


def bound_tenants(sql, args):
    """Every value the statement compares a ``tenant_id`` column with, read from its bind slots."""
    return [args[int(slot) - 1] for slot in re.findall(r"\btenant_id\s*=\s*\$(\d+)", sql, re.I)]


def only(calls, marker):
    """The one recorded ``(sql, args)`` whose flattened text contains ``marker``."""
    hits = [(flat(sql), args) for sql, args in calls if marker in flat(sql)]
    assert len(hits) == 1, f"expected exactly one statement containing {marker!r}, got {len(hits)}"
    return hits[0]


def sqlite_sql(sql):
    """The few Postgres spellings these statements use, in SQLite's."""
    sql = re.sub(r"\$(\d+)", r"?\1", flat(sql))
    return sql.replace("E'\\n'", "char(10)").replace("NOW()", "CURRENT_TIMESTAMP")


# --- alerts.ack_alert: the note update and the re-read ----------------------------------

ALERT_ID = "d4444444-4444-4444-8444-444444444444"
OTHER_ALERT_ID = "e5555555-5555-4555-8555-555555555555"


def alert_row(notes):
    return {"event_id": ALERT_ID, "created_at": datetime(2026, 11, 13, tzinfo=timezone.utc),
            "severity": "HIGH", "status": "investigating", "title": "Carrier down",
            "description": "d", "evidence": None, "assigned_to": USER, "resolved_at": None,
            "resolution_notes": notes, "alert_type": "Network"}


class AlertConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return alert_row("[ack by x] looking")

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


async def run_ack(monkeypatch, tenant):
    from app.api.v1.endpoints import alerts

    conn = AlertConn()

    @asynccontextmanager
    async def acquire(_pool, owner):
        assert owner == tenant
        yield conn

    monkeypatch.setattr(alerts, "acquire_with_tenant", acquire)
    result = await alerts.ack_alert(
        ALERT_ID, alerts.AckRequest(note="looking"),
        current_user=SimpleNamespace(tenant_id=tenant, id=USER),
        db_client=SimpleNamespace(pool=object()),
    )
    assert result.id == ALERT_ID
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_ack_note_update_names_the_callers_tenant(monkeypatch, tenant):
    conn = await run_ack(monkeypatch, tenant)
    sql, args = only(conn.calls, "SET resolution_notes = COALESCE(resolution_notes")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the caller's tenant: {sql} {args}"
    assert args[0] == ALERT_ID


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_ack_reread_names_the_callers_tenant(monkeypatch, tenant):
    conn = await run_ack(monkeypatch, tenant)
    sql, args = only(conn.calls, "FROM security_events WHERE event_id = $1")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the caller's tenant: {sql} {args}"
    assert args[0] == ALERT_ID


SECURITY_EVENTS = """
CREATE TABLE security_events (event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT,
                              title TEXT, resolution_notes TEXT);
INSERT INTO security_events VALUES ('d4444444-4444-4444-8444-444444444444', 'a1111111-1111-4111-8111-111111111111',
                                    'investigating', 'A alert', 'first');
INSERT INTO security_events VALUES ('e5555555-5555-4555-8555-555555555555', 'b2222222-2222-4222-8222-222222222222',
                                    'open', 'B alert', 'secret');
"""


async def test_ack_statements_cannot_reach_another_tenants_alert(monkeypatch):
    conn = await run_ack(monkeypatch, TENANT_A)
    update_sql, update_args = only(conn.calls, "SET resolution_notes = COALESCE(resolution_notes")
    select_sql, select_args = only(conn.calls, "FROM security_events WHERE event_id = $1")
    db = sqlite3.connect(":memory:")
    db.executescript(SECURITY_EVENTS)
    # Tenant A's session, tenant B's alert id: the statement must match nothing.
    foreign_update = (OTHER_ALERT_ID, "[ack by a] injected", *update_args[2:])
    assert db.execute(sqlite_sql(update_sql), foreign_update).rowcount == 0
    assert db.execute("SELECT resolution_notes FROM security_events WHERE event_id = ?",
                      (OTHER_ALERT_ID,)).fetchone() == ("secret",)
    foreign_select = (OTHER_ALERT_ID, *select_args[1:])
    assert db.execute(sqlite_sql(select_sql.replace("SELECT event_id, created_at, severity, status, title, "
                                                    "description, evidence, assigned_to, resolved_at, "
                                                    "resolution_notes, alert_type", "SELECT event_id")),
                      foreign_select).fetchall() == []
    # The caller's own alert still matches both.
    own_update = (ALERT_ID, "[ack by a] looking", *update_args[2:])
    assert db.execute(sqlite_sql(update_sql), own_update).rowcount == 1


# --- credential_resolver: the last_used_at touch ------------------------------------------

CREDENTIAL_ID = "f6666666-6666-4666-8666-666666666666"
OTHER_CREDENTIAL_ID = "a7777777-7777-4777-8777-777777777777"


class CredentialConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return {"id": CREDENTIAL_ID, "encrypted_key": "ciphertext"}

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


class PlainEncryption:
    def decrypt(self, _ciphertext):
        return "provider-key"


async def run_resolve(monkeypatch, tenant):
    import app.domain.services.credential_resolver as module

    conn = CredentialConn()

    @asynccontextmanager
    async def acquire(_pool, owner):
        assert owner == tenant
        yield conn

    monkeypatch.setattr(module, "acquire_with_tenant", acquire)
    resolver = module.CredentialResolver(db_pool=object(), encryption_service=PlainEncryption())
    outcome, material = await resolver._resolve_tenant(
        provider="groq", tenant_id=tenant, credential_kind="api_key")
    assert outcome == module._FOUND and material is not None
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_credential_last_used_touch_names_the_tenant_that_owns_the_row(monkeypatch, tenant):
    conn = await run_resolve(monkeypatch, tenant)
    select_sql, select_args = only(conn.calls, "FROM tenant_ai_credentials")
    assert bound_tenants(select_sql, select_args) == [tenant]
    sql, args = only(conn.calls, "SET last_used_at = NOW()")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the row's tenant: {sql} {args}"
    assert args[0] == CREDENTIAL_ID


async def test_credential_last_used_touch_cannot_mark_another_tenants_key(monkeypatch):
    conn = await run_resolve(monkeypatch, TENANT_A)
    sql, args = only(conn.calls, "SET last_used_at = NOW()")
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE tenant_ai_credentials (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, last_used_at TEXT);"
        f"INSERT INTO tenant_ai_credentials VALUES ('{CREDENTIAL_ID}', '{TENANT_A}', NULL);"
        f"INSERT INTO tenant_ai_credentials VALUES ('{OTHER_CREDENTIAL_ID}', '{TENANT_B}', NULL);"
    )
    # Tenant A's resolver, tenant B's credential id: nothing may be written.
    assert db.execute(sqlite_sql(sql), (OTHER_CREDENTIAL_ID, *args[1:])).rowcount == 0
    assert db.execute("SELECT last_used_at FROM tenant_ai_credentials WHERE id = ?",
                      (OTHER_CREDENTIAL_ID,)).fetchone() == (None,)
    assert db.execute(sqlite_sql(sql), args).rowcount == 1


# --- admin/media._resume_media_deletion_intent --------------------------------------------

INTENT_ID = UUID("b8888888-8888-4888-8888-888888888888")
ACTOR_ID = UUID("c9999999-9999-4999-8999-999999999999")


class IntentConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return False  # no compliance hold

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return {"id": INTENT_ID, "actor_id": ACTOR_ID, "status": "intent_committed",
                "resource_snapshot": {}, "response_body": None, "attempt_count": 2,
                "attempt_actor_ids": [ACTOR_ID]}


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_resumed_deletion_intent_update_names_the_intents_own_tenant(tenant):
    from app.api.v1.endpoints.admin import media

    conn = IntentConn()
    row = {"id": INTENT_ID, "tenant_id": UUID(tenant), "status": "failed"}
    claim = await media._resume_media_deletion_intent(conn, row, ACTOR_ID)
    assert claim.status == "intent_committed"
    sql, args = only(conn.calls, "UPDATE admin_media_deletion_intents")
    assert bound_tenants(sql, args) == [UUID(tenant)], f"no tenant predicate bound to the intent's tenant: {sql} {args}"
    assert args[0] == INTENT_ID
    assert args[1] == "intent_committed"
    assert args[2] == ACTOR_ID


# === The claims the reviewed exceptions rest on (these pass before and after the change) ===
#
# Each statement below stays in ``tenant_sql_baseline.json`` as a reviewed exception because the
# scanner cannot prove a predicate that is really there (an optional-tenant OR, EXISTS, ON CONFLICT
# DO UPDATE, a CTE over call_legs, a dynamic WHERE). The reason recorded for it names the tenant the
# statement binds; these tests run the real code and check that claim.


class Recorder:
    """A connection double that records every statement and answers with canned values."""

    def __init__(self, *, fetch=None, fetchrow=None, fetchval=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetch, self._fetchrow, self._fetchval = fetch or [], fetchrow, fetchval

    def is_in_transaction(self):
        return True

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetch

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchrow

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchval

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


def patch_acquire(monkeypatch, module, conn):
    """Replace ``module.acquire_with_tenant`` with a recorder; returns the tenants it was opened for."""
    owners: list = []

    @asynccontextmanager
    async def acquire(_pool, owner, **_kwargs):
        owners.append(owner)
        yield conn

    monkeypatch.setattr(module, "acquire_with_tenant", acquire)
    return owners


# --- audit_logs: resolve_tenant_scope is None only for a platform administrator ---------------

@pytest.mark.parametrize("role,own,requested,expected", [
    ("platform_admin", None, None, None),
    ("platform_admin", TENANT_A, None, None),
    ("platform_admin", TENANT_A, TENANT_B, TENANT_B),
    ("tenant_admin", TENANT_A, None, TENANT_A),
    ("tenant_admin", TENANT_A, TENANT_A, TENANT_A),
    ("agent", TENANT_B, None, TENANT_B),
], ids=["platform-all", "platform-home-does-not-pin", "platform-narrowed", "tenant-admin",
        "tenant-admin-names-own", "agent"])
def test_audit_scope_is_none_only_for_the_platform_administrator(role, own, requested, expected):
    from app.api.v1.endpoints.audit_logs import resolve_tenant_scope

    user = {"role": role, "tenant_id": own}
    assert resolve_tenant_scope(user, requested, denied_detail="no") == expected


@pytest.mark.parametrize("role,own,requested", [
    ("tenant_admin", TENANT_A, TENANT_B),
    ("tenant_admin", None, None),
    ("agent", None, TENANT_A),
], ids=["other-tenant", "no-tenant", "agent-no-tenant"])
def test_audit_scope_refuses_a_caller_without_a_tenant_or_naming_another(role, own, requested):
    from fastapi import HTTPException

    from app.api.v1.endpoints.audit_logs import resolve_tenant_scope

    with pytest.raises(HTTPException) as refused:
        resolve_tenant_scope({"role": role, "tenant_id": own}, requested, denied_detail="no")
    assert refused.value.status_code == 403


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_event_stats_bind_the_callers_tenant(monkeypatch, tenant):
    from app.api.v1.endpoints import audit_logs

    conn = Recorder()
    owners = patch_acquire(monkeypatch, audit_logs, conn)
    await audit_logs.get_event_stats(days=7, tenant_id=None, db_pool=object(),
                                     current_user={"role": "tenant_admin", "tenant_id": tenant})
    sql, args = only(conn.calls, "FROM audit_logs")
    assert "tenant_id = $2" in sql and args[1] == tenant and owners == [tenant]


async def test_event_stats_read_every_tenant_only_for_the_platform_administrator(monkeypatch):
    from app.api.v1.endpoints import audit_logs

    conn = Recorder()
    owners = patch_acquire(monkeypatch, audit_logs, conn)
    await audit_logs.get_event_stats(days=7, tenant_id=None, db_pool=object(),
                                     current_user={"role": "platform_admin", "tenant_id": TENANT_A})
    _sql, args = only(conn.calls, "FROM audit_logs")
    assert args[1] is None and owners == [None]


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_failed_login_stats_bind_the_callers_tenant(monkeypatch, tenant):
    from app.api.v1.endpoints import audit_logs

    conn = Recorder(fetchrow={"total_failed": 0, "unique_ips": 0, "blocked_attempts": 0})
    owners = patch_acquire(monkeypatch, audit_logs, conn)
    await audit_logs.get_failed_login_stats(days=7, db_pool=object(),
                                            current_user={"role": "tenant_admin", "tenant_id": tenant})
    sql, args = only(conn.calls, "FROM login_attempts la")
    assert "up.tenant_id = $2::uuid" in sql and args[1] == tenant and owners == [tenant]


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_audit_query_appends_the_tenant_predicate_for_the_supplied_tenant(monkeypatch, tenant):
    from app.domain.services import audit_logger as module

    conn = Recorder()
    owners = patch_acquire(monkeypatch, module, conn)
    await module.AuditLogger(object(), signing_key="k").query(tenant_id=tenant, limit=5)
    sql, args = only(conn.calls, "FROM audit_logs a")
    assert bound_tenants(sql, args) == [UUID(tenant)] and owners == [tenant]


# --- billing and usage: the tenant is bound on every relation -------------------------------

@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_invoice_read_binds_the_callers_tenant_and_repeats_it_on_the_snapshot(monkeypatch, tenant):
    import app.core.db_utils as db_utils
    from app.api.v1.endpoints import billing

    conn = Recorder()
    owners = patch_acquire(monkeypatch, db_utils, conn)
    await billing._invoice_rows(object(), tenant, limit=5)
    sql, args = only(conn.calls, "FROM invoices i")
    assert "i.tenant_id=$1" in sql and "tenant_id=i.tenant_id" in sql
    assert args[0] == UUID(tenant) and owners == [tenant]


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_window_usage_binds_the_tenant_on_calls_and_on_the_transfer_parent(tenant):
    from app.domain.services import topup_usage

    conn = Recorder()
    window = {"key": "2026-11-01", "start": datetime(2026, 11, 1, tzinfo=timezone.utc),
              "end": datetime(2026, 12, 1, tzinfo=timezone.utc)}
    await topup_usage._window_usage_minutes(conn, tenant, [window])
    sql, args = only(conn.calls, "FROM unnest(")
    assert "c.tenant_id=$1::uuid" in sql and "parent.tenant_id=$1::uuid" in sql
    assert args[0] == tenant


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_ai_options_upsert_inserts_only_the_callers_tenant(tenant):
    from app.api.v1.endpoints.ai_options._shared import _upsert_tenant_config
    from app.domain.models.ai_config import AIProviderConfig

    conn = Recorder()
    await _upsert_tenant_config(conn, tenant, AIProviderConfig())
    sql, args = only(conn.calls, "INSERT INTO tenant_ai_configs")
    assert args[0] == tenant and "( tenant_id, llm_provider" in sql and "ON CONFLICT (tenant_id) DO UPDATE" in sql


async def test_compliance_hold_check_is_keyed_to_the_one_tenant_it_is_given():
    from app.domain.services import media_privacy

    conn = Recorder(fetchval=False)
    assert await media_privacy.has_active_compliance_hold(conn, TENANT_A) is False
    sql, args = only(conn.calls, "suspension_events")
    assert args == (TENANT_A,) and "se.target_id = $1" in sql and "held_tenant.id = $1" in sql


# --- dynamic lists: the fixed WHERE is the caller's tenant ----------------------------------

@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_action_list_and_count_start_from_the_callers_tenant(monkeypatch, tenant):
    import app.core.db_utils as db_utils
    from app.api.v1.endpoints import assistant_ws

    conn = Recorder(fetchval=0)
    owners = patch_acquire(monkeypatch, db_utils, conn)
    await assistant_ws.list_actions(
        current_user=SimpleNamespace(tenant_id=tenant, id=USER), db_client=SimpleNamespace(pool=object()),
        type=None, status=None, lead_id=None, from_date=None, to_date=None,
        sort_by="created_at", sort_dir="desc", page=1, page_size=20)
    assert len(conn.calls) == 2 and owners == [tenant]
    for sql, args in conn.calls:
        assert "WHERE tenant_id=$1::uuid" in flat(sql) and args[0] == tenant


async def test_action_list_refuses_a_caller_without_a_tenant():
    from fastapi import HTTPException

    from app.api.v1.endpoints import assistant_ws

    with pytest.raises(HTTPException) as refused:
        await assistant_ws.list_actions(
            current_user=SimpleNamespace(tenant_id=None, id=USER), db_client=SimpleNamespace(pool=object()),
            type=None, status=None, lead_id=None, from_date=None, to_date=None,
            sort_by="created_at", sort_dir="desc", page=1, page_size=20)
    assert refused.value.status_code == 403


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_stream_event_list_starts_from_the_callers_tenant(monkeypatch, tenant):
    from app.api.v1.endpoints import stream_events

    conn = Recorder()
    owners = patch_acquire(monkeypatch, stream_events, conn)
    await stream_events.list_stream_events(
        category=None, severity=None, since=None, cursor=None, limit=50,
        current_user=SimpleNamespace(tenant_id=tenant, id=USER), db_client=SimpleNamespace(pool=object()))
    sql, args = only(conn.calls, "FROM stream_events")
    assert "WHERE tenant_id = $1" in sql and args[0] == tenant and owners == [tenant]


# --- notification services: the action record and its receipt carry the tenant ---------------

def sms_service_without_provider():
    from app.services import sms_service

    service = sms_service.SMSService.__new__(sms_service.SMSService)
    service.db_pool = object()
    return sms_service, service


def email_service_without_connector():
    from app.services import email_service

    service = email_service.EmailService.__new__(email_service.EmailService)
    service.db_pool = object()
    service.supabase = object()  # no .table: the SQL path
    return email_service, service


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_sms_action_record_and_receipt_bind_the_tenant(monkeypatch, tenant):
    module, service = sms_service_without_provider()
    conn = Recorder()
    patch_acquire(monkeypatch, module, conn)
    action_id = await service._create_action_record(tenant, None, None, None, "system", "key", {})
    sql, args = only(conn.calls, "INSERT INTO assistant_actions")
    assert re.search(r"\(\s*id,\s*tenant_id,", sql) and "VALUES ($1, $2," in sql
    assert args[:2] == (action_id, tenant)
    await service._update_action_status(tenant, action_id, "sent", output_data={"id": 1}, error="e")
    sql, args = only(conn.calls, "UPDATE assistant_actions")
    assert bound_tenants(sql, args) == [tenant] and action_id in args


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_email_action_record_and_receipt_bind_the_tenant(monkeypatch, tenant):
    module, service = email_service_without_connector()
    conn = Recorder()
    patch_acquire(monkeypatch, module, conn)
    action_id = await service._create_action_record(tenant, None, None, None, "system", {})
    sql, args = only(conn.calls, "INSERT INTO assistant_actions")
    assert re.search(r"\(\s*id,\s*tenant_id,", sql) and "VALUES ($1, $2," in sql
    assert args[:2] == (action_id, tenant)
    await service._update_action_status(tenant, action_id, "sent", output_data={"id": 1}, error="e")
    sql, args = only(conn.calls, "UPDATE assistant_actions")
    assert bound_tenants(sql, args) == [tenant] and action_id in args and args[-1] == "pending"


# --- support receipts: a tenant matches its own receipts, platform staff the NULL-tenant ones ---

@pytest.mark.parametrize("tenant", [TENANT_A, TENANT_B, None], ids=["tenant-a", "tenant-b", "platform-staff"])
async def test_support_receipt_lookup_binds_the_tenant_or_null_exactly(tenant):
    from app.domain.services.support import receipts

    conn = Recorder()
    request_id = UUID("d0000000-0000-4000-8000-000000000000")
    assert await receipts.claim(conn, tenant_id=tenant, request_id=request_id, actor_id=USER,
                                operation="op") is None
    sql, args = only(conn.calls, "FROM support_request_receipts")
    assert "tenant_id IS NOT DISTINCT FROM $1::uuid" in sql and args == (tenant, request_id)
