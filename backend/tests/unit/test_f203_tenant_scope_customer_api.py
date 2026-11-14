"""F2-03 (customer-data slice): calls, recordings, DNC, knowledge and payment-event
statements name their tenant explicitly.

Production enforces row-level security, but the project rule is an explicit
``tenant_id`` predicate in the SQL itself: a role that bypasses RLS (the prod
application role did) must not be able to read or write another tenant's row.
Every test runs real code on a fake connection that records ``(sql, args)``.

Section 1 - statements that lacked a predicate and now carry one:

* ``apply_topup_event`` read the order's ledger rows by order id alone, after it
  had already verified the order's tenant (credit lookup and reversal lookup).
* ``RecordingService._resolve_call_uuid`` made its tenant optional
  (``$2::uuid IS NULL OR tenant_id = $2``), so an unparsable tenant searched
  every tenant; ``_update_call_recording_url`` updated ``calls`` by id alone.
* ``_finalise_test_call`` updated ``calls`` by id alone.
* The live-call, rejected-inbound, call-issue and recording lists joined
  ``campaigns`` / ``calls`` by id alone, as ``list_calls`` and ``get_call``
  already do not.
Where the statement is simple enough it is also executed on SQLite beside a
second tenant's rows.

Section 2 - the claims the baseline exceptions for this slice rest on. These
statements already bind the tenant (or are platform-wide or provider-discovery
lookups by design); the F2-03 scanner cannot prove them (dynamic SQL, EXISTS,
CTE, ``ON CONFLICT``, ``NOW()`` in VALUES, a nullable platform tenant). They
pass before and after the change; they exist so a later edit that drops the
predicate fails here, not only in the ratchet.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.api.v1.endpoints.calls as calls_module
from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaign_test_ws, recordings
from app.domain.services import billing_topup_events as events
from app.domain.services import dnc_service, recording_service
from app.services.scripts.knowledge import ingest_service
from app.services.scripts.knowledge.enricher import NodeEnrichment
from tests.unit.test_billing_topup_events import apply, fixture_data

TENANT_A = UUID("a1111111-1111-4111-8111-111111111111")
TENANT_B = UUID("b2222222-2222-4222-8222-222222222222")
CAMPAIGN = UUID("c3333333-3333-4333-8333-333333333333")
CALL = UUID("d4444444-4444-4444-8444-444444444444")
PBX_CHANNEL = "talky-out-da0f496d-1234-4a2b-9c3d-abcdef012345"


# ── shared helpers ─────────────────────────────────────────────────────────


def squash(sql: str) -> str:
    return " ".join(sql.split())


def tight(sql: str) -> str:
    """Squashed SQL with no space around ``=`` so predicates compare as text."""
    return re.sub(r"\s*=\s*", "=", squash(sql))


def bound_tenants(sql: str, args: tuple) -> list:
    """Every value the statement compares ``tenant_id`` with, read from its bind slots."""
    return [args[int(n) - 1] for n in re.findall(r"\btenant_id\s*=\s*\$(\d+)", sql, re.I)]


def joins_of(sql: str, target: str) -> list[str]:
    """The ON conditions of every ``JOIN <target>`` in the statement."""
    return re.findall(rf"JOIN {re.escape(target)} ON (.*?)(?= WHERE | LEFT JOIN | JOIN |$)", tight(sql))


class NoTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class SqlConn:
    """Fake asyncpg connection: records ``(sql, args)`` and answers with canned values."""

    def __init__(self, *, row=None, rows=(), scalar=None, insert_row=None):
        self.calls: list[tuple[str, tuple]] = []
        self.row, self.rows, self.scalar, self.insert_row = row, list(rows), scalar, insert_row

    async def fetchrow(self, sql, *args):
        self.calls.append((squash(sql), args))
        if self.insert_row is not None or sql.lstrip().upper().startswith("INSERT"):
            return self.insert_row if sql.lstrip().upper().startswith("INSERT") else self.row
        return self.row

    async def fetch(self, sql, *args):
        self.calls.append((squash(sql), args))
        return self.rows

    async def fetchval(self, sql, *args):
        self.calls.append((squash(sql), args))
        return self.scalar

    async def execute(self, sql, *args):
        self.calls.append((squash(sql), args))
        return "UPDATE 1"

    def transaction(self):
        return NoTransaction()


def patch_acquire(monkeypatch, target, conn):
    """Replace ``acquire_with_tenant`` on ``target`` (module or dotted path); keeps each call."""
    seen: list[tuple] = []

    @asynccontextmanager
    async def fake(_pool, tenant_id, **kwargs):
        seen.append((tenant_id, kwargs))
        yield conn

    if isinstance(target, str):
        monkeypatch.setattr(target, fake)
    else:
        monkeypatch.setattr(target, "acquire_with_tenant", fake)
    return seen


def run_on_sqlite(db, sql: str, args: tuple):
    """Execute the recorded statement as sent: ``$n`` slots become named binds."""
    statement = re.sub(r"::[a-z_]+(?:\[\])?", "", sql, flags=re.I).replace("NOW()", "CURRENT_TIMESTAMP")
    statement = re.sub(r"\s+RETURNING\b.*$", "", statement, flags=re.I | re.S)
    binds = {f"p{n}": str(a) if isinstance(a, UUID) else a for n, a in enumerate(args, 1)}
    return db.execute(re.sub(r"\$(\d+)", r":p\1", statement), binds)


def sqlite_with(schema: str, insert: str = "", rows=()):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_function("GREATEST", 2, lambda a, b: max(a, b))
    db.executescript(schema)
    if insert:
        db.executemany(insert, rows)
    return db


# ═══════════════════════════════════════════════════════════════════════════
# 1a. apply_topup_event: ledger reads name the tenant of the verified order
# ═══════════════════════════════════════════════════════════════════════════

CREDIT_LOOKUP = "COUNT(*) AS count"
REVERSAL_LOOKUP = "kind IN ('refund','dispute')"


def record_statements(fixture) -> list[tuple[str, tuple]]:
    """Wrap the fixture's connection so every statement and its arguments are kept."""
    seen: list[tuple[str, tuple]] = []
    for name in ("fetch", "fetchrow", "fetchval", "execute"):
        inner = getattr(fixture.conn, name)

        async def call(sql, *args, _inner=inner):
            seen.append((squash(sql), args))
            return await _inner(sql, *args)

        setattr(fixture.conn, name, call)
    return seen


