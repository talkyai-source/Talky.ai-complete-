"""F3-01: actual duplicate HTTP route, relational copy and atomic audit.

The store executes application SQL in an isolated in-memory SQLite database.
Only PostgreSQL UUID casts, row locks, local GUCs and the advisory lock are
adapted; this is relational behavior evidence, not a PostgreSQL integration run.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import re
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app.api.v1 import dependencies
from app.api.v1.endpoints import campaigns
from app.core.error_handlers import register_error_handlers
from app.domain.services.campaign_direction_guard import CAMPAIGN_DIRECTION_LOCK_SQL


TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
OTHER = "bbeeffaa-1234-4abc-8def-0123456789ab"
ACTOR = "ccddeeff-1234-4abc-8def-0123456789ab"
SOURCE = "ddeeffaa-1234-4abc-8def-0123456789ab"
FOREIGN = "eeffbbcc-1234-4abc-8def-0123456789ab"
DOC = "ffaabbcc-1234-4abc-8def-0123456789ab"
ROOT = "abcdefab-1234-4abc-8def-0123456789ab"
CHILD = "bcdefabc-1234-4abc-8def-0123456789ab"
NOW = "2026-11-06T12:00:00+00:00"
OLD = "2026-08-01T12:00:00+00:00"
JSON_COLUMNS = {"script_config", "calling_config", "options", "keywords", "example_questions"}
SETTINGS = {
    "description": "Synthetic copied settings", "system_prompt": "Approved business instructions",
    "voice_id": "synthetic-voice", "tts_provider": "cartesia", "max_concurrent_calls": 3,
    "retry_failed": True, "max_retries": 2, "goal": "Arrange a demonstration",
    "script_config": {"first_speaker": "callee", "pipeline_mode": "realtime",
        "realtime_voice": "synthetic-realtime", "realtime_model": "synthetic-model",
        "campaign_brief": {"required_lead_fields": [{"field_key": "region", "label": "Region"}]}},
    "calling_config": {"timezone": "Asia/Karachi", "allowed_days": [0, 2],
        "time_window_start": "09:00", "time_window_end": "17:00", "outbound_line_id": ACTOR},
    "knowledge_mode": "tool", "knowledge_model": "synthetic-budget-model",
    "prompt_version_pin": "lead_gen@2",
}


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("gen_random_uuid", 0, lambda: str(uuid4()))
        self.db.create_function("now", 0, lambda: NOW)
        self.db.executescript("""
            CREATE TABLE campaigns (
                id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()), tenant_id TEXT NOT NULL,
                name TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'draft',
                system_prompt TEXT DEFAULT '', voice_id TEXT, tts_provider TEXT,
                max_concurrent_calls INTEGER DEFAULT 10, retry_failed INTEGER DEFAULT 1,
                max_retries INTEGER DEFAULT 3, goal TEXT, script_config TEXT DEFAULT '{}',
                calling_config TEXT DEFAULT '{}', direction TEXT DEFAULT 'outbound',
                knowledge_mode TEXT DEFAULT 'none', knowledge_model TEXT, prompt_version_pin TEXT,
                total_leads INTEGER DEFAULT 0, calls_completed INTEGER DEFAULT 0,
                calls_failed INTEGER DEFAULT 0, created_at TEXT DEFAULT (now()),
                updated_at TEXT DEFAULT (now()), started_at TEXT, completed_at TEXT);
            CREATE TABLE campaign_knowledge_sources (
                id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()), campaign_id TEXT, tenant_id TEXT,
                filename TEXT, raw_md TEXT, token_count INTEGER DEFAULT 0, version INTEGER DEFAULT 1,
                status TEXT DEFAULT 'processing', error TEXT,
                created_at TEXT DEFAULT (now()), updated_at TEXT DEFAULT (now()));
            CREATE TABLE campaign_knowledge_nodes (
                id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()), campaign_id TEXT, tenant_id TEXT,
                source_id TEXT, parent_id TEXT, depth INTEGER DEFAULT 0, path TEXT,
                position INTEGER DEFAULT 0, heading TEXT, content TEXT DEFAULT '', summary TEXT,
                voice_answer TEXT, keywords TEXT, example_questions TEXT, search_text TEXT DEFAULT '',
                search_tsv TEXT, priority INTEGER DEFAULT 0, hit_count INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1, created_at TEXT DEFAULT (now()), updated_at TEXT DEFAULT (now()));
            CREATE TABLE campaign_lead_fields (
                id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()), tenant_id TEXT, campaign_id TEXT,
                field_key TEXT, label TEXT, field_type TEXT DEFAULT 'text', is_required INTEGER DEFAULT 0,
                agent_visible INTEGER DEFAULT 1, user_visible INTEGER DEFAULT 1, options TEXT,
                sort_order INTEGER DEFAULT 0, created_at TEXT DEFAULT (now()), updated_at TEXT DEFAULT (now()));
            CREATE TABLE leads (id TEXT, tenant_id TEXT, campaign_id TEXT);
            CREATE TABLE calls (id TEXT, tenant_id TEXT, campaign_id TEXT);
            CREATE TABLE dialer_jobs (id TEXT, tenant_id TEXT, campaign_id TEXT);
            CREATE TABLE audit_receipts (body TEXT);
        """)
        self.events = []
        self.active_leases = 0
        self.locked = False
        self.fail_nodes = False
        self.insert("campaigns", {"id": SOURCE, "tenant_id": TENANT, "name": "Summer sales",
            **SETTINGS, "status": "running", "total_leads": 71, "calls_completed": 37,
            "calls_failed": 9, "started_at": OLD, "completed_at": OLD,
            "created_at": OLD, "updated_at": OLD})
        self.insert("campaigns", {"id": FOREIGN, "tenant_id": OTHER, "name": "Private foreign campaign"})
        self.insert("campaign_knowledge_sources", {"id": DOC, "tenant_id": TENANT,
            "campaign_id": SOURCE, "filename": "prices.md", "raw_md": "# Prices\n$20 per month",
            "token_count": 12, "version": 3, "status": "ready"})
        for node_id, parent, depth in [(ROOT, None, 0), (CHILD, ROOT, 1)]:
            self.insert("campaign_knowledge_nodes", {"id": node_id, "tenant_id": TENANT,
                "campaign_id": SOURCE, "source_id": DOC, "parent_id": parent, "depth": depth,
                "path": str(depth), "heading": "Prices" if depth == 0 else "Monthly",
                "content": "$20 per month", "summary": "Published summary", "voice_answer": "Twenty dollars",
                "keywords": ["price"], "example_questions": ["How much?"], "search_text": "price twenty",
                "search_tsv": "'price':1 'twenty':2", "priority": 4, "hit_count": 93,
                "enabled": depth == 0})
        self.insert("campaign_lead_fields", {"tenant_id": TENANT, "campaign_id": SOURCE,
            "field_key": "region", "label": "Region", "is_required": True,
            "field_type": "select", "options": ["North", "South"], "sort_order": 2})
        self.insert("campaign_knowledge_sources", {"tenant_id": OTHER, "campaign_id": FOREIGN,
            "filename": "foreign.md", "raw_md": "Foreign private material", "status": "ready"})
        for table in ("leads", "calls", "dialer_jobs"):
            self.insert(table, {"id": str(uuid4()), "tenant_id": TENANT, "campaign_id": SOURCE})

    def insert(self, table, row):
        values = [json.dumps(value) if isinstance(value, (dict, list)) else value for value in row.values()]
        self.db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", values)

    def rows(self, table, campaign_id=None):
        sql, args = (f"SELECT * FROM {table}", ()) if campaign_id is None else (
            f"SELECT * FROM {table} WHERE campaign_id=?", (str(campaign_id),))
        return [self.record(row) for row in self.db.execute(sql, args).fetchall()]

    @staticmethod
    def record(row):
        result = dict(row)
        for name in JSON_COLUMNS & result.keys():
            if isinstance(result[name], str):
                result[name] = json.loads(result[name])
        return result

    @asynccontextmanager
    async def acquire(self):
        self.active_leases += 1
        try:
            yield self
        finally:
            self.active_leases -= 1

    @asynccontextmanager
    async def transaction(self):
        self.db.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self.db.execute("ROLLBACK")
            self.events.append("rollback")
            raise
        else:
            self.db.execute("COMMIT")
            self.events.append("commit")
        finally:
            self.locked = False

    def is_in_transaction(self):
        return self.db.in_transaction

    def query(self, sql, args):
        assert self.db.in_transaction
        assert not re.search(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+(?:leads|calls|dialer_jobs)\b", sql, re.I), sql
        if sql.lstrip().upper().startswith("INSERT"):
            assert self.locked, "Audit chain must be locked before business writes"
            self.events.append("write")
            if self.fail_nodes and "INSERT INTO campaign_knowledge_nodes" in sql:
                raise RuntimeError("synthetic node persistence failure")
        adapted = re.sub(r"::(?:uuid|jsonb|text|tsvector)\b", "", sql)
        adapted = re.sub(r"\s+FOR (?:NO KEY UPDATE|KEY SHARE|UPDATE|SHARE)(?: OF \w+)?", "", adapted, flags=re.I)
        adapted = re.sub(r"\$(\d+)", r"?\1", adapted)
        values = [str(value) if isinstance(value, UUID) else json.dumps(value)
                  if isinstance(value, (dict, list)) else value for value in args]
        return self.db.execute(adapted, values)

    async def execute(self, sql, *args):
        if re.fullmatch(r"SET LOCAL (?:statement_timeout|lock_timeout) = '\d+ms'", sql):
            return "SET"
        if sql == f"SET LOCAL app.current_tenant_id = '{TENANT}'":
            return "SET"
        if sql == CAMPAIGN_DIRECTION_LOCK_SQL:
            assert self.locked and len(args) == 1 and str(UUID(args[0])) == args[0]
            self.events.append("direction_lock")
            return "SELECT 1"
        cursor = self.query(sql, args)
        return f"OK {cursor.rowcount}"

    async def fetchrow(self, sql, *args):
        row = self.query(sql, args).fetchone()
        return self.record(row) if row is not None else None

    async def fetch(self, sql, *args):
        return [self.record(row) for row in self.query(sql, args).fetchall()]

    async def fetchval(self, sql, *args):
        row = self.query(sql, args).fetchone()
        return row[0] if row is not None else None


class Audit:
    def __init__(self, store):
        self.store, self.fail = store, False

    async def lock_transaction(self, conn):
        assert conn is self.store and conn.is_in_transaction()
        assert "write" not in conn.events
        conn.locked = True
        conn.events.append("audit_lock")

    async def log_in_transaction(self, conn, **entry):
        assert conn is self.store and conn.is_in_transaction() and conn.locked
        if self.fail:
            raise RuntimeError("synthetic unavailable audit")
        conn.insert("audit_receipts", {"body": json.dumps(entry, default=str)})
        conn.events.append("audit")
        return uuid4()


@pytest.fixture
def harness(monkeypatch):
    store, app = Store(), FastAPI()
    audit = Audit(store)
    identity = {"role": "tenant_admin", "authenticated": True}

    def current():
        if not identity["authenticated"]:
            raise HTTPException(401, "Authentication required")
        return dependencies.CurrentUser(id=ACTOR, tenant_id=TENANT,
            email="synthetic@example.invalid", role=identity["role"])

    monkeypatch.setattr("app.core.container.get_db_pool_from_container", lambda: None)
    app.dependency_overrides[dependencies.get_current_user] = current
    app.dependency_overrides[dependencies.get_db_client] = lambda: SimpleNamespace(pool=store)
    app.dependency_overrides[dependencies.get_audit_logger] = lambda: audit
    app.dependency_overrides[campaigns.rate_limit_dependency] = lambda: None
    app.dependency_overrides[campaigns.idempotency_dependency] = lambda: None
    register_error_handlers(app)
    app.include_router(campaigns.router, prefix="/api/v1")
    yield store, audit, identity, app
    assert store.active_leases == 0
    store.db.close()


async def duplicate(harness, source=SOURCE):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness[3],
            raise_app_exceptions=False), base_url="http://test") as client:
        return await client.post(f"/api/v1/campaigns/{source}/duplicate", timeout=2)


async def test_duplicate_is_persisted_draft_with_settings_voice_and_no_activity(harness):
    store = harness[0]
    before = deepcopy(store.rows("campaigns"))
    response = await duplicate(harness)
    assert response.status_code == 201, response.text
    copied = response.json()["campaign"]
    assert str(UUID(copied["id"])) not in {SOURCE, FOREIGN}
    persisted = next(row for row in store.rows("campaigns") if row["id"] == copied["id"])
    assert persisted["name"] == "Summer sales - Copy" and persisted["status"] == "draft"
    assert persisted["tenant_id"] == TENANT and persisted["direction"] == "outbound"
    for key, value in SETTINGS.items():
        assert persisted[key] == value, key
    for counter in ("total_leads", "calls_completed", "calls_failed"):
        assert persisted[counter] == 0
    assert persisted["started_at"] is None and persisted["completed_at"] is None
    assert persisted["created_at"] == NOW and persisted["updated_at"] == NOW
    for table in ("leads", "calls", "dialer_jobs"):
        assert store.rows(table, copied["id"]) == [] and len(store.rows(table, SOURCE)) == 1
    assert [row for row in store.rows("campaigns") if row["id"] in {SOURCE, FOREIGN}] == before
    receipts = [json.loads(row["body"]) for row in store.rows("audit_receipts")]
    assert len(receipts) == 1 and receipts[0]["action"] == "campaign.duplicate"
    assert receipts[0]["tenant_id"] == TENANT and receipts[0]["actor_id"] == ACTOR
    assert str(receipts[0]["resource_id"]) == copied["id"]
    assert receipts[0]["before_state"]["id"] == SOURCE
    assert receipts[0]["after_state"]["status"] == "draft"
    assert store.events.index("audit_lock") < store.events.index("write") < store.events.index("audit") < store.events.index("commit")


async def test_duplicate_remaps_knowledge_tree_and_field_definitions_without_sharing_rows(harness):
    store = harness[0]
    response = await duplicate(harness)
    assert response.status_code == 201, response.text
    clone_id = response.json()["campaign"]["id"]
    docs = store.rows("campaign_knowledge_sources", clone_id)
    nodes = store.rows("campaign_knowledge_nodes", clone_id)
    assert len(docs) == 1 and docs[0]["id"] != DOC and docs[0]["tenant_id"] == TENANT
    assert docs[0]["raw_md"] == "# Prices\n$20 per month" and docs[0]["version"] == 3
    assert docs[0]["status"] == "ready"
    assert len(nodes) == 2 and {row["id"] for row in nodes}.isdisjoint({ROOT, CHILD})
    parent = next(row for row in nodes if row["parent_id"] is None)
    child = next(row for row in nodes if row["parent_id"] is not None)
    assert child["parent_id"] == parent["id"]
    for row in nodes:
        assert row["source_id"] == docs[0]["id"] and row["tenant_id"] == TENANT
        assert row["hit_count"] == 0 and row["search_tsv"] == "'price':1 'twenty':2"
        assert row["content"] == "$20 per month" and row["keywords"] == ["price"]
    assert parent["enabled"] and not child["enabled"]
    definitions = store.rows("campaign_lead_fields", clone_id)
    assert len(definitions) == 1 and definitions[0]["field_key"] == "region"
    assert definitions[0]["options"] == ["North", "South"] and definitions[0]["tenant_id"] == TENANT
    assert definitions[0]["id"] != store.rows("campaign_lead_fields", SOURCE)[0]["id"]
    store.db.execute("UPDATE campaign_knowledge_nodes SET content='Edited copy' WHERE campaign_id=?", (clone_id,))
    assert all(row["content"] == "$20 per month" for row in store.rows("campaign_knowledge_nodes", SOURCE))


@pytest.mark.parametrize("target", [FOREIGN, "12345678-abcd-4abc-8def-0123456789ab"], ids=["foreign", "missing"])
async def test_foreign_or_missing_source_has_no_copy_or_receipt(harness, target):
    response = await duplicate(harness, target)
    assert response.status_code == 404
    assert len(harness[0].rows("campaigns")) == 2
    assert harness[0].rows("audit_receipts") == []


@pytest.mark.parametrize("kind", ["audit", "knowledge"], ids=["audit-rollback", "knowledge-rollback"])
async def test_copy_and_audit_roll_back_together(harness, kind):
    store, audit = harness[:2]
    audit.fail, store.fail_nodes = kind == "audit", kind == "knowledge"
    before = {table: store.rows(table) for table in ("campaigns", "campaign_knowledge_sources", "campaign_knowledge_nodes", "campaign_lead_fields")}
    response = await duplicate(harness)
    assert response.status_code >= 500, response.text
    assert "synthetic" not in response.text
    assert {table: store.rows(table) for table in before} == before
    assert store.rows("audit_receipts") == [] and "rollback" in store.events


@pytest.mark.parametrize("kind,expected", [("anonymous", 401), ("readonly", 403), ("invalid-id", 422)])
async def test_duplicate_requires_auth_create_permission_and_uuid(harness, kind, expected):
    harness[2]["authenticated"] = kind != "anonymous"
    harness[2]["role"] = "readonly" if kind == "readonly" else "tenant_admin"
    response = await duplicate(harness, "not-a-uuid" if kind == "invalid-id" else SOURCE)
    assert response.status_code == expected, response.text
    assert harness[0].events == [] and len(harness[0].rows("campaigns")) == 2


@pytest.mark.parametrize("status", ["processing", "failed"])
async def test_incomplete_knowledge_is_refused_without_a_partial_campaign(harness, status):
    harness[0].db.execute("UPDATE campaign_knowledge_sources SET status=? WHERE id=?", (status, DOC))
    response = await duplicate(harness)
    assert response.status_code == 409, response.text
    assert len(harness[0].rows("campaigns")) == 2 and harness[0].rows("audit_receipts") == []


async def test_inbound_source_is_not_copied_without_its_managed_routing_contract(harness):
    harness[0].db.execute("UPDATE campaigns SET direction='inbound' WHERE id=?", (SOURCE,))
    response = await duplicate(harness)
    assert response.status_code == 409, response.text
    assert len(harness[0].rows("campaigns")) == 2 and harness[0].rows("audit_receipts") == []


async def test_empty_knowledge_campaign_can_be_duplicated(harness):
    store = harness[0]
    store.db.execute("DELETE FROM campaign_knowledge_nodes WHERE campaign_id=?", (SOURCE,))
    store.db.execute("DELETE FROM campaign_knowledge_sources WHERE campaign_id=?", (SOURCE,))
    store.db.execute("UPDATE campaigns SET knowledge_mode='none' WHERE id=?", (SOURCE,))
    response = await duplicate(harness)
    assert response.status_code == 201, response.text
    assert store.rows("campaign_knowledge_nodes", response.json()["campaign"]["id"]) == []


async def test_duplicate_name_stays_inside_existing_255_character_column(harness):
    harness[0].db.execute("UPDATE campaigns SET name=? WHERE id=?", ("N" * 255, SOURCE))
    response = await duplicate(harness)
    assert response.status_code == 201, response.text
    assert response.json()["campaign"]["name"] == "N" * 248 + " - Copy"
