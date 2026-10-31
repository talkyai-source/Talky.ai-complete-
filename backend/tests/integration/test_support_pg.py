"""Support behaviour on migrated PostgreSQL: replays, concurrency, numbering,
status flows, the bot conversation, limits, jobs and notification receipts."""
# ruff: noqa: F811 -- pytest discovers the imported support_db fixture by parameter name.
from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from app.domain.services.support import conversation_service, jobs, notifications
from app.domain.services.support.bot.types import BotTurn, Finding, Source, ToolTrace
from app.domain.services.support.config import load_settings
from tests.helpers.support_db import new_request_id, support_db  # noqa: F401

pytestmark = pytest.mark.integration


async def _ticket(db, who="a_user", **overrides):
    body = {"request_id": new_request_id(), "subject": "Calls stopped", "category": "calls_campaigns",
            "description": "The campaign stopped calling at noon."}
    body.update(overrides)
    response = await db.http(who, "POST", "/support/tickets", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _fake_bot(monkeypatch, turn: BotTurn, calls: list | None = None):
    async def fake_run_turn(request, *, executor, **kwargs):
        if calls is not None:
            calls.append(request)
        return turn
    monkeypatch.setattr(conversation_service, "run_turn", fake_run_turn)


async def test_request_id_replay_returns_the_original_result(support_db):
    db = support_db
    body = {"request_id": new_request_id(), "subject": "Billing question", "category": "billing",
            "description": "Where do I download invoices?"}
    first = await db.http("a_user", "POST", "/support/tickets", json=body)
    replay = await db.http("a_user", "POST", "/support/tickets", json={**body, "subject": "Changed"})
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == first.json()
    count = await db.owner.fetchval("SELECT count(*) FROM support_tickets WHERE tenant_id = $1", db.tenants["a"])
    assert count == 1
    reused = await db.http("a_user", "POST", f"/support/tickets/{first.json()['id']}/resolve",
                           json={"request_id": body["request_id"]})
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "request_id_conflict"
    colleague = await db.http("a_colleague", "POST", "/support/tickets", json=body)
    assert colleague.status_code == 409  # someone else's request id never returns their result


async def test_message_and_staff_replays_do_not_duplicate(support_db):
    db = support_db
    ticket = await _ticket(db)
    message = {"request_id": new_request_id(), "body": "Any update?"}
    one = await db.http("a_user", "POST", f"/support/tickets/{ticket['id']}/messages", json=message)
    two = await db.http("a_user", "POST", f"/support/tickets/{ticket['id']}/messages", json=message)
    assert one.status_code == 201 and two.status_code == 200 and one.json() == two.json()
    patch = {"request_id": new_request_id(), "version": 2, "status": "in_progress"}
    first = await db.http("staff", "PATCH", f"/admin/support/tickets/{ticket['id']}", json=patch)
    again = await db.http("staff", "PATCH", f"/admin/support/tickets/{ticket['id']}", json=patch)
    assert first.status_code == 200, first.text
    assert again.status_code == 200 and again.json() == first.json()
    assert await db.owner.fetchval("SELECT count(*) FROM support_ticket_messages WHERE ticket_id = $1",
                                   UUID(ticket["id"])) == 2


async def test_two_concurrent_patches_exactly_one_conflict(support_db):
    db = support_db
    ticket = await _ticket(db)
    path = f"/admin/support/tickets/{ticket['id']}"
    results = await asyncio.gather(
        db.http("staff", "PATCH", path, json={"request_id": new_request_id(), "version": 1, "priority": "high"}),
        db.http("staff", "PATCH", path, json={"request_id": new_request_id(), "version": 1, "priority": "low"}))
    codes = sorted(response.status_code for response in results)
    assert codes == [200, 409], [r.text for r in results]
    conflict = next(r for r in results if r.status_code == 409)
    assert conflict.json()["error"]["code"] == "version_conflict"


async def test_ticket_numbers_come_from_one_sequence(support_db):
    db = support_db
    first, second = await _ticket(db), await _ticket(db, who="b_user")
    a, b = int(first["number"].removeprefix("SUP-")), int(second["number"].removeprefix("SUP-"))
    assert b == a + 1
    assert first["number"] == f"SUP-{a:06d}"


async def test_priority_is_computed_and_customers_cannot_set_it(support_db):
    db = support_db
    high = await _ticket(db, category="billing", subject="Charged twice", description="I was charged twice for May.")
    assert high["priority"] == "high"
    low = await _ticket(db, category="feature_request", subject="Zoho please", description="Please add a Zoho CRM sync.")
    assert low["priority"] == "low"
    urgent = await _ticket(db, category="other", subject="Security issue", description="I found a vulnerability in login.")
    assert urgent["priority"] == "urgent"
    refused = await db.http("a_user", "POST", "/support/tickets", json={
        "request_id": new_request_id(), "subject": "Make it urgent", "category": "other",
        "description": "I want to choose the priority.", "priority": "urgent"})
    assert refused.status_code == 422
    row = await db.owner.fetchrow("SELECT created_at, first_response_due_at, resolution_due_at FROM support_tickets "
                                  "WHERE id = $1", UUID(high["id"]))
    assert row["first_response_due_at"] - row["created_at"] == timedelta(hours=4)
    assert row["resolution_due_at"] - row["created_at"] == timedelta(hours=24)


async def test_customer_and_staff_status_flow(support_db):
    db = support_db
    ticket = await _ticket(db)
    path = f"/support/tickets/{ticket['id']}"
    staff_path = f"/admin/support/tickets/{ticket['id']}"
    assert (await db.http("staff", "PATCH", staff_path, json={"request_id": new_request_id(), "version": 1,
                                                             "status": "closed"})).status_code == 409
    waiting = await db.http("staff", "PATCH", staff_path, json={
        "request_id": new_request_id(), "version": 1, "status": "waiting_on_customer",
        "assignee_user_id": str(db.users["staff"])})
    assert waiting.status_code == 200, waiting.text
    await db.http("a_user", "POST", f"{path}/messages", json={"request_id": new_request_id(), "body": "Here it is"})
    assert (await db.http("a_user", "GET", path)).json()["status"] == "in_progress"
    resolved = await db.http("a_user", "POST", f"{path}/resolve", json={"request_id": new_request_id()})
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["allowed_actions"] == ["reply", "reopen", "rate"]
    reopened = await db.http("a_user", "POST", f"{path}/reopen", json={"request_id": new_request_id()})
    assert reopened.json()["status"] == "in_progress"
    assert (await db.http("a_user", "POST", f"{path}/csat", json={"request_id": new_request_id(), "score": 4})
            ).status_code == 409
    await db.http("a_user", "POST", f"{path}/resolve", json={"request_id": new_request_id()})
    rated = await db.http("a_user", "POST", f"{path}/csat", json={"request_id": new_request_id(), "score": 5,
                                                                 "comment": "Quick fix"})
    assert rated.json()["csat"] == {"score": 5, "comment": "Quick fix"}
    assert (await db.http("a_user", "POST", f"{path}/csat", json={"request_id": new_request_id(), "score": 1})
            ).status_code == 409
    await db.owner.execute("UPDATE support_tickets SET resolved_at = now() - interval '8 days' WHERE id = $1",
                           UUID(ticket["id"]))
    assert (await db.http("a_user", "POST", f"{path}/reopen", json={"request_id": new_request_id()})).status_code == 409
    closed = await jobs.close_resolved(db.pool)
    assert closed >= 1
    assert (await db.http("a_user", "GET", path)).json()["status"] == "closed"
    assert (await db.http("a_user", "POST", f"{path}/messages", json={"request_id": new_request_id(),
                                                                     "body": "again"})).status_code == 409


async def test_first_public_staff_reply_sets_first_response_once(support_db):
    db = support_db
    ticket = await _ticket(db)
    await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages",
                  json={"request_id": new_request_id(), "body": "internal", "internal": True})
    assert await db.owner.fetchval("SELECT first_responded_at FROM support_tickets WHERE id = $1",
                                   UUID(ticket["id"])) is None
    await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages",
                  json={"request_id": new_request_id(), "body": "public", "internal": False})
    first = await db.owner.fetchval("SELECT first_responded_at FROM support_tickets WHERE id = $1", UUID(ticket["id"]))
    await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages",
                  json={"request_id": new_request_id(), "body": "second", "internal": False})
    assert first is not None
    assert await db.owner.fetchval("SELECT first_responded_at FROM support_tickets WHERE id = $1",
                                   UUID(ticket["id"])) == first