def statement_with(seen, marker: str) -> tuple[str, tuple]:
    hits = [(sql, args) for sql, args in seen if marker in sql]
    assert len(hits) == 1, f"expected exactly one statement containing {marker!r}, got {len(hits)}"
    return hits[0]


@pytest.mark.parametrize("marker", [CREDIT_LOOKUP, REVERSAL_LOOKUP], ids=["credit-lookup", "reversal-lookup"])
@pytest.mark.parametrize("order", ["first", "second"])
async def test_ledger_reads_bind_the_tenant_of_the_order_they_just_verified(marker, order):
    fixture = fixture_data()
    seen = record_statements(fixture)
    await apply(fixture)
    sql, args = statement_with(seen, marker)
    assert bound_tenants(sql, args) == [fixture.store["order"]["tenant_id"]], (
        f"ledger read has no tenant predicate bound to the order's tenant\n{sql}\n{args}"
    )


async def test_another_tenants_ledger_rows_for_the_same_order_id_are_not_counted():
    fixture = fixture_data()
    seen = record_statements(fixture)
    await apply(fixture)
    order = fixture.store["order"]
    own, other = str(order["tenant_id"]), str(uuid4())
    db = sqlite_with(
        "CREATE TABLE billing_ledger (id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, order_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, minutes_delta INTEGER, amount_cents INTEGER, currency TEXT);",
        "INSERT INTO billing_ledger (tenant_id, order_id, kind, minutes_delta, amount_cents, currency)"
        " VALUES (?,?,?,?,?,?)",
        [(own, str(order["id"]), "topup", 250, 2500, "gbp"),
         (other, str(order["id"]), "topup", 999, 9999, "gbp"),
         (other, str(order["id"]), "refund", -999, -9999, "gbp")],
    )
    credit = run_on_sqlite(db, *statement_with(seen, CREDIT_LOOKUP)).fetchone()
    assert (credit["count"], credit["minutes"], credit["amount"]) == (1, 250, 2500)
    assert run_on_sqlite(db, *statement_with(seen, REVERSAL_LOOKUP)).fetchall() == []


