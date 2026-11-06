"""F1-26 durable call workflows: real HTTP handlers, transactional fake DB.

No network, environment file, storage provider, or actual PostgreSQL is used.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import re
from uuid import UUID

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_pool
from app.core.error_handlers import register_error_handlers


TENANT = "a0000000-0000-4000-8000-000000000001"
OTHER = "b0000000-0000-4000-8000-000000000002"
ACTOR = "c0000000-0000-4000-8000-000000000003"
COLLEAGUE = "c0000000-0000-4000-8000-000000000004"
CALL = "d0000000-0000-4000-8000-000000000005"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
BODY = {
    "leadType": "hot", "notes": "Synthetic private note: asks about budget 12345",
    "form": {"contact": "Synthetic Person +14155552671", "interest": "Synthetic private interest",
             "nextStep": "Synthetic private callback plan", "completed": True},
}


def api_module():
    path = Path(__file__).resolve().parents[2] / "app/api/v1/endpoints/call_workflow.py"
    assert path.is_file(), "F1-26 has no durable call-workflow API"
    return importlib.import_module("app.api.v1.endpoints.call_workflow")


def normalized(sql):
    return " ".join(sql.lower().split())


def argument(sql, column, args):
    match = re.search(rf"(?<![a-z_])(?:\w+\.)?{column}\s*=\s*\$(\d+)", sql)
    assert match, f"Missing explicit {column} predicate: {sql}"
    return str(args[int(match.group(1)) - 1])


class Store:
    def __init__(self):
        self.calls = {(TENANT, CALL)}
        self.rows = {}
        self.receipts = []
        self.events = []
        self.queries = []
        self.serial = asyncio.Lock()
        self.fail_audit = False
        self.fail_commit = False

    @asynccontextmanager
    async def acquire(self):
        yield Connection(self)


class Connection:
    def __init__(self, store):
        self.store = store
        self.depth = 0
        self.audit_locked = False

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self):
        if self.depth == 0:
            await self.store.serial.acquire()
        snapshot = deepcopy((self.store.rows, self.store.receipts))
        self.depth += 1
        try:
            yield self
            if self.depth == 1 and self.store.fail_commit:
                raise RuntimeError("synthetic commit unavailable")
        except BaseException:
            self.store.rows, self.store.receipts = snapshot
            self.store.events.append("rollback")
            raise
        else:
            self.store.events.append("commit")
        finally:
            self.depth -= 1
            if self.depth == 0:
                self.audit_locked = False
                self.store.serial.release()

    async def execute(self, sql, *args):
        query = normalized(sql)
        if query.startswith("set ") or "set_config(" in query:
            return "SET"
        raise AssertionError(f"Unexpected execute: {query}")

    async def fetchrow(self, sql, *args):
        query = normalized(sql)
        self.store.queries.append((query, args))
        if query.startswith("select") and "from calls" in query:
            tenant = argument(query, "tenant_id", args)
            call = argument(query, "id", args)
            if "for update" in query:
                assert self.audit_locked, "Audit chain lock must precede call row lock"
                self.store.events.append("call_lock")
            return {"id": UUID(call), "tenant_id": UUID(tenant)} if (tenant, call) in self.store.calls else None
        if query.startswith("select") and "from call_workflows" in query:
            key = (argument(query, "tenant_id", args), argument(query, "call_id", args))
            if "for update" in query:
                assert self.audit_locked
            return deepcopy(self.store.rows.get(key))
        if query.startswith("insert into call_workflows"):
            assert self.audit_locked and self.depth > 0
            match = re.search(r"call_workflows\s*\((.*?)\)\s*values\s*\((.*?)\)", query)
            assert match, query
            columns = [value.strip() for value in match[1].split(",")]
            bindings = match[2].split(",")
            row = {}
            for column, binding in zip(columns, bindings):
                parameter = re.match(r"\s*\$(\d+)", binding)
                row[column] = args[int(parameter[1]) - 1] if parameter else NOW
            row.setdefault("created_at", NOW)
            row.setdefault("updated_at", NOW)
            key = (str(row["tenant_id"]), str(row["call_id"]))
            assert key not in self.store.rows, "Call lock must serialize first-write/import decisions"
            self.store.rows[key] = deepcopy(row)
            self.store.events.append("write")
            return deepcopy(row)
        if query.startswith("update call_workflows"):
            assert self.audit_locked and self.depth > 0
            clause = query.split(" where ", 1)[1]
            key = (argument(clause, "tenant_id", args), argument(clause, "call_id", args))
            if key not in self.store.rows:
                return None
            row = self.store.rows[key]
            assignments = query.split(" set ", 1)[1].split(" where ", 1)[0]
            for column, parameter in re.findall(r"(\w+)\s*=\s*\$(\d+)", assignments):
                row[column] = args[int(parameter) - 1]
            row["updated_at"] = NOW
            self.store.events.append("write")
            return deepcopy(row)
        raise AssertionError(f"Unexpected fetchrow: {query}")


class Audit:
    def __init__(self, store):
        self.store = store

    async def lock_transaction(self, conn):
        assert conn.store is self.store and conn.depth > 0
        assert not conn.audit_locked
        conn.audit_locked = True
        self.store.events.append("audit_lock")

    async def log_in_transaction(self, conn, **entry):
        assert conn.store is self.store and conn.depth > 0 and conn.audit_locked
        if self.store.fail_audit:
            raise RuntimeError("synthetic audit unavailable")
        self.store.receipts.append(deepcopy(entry))
        self.store.events.append("audit")
        return UUID("e0000000-0000-4000-8000-000000000006")


@pytest.fixture
def harness(monkeypatch):
    module = api_module()
    store = Store()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(module.router, prefix="/api/v1")
    principal = {"id": ACTOR, "tenant_id": TENANT, "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        email="synthetic@example.invalid", **principal)
    app.dependency_overrides[get_db_pool] = lambda: store
    app.dependency_overrides[get_audit_logger] = lambda: Audit(store)
    # Exercise the real role permission dependency with its documented local
    # fallback, without initializing any application infrastructure.
    monkeypatch.setattr("app.core.container.get_db_pool_from_container", lambda: None)
    return app, store, principal


async def request(app, method, body=None, *, call=CALL):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://test") as client:
        return await client.request(method, f"/api/v1/calls/{call}/workflow", json=body)


async def test_owned_call_without_saved_workflow_has_no_fabricated_entry(harness):
    app, store, _ = harness
    response = await request(app, "GET")
    assert response.status_code == 200, response.text
    assert response.json() == {"workflow": None}
    assert not store.rows and not store.receipts


async def test_save_is_durable_and_visible_to_another_user_in_same_tenant(harness):
    app, store, principal = harness
    response = await request(app, "PUT", BODY)
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    saved = response.json()["workflow"]
    assert {key: saved[key] for key in BODY} == BODY
    assert datetime.fromisoformat(saved["updatedAt"].replace("Z", "+00:00")) == NOW
    assert str(store.rows[TENANT, CALL]["updated_by"]) == ACTOR
    principal["id"] = COLLEAGUE
    response = await request(app, "GET")
    assert response.status_code == 200 and response.json() == {"workflow": saved}
    assert len(store.receipts) == 1


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_foreign_or_missing_call_is404_without_workflow_disclosure(harness, method):
    app, store, principal = harness
    assert (await request(app, "PUT", BODY)).status_code == 200
    before = deepcopy((store.rows, store.receipts))
    principal["tenant_id"] = OTHER
    response = await request(app, method, BODY if method == "PUT" else None)
    assert response.status_code == 404, response.text
    assert BODY["notes"] not in response.text
    assert (store.rows, store.receipts) == before
    assert "call_workflows" not in store.queries[-1][0], "Ownership refusal must precede workflow read"


@pytest.mark.parametrize("role,method,expected", [
    ("readonly", "GET", 200), ("readonly", "PUT", 403),
    ("billing_user", "GET", 403), ("billing_user", "PUT", 403),
    ("agent", "PUT", 200),
])
async def test_read_and_write_follow_calls_permissions(harness, role, method, expected):
    app, store, principal = harness
    principal["role"] = role
    response = await request(app, method, BODY if method == "PUT" else None)
    assert response.status_code == expected, response.text
    if expected == 403:
        assert not store.queries and not store.rows and not store.receipts


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_missing_tenant_context_is403(harness, method):
    app, store, principal = harness
    principal.update(role="platform_admin", tenant_id=None)
    response = await request(app, method, BODY if method == "PUT" else None)
    assert response.status_code == 403 and not store.queries


@pytest.mark.parametrize("field,value", [
    ("leadType", "qualified"), ("notes", "n" * 4001),
    ("contact", "c" * 161), ("interest", "i" * 1001), ("nextStep", "s" * 1001),
    ("completed", "true"), ("unknown", True), ("updatedAt", "1999-01-01T00:00:00Z"),
    ("create_only", "false"),
])
async def test_invalid_payload_refused_before_database_work(harness, field, value):
    app, store, _ = harness
    body = deepcopy(BODY)
    target = body["form"] if field in body["form"] else body
    target[field] = value
    response = await request(app, "PUT", body)
    assert response.status_code == 422, response.text
    assert not store.queries and not store.rows and not store.receipts


@pytest.mark.parametrize("field", ["notes", "contact", "interest", "nextStep"])
@pytest.mark.parametrize("invalid", ["\x00", "\ud800", "\udfff"])
async def test_postgres_invalid_text_is422_before_database_work(harness, field, invalid):
    app, store, _ = harness
    body = deepcopy(BODY)
    target = body if field == "notes" else body["form"]
    target[field] = "synthetic prefix" + invalid + "suffix"
    # Escaped JSON deliberately reaches the server even for an unpaired
    # surrogate; the HTTP client's own UTF-8 encoder must not be the refusal.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://test") as client:
        response = await client.put(f"/api/v1/calls/{CALL}/workflow",
                                    content=json.dumps(body, ensure_ascii=True).encode("ascii"),
                                    headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert not store.queries and not store.rows and not store.receipts


async def test_ordinary_multiline_notes_and_unicode_are_preserved(harness):
    app, _, _ = harness
    body = {**deepcopy(BODY), "notes": "Line one\n\tLine two\r\nCaf\u00e9 \U0001f600"}
    response = await request(app, "PUT", body)
    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["notes"] == body["notes"]


async def test_exact_ui_lengths_and_all_lead_types_are_accepted(harness):
    app, store, _ = harness
    body = {"notes": "n" * 4000, "form": {
        "contact": "c" * 160, "interest": "i" * 1000, "nextStep": "s" * 1000, "completed": False}}
    for lead_type in ("cold", "warm", "hot", "follow_up"):
        response = await request(app, "PUT", {**body, "leadType": lead_type})
        assert response.status_code == 200, response.text
        assert response.json()["workflow"]["leadType"] == lead_type
    assert len(store.receipts) == 4


async def test_import_create_only_returns_existing_server_state_without_overwrite(harness):
    app, store, _ = harness
    original = (await request(app, "PUT", BODY)).json()["workflow"]
    before = deepcopy((store.rows, store.receipts))
    stale = {**deepcopy(BODY), "notes": "stale browser-only note", "create_only": True}
    response = await request(app, "PUT", stale)
    assert response.status_code == 200, response.text
    assert response.json() == {"workflow": original, "created": False}
    assert (store.rows, store.receipts) == before


async def test_concurrent_imports_create_once_and_return_one_canonical_row(harness):
    app, store, _ = harness
    bodies = [{**deepcopy(BODY), "notes": text, "create_only": True} for text in ("device one", "device two")]
    responses = await asyncio.gather(*(request(app, "PUT", body) for body in bodies))
    assert [response.status_code for response in responses] == [200, 200]
    documents = [response.json() for response in responses]
    assert sorted(doc["created"] for doc in documents) == [False, True]
    assert documents[0]["workflow"] == documents[1]["workflow"]
    assert len(store.rows) == len(store.receipts) == 1


async def test_update_audit_is_atomic_attributed_and_contains_no_plaintext_form_data(harness):
    app, store, principal = harness
    assert (await request(app, "PUT", BODY)).status_code == 200
    principal["id"] = COLLEAGUE
    changed = {**deepcopy(BODY), "notes": "A second private synthetic note", "leadType": "follow_up"}
    response = await request(app, "PUT", changed)
    assert response.status_code == 200 and response.json()["created"] is False
    assert len(store.receipts) == 2
    receipt = store.receipts[-1]
    assert str(receipt["actor_id"]) == COLLEAGUE
    assert str(receipt["tenant_id"]) == TENANT
    assert str(receipt["resource_id"]) == CALL
    assert receipt["before_state"] != receipt["after_state"]
    assert str(store.rows[TENANT, CALL]["updated_by"]) == COLLEAGUE
    serialized = json.dumps(store.receipts, default=str)
    for text in [BODY["notes"], changed["notes"], BODY["form"]["contact"], BODY["form"]["interest"], BODY["form"]["nextStep"]]:
        assert text not in serialized
    assert "sha256" in serialized and "length" in serialized
    assert store.events.index("audit_lock") < store.events.index("call_lock") < store.events.index("write") < store.events.index("audit")


@pytest.mark.parametrize("failure", ["audit", "commit"])
@pytest.mark.parametrize("existing", [False, True])
async def test_failed_audit_or_commit_preserves_prior_workflow_and_receipts(harness, failure, existing):
    app, store, _ = harness
    if existing:
        assert (await request(app, "PUT", BODY)).status_code == 200
    before = deepcopy((store.rows, store.receipts))
    setattr(store, "fail_" + failure, True)
    response = await request(app, "PUT", {**deepcopy(BODY), "notes": "must not survive failure"})
    assert response.status_code == 503, response.text
    assert "synthetic" not in response.text and "must not survive" not in response.text
    assert (store.rows, store.receipts) == before
    assert "rollback" in store.events
