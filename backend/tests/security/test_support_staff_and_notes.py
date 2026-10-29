"""Staff console boundaries, internal notes, and bot tools against real SQL.

Internal notes (and their files) never reach a customer response; the bot's
read-only tools bind the signed-in tenant even when the model names another
tenant's call or campaign.
"""
# ruff: noqa: F811 -- pytest discovers the imported support_db fixture by parameter name.
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.domain.services.support.bot.diagnostics import ToolExecutor
from tests.helpers.support_db import new_request_id, support_db  # noqa: F401

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


async def _ticket(db, who="a_user"):
    response = await db.http(who, "POST", "/support/tickets", json={
        "request_id": new_request_id(), "subject": "Connector keeps expiring", "category": "integrations",
        "description": "HubSpot disconnects every day."})
    assert response.status_code == 201, response.text
    return response.json()


async def test_internal_notes_and_their_files_never_reach_the_customer(support_db):
    db = support_db
    ticket = await _ticket(db)
    upload = await db.http("staff", "POST", "/support/attachments",
                           files={"file": ("internal.png", PNG, "image/png")})
    assert upload.status_code == 201, upload.text
    internal_file = upload.json()["attachment_id"]
    note = await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages", json={
        "request_id": new_request_id(), "body": "INTERNAL: tenant owes us an invoice", "internal": True,
        "attachment_ids": [internal_file]})
    assert note.status_code == 201, note.text
    assert note.json()["is_internal"] is True
    reply = await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages", json={
        "request_id": new_request_id(), "body": "Please reconnect HubSpot from Connectors.", "internal": False})
    assert reply.status_code == 201

    customer = await db.http("a_user", "GET", f"/support/tickets/{ticket['id']}")
    text = json.dumps(customer.json())
    assert "INTERNAL" not in text and internal_file not in text and "is_internal" not in text
    assert [m["author_type"] for m in customer.json()["messages"]] == ["customer", "staff"]
    assert customer.json()["messages"][1]["author_name"] == "Talk-Lee Support"
    assert (await db.http("a_user", "GET", f"/support/attachments/{internal_file}")).status_code == 403
    assert (await db.http("a_admin", "GET", f"/support/attachments/{internal_file}")).status_code == 403

    staff_view = await db.http("staff", "GET", f"/admin/support/tickets/{ticket['id']}")
    bodies = {m["body"]: m["is_internal"] for m in staff_view.json()["messages"]}
    assert bodies["INTERNAL: tenant owes us an invoice"] is True
    assert (await db.http("staff", "GET", f"/support/attachments/{internal_file}")).status_code == 302


async def test_unread_counts_public_staff_replies_only(support_db):
    db = support_db
    ticket = await _ticket(db)
    await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages",
                  json={"request_id": new_request_id(), "body": "note", "internal": True})
    assert (await db.http("a_user", "GET", "/support/unread")).json()["count"] == 0
    await db.http("staff", "POST", f"/admin/support/tickets/{ticket['id']}/messages",
                  json={"request_id": new_request_id(), "body": "public reply", "internal": False})
    unread = (await db.http("a_user", "GET", "/support/unread")).json()
    assert unread == {"count": 1, "ticket_ids": [ticket["id"]]}
    listing = (await db.http("a_user", "GET", "/support/tickets")).json()["items"]
    assert listing[0]["unread"] is True
    await db.http("a_user", "GET", f"/support/tickets/{ticket['id']}")
    assert (await db.http("a_user", "GET", "/support/unread")).json()["count"] == 0


async def test_staff_console_sees_every_tenant_and_filters(support_db):
    db = support_db
    a_ticket, b_ticket = await _ticket(db, "a_user"), await _ticket(db, "b_user")
    queue = await db.http("staff", "GET", f"/admin/support/tickets?tenant_id={db.tenants['b']}")
    ids = {item["id"] for item in queue.json()["items"]}
    assert b_ticket["id"] in ids and a_ticket["id"] not in ids
    by_number = await db.http("staff", "GET", f"/admin/support/tickets?q={a_ticket['number']}")
    assert [item["id"] for item in by_number.json()["items"]] == [a_ticket["id"]]
    detail = (await db.http("staff", "GET", f"/admin/support/tickets/{a_ticket['id']}")).json()
    assert detail["tenant"]["id"] == str(db.tenants["a"])
    assert detail["version"] == 1 and detail["sla"]["breached"] is False
    assert detail["events"][0]["type"] == "created"


async def test_staff_routes_are_404_when_support_is_switched_off(support_db, monkeypatch):
    db = support_db
    monkeypatch.setenv("SUPPORT_ENABLED", "false")
    assert (await db.http("staff", "GET", "/admin/support/tickets")).status_code == 404
    assert (await db.http("a_user", "GET", "/support/tickets")).status_code == 404
    config = await db.http("a_user", "GET", "/support/config")
    assert config.status_code == 200 and config.json()["enabled"] is False


async def test_staff_writes_are_audited_in_the_same_transaction(support_db):
    db = support_db
    ticket = await _ticket(db)
    patch = await db.http("staff", "PATCH", f"/admin/support/tickets/{ticket['id']}",
                          json={"request_id": new_request_id(), "version": 1, "priority": "high"})
    assert patch.status_code == 200, patch.text
    rows = await db.owner.fetch(
        "SELECT action, after_state FROM audit_logs WHERE resource_id = $1 AND actor_id = $2",
        __import__("uuid").UUID(ticket["id"]), db.users["staff"])
    assert [row["action"] for row in rows] == ["support.ticket.update"]


async def _seed_call(db, tenant_key):
    call_id, campaign_id = uuid4(), await db.seed_campaign(tenant_key, f"Calls {uuid4().hex[:6]}")
    await db.owner.execute(
        "INSERT INTO calls (id, tenant_id, campaign_id, phone_number, status) "
        "VALUES ($1, $2, $3, '+15550100999', 'completed')", call_id, db.tenants[tenant_key], campaign_id)
    return call_id


async def test_bot_tools_cannot_read_another_tenant_even_with_its_ids(support_db):
    db = support_db
    foreign_call = await _seed_call(db, "b")
    foreign_campaign = await db.seed_campaign("b", "Tenant B secret campaign")
    own_call = await _seed_call(db, "a")
    executor = ToolExecutor(pool=db.pool, redis=None, tenant_id=str(db.tenants["a"]), user_id=str(db.users["a_user"]))

    assert (await executor.execute("get_call_details", {"call_id": str(foreign_call)})).result == {"error": "not_found"}
    for campaign in (str(foreign_campaign), "Tenant B secret campaign"):
        outcome = await executor.execute("get_campaign_status", {"campaign": campaign})
        assert outcome.result == {"error": "not_found"}
    own = await executor.execute("get_call_details", {"call_id": str(own_call)})
    assert "error" not in own.result
    assert "+15550100999" not in json.dumps(own.result)


async def test_every_bot_tool_runs_against_the_real_schema(support_db):
    db = support_db
    await _ticket(db)
    executor = ToolExecutor(pool=db.pool, redis=None, tenant_id=str(db.tenants["a"]), user_id=str(db.users["a_user"]))
    for name, args in (("search_help_articles", {"query": "calling hours"}), ("get_account_overview", {}),
                       ("get_recent_call_issues", {}), ("get_phone_numbers_status", {}),
                       ("get_connectors_status", {}), ("find_open_tickets", {})):
        outcome = await executor.execute(name, args)
        assert outcome.result.get("error") != "check_unavailable", (name, outcome.result)
    tickets = await executor.execute("find_open_tickets", {})
    assert json.dumps(tickets.result).count("SUP-") == 1