# ═══════════════════════════════════════════════════════════════════════════
# 1b. RecordingService: call resolution and the recording_url update
# ═══════════════════════════════════════════════════════════════════════════


def recording_service_with(monkeypatch, conn):
    seen = patch_acquire(monkeypatch, recording_service, conn)
    service = recording_service.RecordingService(object(), s3_client=SimpleNamespace(bucket="b", region="r"))
    return service, seen


async def test_call_id_lookup_without_a_usable_tenant_searches_no_tenant_at_all():
    conn = SqlConn(row={"id": uuid4()})
    service = recording_service.RecordingService(object(), s3_client=SimpleNamespace(bucket="b", region="r"))
    assert await service._resolve_call_uuid(PBX_CHANNEL, "not-a-uuid", conn) is None
    assert conn.calls == [], "an unparsable tenant must not turn the lookup into a cross-tenant search"


async def test_call_id_lookup_binds_the_tenant_with_no_optional_escape():
    conn = SqlConn(row={"id": CALL})
    service = recording_service.RecordingService(object(), s3_client=SimpleNamespace(bucket="b", region="r"))
    assert await service._resolve_call_uuid(PBX_CHANNEL, str(TENANT_A), conn) == CALL
    ((sql, args),) = conn.calls
    assert bound_tenants(sql, args) == [TENANT_A]
    assert not re.search(r"\bOR\b", sql, re.I), f"tenant predicate is optional: {sql}"


async def test_call_id_lookup_never_returns_another_tenants_newer_call():
    conn = SqlConn(row={"id": CALL})
    service = recording_service.RecordingService(object(), s3_client=SimpleNamespace(bucket="b", region="r"))
    await service._resolve_call_uuid(PBX_CHANNEL, str(TENANT_A), conn)
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, external_call_uuid TEXT, created_at TEXT);",
        "INSERT INTO calls VALUES (?,?,?,?)",
        [("own-call", str(TENANT_A), PBX_CHANNEL, "2026-11-01"),
         ("newer-call-of-b", str(TENANT_B), PBX_CHANNEL, "2026-11-02")],
    )
    assert run_on_sqlite(db, sql, args).fetchone()["id"] == "own-call"


@pytest.mark.parametrize("tenant", [TENANT_A, TENANT_B], ids=["tenant-a", "tenant-b"])
async def test_recording_url_update_names_the_tenant(monkeypatch, tenant):
    conn = SqlConn()
    service, acquired = recording_service_with(monkeypatch, conn)
    await service._update_call_recording_url(str(CALL), str(tenant), uuid4())
    ((sql, args),) = conn.calls
    assert acquired and str(acquired[0][0]) == str(tenant)
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the caller's tenant\n{sql}\n{args}"


async def test_recording_url_update_cannot_reach_another_tenants_call(monkeypatch):
    conn = SqlConn()
    service, _ = recording_service_with(monkeypatch, conn)
    await service._update_call_recording_url(str(CALL), str(TENANT_A), uuid4())
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, recording_url TEXT, updated_at TEXT);",
        "INSERT INTO calls VALUES (?,?,NULL,NULL)",
        [(str(CALL), str(TENANT_B))],  # the call id belongs to tenant B
    )
    assert run_on_sqlite(db, sql, args).rowcount == 0
    assert db.execute("SELECT recording_url FROM calls").fetchone()[0] is None
    owner_args = (args[0], args[1], TENANT_B)
    assert run_on_sqlite(db, sql, owner_args).rowcount == 1


