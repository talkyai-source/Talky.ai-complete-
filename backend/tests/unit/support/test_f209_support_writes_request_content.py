"""Every support write refuses a reused request_id with different content (QA F2-09, QA-BE-061).

622753c6 made POST /support/tickets compare the request's content when its
request_id comes back. Every other support write still answered a reused
request_id with the stored response whatever the new body said, so a changed
message, rating, status, article text or target was dropped while the client
saw a 200. Each write now remembers a fingerprint of its own content (the
receipts.body_fingerprint recipe): the identical request replays the stored
response; a changed one is a 409 request_id_conflict before anything is written.

Covered here: ticket.message, ticket.resolve, ticket.reopen, ticket.csat,
conversation.start, conversation.message, conversation.feedback,
conversation.escalate, staff.ticket.patch, staff.ticket.message,
staff.article.create and staff.article.update.

Run through the real routes, services and receipts code with an in-memory
stand-in for ``support_request_receipts`` and the stores (the PostgreSQL
version is in tests/integration/test_support_pg.py).
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_pool
from app.api.v1.endpoints.auth import limiter
from app.api.v1.endpoints.support import conversations as conversations_endpoint
from app.domain.services.support import (
    article_admin,
    conversation_service,
    conversation_store,
    notifications,
    receipts,
    staff_service,
    ticket_service,
    ticket_store,
)
from app.domain.services.support.bot.types import BotTurn, Source
from tests.helpers.support_db import build_app

TENANT = str(uuid4())
REQUESTER = str(uuid4())
STAFF = str(uuid4())
OTHER_STAFF = str(uuid4())
ATTACHMENT_ONE, ATTACHMENT_TWO = str(uuid4()), str(uuid4())
CAMPAIGN, OTHER_CAMPAIGN, CALL = str(uuid4()), str(uuid4()), str(uuid4())
FINGERPRINT_KEY = "_request_fingerprint"


class _Db:
    """The receipts table and what the writes touch, in memory.

    ``writes`` gets one entry per domain write (never for receipts), so
    "nothing was written" is ``writes`` unchanged.
    """

    def __init__(self, *, jsonb_as_text: bool) -> None:
        self.jsonb_as_text = jsonb_as_text  # asyncpg returns jsonb as str unless a codec is registered
        self.receipts: dict[tuple, dict] = {}
        self.tickets: dict[UUID, dict] = {}
        self.conversations: dict[UUID, dict] = {}
        self.conversation_messages: list[dict] = []
        self.articles: dict[UUID, dict] = {}
        self.writes: list[tuple] = []
        self.model_calls = 0


class _Conn:
    def __init__(self, db: _Db) -> None:
        self.db = db

    def is_in_transaction(self) -> bool:
        return True

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql: str, *args):
        if "INSERT INTO support_request_receipts" in sql:
            tenant_id, request_id, actor_id, operation, _resource, status_code, response = args
            assert (tenant_id, request_id) not in self.db.receipts, "UNIQUE (tenant_id, request_id)"
            self.db.receipts[(tenant_id, request_id)] = {
                "actor_user_id": actor_id, "operation": operation, "status_code": status_code,
                "response": response}
        # pg_advisory_xact_lock: nothing to serialise in one process

    async def fetchval(self, sql: str, *args):
        assert "FROM user_profiles" in sql, sql
        return "Support Person"

    async def fetchrow(self, sql: str, *args):
        text = sql.lstrip()
        if "FROM support_request_receipts" in text:
            tenant_id, request_id = args
            row = self.db.receipts.get((tenant_id, request_id))
            if row is None:
                return None
            row = dict(row)
            if not self.db.jsonb_as_text:
                row["response"] = json.loads(row["response"])
            return row
        if text.startswith("INSERT INTO support_kb_articles"):
            slug, title, body_md, category, url, published, _request_id, updated_by, now = args
            article = {"id": uuid4(), "slug": slug, "title": title, "section": "", "body_md": body_md,
                       "category": category, "source": "staff", "published": published, "url": url,
                       "updated_by": updated_by, "created_at": now, "updated_at": now}
            self.db.articles[article["id"]] = article
            self.db.writes.append(("article_insert", slug))
            return dict(article)
        if text.startswith("SELECT") and "FROM support_kb_articles WHERE id = $1" in text:
            article = self.db.articles.get(args[0])
            return dict(article) if article else None
        if text.startswith("UPDATE support_kb_articles"):
            article = self.db.articles[args[0]]
            for name, position in re.findall(r"(\w+) = \$(\d+)", text.split("WHERE")[0]):
                article[name] = args[int(position) - 1]
            self.db.writes.append(("article_update", args[0]))
            return dict(article)
        raise AssertionError(f"unexpected statement: {sql}")


class _Audit:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def lock_transaction(self, conn) -> None:
        return None

    async def log_in_transaction(self, conn, **kwargs) -> None:
        self.db.writes.append(("audit", kwargs.get("action")))

    async def log(self, *args, **kwargs) -> None:
        return None


class _Harness:
    def __init__(self, monkeypatch, *, jsonb_as_text: bool) -> None:
        self.monkeypatch = monkeypatch
        self.db = _Db(jsonb_as_text=jsonb_as_text)
        self.current = {"id": REQUESTER, "role": "user", "tenant_id": TENANT}
        db = self.db

        # --- ticket store -------------------------------------------------
        async def fetch_ticket(conn, ticket_id, tenant_id, *, for_update=False):
            ticket = db.tickets.get(ticket_id)
            return dict(ticket) if ticket and ticket["tenant_id"] == tenant_id else None

        async def fetch_ticket_tenant(conn, ticket_id):
            ticket = db.tickets.get(ticket_id)
            return ticket["tenant_id"] if ticket else None

        async def update_ticket(conn, ticket_id, tenant_id, *, now, **fields):
            db.writes.append(("ticket_update", ticket_id, tuple(sorted(fields))))
            ticket = db.tickets[ticket_id]
            ticket.update(fields)
            ticket["version"] += 1
            return ticket["version"]

        async def insert_ticket_message(conn, *, ticket_id, tenant_id, author_id, author_type, body, is_internal,
                                        request_id, now):
            db.writes.append(("ticket_message", ticket_id, body))
            return {"id": uuid4(), "ticket_id": ticket_id, "author_user_id": author_id, "author_type": author_type,
                    "body": body, "is_internal": is_internal, "created_at": now}

        async def insert_event(conn, *, type, **kwargs):
            db.writes.append(("event", type))

        async def attach_files(conn, *, attachment_ids, **kwargs):
            db.writes.append(("attach", tuple(sorted(str(a) for a in attachment_ids))))

        async def mark_read(conn, **kwargs):
            db.writes.append(("read",))

        async def no_attachments(conn, ticket_id, tenant_id, message_ids):
            return []

        async def no_recent_ticket(conn, **kwargs):
            return None

        for name, fake in (("fetch_ticket", fetch_ticket), ("fetch_ticket_tenant", fetch_ticket_tenant),
                           ("update_ticket", update_ticket), ("insert_message", insert_ticket_message),
                           ("insert_event", insert_event), ("attach_files", attach_files),
                           ("mark_read", mark_read), ("fetch_attachments", no_attachments),
                           ("find_recent_open_ticket", no_recent_ticket)):
            monkeypatch.setattr(ticket_store, name, fake)

        async def customer_view(conn, ticket, actor, now):
            return {"id": str(ticket["id"]), "status": ticket["status"], "csat_score": ticket["csat_score"]}

        async def staff_detail(conn, ticket, now):
            return {"id": str(ticket["id"]), "status": ticket["status"], "priority": ticket["priority"],
                    "category": ticket["category"], "version": ticket["version"]}

        # --- conversation store -------------------------------------------
        async def insert_conversation(conn, *, tenant_id, user_id, page_context, request_id, now):
            conversation = self._conversation(tenant_id=tenant_id, user_id=user_id, page_context=dict(page_context))
            db.writes.append(("conversation", dict(page_context)))
            return dict(conversation)

        async def fetch_conversation(conn, conversation_id, tenant_id, *, for_update=False):
            conversation = db.conversations.get(conversation_id)
            return dict(conversation) if conversation and conversation["tenant_id"] == tenant_id else None

        async def update_conversation(conn, conversation_id, tenant_id, *, now, **fields):
            db.writes.append(("conversation_update", conversation_id, tuple(sorted(fields))))
            db.conversations[conversation_id].update(fields)

        async def insert_conversation_message(conn, *, conversation_id, tenant_id, role, content, now, sources=None,
                                              tool_calls=None, diagnostics=None, model=None, latency_ms=None,
                                              in_reply_to=None, request_id=None):
            row = {"id": uuid4(), "conversation_id": conversation_id, "role": role, "content": content,
                   "sources": sources or [], "tool_calls": tool_calls or [], "diagnostics": diagnostics or {},
                   "in_reply_to": in_reply_to, "request_id": request_id, "created_at": now}
            db.conversation_messages.append(row)
            db.writes.append(("conversation_message", role, content))
            return dict(row)

        async def fetch_conversation_messages(conn, conversation_id, tenant_id):
            return [dict(m) for m in db.conversation_messages if m["conversation_id"] == conversation_id]

        async def find_user_message(conn, *, tenant_id, request_id):
            return next((dict(m) for m in db.conversation_messages
                         if m["role"] == "user" and m["request_id"] == request_id), None)

        async def count_user_turns(conn, conversation_id, tenant_id):
            return sum(1 for m in db.conversation_messages
                       if m["conversation_id"] == conversation_id and m["role"] == "user")

        async def count_tenant_messages_since(conn, tenant_id, since):
            return sum(1 for m in db.conversation_messages if m["role"] == "user")

        async def owns(conn, table, record_id, tenant_id):
            return True

        async def run_turn(request, *, executor, **kwargs):
            db.model_calls += 1
            return BotTurn(answer="Check the caller ID status under Phone numbers.",
                           sources=(Source("caller-id", "Caller ID", "/docs/caller-id"),))

        for name, fake in (("insert_conversation", insert_conversation), ("fetch_conversation", fetch_conversation),
                           ("update_conversation", update_conversation),
                           ("insert_message", insert_conversation_message),
                           ("fetch_messages", fetch_conversation_messages), ("find_user_message", find_user_message),
                           ("count_user_turns", count_user_turns),
                           ("count_tenant_messages_since", count_tenant_messages_since)):
            monkeypatch.setattr(conversation_store, name, fake)

        # --- services, routes and what they call around the transaction ---
        async def nothing(*args, **kwargs):
            return None

        @asynccontextmanager
        async def acquire(pool, tenant_id, **kwargs):
            yield _Conn(db)

        monkeypatch.setattr(ticket_service, "_ticket_view", customer_view)
        monkeypatch.setattr(staff_service, "_detail", staff_detail)
        monkeypatch.setattr(conversation_service, "tenant_owns", owns)
        monkeypatch.setattr(conversation_service, "run_turn", run_turn)
        monkeypatch.setattr(conversations_endpoint, "_redis", lambda: None)
        for module in (ticket_service, staff_service, conversation_service, article_admin):
            monkeypatch.setattr(module, "acquire_with_tenant", acquire)
        for module in (staff_service, article_admin):
            monkeypatch.setattr(module, "require_platform_admin_mutation", nothing)
        monkeypatch.setattr(notifications, "ticket_resolved", nothing)
        monkeypatch.setattr(notifications, "staff_replied", nothing)
        monkeypatch.setattr(limiter, "enabled", False)
        monkeypatch.setenv("SUPPORT_ENABLED", "true")
        monkeypatch.delenv("SUPPORT_TENANT_ALLOWLIST", raising=False)

        self.app = build_app()
        self.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=self.current["id"], email="person@example.invalid", role=self.current["role"],
            tenant_id=self.current["tenant_id"])
        self.app.dependency_overrides[get_db_pool] = lambda: object()
        self.app.dependency_overrides[get_audit_logger] = lambda: _Audit(db)

    # --- seeds -------------------------------------------------------------
    def _ticket(self, status: str) -> UUID:
        now = datetime.now(timezone.utc)
        ticket_id = uuid4()
        self.db.tickets[ticket_id] = {
            "id": ticket_id, "tenant_id": TENANT, "requester_user_id": REQUESTER, "status": status,
            "priority": "normal", "category": "billing", "subject": "Billing question", "assignee_user_id": None,
            "version": 1, "created_at": now - timedelta(days=2), "updated_at": now - timedelta(days=1),
            "resolved_at": now - timedelta(days=1) if status == "resolved" else None, "closed_at": None,
            "csat_score": None, "csat_comment": None, "first_responded_at": None, "requester_name": "Requester",
            "related_campaign_id": None, "related_call_id": None}
        return ticket_id

    def _conversation(self, *, tenant_id: str = TENANT, user_id: str = REQUESTER, page_context=None) -> dict:
        now = datetime.now(timezone.utc)
        conversation = {"id": uuid4(), "tenant_id": tenant_id, "user_id": user_id, "status": "active",
                        "page_context": page_context or {}, "not_solved_count": 0, "escalation": None,
                        "ticket_id": None, "created_at": now, "updated_at": now, "ended_at": None}
        self.db.conversations[conversation["id"]] = conversation
        return conversation

    def _article(self) -> UUID:
        now = datetime.now(timezone.utc)
        article_id = uuid4()
        self.db.articles[article_id] = {
            "id": article_id, "slug": f"seed-{article_id.hex[:8]}", "title": "Original title", "section": "",
            "body_md": "Original body", "category": "billing", "source": "staff", "published": True,
            "url": "/support/articles/seed", "updated_by": STAFF, "created_at": now, "updated_at": now}
        return article_id

    def arrange(self, spec: "Spec") -> "World":
        for name, value in spec.env.items():
            self.monkeypatch.setenv(name, value)
        self.current.update({"id": STAFF, "role": "platform_admin", "tenant_id": None} if spec.staff
                            else {"id": REQUESTER, "role": "user", "tenant_id": TENANT})
        ids: dict[str, UUID | None] = {"main": None, "other": None}
        for slot in ids:
            if spec.world in ("open_ticket", "resolved_ticket"):
                ids[slot] = self._ticket("open" if spec.world == "open_ticket" else "resolved")
            elif spec.world == "conversation":
                ids[slot] = self._conversation()["id"]
            elif spec.world == "article":
                ids[slot] = self._article()
        return World(self, spec, ids)


@dataclass
class Spec:
    """One support write: its route, a request that works, and what changes its meaning."""
    id: str
    operation: str                       # the name its receipt is stored under
    method: str
    path: str                            # {main} is the target the request names
    base: dict                           # the body without request_id
    created: int                         # status of the first, real request
    world: str                           # what to seed: *_ticket, conversation, article or none
    conflicts: dict[str, tuple[str, dict]]   # label -> (target, body overrides): a different request
    same: dict[str, dict] = field(default_factory=dict)   # label -> overrides that are the same request
    staff: bool = False
    tenant: str | None = TENANT          # the tenant the receipt is keyed by (None: platform context)
    env: dict[str, str] = field(default_factory=dict)


class World:
    def __init__(self, harness: _Harness, spec: Spec, ids: dict[str, UUID | None]) -> None:
        self.harness, self.spec, self.ids = harness, spec, ids
        self.request_id = uuid4()
        self.actor = STAFF if spec.staff else REQUESTER

    async def send(self, overrides: dict | None = None, *, target: str = "main") -> httpx.Response:
        body = {**self.spec.base, **(overrides or {}), "request_id": str(self.request_id)}
        path = self.spec.path.format(main=self.ids[target])
        transport = httpx.ASGITransport(app=self.harness.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(self.spec.method, f"/api/v1{path}", json=body)


SPECS = [
    Spec("ticket.message", "ticket.message", "POST", "/support/tickets/{main}/messages",
         base={"body": "Any update on this?", "attachment_ids": [ATTACHMENT_ONE, ATTACHMENT_TWO]},
         created=201, world="open_ticket",
         conflicts={"body": ("main", {"body": "Here is more detail than before."}),
                    "attachment": ("main", {"attachment_ids": [ATTACHMENT_ONE]}),
                    "ticket": ("other", {})},
         same={"attachments reordered and repeated":
               {"attachment_ids": [ATTACHMENT_TWO, ATTACHMENT_ONE, ATTACHMENT_TWO]}}),
    Spec("ticket.resolve", "ticket.resolve", "POST", "/support/tickets/{main}/resolve",
         base={}, created=200, world="open_ticket", conflicts={"ticket": ("other", {})}),
    Spec("ticket.reopen", "ticket.reopen", "POST", "/support/tickets/{main}/reopen",
         base={}, created=200, world="resolved_ticket", conflicts={"ticket": ("other", {})}),
    Spec("ticket.csat", "ticket.csat", "POST", "/support/tickets/{main}/csat",
         base={"score": 4}, created=200, world="resolved_ticket",
         conflicts={"score": ("main", {"score": 5}),
                    "comment": ("main", {"comment": "It was quick."}),
                    "ticket": ("other", {})},
         same={"empty comment is no comment": {"comment": ""}}),
    Spec("conversation.start", "conversation.start", "POST", "/support/conversations",
         base={"page_context": {"route": "/campaigns", "campaign_id": CAMPAIGN}}, created=201, world="none",
         conflicts={"route": ("main", {"page_context": {"route": "/billing", "campaign_id": CAMPAIGN}}),
                    "campaign": ("main", {"page_context": {"route": "/campaigns", "campaign_id": OTHER_CAMPAIGN}}),
                    "call": ("main", {"page_context": {"route": "/campaigns", "campaign_id": CAMPAIGN,
                                                       "call_id": CALL}}),
                    "no page context": ("main", {"page_context": {}})},
         same={"viewport and app version are telemetry the client re-reads on a retry":
               {"page_context": {"route": "/campaigns", "campaign_id": CAMPAIGN, "viewport": "390x700",
                                 "app_version": "9.9.9"}},
               "keys the server ignores":
               {"page_context": {"route": "/campaigns", "campaign_id": CAMPAIGN, "something_else": "x"}}}),
    Spec("conversation.message (bot answers)", "conversation.message", "POST",
         "/support/conversations/{main}/messages",
         base={"content": "Why are my calls not going out?"}, created=200, world="conversation",
         env={"SUPPORT_BOT_ENABLED": "true"},
         conflicts={"content": ("main", {"content": "Actually my caller ID is the problem."}),
                    "conversation": ("other", {})}),
    Spec("conversation.message (hand-over)", "conversation.message", "POST",
         "/support/conversations/{main}/messages",
         base={"content": "Why are my calls not going out?"}, created=200, world="conversation",
         env={"SUPPORT_BOT_ENABLED": "false"},
         conflicts={"content": ("main", {"content": "Actually my caller ID is the problem."}),
                    "conversation": ("other", {})}),
    Spec("conversation.feedback", "conversation.feedback", "POST", "/support/conversations/{main}/feedback",
         base={"solved": False}, created=200, world="conversation",
         conflicts={"solved": ("main", {"solved": True}), "conversation": ("other", {})}),
    Spec("conversation.escalate", "conversation.escalate", "POST", "/support/conversations/{main}/escalate",
         base={}, created=200, world="conversation", conflicts={"conversation": ("other", {})}),
    Spec("staff.ticket.patch", "staff.ticket.patch", "PATCH", "/admin/support/tickets/{main}",
         base={"version": 1, "status": "in_progress"}, created=200, world="open_ticket", staff=True,
         conflicts={"version": ("main", {"version": 2}),
                    "status": ("main", {"status": "waiting_on_customer"}),
                    "priority": ("main", {"priority": "high"}),
                    "category": ("main", {"category": "bug"}),
                    "assignee": ("main", {"assignee_user_id": OTHER_STAFF}),
                    "unassign": ("main", {"assignee_user_id": None}),
                    "ticket": ("other", {})},
         same={"a null field is a field not sent": {"priority": None, "category": None}}),
    Spec("staff.ticket.message", "staff.ticket.message", "POST", "/admin/support/tickets/{main}/messages",
         base={"body": "We are looking into it.", "internal": False,
               "attachment_ids": [ATTACHMENT_ONE, ATTACHMENT_TWO]},
         created=201, world="open_ticket", staff=True,
         conflicts={"body": ("main", {"body": "We found the cause."}),
                    "internal": ("main", {"internal": True}),
                    "attachment": ("main", {"attachment_ids": [ATTACHMENT_ONE]}),
                    "ticket": ("other", {})},
         same={"attachments reordered and repeated":
               {"attachment_ids": [ATTACHMENT_TWO, ATTACHMENT_ONE, ATTACHMENT_TWO]}}),
    Spec("staff.article.create", "staff.article.create", "POST", "/admin/support/articles",
         base={"slug": "verify-caller-id", "title": "Verify your caller ID", "body_md": "# Verify\n\nOpen Phone numbers.",
               "category": "phone_numbers", "published": True},
         created=201, world="none", staff=True, tenant=None,
         conflicts={"slug": ("main", {"slug": "verify-a-caller-id"}),
                    "title": ("main", {"title": "Verifying your caller ID"}),
                    "body": ("main", {"body_md": "# Verify\n\nOpen Phone numbers, then Verify."}),
                    "category": ("main", {"category": "other"}),
                    "published": ("main", {"published": False})}),
    Spec("staff.article.update", "staff.article.update", "PATCH", "/admin/support/articles/{main}",
         base={"title": "Renamed article", "published": False}, created=200, world="article", staff=True,
         tenant=None,
         conflicts={"title": ("main", {"title": "Renamed again"}),
                    "body": ("main", {"body_md": "A new body."}),
                    "category": ("main", {"category": "other"}),
                    "published": ("main", {"published": True}),
                    "article": ("other", {})},
         same={"a null field is a field not sent": {"body_md": None, "category": None}}),
]
SPEC_IDS = [spec.id for spec in SPECS]
CONFLICTS = [(spec, label) for spec in SPECS for label in spec.conflicts]
SAMES = [(spec, label) for spec in SPECS for label in spec.same]


@pytest.fixture(params=[False, True], ids=["jsonb_object", "jsonb_text"])
def harness(request, monkeypatch):
    return _Harness(monkeypatch, jsonb_as_text=request.param)


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
async def test_the_identical_request_replays_the_stored_response(harness, spec):
    world = harness.arrange(spec)
    first = await world.send()
    writes = list(harness.db.writes)
    replay = await world.send()

    assert first.status_code == spec.created, first.text
    assert replay.status_code == 200 and replay.headers["Idempotent-Replay"] == "true", replay.text
    assert replay.json() == first.json()
    assert harness.db.writes == writes and len(harness.db.receipts) == 1


@pytest.mark.parametrize("spec,label", CONFLICTS, ids=[f"{spec.id}-{label}" for spec, label in CONFLICTS])
async def test_the_same_request_id_with_changed_content_is_a_409_and_writes_nothing(harness, spec, label):
    world = harness.arrange(spec)
    first = await world.send()
    writes, receipts = list(harness.db.writes), dict(harness.db.receipts)
    target, overrides = spec.conflicts[label]
    refused = await world.send(overrides, target=target)

    assert first.status_code == spec.created, first.text
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "request_id_conflict"
    assert harness.db.writes == writes
    assert harness.db.receipts == receipts
    # The refused request left the stored response alone.
    replay = await world.send()
    assert replay.status_code == 200 and replay.json() == first.json()


@pytest.mark.parametrize("spec,label", SAMES, ids=[f"{spec.id}-{label}" for spec, label in SAMES])
async def test_the_same_content_written_another_way_is_the_same_request(harness, spec, label):
    world = harness.arrange(spec)
    first = await world.send()
    writes = list(harness.db.writes)
    replay = await world.send(spec.same[label])

    assert first.status_code == spec.created, first.text
    assert replay.status_code == 200 and replay.headers["Idempotent-Replay"] == "true", replay.text
    assert replay.json() == first.json()
    assert harness.db.writes == writes


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
async def test_a_receipt_stored_before_fingerprints_still_replays_whatever_the_content(harness, spec):
    world = harness.arrange(spec)
    stored = {"stored": "before fingerprints"}
    harness.db.receipts[(spec.tenant, world.request_id)] = {
        "actor_user_id": world.actor, "operation": spec.operation, "status_code": spec.created,
        "response": json.dumps(stored)}
    target, overrides = next(iter(spec.conflicts.values()))

    replay = await world.send(overrides, target=target)

    assert replay.status_code == 200 and replay.json() == stored, replay.text
    assert harness.db.writes == []


@pytest.mark.parametrize("spec", SPECS, ids=SPEC_IDS)
async def test_the_receipt_keeps_a_fingerprint_the_client_never_sees(harness, spec):
    world = harness.arrange(spec)
    first = await world.send()
    replay = await world.send()

    (receipt,) = harness.db.receipts.values()
    assert FINGERPRINT_KEY in json.loads(receipt["response"])
    assert FINGERPRINT_KEY not in first.json() and FINGERPRINT_KEY not in replay.json()


async def test_a_refused_message_never_reaches_the_model(harness):
    spec = next(s for s in SPECS if s.id == "conversation.message (bot answers)")
    world = harness.arrange(spec)
    first = await world.send()
    refused = await world.send({"content": "A different question entirely."})

    assert first.status_code == 200 and harness.db.model_calls == 1
    assert refused.status_code == 409
    assert harness.db.model_calls == 1
    assert [m["content"] for m in harness.db.conversation_messages if m["role"] == "user"] == [
        spec.base["content"]]


@pytest.mark.parametrize("same_content", [False, True], ids=["other_content", "same_content"])
async def test_a_receipt_that_appears_during_the_model_call_is_checked_against_the_content(
        harness, monkeypatch, same_content):
    """The second claim, after the model call, compares content too."""
    spec = next(s for s in SPECS if s.id == "conversation.message (bot answers)")
    world = harness.arrange(spec)
    fingerprint = receipts.body_fingerprint(
        conversation_id=world.ids["main"],
        content=spec.base["content"] if same_content else "Something this request never said")
    stored = {"message": "written by the request that finished first", FINGERPRINT_KEY: fingerprint}

    async def run_turn_while_another_request_finishes(request, *, executor, **kwargs):
        harness.db.receipts[(TENANT, world.request_id)] = {
            "actor_user_id": REQUESTER, "operation": "conversation.message", "status_code": 200,
            "response": json.dumps(stored)}
        return BotTurn(answer="Late answer.", sources=(Source("caller-id", "Caller ID", "/docs/caller-id"),))

    monkeypatch.setattr(conversation_service, "run_turn", run_turn_while_another_request_finishes)
    response = await world.send()

    # Only step 1's write (the customer's message) happened; step 3 stored no reply.
    assert harness.db.writes == [("conversation_message", "user", spec.base["content"])]
    if same_content:
        assert response.status_code == 200 and response.headers["Idempotent-Replay"] == "true", response.text
        assert response.json() == {"message": stored["message"]}
    else:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "request_id_conflict"


async def test_a_refused_feedback_does_not_end_the_conversation(harness):
    spec = next(s for s in SPECS if s.id == "conversation.feedback")
    world = harness.arrange(spec)
    first = await world.send()
    refused = await world.send({"solved": True})

    assert first.status_code == 200 and refused.status_code == 409
    conversation = harness.db.conversations[world.ids["main"]]
    assert conversation["status"] == "active" and conversation["not_solved_count"] == 1


async def test_a_refused_start_creates_no_second_conversation(harness):
    spec = next(s for s in SPECS if s.id == "conversation.start")
    world = harness.arrange(spec)
    await world.send()
    refused = await world.send({"page_context": {"route": "/billing"}})

    assert refused.status_code == 409
    assert len(harness.db.conversations) == 1
