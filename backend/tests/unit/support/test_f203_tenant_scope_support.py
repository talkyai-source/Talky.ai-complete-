"""F2-03, support slice: support SQL names its tenant explicitly, and the ratchet can see that it does.

Fake connections capture ``(sql, args)``. For each statement they prove the tenant predicate is in the SQL
and that the value bound to its placeholder is the acting tenant. The unread-flag and scanner-proof tests
fail on the code before this change. The rest characterise statements that were already scoped: the dynamic
SQL the scanner cannot prove (still baseline debt) and the premises of the reviewed exceptions recorded in
``tests/unit/tenant_sql_baseline.json``.
"""
from __future__ import annotations

import ast
import re
import runpy
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.services.support import conversation_store, receipts
from app.domain.services.support import ticket_store as store
from app.domain.services.support.bot import diagnostics as d
from app.domain.services.support.errors import SupportForbidden

BACKEND = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

T = str(uuid4())  # the tenant that is acting
U = str(uuid4())  # the person acting
OTHER = str(uuid4())
TICKET, MESSAGE, CONVERSATION, REQUEST = uuid4(), uuid4(), uuid4(), uuid4()

# ``tenant_id = $n`` / ``alias.tenant_id = $n``: the placeholder that carries a tenant value.
_TENANT_PLACEHOLDER = re.compile(r"\b(?:[a-z_]\w*\.)?tenant_id\s*=\s*\$(\d+)", re.I)


def tenant_placeholders(sql: str) -> list[int]:
    return [int(number) for number in _TENANT_PLACEHOLDER.findall(sql)]


class CapturingConn:
    """Records every statement; answers from ``rows`` keyed by a fragment of the SQL."""

    def __init__(self, rows: dict | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[str, tuple]] = []

    def _pick(self, sql: str):
        for key, value in self.rows.items():
            if key in sql:
                return value
        return None

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        value = self._pick(sql)
        return value if isinstance(value, list) else []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        value = self._pick(sql)
        return (value[0] if value else None) if isinstance(value, list) else value

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._pick(sql)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))

    def is_in_transaction(self) -> bool:
        return True


# ---- the unread flag: every table is filtered by the tenant itself ------------------------------------------

async def _list(conn):
    return await store.list_customer_tickets(conn, tenant_id=T, user_id=U, team=False, status=None,
                                             after=None, limit=26)


async def _unread(conn):
    return await store.unread_ticket_ids(conn, tenant_id=T, user_id=U)


@pytest.mark.parametrize("run", [_list, _unread], ids=["list_customer_tickets", "unread_ticket_ids"])
async def test_unread_subqueries_filter_messages_and_reads_by_the_tenant_argument(run):
    conn = CapturingConn()
    await run(conn)
    (sql, args), = conn.calls
    assert args[0] == T and args[1] == U
    # tickets (t), the staff message looked at (sm) and the viewer's read marker (r) each carry the tenant
    # argument themselves, not only a join back to a ticket row.
    for alias in ("t", "sm", "r"):
        assert re.search(rf"\b{alias}\.tenant_id\s*=\s*\$1\b", sql), f"{alias} is not bound to $1: {sql}"
    assert OTHER not in args
    assert all(args[number - 1] == T for number in tenant_placeholders(sql))


# ---- the ratchet's own proof can see the statements that were retired --------------------------------------

@pytest.fixture(scope="module")
def inventory():
    scanner = runpy.run_path(str(BACKEND / "scripts" / "tenant_sql_inventory.py"))
    schema = runpy.run_path(str(BACKEND / "scripts" / "rls_acquire_inventory.py"))
    protected, tenants = schema["discover_rls_tables"](BACKEND)

    def scan(relative: str) -> list[dict]:
        source = (BACKEND / relative).read_text(encoding="utf-8-sig")
        return scanner["scan_source"](source, relative_path=relative, protected_tables=protected,
                                      tenant_tables=tenants)
    return SimpleNamespace(scan=scan, tenant_tables=tenants)


def test_bot_tool_sql_is_visible_to_and_proved_by_the_tenant_scanner(inventory):
    findings = inventory.scan("app/domain/services/support/bot/diagnostics.py")
    assert findings, "the bot tools must still be inventoried"
    unproved = [(row["function"], row["line"]) for row in findings if row["unresolved"] or not row["scoped"]]
    assert not unproved, f"support bot SQL the scanner cannot prove tenant-scoped: {unproved}"


def test_unread_queries_are_proved_by_the_tenant_scanner(inventory):
    findings = inventory.scan("app/domain/services/support/ticket_store.py")
    by_function = {row["function"]: row for row in findings}
    for name in ("list_customer_tickets", "unread_ticket_ids"):
        assert by_function[name]["scoped"] is True, name


