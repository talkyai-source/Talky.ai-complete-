"""POST/GET /telephony/number-requests: the customer side, against an in-memory database.

The fake database keeps rows in dicts, snapshots them when a transaction opens
and restores them on any failure, so "saved together" is checked on real files
in a temp folder and on the rows that survive.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request

from app.api.v1.dependencies import get_current_user, get_db_pool
from app.api.v1.endpoints import telephony_number_requests as route
from app.api.v1.endpoints.auth import limiter
from app.core.error_handlers import register_error_handlers
from app.core.request_id_middleware import RequestIdMiddleware
from app.domain.services.number_requests import config, service
from app.domain.services.number_requests import store as store_module
from app.domain.services.number_requests.store import DocumentStore

BASE = "/api/v1/telephony/number-requests"
USER_ID = "12345678-aaaa-bbbb-cccc-ddddeeeeffff"
TENANT = "11111111-1111-1111-1111-111111111111"
CAP = 4096
PDF = b"%PDF-1.7\n" + b"A" * 200
JPG = b"\xff\xd8\xff\xe0" + b"B" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"C" * 200
SECRET_NAME = "passport-of-jane-zq9.pdf"
SECRET_BYTES = b"SECRETCONTENTMARKER"
KEY = "key-aaaaaaaa-0001"


def build(parts, boundary="XBOUNDARYX"):
    out = b""
    for name, filename, data in parts:
        out += f"--{boundary}\r\n".encode()
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += disp.encode() + b"\r\n\r\n" + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


BIZ = [("use_type", None, b"business"), ("business_registration", "reg.pdf", PDF),
       ("business_photo_id", "id.jpg", JPG)]
PERS = [("use_type", None, b"personal"), ("utility_bill", "bill.pdf", PDF),
        ("personal_photo_id", "id.png", PNG)]


class FakeBackend:
    provider_name = "fake_kms"

    async def wrap_key(self, dek):
        return b"W" + dek

    async def unwrap_key(self, wrapped):
        return wrapped[1:]


class FakeDB:
    """requests keyed by id; unique (tenant, idempotency_key); documents list."""

    def __init__(self):
        self.requests: dict[str, dict] = {}
        self.documents: list[dict] = []
        self.commit_mode = "ok"  # "ok" | "fail_rollback" | "fail_persisted"
        self.fail_acquire = False
        self.calls = 0
        self.next_txid = 1000
        self.txn_status: dict[int, str] = {}
        self.force_status = None  # a text, or "__none__" for SQL NULL
        self.status_error = False
        self.txid_missing = False

    def snapshot(self):
        return copy.deepcopy((self.requests, self.documents))

    def restore(self, snap):
        self.requests, self.documents = snap


class FakeConn:
    def __init__(self, db: FakeDB):
        self.db = db

    async def fetchrow(self, sql, *args):
        db = self.db
        if sql == service.S1_FIND:
            tenant, key = args
            for r in db.requests.values():
                if r["tenant_id"] == tenant and r["idempotency_key"] == key:
                    return dict(r)
            return None
        if sql == service.S2_INSERT_REQUEST:
            rid, tenant, user, use_type, key, fp = args
            for r in db.requests.values():
                if r["tenant_id"] == tenant and r["idempotency_key"] == key:
                    return None
            row = {"id": uuid.UUID(rid), "tenant_id": tenant, "requested_by": user, "use_type": use_type,
                   "idempotency_key": key, "request_fingerprint": fp, "status": "submitted",
                   "submitted_at": datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)}
            db.requests[rid] = row
            return dict(row)
        if sql == service.S5_EXISTS:
            tenant, rid = args
            r = db.requests.get(str(rid))
            return {"id": r["id"]} if r and r["tenant_id"] == tenant else None
        raise AssertionError("unexpected fetchrow: " + sql)

    async def fetchval(self, sql, *args):
        db = self.db
        if sql == service.S6_COUNT_OPEN:
            (tenant,) = args
            return len([r for r in db.requests.values()
                        if r["tenant_id"] == tenant and r["status"] == "submitted"])
        if sql == service.S7_TXID:
            if db.txid_missing:
                return None
            db.next_txid += 1
            self.txid = db.next_txid
            return self.txid
        if sql == service.S8_TXID_STATUS:
            if db.status_error:
                raise RuntimeError("status unavailable")
            if db.force_status is not None:
                return None if db.force_status == "__none__" else db.force_status
            return db.txn_status.get(args[0])
        raise AssertionError("unexpected fetchval: " + sql)

    async def fetch(self, sql, *args):
        assert sql == service.S4_DOCUMENTS
        tenant, rid = args
        return [dict(d) for d in self.db.documents
                if d["tenant_id"] == tenant and str(d["request_id"]) == str(rid)]

    async def execute(self, sql, *args):
        assert sql == service.S3_INSERT_DOCUMENT
        (doc_id, tenant, rid, kind, skey, fname, ctype, size, sha, wrapped, provider) = args
        self.db.documents.append({"id": doc_id, "tenant_id": tenant, "request_id": rid, "kind": kind,
                                  "storage_key": skey, "file_name": fname, "content_type": ctype,
                                  "size_bytes": size, "sha256": sha, "wrapped_key": wrapped})


def fake_acquire(db: FakeDB):
    @contextlib.asynccontextmanager
    async def acquire(pool, tenant_id, **kw):
        assert tenant_id is not None
        db.calls += 1
        if db.fail_acquire:
            raise RuntimeError("db down")
        snap = db.snapshot()
        conn = FakeConn(db)
        try:
            yield conn
        except BaseException:
            db.restore(snap)
            if hasattr(conn, "txid"):
                db.txn_status[conn.txid] = "aborted"
            raise
        if db.commit_mode == "fail_rollback":
            db.restore(snap)
            if hasattr(conn, "txid"):
                db.txn_status[conn.txid] = "aborted"
            db.commit_mode = "ok"
            raise RuntimeError("commit failed")
        if hasattr(conn, "txid"):
            db.txn_status[conn.txid] = "committed"
        if db.commit_mode == "fail_persisted":
            db.commit_mode = "ok"
            raise RuntimeError("commit ack lost")
    return acquire


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_DOCUMENTS_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", str(CAP))
    monkeypatch.setenv("NUMBER_REQUEST_MIN_FREE_DISK_BYTES", "0")
    monkeypatch.setattr("app.core.kms.get_kms_backend", lambda: FakeBackend())
    db = FakeDB()
    monkeypatch.setattr(service, "acquire_with_tenant", fake_acquire(db))
    limiter.reset()
    yield SimpleNamespace(db=db, store=DocumentStore(), root=str(tmp_path / "docs"))
    limiter.reset()


def make_app(role="tenant_admin", tenant=TENANT, signed_in=True):
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.state.limiter = limiter
    register_error_handlers(app)
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[get_db_pool] = lambda: object()
    if signed_in:
        def user(request: Request):
            request.state.authenticated_user_id = USER_ID
            return SimpleNamespace(id=USER_ID, email="a@example.com", role=role, tenant_id=tenant)
        app.dependency_overrides[get_current_user] = user
    else:
        from fastapi import HTTPException

        def deny():
            raise HTTPException(status_code=401, detail="Not authenticated")
        app.dependency_overrides[get_current_user] = deny
    return app


async def post(app, parts=BIZ, key=KEY, boundary="XBOUNDARYX", body=None, headers=None, ctype=True):
    h = {}
    if ctype:
        h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    if key is not None:
        h["Idempotency-Key"] = key
    h.update(headers or {})
    content = body if body is not None else build(parts, boundary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t.invalid") as c:
        return await c.post(BASE, content=content, headers=h)


def files_on_disk(root):
    found = []
    for base, _dirs, names in os.walk(root):
        found += [os.path.join(base, n) for n in names]
    return found


# ---------------------------------------------------------------- success ----

@pytest.mark.parametrize("parts,use,kinds", [
    (BIZ, "business", ["business_registration", "business_photo_id"]),
    (PERS, "personal", ["utility_bill", "personal_photo_id"]),
])
async def test_201_exact_body_and_no_key_hash_or_path(env, parts, use, kinds):
    r = await post(make_app(), parts)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "use_type", "status", "submitted_at", "documents"}
    assert body["use_type"] == use and body["status"] == "submitted"
    assert [d["kind"] for d in body["documents"]] == kinds
    for d in body["documents"]:
        assert set(d) == {"kind", "file_name", "content_type", "size_bytes"}
    text = r.text
    assert ".tnr" not in text and "storage" not in text and "sha256" not in text and TENANT not in text
    assert "Idempotent-Replay" not in r.headers
    assert len(env.db.requests) == 1 and len(env.db.documents) == 2
    assert len([f for f in files_on_disk(env.root) if f.endswith(".tnr")]) == 2
    assert not [f for f in files_on_disk(env.root) if f.endswith(".part")]


async def test_dangerous_file_name_is_stored_cleaned_with_random_storage_key(env):
    parts = [BIZ[0], ("business_registration", "../../etc/pass\x00wd.pdf", PDF), BIZ[2]]
    r = await post(make_app(), parts)
    assert r.status_code == 201
    for d in env.db.documents:
        assert ".." not in d["file_name"] and "/" not in d["file_name"] and "\x00" not in d["file_name"]
        assert re.fullmatch(r"[0-9a-f-]{36}/[0-9a-f-]{36}[.]tnr", d["storage_key"])
        assert "pass" not in d["storage_key"]


async def test_config_route_returns_types_extensions_and_cap(env):
    app = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t.invalid") as c:
        r = await c.get(BASE + "/config")
    assert r.status_code == 200
    assert r.json() == {"accepted_types": ["application/pdf", "image/jpeg", "image/png"],
                        "accepted_extensions": [".pdf", ".jpg", ".jpeg", ".png"],
                        "max_file_bytes": CAP}


async def test_config_route_needs_sign_in(env):
    app = make_app(signed_in=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t.invalid") as c:
        r = await c.get(BASE + "/config")
    assert r.status_code == 401


# ----------------------------------------------------------------- guards ----

async def test_401_when_not_signed_in(env):
    r = await post(make_app(signed_in=False))
    assert r.status_code == 401 and env.db.calls == 0


async def test_ended_session_message(env, monkeypatch):
    from app.api.v1 import dependencies

    @contextlib.asynccontextmanager
    async def acquire(pool, tenant):
        yield object()

    async def missing_session(conn, session_id, *, user_id):
        return None

    monkeypatch.setattr(dependencies, "decode_and_validate_token", lambda _: {"sub": USER_ID, "sid": "session"})
    monkeypatch.setattr(dependencies, "get_db_client", lambda: SimpleNamespace(pool=object()))
    monkeypatch.setattr(dependencies, "acquire_with_tenant", acquire)
    monkeypatch.setattr(dependencies, "get_session_by_id", missing_session)
    app = make_app()
    del app.dependency_overrides[get_current_user]
    response = await post(app, headers={"Authorization": "Bearer dummy"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Your login session has ended. Please sign in again."


async def test_403_tenant_required(env):
    r = await post(make_app(tenant=None))
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "tenant_required" and err["message"] == "Sign in to an account to send documents."


async def test_403_role_not_allowed_when_roles_are_set(env, monkeypatch):
    monkeypatch.setattr(config, "SUBMIT_ROLES", frozenset({"tenant_admin"}))
    r = await post(make_app(role="readonly"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "role_not_allowed"
    assert env.db.calls == 0
    ok = await post(make_app(role="tenant_admin"), key="key-aaaaaaaa-0002")
    assert ok.status_code == 201


def assert_refusal(response, status, code, message, field):
    assert response.status_code == status
    request_id = response.headers["X-Request-ID"]
    assert str(uuid.UUID(request_id)) == request_id
    assert response.json() == {"error": {
        "code": code, "message": message, "details": {"field": field}, "request_id": request_id,
    }}


@pytest.mark.parametrize("key,code", [
    (None, "idempotency_key_required"),
    ("short", "idempotency_key_invalid"),
])
async def test_idempotency_key_codes(env, key, code):
    response = await post(make_app(), key=key)
    assert_refusal(response, 400, code,
                   "Your request could not be sent. Please try again.",
                   "Idempotency-Key")
    assert env.db.calls == 0
    assert not [f for f in files_on_disk(env.root) if f.endswith(".part")]


async def test_same_key_different_documents_is_400_and_creates_nothing(env):
    app = make_app()
    first = await post(app)
    assert first.status_code == 201
    other = [BIZ[0], ("business_registration", "reg.pdf", PDF + b"x"), BIZ[2]]
    r = await post(app, other)
    assert_refusal(r, 400, "idempotency_key_reused",
                   "Your files have changed since your last try. Please press Submit again.",
                   "Idempotency-Key")
    assert len(env.db.requests) == 1 and len(env.db.documents) == 2
    assert len([f for f in files_on_disk(env.root) if f.endswith(".tnr")]) == 2
    assert not [f for f in files_on_disk(env.root) if f.endswith(".part")]


async def test_replay_returns_same_answer_with_header_and_creates_nothing(env):
    app = make_app()
    first = await post(app, boundary="AAA")
    second = await post(app, boundary="BBBBB")  # a new multipart boundary, same documents
    assert second.status_code == 201
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json() == first.json()
    assert len(env.db.requests) == 1 and len(env.db.documents) == 2
    assert len([f for f in files_on_disk(env.root) if f.endswith(".tnr")]) == 2
    assert not [f for f in files_on_disk(env.root) if f.endswith(".part")]


# --------------------------------------------------------- refusal envelopes ----

async def test_413_envelope_and_nothing_kept(env):
    big = b"%PDF-1.7\n" + b"A" * (CAP + 10)
    r = await post(make_app(), [BIZ[0], ("business_registration", "r.pdf", big), BIZ[2]])
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["code"] == "file_too_large"
    assert err["message"] == "Business registration is larger than 0 MB."
    assert err["details"] == {"field": "business_registration", "max_file_bytes": CAP}
    assert not files_on_disk(env.root) or not [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))]
    assert env.db.calls == 0


async def test_415_envelope(env):
    r = await post(make_app(), [BIZ[0], ("business_registration", "r.pdf", b"MZ\x90\x00" + b"x" * 50), BIZ[2]])
    assert r.status_code == 415
    err = r.json()["error"]
    assert err["code"] == "unsupported_file_type"
    assert err["message"] == "Business registration must be a PDF, JPG or PNG file."
    assert err["details"] == {"field": "business_registration",
                              "accepted_types": ["application/pdf", "image/jpeg", "image/png"]}


@pytest.mark.parametrize("parts,code,field", [
    ([("use_type", None, b"company")] + BIZ[1:], "invalid_use_type", "use_type"),
    (BIZ[1:], "invalid_use_type", "use_type"),
    ([BIZ[0], BIZ[1]], "missing_document", "business_photo_id"),
    (BIZ + [("extra", "x.pdf", PDF)], "unexpected_file", "extra"),
    ([BIZ[0], BIZ[1], ("utility_bill", "b.pdf", PDF)], "unexpected_file", "utility_bill"),
    ([BIZ[0], BIZ[1], BIZ[1], BIZ[2]], "duplicate_document", "business_registration"),
    ([BIZ[0], BIZ[1], ("business_photo_id", "e.jpg", b"")], "empty_file", "business_photo_id"),
])
async def test_422_codes(env, parts, code, field):
    r = await post(make_app(), parts)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == code and err["details"]["field"] == field
    assert env.db.calls == 0
    assert not [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))]


@pytest.mark.parametrize("content_type", ["application/json", "multipart/form-data"])
async def test_unreadable_form(env, content_type):
    r = await post(make_app(), body=b"{}", headers={"Content-Type": content_type})
    assert_refusal(r, 422, "invalid_form",
                   "Your documents could not be read. Please choose the files again and press Submit.", "body")


async def test_too_many_form_parts(env, monkeypatch):
    # Exercise the parser's part cap before its independent per-field checks.
    monkeypatch.setattr("app.domain.services.number_requests.intake.MAX_PARTS", 2)
    r = await post(make_app())
    assert_refusal(r, 422, "invalid_form", "Please send only the two documents for this request.", "body")


@pytest.mark.parametrize("elapsed,expected_wait,wording", [
    (0, 3600, "about 1 hour"),
    (3000, 600, "about 10 minutes"),
])
async def test_429_on_the_eleventh_request(env, monkeypatch, elapsed, expected_wait, wording):
    # the hourly limit is under test here, not the cap on waiting requests
    monkeypatch.setattr(config, "MAX_OPEN_REQUESTS_PER_ACCOUNT", 100)
    app = make_app()
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    for i in range(10):
        response = await post(app, key=f"key-aaaaaaaa-{i:04d}")
        assert response.status_code == 201
    now += elapsed
    response = await post(app, key="eleventh")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) == expected_wait
    error = response.json()["error"]
    assert error["details"]["retry_after_seconds"] == expected_wait
    assert error["message"] == f"You have sent too many requests. Please try again in {wording}."


@pytest.mark.parametrize("seconds,words", [
    (0, "less than a minute"), (59, "less than a minute"),
    (60, "about 1 minute"), (90, "about 2 minutes"),
    (3599, "about 1 hour"), (3600, "about 1 hour"), (None, "a little while"),
])
def test_wait_words(seconds, words):
    from app.core.retry_wait import wait_words

    assert wait_words(seconds) == words


# ---------------------------------------------------- saved together / 503 ----

class FailingSecondPublish(DocumentStore):
    def __init__(self):
        super().__init__()
        self.n = 0

    def publish(self, temp_path, key):
        self.n += 1
        if self.n == 2:
            raise OSError("disk full")
        super().publish(temp_path, key)


async def test_storage_failure_on_second_publish_leaves_no_row_and_no_file(env, monkeypatch):
    monkeypatch.setattr(route, "get_store", lambda: FailingSecondPublish())
    r = await post(make_app())
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "storage_unavailable"
    assert err["message"] == "Your documents could not be saved. Nothing was kept. Please try again."
    assert r.headers["Retry-After"] == "30"
    assert env.db.requests == {} and env.db.documents == []
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_commit_failure_confirmed_absent_removes_published_files(env):
    env.db.commit_mode = "fail_rollback"
    r = await post(make_app())
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert env.db.requests == {} and env.db.documents == []
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_commit_ack_lost_but_row_present_answers_as_replay_and_keeps_files(env):
    env.db.commit_mode = "fail_persisted"
    r = await post(make_app())
    assert r.status_code == 201 and r.headers["Idempotent-Replay"] == "true"
    assert len(env.db.requests) == 1
    assert len([f for f in files_on_disk(env.root) if f.endswith(".tnr")]) == 2


async def test_database_down_is_503_and_no_files(env):
    env.db.fail_acquire = True
    r = await post(make_app())
    assert r.status_code == 503
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_kms_failure_is_503_and_no_files(env, monkeypatch):
    class Broken(FakeBackend):
        async def wrap_key(self, dek):
            raise RuntimeError("kms down")
    monkeypatch.setattr("app.core.kms.get_kms_backend", lambda: Broken())
    r = await post(make_app())
    assert r.status_code == 503 and env.db.calls == 0
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_store_that_cannot_write_is_503_without_calling_the_service(env, monkeypatch):
    class Unwritable(DocumentStore):
        def create_temp(self):
            raise PermissionError("read-only")
    monkeypatch.setattr(route, "get_store", lambda: Unwritable())
    r = await post(make_app())
    assert r.status_code == 503 and env.db.calls == 0
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


# ------------------------------------------------------------------ privacy ----

async def test_logs_contain_no_file_name_or_content(env, caplog):
    secret_pdf = b"%PDF-1.7\n" + SECRET_BYTES
    parts = [BIZ[0], ("business_registration", SECRET_NAME, secret_pdf), BIZ[2]]
    with caplog.at_level(logging.DEBUG):
        ok = await post(make_app(), parts)
        env.db.commit_mode = "ok"
        refused = await post(make_app(), [BIZ[0], ("business_registration", SECRET_NAME, b"MZ" + SECRET_BYTES * 2), BIZ[2]],
                             key="key-aaaaaaaa-0009")
        env.db.fail_acquire = True
        failed = await post(make_app(), parts, key="key-aaaaaaaa-0010")
    assert ok.status_code == 201 and refused.status_code == 415 and failed.status_code == 503
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "number_request_saved" in log
    for leaked in (SECRET_NAME, "zq9", SECRET_BYTES.decode(), "passport"):
        assert leaked not in log
    # the 201 answer shows the customer their own cleaned file name by design; errors never do
    assert SECRET_BYTES.decode() not in ok.text
    for r in refused, failed:
        assert SECRET_NAME not in r.text and SECRET_BYTES.decode() not in r.text


async def test_one_saved_line_with_ids_kinds_sizes_and_status(env, caplog):
    with caplog.at_level(logging.INFO, logger=service.logger.name):
        await post(make_app())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("number_request_saved")]
    assert len(lines) == 1
    assert "kinds=business_registration,business_photo_id" in lines[0]
    assert f"sizes={len(PDF)},{len(JPG)}" in lines[0] and "status=submitted" in lines[0]


# ------------------------------------------------ item 1a: waiting-request cap ----

TOO_MANY = "Your earlier requests are still waiting for review. Please wait for a decision before sending more documents."


def add_request(db, status="submitted", tenant=TENANT, n=[0]):
    n[0] += 1
    rid = str(uuid.uuid4())
    db.requests[rid] = {"id": uuid.UUID(rid), "tenant_id": tenant, "requested_by": USER_ID,
                        "use_type": "business", "idempotency_key": f"other-key-{n[0]:08d}",
                        "request_fingerprint": "f" * 64, "status": status,
                        "submitted_at": datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)}


async def test_at_the_cap_is_429_nothing_saved_no_file(env):
    assert config.MAX_OPEN_REQUESTS_PER_ACCOUNT == 3
    for _ in range(3):
        add_request(env.db)
    r = await post(make_app())
    assert r.status_code == 429, r.text
    err = r.json()["error"]
    assert err["code"] == "too_many_open_requests" and err["message"] == TOO_MANY
    assert err["details"] == {"max_open_requests": 3}
    assert len(env.db.requests) == 3 and env.db.documents == []
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_below_the_cap_is_saved(env):
    for _ in range(2):
        add_request(env.db)
    assert (await post(make_app())).status_code == 201


async def test_replay_is_still_answered_at_the_cap(env):
    app = make_app()
    first = await post(app)
    assert first.status_code == 201
    for _ in range(2):
        add_request(env.db)
    again = await post(app)
    assert again.status_code == 201 and again.headers["Idempotent-Replay"] == "true"
    assert again.json() == first.json()


async def test_decided_and_other_account_requests_do_not_count(env):
    for status in ("approved", "rejected", "approved"):
        add_request(env.db, status)
    for _ in range(3):
        add_request(env.db, tenant="22222222-2222-2222-2222-222222222222")
    assert (await post(make_app())).status_code == 201


# --------------------------------------------------- item 1b: free-disk floor ----

class _Usage:
    def __init__(self, free):
        self.total, self.used, self.free = free * 2, free, free


def _stream_must_not_run(monkeypatch):
    called = []

    async def stream(self):
        called.append(1)
        yield b""
    monkeypatch.setattr("starlette.requests.Request.stream", stream)
    return called


async def test_disk_below_the_floor_is_503_and_the_body_is_never_read(env, monkeypatch, caplog):
    monkeypatch.setenv("NUMBER_REQUEST_MIN_FREE_DISK_BYTES", str(5 * 1048576))
    monkeypatch.setattr(route.shutil, "disk_usage", lambda p: _Usage(2 * 1048576))
    called = _stream_must_not_run(monkeypatch)
    with caplog.at_level(logging.WARNING):
        r = await post(make_app())
    assert r.status_code == 503 and called == []
    err = r.json()["error"]
    assert err["code"] == "storage_unavailable"
    assert err["message"] == "Your documents could not be saved. Nothing was kept. Please try again."
    assert r.headers["Retry-After"] == "30"
    lines = [x.getMessage() for x in caplog.records if x.levelno == logging.ERROR]
    assert "number_request_disk_low free_mb=2 floor_mb=5" in lines
    assert env.db.calls == 0 and files_on_disk(env.root) == []


async def test_floor_zero_means_no_check(env, monkeypatch):
    def boom(p):
        raise AssertionError("disk must not be measured")
    monkeypatch.setattr(route.shutil, "disk_usage", boom)
    assert (await post(make_app())).status_code == 201


async def test_plenty_of_disk_proceeds(env, monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_MIN_FREE_DISK_BYTES", str(5 * 1048576))
    monkeypatch.setattr(route.shutil, "disk_usage", lambda p: _Usage(50 * 1048576))
    assert (await post(make_app())).status_code == 201


async def test_measurement_failure_proceeds_and_logs_the_type_only(env, monkeypatch, caplog):
    monkeypatch.setenv("NUMBER_REQUEST_MIN_FREE_DISK_BYTES", str(5 * 1048576))

    def broken(p):
        raise PermissionError("secret-path-zq9")
    monkeypatch.setattr(route.shutil, "disk_usage", broken)
    with caplog.at_level(logging.WARNING):
        r = await post(make_app())
    assert r.status_code == 201
    text = "\n".join(x.getMessage() for x in caplog.records)
    assert "PermissionError" in text and "zq9" not in text


def test_min_free_disk_setting(monkeypatch):
    monkeypatch.delenv("NUMBER_REQUEST_MIN_FREE_DISK_BYTES", raising=False)
    assert config.min_free_disk_bytes() == 5368709120
    for raw, want in (("0", 0), ("123", 123), ("-5", 5368709120), ("abc", 5368709120), ("", 5368709120)):
        monkeypatch.setenv("NUMBER_REQUEST_MIN_FREE_DISK_BYTES", raw)
        assert config.min_free_disk_bytes() == want


# ------------------------------ items 2 and 5: the fate of the transaction ----

UNCONFIRMED = "We could not confirm that your documents were saved. Please send the same files again."


def _unconfirmed(env, r):
    assert r.status_code == 503, r.text
    err = r.json()["error"]
    assert err["code"] == "save_unconfirmed" and err["message"] == UNCONFIRMED
    assert r.headers["Retry-After"] == "30"
    assert len([f for f in files_on_disk(env.root) if f.endswith(".tnr")]) == 2


async def test_row_not_visible_but_transaction_in_progress_keeps_the_files(env):
    env.db.commit_mode = "fail_rollback"  # the row is not visible ...
    env.db.force_status = "in progress"   # ... but the database says the commit may still land
    _unconfirmed(env, await post(make_app()))


async def test_status_null_keeps_the_files(env):
    env.db.commit_mode = "fail_rollback"
    env.db.force_status = "__none__"
    _unconfirmed(env, await post(make_app()))


async def test_status_lookup_error_keeps_the_files(env):
    env.db.commit_mode = "fail_rollback"
    env.db.status_error = True
    _unconfirmed(env, await post(make_app()))


async def test_status_lookup_timeout_keeps_the_files(env, monkeypatch):
    env.db.commit_mode = "fail_rollback"
    monkeypatch.setattr(service, "_CONFIRM_TIMEOUT", 0.05)
    orig = FakeConn.fetchval

    async def slow(self, sql, *args):
        if sql == service.S8_TXID_STATUS:
            await asyncio.sleep(1)
        return await orig(self, sql, *args)
    monkeypatch.setattr(FakeConn, "fetchval", slow)
    _unconfirmed(env, await post(make_app()))


async def test_transaction_id_never_obtained_keeps_the_files(env):
    env.db.commit_mode = "fail_rollback"
    env.db.txid_missing = True
    _unconfirmed(env, await post(make_app()))


async def test_aborted_removes_files_and_keeps_the_old_503_message(env):
    env.db.commit_mode = "fail_rollback"
    r = await post(make_app())
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert "Nothing was kept" in r.json()["error"]["message"]
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_committed_is_a_replay_and_keeps_the_files(env):
    env.db.commit_mode = "fail_persisted"
    r = await post(make_app())
    assert r.status_code == 201 and r.headers["Idempotent-Replay"] == "true"
    assert len([f for f in files_on_disk(env.root) if f.endswith(".tnr")]) == 2


# --------------------------- item 4 / 6: form refusals through the route ----

async def test_boundary_over_256_characters_is_422_not_500(env):
    r = await post(make_app(), boundary="B" * 5000)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_form"
    assert [f for f in files_on_disk(env.root) if f.endswith((".part", ".tnr"))] == []


async def test_document_field_without_a_file_name(env):
    parts = [BIZ[0], ("business_registration", None, b"text"), BIZ[2]]
    r = await post(make_app(), parts)
    assert_refusal(r, 422, "invalid_form", "Please choose a file for Business registration.",
                   "business_registration")
