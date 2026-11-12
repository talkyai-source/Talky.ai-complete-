"""A support-ticket request_id reused with different content is refused (QA F2-09, QA-BE-061).

A create's receipt was keyed by (tenant, request_id) and nothing else about the
request, so POST /support/tickets with the same request_id and a changed body
replayed the first ticket as if the second request had been served: the changed
subject or description was silently dropped. The receipt now also remembers a
fingerprint of the request's content. The identical request still replays the
stored response; a changed one is a 409 that creates nothing.

Run against the real route, service and receipts code with an in-memory stand-in
for ``support_request_receipts`` (the PostgreSQL version is in
tests/integration/test_support_pg.py).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_pool
from app.api.v1.endpoints.support import tickets as tickets_endpoint
from app.domain.services.support import ticket_service
from tests.helpers.support_db import build_app

TENANT = str(uuid4())
REQUESTER = str(uuid4())
COLLEAGUE = str(uuid4())


class _Db:
    """The receipts table and the tickets a create inserts, in memory."""

    def __init__(self, *, jsonb_as_text: bool) -> None:
        self.jsonb_as_text = jsonb_as_text  # asyncpg returns jsonb as str unless a codec is registered
        self.receipts: dict[tuple, dict] = {}
        self.tickets: list[dict] = []


class _Conn:
    def __init__(self, db: _Db) -> None:
        self.db = db

    def is_in_transaction(self) -> bool:
        return True

    async def execute(self, sql: str, *args):
        if "INSERT INTO support_request_receipts" in sql:
            tenant_id, request_id, actor_id, operation, _resource, status_code, response = args
            assert (tenant_id, request_id) not in self.db.receipts, "UNIQUE (tenant_id, request_id)"
            self.db.receipts[(tenant_id, request_id)] = {
                "actor_user_id": actor_id, "operation": operation, "status_code": status_code,
                "response": response}
        # pg_advisory_xact_lock: nothing to serialise in one process

    async def fetchrow(self, sql: str, tenant_id, request_id):
        assert "FROM support_request_receipts" in sql
        row = self.db.receipts.get((tenant_id, request_id))
        if row is None:
            return None
        row = dict(row)
        if not self.db.jsonb_as_text:
            row["response"] = json.loads(row["response"])
        return row


class _Audit:
    async def log(self, *args, **kwargs):
        return None


class _Harness:
    def __init__(self, monkeypatch, *, jsonb_as_text: bool) -> None:
        self.db = _Db(jsonb_as_text=jsonb_as_text)
        self.current = {"id": REQUESTER}
        db = self.db
        store = ticket_service.store

        async def insert_ticket(conn, **fields):
            ticket = {"id": uuid4(), **fields}
            db.tickets.append(ticket)
            return ticket["id"]

        async def insert_message(conn, **fields):
            return {"id": uuid4()}

        async def nothing(*args, **kwargs):
            return None

        async def fetch_ticket(conn, ticket_id, tenant_id, **kwargs):
            return next(t for t in db.tickets if t["id"] == ticket_id)

        async def owns(conn, table, record_id, tenant_id):
            return True

        async def view(conn, ticket, actor, now):
            return {"id": str(ticket["id"]), "subject": ticket["subject"], "category": ticket["category"]}

        @asynccontextmanager
        async def acquire(pool, tenant_id, **kwargs):
            yield _Conn(db)

        for name, fake in (("insert_ticket", insert_ticket), ("insert_message", insert_message),
                           ("attach_files", nothing), ("insert_event", nothing), ("mark_read", nothing),
                           ("fetch_ticket", fetch_ticket), ("tenant_owns", owns)):
            monkeypatch.setattr(store, name, fake)
        monkeypatch.setattr(ticket_service, "_ticket_view", view)
        monkeypatch.setattr(ticket_service, "acquire_with_tenant", acquire)
        monkeypatch.setattr(tickets_endpoint.notifications, "ticket_created", nothing)
        monkeypatch.setenv("SUPPORT_ENABLED", "true")
        monkeypatch.delenv("SUPPORT_TENANT_ALLOWLIST", raising=False)

        self.app = build_app()
        self.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=self.current["id"], email="person@example.invalid", role="user", tenant_id=TENANT)
        self.app.dependency_overrides[get_db_pool] = lambda: object()
        self.app.dependency_overrides[get_audit_logger] = lambda: _Audit()

    async def post(self, body: dict, *, user: str = REQUESTER) -> httpx.Response:
        self.current["id"] = user
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            return await client.post("/api/v1/support/tickets", json=body)


@pytest.fixture(params=[False, True], ids=["jsonb_object", "jsonb_text"])
def harness(request, monkeypatch):
    return _Harness(monkeypatch, jsonb_as_text=request.param)


def _body(**overrides) -> dict:
    body = {"request_id": str(uuid4()), "subject": "Billing question", "category": "billing",
            "description": "Where do I download invoices?"}
    body.update(overrides)
    return body


async def test_the_identical_request_replays_the_stored_response(harness):
    body = _body()
    first = await harness.post(body)
    replay = await harness.post(body)

    assert first.status_code == 201
    assert replay.status_code == 200 and replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == first.json()
    assert len(harness.db.tickets) == 1


@pytest.mark.parametrize("field,changed", [
    ("subject", "A different subject"),
    ("description", "A different description of the problem"),
    ("category", "bug"),
    ("related_campaign_id", str(uuid4())),
    ("related_call_id", str(uuid4())),
    ("conversation_id", str(uuid4())),
    ("attachment_ids", [str(uuid4())]),
])
async def test_the_same_request_id_with_a_changed_body_is_a_409_and_creates_nothing(harness, field, changed):
    body = _body()
    first = await harness.post(body)
    refused = await harness.post({**body, field: changed})

    assert first.status_code == 201
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "request_id_conflict"
    assert len(harness.db.tickets) == 1
    assert len(harness.db.receipts) == 1
    # The refused request left the stored response alone.
    assert (await harness.post(body)).json() == first.json()


async def test_attachment_order_and_repeats_are_the_same_request(harness):
    one, two = str(uuid4()), str(uuid4())
    body = _body(attachment_ids=[one, two])
    first = await harness.post(body)
    replay = await harness.post({**body, "attachment_ids": [two, one, two]})

    assert first.status_code == 201
    assert replay.status_code == 200 and replay.json() == first.json()
    assert len(harness.db.tickets) == 1


async def test_the_clients_response_never_carries_the_stored_fingerprint(harness):
    body = _body()
    first = await harness.post(body)
    replay = await harness.post(body)

    assert set(first.json()) == {"id", "subject", "category"}
    assert set(replay.json()) == {"id", "subject", "category"}


async def test_a_receipt_stored_before_fingerprints_still_replays_whatever_the_body(harness):
    request_id = uuid4()
    stored = {"id": str(uuid4()), "subject": "Stored subject", "category": "billing"}
    harness.db.receipts[(TENANT, request_id)] = {
        "actor_user_id": REQUESTER, "operation": "ticket.create", "status_code": 201,
        "response": json.dumps(stored)}

    replay = await harness.post(_body(request_id=str(request_id), subject="Another subject entirely"))

    assert replay.status_code == 200 and replay.json() == stored
    assert harness.db.tickets == []


async def test_someone_elses_request_id_is_still_a_409(harness):
    body = _body()
    await harness.post(body)
    other = await harness.post(body, user=COLLEAGUE)

    assert other.status_code == 409
    assert other.json()["error"]["code"] == "request_id_conflict"
    assert len(harness.db.tickets) == 1


def test_the_fingerprint_ignores_the_request_id_and_the_spelling_of_ids():
    from app.domain.services.support import receipts

    uid = UUID("0c0ffee0-0000-4000-8000-000000000001")
    assert receipts.body_fingerprint(a="x", ids=[uid]) == receipts.body_fingerprint(ids=[str(uid)], a="x")
    assert receipts.body_fingerprint(a="x") != receipts.body_fingerprint(a="y")