# ---- every statement a bot tool runs binds the executor's tenant --------------------------------------------

CID, CALL, JOB = str(uuid4()), str(uuid4()), str(uuid4())
TOOL_ROWS = {
    "FROM tenants": {"plan_id": "plan-1", "subscription_status": "active", "status": "active"},
    "FROM plans": {"name": "Basic", "price": 99},
    "FROM campaigns": {"id": CID, "name": "C", "status": "running", "direction": "outbound", "updated_at": None},
    "FROM dialer_jobs": [{"failure_reason": "out_of_minutes", "failure_category": None, "n": 3}],
    "FROM calls": {"id": CALL, "campaign_id": None, "status": "completed", "outcome": "ok", "direction": "outbound",
                   "started_at": None, "ended_at": None, "duration_seconds": 4, "dialer_job_id": JOB},
    "FROM tenant_phone_numbers": [{"e164": "+442046132300", "status": "verified", "verified_at": None,
                                   "provider": "x"}],
    "FROM connectors": [{"type": "crm", "provider": "p", "name": "n", "status": "ok",
                         "provider_revocation_status": None}],
    "FROM support_tickets": [{"id": "i", "number": 7, "subject": "s", "status": "open", "category": "bug"}],
}
TOOL_CALLS = [
    ("get_account_overview", {}),
    ("get_campaign_status", {"campaign": CID}),
    ("get_campaign_status", {"campaign": "Their Campaign"}),
    ("get_recent_call_issues", {}),
    ("get_call_details", {"call_id": CALL}),
    ("get_phone_numbers_status", {}),
    ("get_connectors_status", {}),
    ("find_open_tickets", {}),
]


class _NoBlockRedis:
    async def get(self, key):
        return None


async def test_every_statement_the_bot_tools_run_binds_the_executor_tenant(monkeypatch):
    conn = CapturingConn(TOOL_ROWS)

    @asynccontextmanager
    async def acquire(pool, tenant):
        assert tenant == T
        yield conn

    async def minutes(connection, tenant):
        assert tenant == T
        return SimpleNamespace(state="known", exhausted=False, as_dict=lambda: {})

    monkeypatch.setattr(d, "acquire_with_tenant", acquire)
    monkeypatch.setattr(d, "compute_minutes_status", minutes)
    executor = d.ToolExecutor(pool=object(), redis=_NoBlockRedis(), tenant_id=T, user_id=U)
    for name, arguments in TOOL_CALLS:
        outcome = await executor.execute(name, arguments)
        assert "error" not in outcome.result, (name, outcome.result)

    assert len({sql for sql, _ in conn.calls}) == 11, "every bot statement must be exercised"
    for sql, args in conn.calls:
        placeholders = tenant_placeholders(sql)
        if placeholders:
            assert all(args[number - 1] == T for number in placeholders), (sql, args)
        elif "FROM tenants WHERE id = $1" in sql:
            assert args == (T,), (sql, args)  # tenants is keyed by id: the id IS the executor's tenant
        else:
            assert "FROM plans WHERE id = $1" in sql, f"statement names no tenant: {sql}"  # global plan catalogue
        assert OTHER not in args


# ---- platform-staff exceptions: the premises their baseline reasons state ------------------------------------

STORE_READS = {
    "fetch_ticket": lambda conn: store.fetch_ticket(conn, TICKET, T),
    "fetch_ticket_for_update": lambda conn: store.fetch_ticket(conn, TICKET, T, for_update=True),
    "fetch_messages": lambda conn: store.fetch_messages(conn, TICKET, T, include_internal=False),
    "fetch_events": lambda conn: store.fetch_events(conn, TICKET, T),
}


@pytest.mark.parametrize("name", sorted(STORE_READS))
async def test_ticket_reads_that_join_global_accounts_still_bind_the_tenant(name):
    conn = CapturingConn()
    await STORE_READS[name](conn)
    (sql, args), = conn.calls
    placeholders = tenant_placeholders(sql)
    assert placeholders, sql
    assert all(args[number - 1] == T for number in placeholders), (sql, args)
    assert "user_profiles" in sql  # the join the baseline exception explains (display name by primary key)