# ═══════════════════════════════════════════════════════════════════════════
# 1c. campaign test call: closing the row out
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("tenant", [TENANT_A, TENANT_B], ids=["tenant-a", "tenant-b"])
async def test_finalising_a_test_call_names_the_tenant(monkeypatch, tenant):
    conn = SqlConn()
    patch_acquire(monkeypatch, "app.core.db_utils.acquire_with_tenant", conn)
    await campaign_test_ws._finalise_test_call(
        SimpleNamespace(db_pool=object()), str(tenant), str(CALL), time.time() - 3
    )
    ((sql, args),) = conn.calls
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the session's tenant\n{sql}\n{args}"


async def test_finalising_cannot_close_another_tenants_call(monkeypatch):
    conn = SqlConn()
    patch_acquire(monkeypatch, "app.core.db_utils.acquire_with_tenant", conn)
    await campaign_test_ws._finalise_test_call(
        SimpleNamespace(db_pool=object()), str(TENANT_A), str(CALL), time.time() - 3
    )
    sql, args = conn.calls[0]
    db = sqlite_with(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, status TEXT, duration_seconds INTEGER,"
        " ended_at TEXT);",
        "INSERT INTO calls VALUES (?,?,'in_progress',NULL,NULL)",
        [(str(CALL), str(TENANT_B))],
    )
    assert run_on_sqlite(db, sql, args).rowcount == 0
    assert db.execute("SELECT status FROM calls").fetchone()[0] == "in_progress"


# ═══════════════════════════════════════════════════════════════════════════
# 1d. call and recording lists: every tenant-owned relation names the tenant
# ═══════════════════════════════════════════════════════════════════════════


def user(tenant=TENANT_A) -> CurrentUser:
    return CurrentUser(id="user-1", email="user@example.com", tenant_id=str(tenant))


DB_CLIENT = SimpleNamespace(pool=object())


@pytest.mark.parametrize("filtered", [False, True], ids=["unfiltered", "campaign-and-direction"])
async def test_live_calls_join_campaigns_within_the_tenant(monkeypatch, filtered):
    conn = SqlConn()
    acquired = patch_acquire(monkeypatch, calls_module, conn)
    await calls_module.list_live_calls(
        campaign_id=CAMPAIGN if filtered else None,
        direction="inbound" if filtered else None,
        recent_window_seconds=60,
        current_user=user(),
        db_client=DB_CLIENT,
    )
    ((sql, args),) = conn.calls
    assert [tenant for tenant, _ in acquired] == [str(TENANT_A)]
    assert bound_tenants(sql, args) == [str(TENANT_A)]
    assert joins_of(sql, "campaigns camp") == ["camp.id=c.campaign_id AND camp.tenant_id=c.tenant_id"]


@pytest.mark.parametrize("filtered", [False, True], ids=["unfiltered", "campaign"])
async def test_rejected_inbound_calls_join_campaigns_within_the_tenant(monkeypatch, filtered):
    conn = SqlConn()
    patch_acquire(monkeypatch, calls_module, conn)
    await calls_module.list_rejected_inbound_calls(
        page=1, page_size=50, campaign_id=CAMPAIGN if filtered else None,
        current_user=user(), db_client=DB_CLIENT,
    )
    ((sql, args),) = conn.calls
    assert args[0] == TENANT_A
    assert tight(sql).count("r.tenant_id=$1") == 1 and tight(sql).count("c.tenant_id=$1") == 1
    assert joins_of(sql, "campaigns camp") == [
        "camp.id=r.campaign_id AND camp.tenant_id=r.tenant_id",
        "camp.id=c.campaign_id AND camp.tenant_id=c.tenant_id",
    ]


@pytest.fixture
def no_minutes_lookup(monkeypatch):
    async def minutes(_tenant):
        return SimpleNamespace(exhausted=False, state="known")

    monkeypatch.setattr("app.domain.services.minutes_quota.tenant_minutes_status", minutes)


