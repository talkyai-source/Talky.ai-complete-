"""Staff routes for telephony number requests: list, read, open a document, decide.

No database: a small in-memory connection answers the exact statements in
staff_service, so a changed statement fails these tests. The Postgres proof is
tests/integration/test_telephony_number_request_staff_pg.py (CI only).
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_audit_logger, get_current_user, get_db_pool
from app.api.v1.endpoints.admin import telephony_number_requests as ep
from app.core.error_handlers import register_error_handlers
from app.domain.services.audit_logger import AuditEvent
from app.domain.services.number_requests import notify, staff_service as ss
from app.domain.services.number_requests.errors import DocumentFileMissing, DocumentUnreadable

BASE = "/api/v1/admin/telephony/number-requests"
STAFF = "11111111-aaaa-4bbb-8ccc-ddddeeeeffff"
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
SECRET_NAME = "passport_of_jane_doe.pdf"
NOTE_TEXT = "The utility bill is older than three months"
PLAINTEXT = b"%PDF-1.7 synthetic body zq9marker"


class World:
    def __init__(self):
        self.tenants = {}      # id -> business_name
        self.requests = {}     # (tenant, id) -> row
        self.docs = {}         # (tenant, request, id) -> row
        self.emails = {}       # (tenant, user) -> email
        self.statements = []
        self.order = []
        self.audit_calls = []
        self.buffered = []
        self.fail_audit = False
        self.store_error = None
        self.plaintext = PLAINTEXT

    def tenant(self, name="Acme Dental"):
        tid = str(uuid4())
        self.tenants[tid] = name
        return tid

    def request(self, tenant, *, status="submitted", minutes=0, use_type="business"):
        rid, uid = str(uuid4()), str(uuid4())
        row = {"id": rid, "tenant_id": tenant, "requested_by_user_id": uid, "use_type": use_type, "status": status,
               "review_note": None, "reviewed_by_user_id": None,
               "reviewed_at": None if status == "submitted" else NOW,
               "submitted_at": NOW - timedelta(minutes=minutes), "files_deleted_at": None}
        self.requests[(tenant, rid)] = row
        self.emails[(tenant, uid)] = "owner@example.com"
        return row

    def doc(self, tenant, request_id, kind="business_registration", **over):
        did = str(uuid4())
        row = {"id": did, "kind": kind, "storage_key": f"{tenant}/{did}.tnr", "file_name": SECRET_NAME,
               "content_type": "application/pdf", "size_bytes": len(PLAINTEXT),
               "sha256": hashlib.sha256(PLAINTEXT).hexdigest(), "wrapped_key": b"wrapped",
               "scan_status": "not_scanned", "created_at": NOW, "files_deleted_at": None}
        row.update(over)
        self.docs[(tenant, request_id, did)] = row
        return row


class Conn:
    def __init__(self, world):
        self.w = world

    async def fetch(self, sql, *a):
        self.w.statements.append((sql, a))
        if sql == ss._T0:
            after, limit = a
            ids = sorted(t for t in self.w.tenants if after is None or t > after)[:limit]
            return [{"id": t, "business_name": self.w.tenants[t]} for t in ids]
        if sql == ss._T1:
            tenant, status, limit = a
            rows = [r for (t, _), r in self.w.requests.items() if t == tenant and r["status"] == status]
            rows.sort(key=lambda r: (r["submitted_at"], r["id"]), reverse=True)
            return rows[:limit]
        if sql == ss._T3:
            tenant, request_id = a
            return [r for (t, q, _), r in self.w.docs.items() if (t, q) == (tenant, request_id)]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        self.w.statements.append((sql, a))
        if sql == ss._T2:
            return self.w.requests.get((a[0], a[1]))
        if sql == ss._T4:
            return self.w.docs.get((a[0], a[1], a[2]))
        if sql == ss._T5:
            self.w.order.append("update")
            tenant, rid, status, note, reviewer = a
            row = self.w.requests.get((tenant, rid))
            # The conditional is part of the statement: without it Postgres would update any row.
            conditional = "status = 'submitted'" in sql
            if row is None or (conditional and row["status"] != "submitted"):
                return None
            row.update(status=status, review_note=note, reviewed_by_user_id=reviewer, reviewed_at=NOW)
            return {k: row[k] for k in ("id", "status", "reviewed_at", "requested_by_user_id", "submitted_at")}
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        self.w.statements.append((sql, a))
        if sql == ss._T7:
            return self.w.emails.get((a[0], a[1]))
        if sql == ss._T8:
            return self.w.tenants.get(a[0])
        raise AssertionError(sql)


class FakeStore:
    def __init__(self, world):
        self.w = world

    def read_plaintext(self, key, *, tenant_id, document_id, kind, dek):
        self.w.order.append("read_file")
        if self.w.store_error is not None:
            raise self.w.store_error
        return self.w.plaintext


def make_app(monkeypatch, world, *, role="platform_admin"):
    @asynccontextmanager
    async def acquire(pool, tenant, **kw):
        assert tenant is None and kw.get("user_id") == STAFF
        world.order.append("acquire")
        yield Conn(world)

    async def session(conn, request, user):
        world.order.append("session")

    async def authority(conn, actor):
        world.order.append("authority")

    async def mutation_guard(conn, actor):
        world.order.append("mutation_guard")

    async def unwrap(wrapped):
        return b"k" * 32

    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(ep, "_session", session)
    monkeypatch.setattr(ep, "require_current_platform_admin", authority)
    monkeypatch.setattr(ep, "require_platform_admin_mutation", mutation_guard)
    monkeypatch.setattr(ep.document_store, "unwrap_dek", unwrap)
    monkeypatch.setattr(ep.document_store, "get_store", lambda: FakeStore(world))
    sent = []

    async def send(**kw):
        sent.append(kw)
        return world.email_outcome

    world.email_outcome = "sent"
    world.sent = sent
    monkeypatch.setattr(notify, "send_decision_email", send)

    async def lock(conn):
        world.order.append("lock")

    async def log_in(conn, **kw):
        if world.fail_audit:
            raise RuntimeError("audit down")
        world.order.append("audit")
        world.audit_calls.append(kw)

    async def log(*a, **kw):
        world.buffered.append((a, kw))

    audit = SimpleNamespace(lock_transaction=lock, log_in_transaction=log_in, log=log)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(ep.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_db_pool] = lambda: object()
    app.dependency_overrides[get_audit_logger] = lambda: audit
    if role is None:
        def unauthenticated():
            from fastapi import HTTPException
            raise HTTPException(401, "Not authenticated")
        app.dependency_overrides[get_current_user] = unauthenticated
    else:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=STAFF, email="staff@example.com", role=role, tenant_id=None)
    return app


async def call(app, method, path="", **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as c:
        return await c.request(method, BASE + path, **kw)


@pytest.fixture
def world():
    return World()


def all_paths(tenant, request_id, document_id):
    return [("GET", ""), ("GET", f"/{tenant}/{request_id}"),
            ("GET", f"/{tenant}/{request_id}/documents/{document_id}"),
            ("POST", f"/{tenant}/{request_id}/decision")]


# -- who may use it ----------------------------------------------------------------------

@pytest.mark.parametrize("role", ["tenant_admin", "user", "viewer", "agent"])
async def test_a_customer_role_gets_403_on_all_four_routes(monkeypatch, world, role):
    app = make_app(monkeypatch, world, role=role)
    t, q, d = (str(uuid4()) for _ in range(3))
    for method, path in all_paths(t, q, d):
        kw = {"json": {"decision": "approved"}} if method == "POST" else {}
        r = await call(app, method, path, **kw)
        assert r.status_code == 403, (method, path)
    assert world.statements == [] and world.audit_calls == [] and world.buffered == []


async def test_not_signed_in_gets_401_on_all_four_routes(monkeypatch, world):
    app = make_app(monkeypatch, world, role=None)
    t, q, d = (str(uuid4()) for _ in range(3))
    for method, path in all_paths(t, q, d):
        kw = {"json": {"decision": "approved"}} if method == "POST" else {}
        assert (await call(app, method, path, **kw)).status_code == 401
    assert world.statements == []


# -- the list ----------------------------------------------------------------------------

async def test_list_merges_accounts_newest_first_pages_and_audits(monkeypatch, world):
    a, b = world.tenant("Acme"), world.tenant("Beta")
    r1 = world.request(a, minutes=30)
    r2 = world.request(b, minutes=10)
    r3 = world.request(a, minutes=20)
    world.request(b, status="approved", minutes=1)
    app = make_app(monkeypatch, world)

    r = await call(app, "GET", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["items"]] == [r2["id"], r3["id"]]
    assert (body["limit"], body["offset"], body["has_more"]) == (2, 0, True)
    assert set(body["items"][0]) == {"id", "tenant_id", "business_name", "requested_by_user_id", "use_type",
                                     "status", "submitted_at", "reviewed_at", "files_deleted"}
    assert body["items"][0]["business_name"] == "Beta" and body["items"][0]["files_deleted"] is False

    r = await call(app, "GET", params={"limit": 2, "offset": 2})
    assert [i["id"] for i in r.json()["items"]] == [r1["id"]] and r.json()["has_more"] is False

    r = await call(app, "GET", params={"status": "approved"})
    assert len(r.json()["items"]) == 1 and r.json()["items"][0]["status"] == "approved"

    # every statement on a request table carries one account's id
    for sql, args in world.statements:
        if sql == ss._T1:
            assert args[0] in world.tenants and "tenant_id = $1" in sql

    first, = world.buffered[:1]
    assert first[0] == (AuditEvent.RECORD_VIEWED,)
    assert first[1] == {"action": "telephony_number_requests.listed", "actor_id": STAFF,
                        "actor_role": "platform_admin", "resource_type": "telephony_number_request",
                        "metadata": {"status": "submitted", "limit": 2, "offset": 0, "returned": 2}}


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"offset": -1}, {"offset": 1001},
                                    {"status": "deleted"}])
async def test_list_rejects_out_of_range_input(monkeypatch, world, params):
    r = await call(make_app(monkeypatch, world), "GET", params=params)
    assert r.status_code == 422 and world.statements == []


# -- one request -------------------------------------------------------------------------

async def test_detail_shows_documents_without_content_or_keys_and_audits_in_the_transaction(monkeypatch, world):
    t = world.tenant("Acme")
    req = world.request(t)
    d1 = world.doc(t, req["id"], "business_registration")
    world.doc(t, req["id"], "business_photo_id", files_deleted_at=NOW, file_name=None, wrapped_key=None)
    app = make_app(monkeypatch, world)

    r = await call(app, "GET", f"/{t}/{req['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["requester_email"] == "owner@example.com" and body["business_name"] == "Acme"
    assert [d["kind"] for d in body["documents"]] == ["business_registration", "business_photo_id"]
    assert [d["available"] for d in body["documents"]] == [True, False]
    assert set(body["documents"][0]) == {"id", "kind", "file_name", "content_type", "size_bytes", "sha256",
                                         "scan_status", "created_at", "available"}
    text = r.text
    assert "storage_key" not in text and "wrapped_key" not in text and ".tnr" not in text
    assert world.order == ["acquire", "lock", "authority", "session", "audit"]
    (call_kw,) = world.audit_calls
    assert call_kw == {"event_type": AuditEvent.RECORD_VIEWED, "action": "telephony_number_request.viewed",
                       "actor_id": STAFF, "actor_role": "platform_admin", "tenant_id": t,
                       "resource_type": "telephony_number_request", "resource_id": req["id"],
                       "metadata": {"documents": 2}}
    assert d1["id"] in text


async def test_unknown_request_is_404_and_writes_no_audit_row(monkeypatch, world):
    t = world.tenant()
    r = await call(make_app(monkeypatch, world), "GET", f"/{t}/{uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "number_request_not_found"
    assert world.audit_calls == []


async def test_a_request_asked_for_under_the_wrong_account_is_404(monkeypatch, world):
    a, b = world.tenant(), world.tenant()
    req = world.request(a)
    r = await call(make_app(monkeypatch, world), "GET", f"/{b}/{req['id']}")
    assert r.status_code == 404


async def test_a_non_uuid_path_is_422_before_any_statement(monkeypatch, world):
    r = await call(make_app(monkeypatch, world), "GET", "/not-a-uuid/also-not")
    assert r.status_code == 422 and world.statements == []


# -- opening a document ------------------------------------------------------------------

async def _open(monkeypatch, world, **doc_over):
    t = world.tenant()
    req = world.request(t)
    doc = world.doc(t, req["id"], **doc_over)
    r = await call(make_app(monkeypatch, world), "GET", f"/{t}/{req['id']}/documents/{doc['id']}")
    return r, t, req, doc


async def test_open_document_sends_the_bytes_with_safe_headers_after_the_audit_row(monkeypatch, world):
    r, t, req, doc = await _open(monkeypatch, world)
    assert r.status_code == 200 and r.content == PLAINTEXT
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="business_registration-{doc["id"][:8]}.pdf"'
    assert SECRET_NAME not in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["x-document-scan"] == "not_scanned"
    assert world.order.index("read_file") < world.order.index("audit")
    assert world.order[-3:] == ["lock", "authority", "audit"]
    (kw,) = world.audit_calls
    assert kw == {"event_type": AuditEvent.RECORD_VIEWED, "action": "telephony_number_request.document_opened",
                  "actor_id": STAFF, "actor_role": "platform_admin", "tenant_id": t,
                  "resource_type": "telephony_number_request_document", "resource_id": doc["id"],
                  "metadata": {"request_id": req["id"], "kind": "business_registration",
                               "size_bytes": len(PLAINTEXT), "sha256_verified": True,
                               "scan_status": "not_scanned"}}


async def test_a_failed_audit_write_means_no_document_bytes(monkeypatch, world):
    world.fail_audit = True
    r, *_ = await _open(monkeypatch, world)
    assert r.status_code == 503 and b"zq9marker" not in r.content
    assert r.json()["error"]["code"] == "staff_unavailable"


async def test_document_of_another_request_is_404(monkeypatch, world):
    t = world.tenant()
    a, b = world.request(t), world.request(t)
    doc = world.doc(t, a["id"])
    r = await call(make_app(monkeypatch, world), "GET", f"/{t}/{b['id']}/documents/{doc['id']}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "document_not_found"
    assert world.audit_calls == []


async def test_deleted_document_is_410(monkeypatch, world):
    r, *_ = await _open(monkeypatch, world, files_deleted_at=NOW, file_name=None, wrapped_key=None)
    assert r.status_code == 410 and r.json()["error"]["code"] == "document_deleted"
    assert world.audit_calls == []


async def test_clean_scan_setting_refuses_an_unscanned_document_with_409(monkeypatch, world):
    monkeypatch.setenv("NUMBER_REQUEST_REQUIRE_CLEAN_SCAN", "true")
    r, *_ = await _open(monkeypatch, world)
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_not_cleared"
    assert r.json()["error"]["details"]["scan_status"] == "not_scanned"
    assert b"zq9marker" not in r.content and world.audit_calls == []


async def test_clean_scan_setting_allows_a_clean_document(monkeypatch, world):
    monkeypatch.setenv("NUMBER_REQUEST_REQUIRE_CLEAN_SCAN", "true")
    r, *_ = await _open(monkeypatch, world, scan_status="clean")
    assert r.status_code == 200 and r.headers["x-document-scan"] == "clean"


async def test_an_infected_document_is_never_opened(monkeypatch, world):
    r, *_ = await _open(monkeypatch, world, scan_status="infected")
    assert r.status_code == 409 and world.audit_calls == []


async def test_missing_file_is_404_and_unreadable_or_tampered_is_500(monkeypatch, world):
    world.store_error = DocumentFileMissing("x")
    r, *_ = await _open(monkeypatch, world)
    assert r.status_code == 404 and r.json()["error"]["code"] == "document_file_missing"
    world2 = World()
    world2.store_error = DocumentUnreadable("x")
    r, *_ = await _open(monkeypatch, world2)
    assert r.status_code == 500 and r.json()["error"]["code"] == "document_unreadable"
    world3 = World()
    world3.plaintext = b"%PDF-1.7 something else"
    r, *_ = await _open(monkeypatch, world3)
    assert r.status_code == 500 and r.json()["error"]["code"] == "document_unreadable"
    assert world3.audit_calls == []


# -- deciding ----------------------------------------------------------------------------

async def _decide(app, t, rid, **body):
    return await call(app, "POST", f"/{t}/{rid}/decision", json=body)


async def test_approve_returns_200_orders_the_calls_and_audits_without_the_note(monkeypatch, world):
    t = world.tenant()
    req = world.request(t)
    app = make_app(monkeypatch, world)
    r = await _decide(app, t, req["id"], decision="approved", note="  Looks fine  ")
    assert r.status_code == 200
    assert set(r.json()) == {"id", "status", "reviewed_at", "email"}
    assert (r.json()["status"], r.json()["email"]) == ("approved", "sent")
    assert world.order == ["acquire", "lock", "mutation_guard", "session", "update", "audit"]
    assert world.requests[(t, req["id"])]["reviewed_by_user_id"] == STAFF
    assert world.requests[(t, req["id"])]["review_note"] == "Looks fine"
    (kw,) = world.audit_calls
    assert kw == {"event_type": AuditEvent.TENANT_UPDATED, "action": "telephony_number_request.approved",
                  "actor_id": STAFF, "actor_role": "platform_admin", "tenant_id": t,
                  "resource_type": "telephony_number_request", "resource_id": req["id"],
                  "before_state": {"status": "submitted"}, "after_state": {"status": "approved"},
                  "metadata": {"note_length": len("Looks fine")}}
    (mail,) = world.sent
    assert mail["recipient"] == "owner@example.com" and mail["decision"] == "approved"


async def test_a_second_decision_is_409_already_decided(monkeypatch, world):
    t = world.tenant()
    req = world.request(t)
    app = make_app(monkeypatch, world)
    assert (await _decide(app, t, req["id"], decision="approved")).status_code == 200
    r = await _decide(app, t, req["id"], decision="rejected", note="changed my mind")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_decided"
    assert r.json()["error"]["details"]["status"] == "approved"
    assert len(world.audit_calls) == 1 and len(world.sent) == 1
    assert world.requests[(t, req["id"])]["status"] == "approved"


async def test_reject_needs_a_note(monkeypatch, world):
    t = world.tenant()
    req = world.request(t)
    app = make_app(monkeypatch, world)
    for body in ({"decision": "rejected"}, {"decision": "rejected", "note": "   "},
                 {"decision": "rejected", "note": None}):
        r = await _decide(app, t, req["id"], **body)
        assert r.status_code == 422 and r.json()["error"]["code"] == "note_required"
    assert world.requests[(t, req["id"])]["status"] == "submitted" and world.audit_calls == []


async def test_reject_with_a_note_audits_only_its_length_and_sends_the_note_to_the_email(monkeypatch, world):
    t = world.tenant()
    req = world.request(t)
    r = await _decide(make_app(monkeypatch, world), t, req["id"], decision="rejected", note=NOTE_TEXT)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    (kw,) = world.audit_calls
    assert kw["action"] == "telephony_number_request.rejected"
    assert kw["metadata"] == {"note_length": len(NOTE_TEXT)} and NOTE_TEXT not in repr(kw)
    assert world.sent[0]["note"] == NOTE_TEXT


@pytest.mark.parametrize("body", [{"decision": "maybe"}, {"decision": "approved", "extra": 1},
                                  {"decision": "approved", "note": "x" * 1001}, {}])
async def test_bad_decision_bodies_are_422(monkeypatch, world, body):
    t = world.tenant()
    req = world.request(t)
    r = await _decide(make_app(monkeypatch, world), t, req["id"], **body)
    assert r.status_code == 422 and world.statements == []


async def test_deciding_an_unknown_request_is_404(monkeypatch, world):
    t = world.tenant()
    r = await _decide(make_app(monkeypatch, world), t, str(uuid4()), decision="approved")
    assert r.status_code == 404 and r.json()["error"]["code"] == "number_request_not_found"
    assert world.audit_calls == []


async def test_a_failed_audit_write_undoes_nothing_it_never_commits_and_sends_no_email(monkeypatch, world):
    world.fail_audit = True
    t = world.tenant()
    req = world.request(t)
    r = await _decide(make_app(monkeypatch, world), t, req["id"], decision="approved")
    assert r.status_code == 503 and r.json()["error"]["code"] == "decision_unconfirmed"
    assert world.sent == []


@pytest.mark.parametrize("outcome", ["sent", "failed", "skipped"])
async def test_the_email_outcome_is_passed_through_and_never_changes_the_decision(monkeypatch, world, outcome):
    world.sent = []
    t = world.tenant()
    req = world.request(t)
    app = make_app(monkeypatch, world)
    world.email_outcome = outcome
    r = await _decide(app, t, req["id"], decision="approved")
    assert r.status_code == 200 and r.json()["email"] == outcome and r.json()["status"] == "approved"


async def test_the_email_is_off_when_disabled_and_nothing_is_sent(monkeypatch, world):
    monkeypatch.setenv("NUMBER_REQUEST_DECISION_EMAIL", "false")
    t = world.tenant()
    req = world.request(t)
    r = await _decide(make_app(monkeypatch, world), t, req["id"], decision="approved")
    assert r.status_code == 200 and r.json()["email"] == "off" and world.sent == []


# -- nothing sensitive in logs ------------------------------------------------------------

async def test_no_file_name_content_or_note_reaches_the_logs(monkeypatch, world, caplog):
    t = world.tenant()
    req = world.request(t)
    doc = world.doc(t, req["id"])
    app = make_app(monkeypatch, world)
    with caplog.at_level(logging.DEBUG):
        await call(app, "GET", f"/{t}/{req['id']}/documents/{doc['id']}")
        world.store_error = DocumentUnreadable(SECRET_NAME)
        await call(app, "GET", f"/{t}/{req['id']}/documents/{doc['id']}")
        world.fail_audit = True
        await _decide(app, t, req["id"], decision="rejected", note=NOTE_TEXT)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for leaked in (SECRET_NAME, "zq9marker", NOTE_TEXT, "owner@example.com"):
        assert leaked not in text
    assert "number_request_document_unreadable" in text


def test_the_four_routes_are_mounted_under_admin():
    from fastapi.routing import iter_route_contexts
    from app.api.v1.endpoints.admin import router
    paths = {(m, r.path) for r in iter_route_contexts(router.routes) for m in (r.methods or ())}
    base = "/admin/telephony/number-requests"
    assert ("GET", base) in paths
    assert ("GET", base + "/{tenant_id}/{request_id}") in paths
    assert ("GET", base + "/{tenant_id}/{request_id}/documents/{document_id}") in paths
    assert ("POST", base + "/{tenant_id}/{request_id}/decision") in paths