async def test_bot_conversation_to_ticket(support_db, monkeypatch):
    db = support_db
    campaign = await db.seed_campaign("a")
    finding = Finding("caller_id_not_verified", "Caller ID not verified", "Staff must verify it.", staff_only=True)
    _fake_bot(monkeypatch, BotTurn(answer="Your caller ID is still being verified.", category="phone_numbers",
                                   sources=(Source("troubleshooting-x", "Troubleshooting", "/docs/troubleshooting#x"),),
                                   tool_calls=(ToolTrace("get_phone_numbers_status", "1 number pending"),),
                                   findings=(finding,), resolution_prompt=True, model="fake", latency_ms=5))
    started = await db.http("a_user", "POST", "/support/conversations", json={
        "request_id": new_request_id(), "page_context": {"route": "/campaigns/x", "campaign_id": str(campaign)}})
    conversation = started.json()["conversation_id"]
    path = f"/support/conversations/{conversation}"
    answer = await db.http("a_user", "POST", f"{path}/messages",
                           json={"request_id": new_request_id(), "content": "Why are calls not going out?"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["resolution_prompt"] is True and answer.json()["escalation"] is None
    assert answer.json()["message"]["sources"] == [{"slug": "troubleshooting-x", "title": "Troubleshooting",
                                                    "url": "/docs/troubleshooting#x"}]
    first = await db.http("a_user", "POST", f"{path}/feedback", json={"request_id": new_request_id(), "solved": False})
    assert first.json()["escalation"] is None and first.json()["message"]["role"] == "assistant"
    second = await db.http("a_user", "POST", f"{path}/feedback", json={"request_id": new_request_id(), "solved": False})
    escalation = second.json()["escalation"]
    assert escalation["reason"] == "not_solved_twice" and escalation["priority_hint"] == "high"
    assert escalation["draft"]["category"] == "phone_numbers"
    assert escalation["draft"]["related_campaign_id"] == str(campaign)
    ticket = await _ticket(db, conversation_id=conversation, **{k: escalation["draft"][k] for k in
                                                               ("subject", "category", "description")})
    assert ticket["priority"] == "high"
    diagnostics = await db.owner.fetchval("SELECT diagnostics FROM support_tickets WHERE id = $1", UUID(ticket["id"]))
    assert "caller_id_not_verified" in diagnostics and "get_phone_numbers_status" in diagnostics
    state = (await db.http("a_user", "GET", path)).json()
    assert state["status"] == "escalated"
    assert (await db.http("a_user", "POST", f"{path}/messages", json={"request_id": new_request_id(),
                                                                     "content": "hello?"})).status_code == 409
    again = await db.http("a_user", "POST", "/support/tickets", json={
        "request_id": new_request_id(), "conversation_id": conversation, "subject": "Dup", "category": "other",
        "description": "A second ticket from the same chat."})
    assert again.status_code == 409


async def test_sensitive_topic_hands_over_without_calling_the_model(support_db, monkeypatch):
    db = support_db
    calls: list = []
    _fake_bot(monkeypatch, BotTurn(answer="unused"), calls)
    existing = await _ticket(db, category="billing", subject="Invoice wrong", description="My invoice total is wrong.")
    conversation = (await db.http("a_user", "POST", "/support/conversations",
                                  json={"request_id": new_request_id()})).json()["conversation_id"]
    response = await db.http("a_user", "POST", f"/support/conversations/{conversation}/messages",
                             json={"request_id": new_request_id(), "content": "I want a refund for last month"})
    escalation = response.json()["escalation"]
    assert calls == []
    assert escalation["reason"] == "refund_or_billing_dispute"
    assert escalation["draft"]["category"] == "billing"
    assert escalation["existing_ticket"]["id"] == existing["id"]


async def test_model_failure_fails_open_to_the_ticket_form(support_db, monkeypatch):
    db = support_db
    _fake_bot(monkeypatch, BotTurn(answer="sorry", error="timeout"))
    conversation = (await db.http("a_user", "POST", "/support/conversations",
                                  json={"request_id": new_request_id()})).json()["conversation_id"]
    response = await db.http("a_user", "POST", f"/support/conversations/{conversation}/messages",
                             json={"request_id": new_request_id(), "content": "My dashboard is blank"})
    assert response.status_code == 200
    assert response.json()["escalation"]["reason"] == "bot_unavailable"


async def test_turn_and_daily_limits_offer_a_ticket(support_db, monkeypatch):
    db = support_db
    monkeypatch.setenv("SUPPORT_MAX_TURNS", "2")
    _fake_bot(monkeypatch, BotTurn(answer="Try the Calls page.", sources=(Source("a-b", "A", "/docs/a#b"),)))
    conversation = (await db.http("a_user", "POST", "/support/conversations",
                                  json={"request_id": new_request_id()})).json()["conversation_id"]
    path = f"/support/conversations/{conversation}/messages"
    for _ in range(2):
        ok = await db.http("a_user", "POST", path, json={"request_id": new_request_id(), "content": "question"})
        assert ok.json()["escalation"] is None
    limited = await db.http("a_user", "POST", path, json={"request_id": new_request_id(), "content": "one more"})
    assert limited.json()["escalation"]["reason"] == "limit_reached"
    monkeypatch.setenv("SUPPORT_MAX_TURNS", "20")
    monkeypatch.setenv("SUPPORT_DAILY_MESSAGE_CAP_PER_TENANT", "3")
    other = (await db.http("a_colleague", "POST", "/support/conversations",
                           json={"request_id": new_request_id()})).json()["conversation_id"]
    capped = await db.http("a_colleague", "POST", f"/support/conversations/{other}/messages",
                           json={"request_id": new_request_id(), "content": "hello"})
    assert capped.json()["escalation"]["reason"] == "limit_reached"


async def test_missed_deadlines_are_marked_once_and_alerted(support_db, monkeypatch):
    db = support_db
    alerts: list = []

    async def fake_alert(pool, ticket_id, tenant_id, headline, settings, *, kind):
        alerts.append((ticket_id, kind))
    monkeypatch.setattr(notifications, "alert_staff", fake_alert)
    ticket = await _ticket(db)
    await db.owner.execute("UPDATE support_tickets SET first_response_due_at = now() - interval '1 minute' "
                           "WHERE id = $1", UUID(ticket["id"]))
    settings = load_settings()
    first = await jobs.mark_missed_deadlines(db.pool, settings)
    second = await jobs.mark_missed_deadlines(db.pool, settings)
    assert first["first_response"] >= 1 and second["first_response"] == 0
    assert [kind for tid, kind in alerts if str(tid) == ticket["id"]] == ["sla_first_response"]
    events = await db.owner.fetch("SELECT to_value FROM support_ticket_events WHERE ticket_id = $1 AND type = "
                                  "'sla_breached'", UUID(ticket["id"]))
    assert [e["to_value"] for e in events] == ["first_response"]
    queue = await db.http("staff", "GET", "/admin/support/tickets?sla=breached")
    assert ticket["id"] in {item["id"] for item in queue.json()["items"]}


async def test_hourly_housekeeping(support_db):
    db = support_db
    upload = await db.http("a_user", "POST", "/support/attachments",
                           files={"file": ("notes.txt", b"call log line 1\n", "text/plain")})
    attachment = UUID(upload.json()["attachment_id"])
    await db.owner.execute("UPDATE support_ticket_attachments SET created_at = now() - interval '25 hours' "
                           "WHERE id = $1", attachment)
    conversation = UUID((await db.http("a_user", "POST", "/support/conversations",
                                       json={"request_id": new_request_id()})).json()["conversation_id"])
    await db.owner.execute("UPDATE support_conversations SET updated_at = now() - interval '25 hours' WHERE id = $1",
                           conversation)
    summary = await jobs.run_hourly_pass(db.pool, load_settings())
    assert summary["uploads_deleted"] >= 1 and summary["conversations_abandoned"] >= 1
    assert await db.owner.fetchval("SELECT count(*) FROM support_ticket_attachments WHERE id = $1", attachment) == 0
    assert await db.owner.fetchval("SELECT status FROM support_conversations WHERE id = $1",
                                   conversation) == "abandoned"
    await db.owner.execute("UPDATE support_conversations SET ended_at = now() - interval '200 days' WHERE id = $1",
                           conversation)
    assert await jobs.purge_old_transcripts(db.pool, load_settings()) >= 1
    assert await db.owner.fetchval("SELECT count(*) FROM support_conversations WHERE id = $1", conversation) == 0


async def test_notification_outcomes_are_recorded_and_never_raise(support_db, monkeypatch):
    db = support_db
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    import app.domain.services.notification_service as notification_module
    monkeypatch.setattr(notification_module, "_notification_service", None)
    ticket = await _ticket(db)
    await notifications.ticket_created(db.pool, UUID(ticket["id"]), str(db.tenants["a"]), "high", load_settings())
    rows = await db.owner.fetch("SELECT from_value, to_value FROM support_ticket_events WHERE ticket_id = $1 AND "
                                "type = 'notification' ORDER BY created_at", UUID(ticket["id"]))
    outcomes = {row["from_value"]: row["to_value"] for row in rows}
    assert outcomes["email:ticket_created"] == "skipped"
    assert outcomes["slack:staff_new_ticket"] in {"skipped", "failed"}