@pytest.mark.parametrize("filtered", [False, True], ids=["unfiltered", "campaign"])
async def test_outbound_call_issues_stay_within_the_tenant_in_every_relation(monkeypatch, no_minutes_lookup, filtered):
    conn = SqlConn()
    patch_acquire(monkeypatch, calls_module, conn)
    await calls_module.list_call_issues(
        campaign_id=str(CAMPAIGN) if filtered else None,
        window_minutes=60, direction="outbound", current_user=user(), db_client=DB_CLIENT,
    )
    sql, args = conn.calls[0]
    assert bound_tenants(sql, args) == [str(TENANT_A)]
    assert joins_of(sql, "campaigns camp") == ["camp.id=dj.campaign_id AND camp.tenant_id=dj.tenant_id"]
    superseding = re.search(r"FROM calls c2 WHERE (.*?)\)", tight(sql)).group(1)
    assert "c2.tenant_id=dj.tenant_id" in superseding, f"newer-call check reads any tenant's calls: {superseding}"


@pytest.mark.parametrize("filtered", [False, True], ids=["unfiltered", "campaign"])
async def test_inbound_call_issues_bind_the_tenant_with_and_without_a_campaign(monkeypatch, filtered):
    conn = SqlConn()
    patch_acquire(monkeypatch, calls_module, conn)
    await calls_module.list_call_issues(
        campaign_id=str(CAMPAIGN) if filtered else None,
        window_minutes=60, direction="inbound", current_user=user(), db_client=DB_CLIENT,
    )
    ((sql, args),) = conn.calls
    assert args[0] == TENANT_A and "c.tenant_id=$1" in tight(sql)
    assert joins_of(sql, "campaigns camp") == ["camp.id=c.campaign_id AND camp.tenant_id=c.tenant_id"]
    assert (args[-1] == CAMPAIGN) is filtered