@pytest.mark.parametrize("staff", [False, True])
async def test_attach_files_adopts_tenantless_uploads_only_when_asked_by_staff(staff):
    conn = CapturingConn({"UPDATE support_ticket_attachments": [{"id": MESSAGE}]})
    await store.attach_files(conn, tenant_id=T, ticket_id=TICKET, message_id=MESSAGE, attachment_ids=[MESSAGE],
                             uploader_id=U, staff=staff, now=NOW)
    (sql, args), = conn.calls
    assert args[0] == T and args[4] == U and args[6] is staff
    assert "AND (tenant_id = $1 OR ($7 AND tenant_id IS NULL))" in sql  # NULL tenant is reachable only via $7


async def test_attach_files_refuses_when_any_upload_is_not_available():
    conn = CapturingConn({"UPDATE support_ticket_attachments": []})
    with pytest.raises(SupportForbidden):
        await store.attach_files(conn, tenant_id=T, ticket_id=TICKET, message_id=MESSAGE,
                                 attachment_ids=[MESSAGE], uploader_id=U, staff=False, now=NOW)


def test_knowledge_base_articles_stay_global_content_without_a_tenant_column(inventory):
    # The search_articles exception rests on this; a tenant_id column would make its predicate mandatory.
    assert "support_kb_articles" not in inventory.tenant_tables


# ---- statements the scanner cannot prove (dynamic SET, FOR UPDATE, DO UPDATE, IS NOT DISTINCT) ---------------
# They stay in the baseline as debt; these show each one already binds the acting tenant explicitly.

DEBT_STATEMENTS = {
    "update_ticket": (lambda c: store.update_ticket(c, TICKET, T, now=NOW, status="open"),
                      {"UPDATE support_tickets": 2}),
    "update_conversation": (lambda c: conversation_store.update_conversation(c, CONVERSATION, T, now=NOW,
                                                                             status="active"), {}),
    "fetch_conversation": (lambda c: conversation_store.fetch_conversation(c, CONVERSATION, T), {}),
    "fetch_conversation_for_update": (
        lambda c: conversation_store.fetch_conversation(c, CONVERSATION, T, for_update=True), {}),
    "tenant_owns": (lambda c: store.tenant_owns(c, "calls", MESSAGE, T), {}),
}


@pytest.mark.parametrize("name", sorted(DEBT_STATEMENTS))
async def test_dynamic_sql_the_scanner_cannot_resolve_still_binds_the_acting_tenant(name):
    run, rows = DEBT_STATEMENTS[name]
    conn = CapturingConn(rows)
    await run(conn)
    (sql, args), = conn.calls
    placeholders = tenant_placeholders(sql)
    assert placeholders, sql
    assert all(args[number - 1] == T for number in placeholders), (sql, args)


async def test_read_marker_upsert_binds_the_tenant_and_never_updates_another_tenants_row():
    conn = CapturingConn()
    await store.mark_read(conn, user_id=U, ticket_id=TICKET, tenant_id=T, now=NOW)
    (sql, args), = conn.calls
    assert "(user_id, ticket_id, tenant_id, last_seen_at)" in sql and args[:3] == (U, TICKET, T)
    assert "WHERE support_ticket_reads.tenant_id = EXCLUDED.tenant_id" in sql


async def test_receipt_lookup_matches_the_acting_tenant_or_the_platform_scope_only():
    conn = CapturingConn()
    await receipts.claim(conn, tenant_id=T, request_id=REQUEST, actor_id=U, operation="ticket.create")
    sql, args = conn.calls[-1]
    assert "tenant_id IS NOT DISTINCT FROM $1::uuid" in sql  # NULL only for platform-scope (article) receipts
    assert args == (T, REQUEST)


def _calls_named(path: Path, name: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", None)) == name]


def _app_files_mentioning(text: str) -> list[Path]:
    return [path for path in sorted((BACKEND / "app").rglob("*.py"))
            if "__pycache__" not in path.parts and text in path.read_text(encoding="utf-8-sig")]


def test_the_cross_tenant_ticket_lookup_is_only_called_by_the_platform_staff_service():
    callers = {path.relative_to(BACKEND / "app").as_posix() for path in _app_files_mentioning("fetch_ticket_tenant")
               if path.name != "ticket_store.py" and _calls_named(path, "fetch_ticket_tenant")}
    assert callers == {"domain/services/support/staff_service.py"}


def test_only_the_staff_service_passes_staff_true_to_attach_files():
    seen = {}
    for path in _app_files_mentioning("attach_files"):
        for call in _calls_named(path, "attach_files"):
            flag = {keyword.arg: keyword.value for keyword in call.keywords}.get("staff")
            assert isinstance(flag, ast.Constant), f"{path.name}: staff must be a literal at the call site"
            seen.setdefault(path.name, set()).add(flag.value)
    assert seen == {"staff_service.py": {True}, "ticket_service.py": {False}}