@pytest.mark.parametrize("call_filter", [None, str(CALL)], ids=["all-calls", "one-call"])
async def test_recording_list_joins_calls_within_the_tenant_and_counts_only_the_tenant(monkeypatch, call_filter):
    conn = SqlConn()
    patch_acquire(monkeypatch, recordings, conn)
    await recordings.list_recordings(
        call_id=call_filter, page=1, page_size=10, current_user=user(), db_client=DB_CLIENT,
    )
    (listing, list_args), (count, count_args) = conn.calls
    assert bound_tenants(listing, list_args) == [str(TENANT_A)] == bound_tenants(count, count_args)
    assert joins_of(listing, "calls c") == ["c.id=r.call_id AND c.tenant_id=r.tenant_id"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. Claims the baseline exceptions rest on (pass before and after)
# ═══════════════════════════════════════════════════════════════════════════


class AuditStub:
    def __init__(self):
        self.events = []

    async def log(self, **kwargs):
        self.events.append(kwargs)


async def test_recording_policy_upsert_can_only_replace_the_callers_own_row(monkeypatch):
    conn = SqlConn(row={"default_consent_mode": "one_party", "updated_at": None})
    patch_acquire(monkeypatch, recordings, conn)
    admin = CurrentUser(id=str(uuid4()), email="a@example.com", tenant_id=str(TENANT_A), role="tenant_admin")
    await recordings.put_recording_policy(
        body=recordings.RecordingPolicyBody(default_consent_mode="one_party", retention_days=30),
        user=admin, db_pool=object(), audit_logger=AuditStub(),
    )
    ((sql, args),) = conn.calls
    assert args[0] == TENANT_A and "tenant_id, default_consent_mode" in sql and "VALUES ($1," in sql
    assert "ON CONFLICT (tenant_id) DO UPDATE" in sql, "the conflict key must be the tenant itself"


async def test_orphan_audio_check_and_failed_row_bind_the_tenant(monkeypatch):
    conn = SqlConn(scalar=True)
    service, _ = recording_service_with(monkeypatch, conn)
    await service._discard_unregistered_object(str(CALL), str(TENANT_A), "local", "x.wav")
    ((sql, args),) = conn.calls
    assert bound_tenants(sql, args) == [TENANT_A] and args[1:] == ("local", "x.wav")

    conn = SqlConn()
    service, _ = recording_service_with(monkeypatch, conn)
    await service._mark_upload_failed(str(CALL), str(TENANT_A), str(CAMPAIGN), "boom")
    ((sql, args),) = conn.calls
    assert "INSERT INTO recordings_s3 ( call_id, tenant_id," in sql and "ON CONFLICT DO NOTHING" in sql
    assert args[:3] == (CALL, TENANT_A, CAMPAIGN), "the failed row must carry the caller's tenant"


def dnc_conn(tenant):
    return SqlConn(
        insert_row=None,  # the INSERT conflicts, so the refresh UPDATE runs
        row={"id": uuid4(), "tenant_id": tenant, "normalized_number": "+14155550123", "source": "manual_admin",
             "reason": None, "expires_at": None, "created_at": None},
    )


DNC_SCHEMA = (
    "CREATE TABLE dnc_entries (id INTEGER PRIMARY KEY, tenant_id TEXT, normalized_number TEXT, source TEXT,"
    " reason TEXT, expires_at TEXT, created_at TEXT, updated_at TEXT);"
)


def dnc_db():
    """The same suppression held by tenant A, tenant B and the platform-wide (NULL tenant) list."""
    return sqlite_with(
        DNC_SCHEMA,
        "INSERT INTO dnc_entries (tenant_id, normalized_number, source) VALUES (?, '+14155550123', 'manual_admin')",
        [(str(TENANT_A),), (str(TENANT_B),), (None,)],
    )


def refreshed_owners(db):
    return sorted(str(row[0]) for row in db.execute("SELECT tenant_id FROM dnc_entries WHERE updated_at IS NOT NULL"))


@pytest.mark.parametrize("tenant", [TENANT_A, None], ids=["tenant", "platform-wide"])
async def test_dnc_add_refresh_touches_only_the_named_tenants_row(monkeypatch, tenant):
    conn = dnc_conn(tenant)
    patch_acquire(monkeypatch, dnc_service, conn)
    await dnc_service.DNCService(object()).add(
        tenant_id=str(tenant) if tenant else None, e164="+14155550123", source="manual_admin",
    )
    (insert_sql, insert_args), (update_sql, update_args) = conn.calls
    assert insert_args[0] == (str(tenant) if tenant else None) == update_args[0]
    db = dnc_db()
    assert run_on_sqlite(db, update_sql, update_args).rowcount == 1
    assert refreshed_owners(db) == [str(tenant) if tenant else "None"]


@pytest.mark.parametrize("tenant", [TENANT_A, None], ids=["tenant", "platform-wide"])
async def test_dnc_bulk_import_refresh_touches_only_the_named_tenants_row(monkeypatch, tenant):
    conn = SqlConn(insert_row=None, row={"id": 1})
    patch_acquire(monkeypatch, dnc_service, conn)
    result = await dnc_service.DNCService(object()).bulk_import(
        tenant_id=str(tenant) if tenant else None, numbers=["+14155550123"], source="manual_admin",
    )
    assert result["accepted_count"] == 1
    (insert_sql, insert_args), (update_sql, update_args) = conn.calls
    assert insert_args[0] == (str(tenant) if tenant else None) == update_args[0]
    db = dnc_db()
    assert run_on_sqlite(db, update_sql, update_args).rowcount == 1
    assert refreshed_owners(db) == [str(tenant) if tenant else "None"]


async def test_test_call_row_is_inserted_for_the_tenant_that_owns_the_locked_campaign(monkeypatch):
    conn = SqlConn(row={"direction": "outbound"})
    patch_acquire(monkeypatch, "app.core.db_utils.acquire_with_tenant", conn)
    session = SimpleNamespace(call_id=str(CALL), talklee_call_id=None)
    config = SimpleNamespace(prompt_template="t", prompt_version="v", prompt_hash="h")
    await campaign_test_ws._record_test_call(
        SimpleNamespace(db_pool=object()), str(TENANT_A), str(CAMPAIGN), session, config
    )
    (lock_sql, lock_args), (insert_sql, insert_args) = conn.calls
    assert bound_tenants(lock_sql, lock_args) == [TENANT_A] and "FOR SHARE" in lock_sql
    assert "( id, tenant_id, campaign_id," in insert_sql
    assert insert_args[1] == TENANT_A and insert_args[2] == CAMPAIGN


async def test_websocket_tenant_bootstrap_reads_only_the_verified_subjects_profile(monkeypatch):
    conn = SqlConn(row={"tenant_id": TENANT_A})
    acquired = patch_acquire(monkeypatch, "app.core.db_utils.acquire_with_tenant", conn)
    assert await campaign_test_ws._resolve_user_tenant(object(), "user-1") == str(TENANT_A)
    ((sql, args),) = conn.calls
    assert sql == "SELECT tenant_id FROM user_profiles WHERE id = $1" and args == ("user-1",)
    assert acquired == [(None, {"user_id": "user-1"})]


class KnowledgeConn(SqlConn):
    async def fetchval(self, sql, *args):
        self.calls.append((squash(sql), args))
        if "INSERT INTO campaign_knowledge" in sql or "SET status = 'ready'" in sql:
            return str(uuid4())
        return 10 if "SUM(token_count)" in sql else 2

    async def execute(self, sql, *args):
        self.calls.append((squash(sql), args))
        return "UPDATE 1"


async def test_knowledge_statements_bind_the_tenant_the_caller_passed():
    tenant, campaign, source = str(TENANT_A), str(CAMPAIGN), str(uuid4())
    conn = KnowledgeConn()
    await ingest_service.create_processing_source(
        object(), campaign_id=campaign, tenant_id=tenant, raw_md="# A\nb", filename="a.md", conn=conn)
    sql, args = conn.calls[0]
    assert "(campaign_id, tenant_id, filename, raw_md, status) SELECT $1, $2," in sql
    assert "FROM campaigns WHERE id = $1 AND tenant_id = $2" in sql and args[:2] == (campaign, tenant)

    conn = KnowledgeConn()
    prepared = ingest_service.prepare_markdown("# First\nOne\n# Second\nTwo", max_nodes=2)
    await ingest_service.persist_prepared_document(
        conn, campaign_id=campaign, tenant_id=tenant, source_id=source,
        prepared=prepared, enrichments=[NodeEnrichment(), NodeEnrichment()])
    nodes = [(s, a) for s, a in conn.calls if s.startswith("INSERT INTO campaign_knowledge_nodes")]
    assert len(nodes) == 2 and all("(campaign_id, tenant_id, source_id," in s and a[:2] == (campaign, tenant)
                                   for s, a in nodes)

    for scoped_to_campaign in (False, True):
        conn = KnowledgeConn()
        await ingest_service.recover_stale_processing_sources(
            object(), tenant_id=tenant, campaign_id=campaign if scoped_to_campaign else None, conn=conn)
        ((sql, args),) = conn.calls
        assert "WHERE tenant_id = $1" in sql and args[0] == tenant
        assert ("AND campaign_id = $2" in sql) is scoped_to_campaign


async def test_top_up_order_found_by_provider_metadata_is_refused_if_it_names_another_tenant():
    fixture = fixture_data()
    seen = record_statements(fixture)
    fixture.session["metadata"]["tenant_id"] = str(uuid4())  # session and payment share this dict
    with pytest.raises(events.BillingTopupReviewRequired, match="session_order_terms_mismatch"):
        await apply(fixture)
    assert not [sql for sql, _ in seen if sql.startswith(("INSERT", "UPDATE"))]
    assert fixture.store["ledger"] == []


async def test_payment_without_top_up_metadata_is_only_looked_up_by_its_payment_id_and_writes_nothing():
    fixture = fixture_data()
    seen = record_statements(fixture)
    fixture.payment["metadata"] = {}
    result = await apply(fixture, "charge.refunded", "evt_unrelated")
    assert result["status"] == "ignored"
    sql, args = statement_with(seen, "FROM topup_orders WHERE provider_payment_id")
    assert sql == "SELECT id FROM topup_orders WHERE provider_payment_id=$1" and args == ("pi_fixture",)
    assert not [sql for sql, _ in seen if sql.startswith(("INSERT", "UPDATE"))]
