"""Support isolation on migrated PostgreSQL with a restricted (non-bypass) role.

Another tenant's ticket, message, attachment or conversation is 403 for reads
and writes; a client-supplied tenant_id can't select another tenant; the team
list needs support:read_team; a colleague's ticket is read-only.
"""
# ruff: noqa: F811 -- pytest discovers the imported support_db fixture by parameter name.
from __future__ import annotations

from uuid import uuid4

import pytest

from tests.helpers.support_db import new_request_id, support_db  # noqa: F401

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _ticket(db, who="a_user", **overrides):
    body = {"request_id": new_request_id(), "subject": "Calls are not going out",
            "category": "calls_campaigns", "description": "Nothing has dialled since this morning."}
    body.update(overrides)
    response = await db.http(who, "POST", "/support/tickets", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _conversation(db, who="a_user"):
    response = await db.http(who, "POST", "/support/conversations",
                             json={"request_id": new_request_id(), "page_context": {"route": "/campaigns"}})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


async def test_other_tenants_ticket_is_403_for_every_read_and_write(support_db):
    db = support_db
    ticket = await _ticket(db)
    path = f"/support/tickets/{ticket['id']}"
    assert (await db.http("b_user", "GET", path)).status_code == 403
    for action, body in (("messages", {"request_id": new_request_id(), "body": "hello"}),
                         ("resolve", {"request_id": new_request_id()}),
                         ("reopen", {"request_id": new_request_id()}),
                         ("csat", {"request_id": new_request_id(), "score": 5})):
        response = await db.http("b_user", "POST", f"{path}/{action}", json=body)
        assert response.status_code == 403, (action, response.text)
    # Nothing leaked into tenant B's own lists either.
    listing = (await db.http("b_user", "GET", "/support/tickets")).json()
    assert listing["items"] == []


async def test_unknown_ticket_is_404_not_403(support_db):
    assert (await support_db.http("a_user", "GET", f"/support/tickets/{uuid4()}")).status_code == 404


async def test_other_tenants_conversation_is_403(support_db):
    db = support_db
    conversation = await _conversation(db)
    path = f"/support/conversations/{conversation}"
    assert (await db.http("b_user", "GET", path)).status_code == 403
    for action, body in (("messages", {"request_id": new_request_id(), "content": "hi"}),
                         ("feedback", {"request_id": new_request_id(), "solved": True}),
                         ("escalate", {"request_id": new_request_id()})):
        assert (await db.http("b_user", "POST", f"{path}/{action}", json=body)).status_code == 403, action


async def test_colleagues_conversation_is_403(support_db):
    db = support_db
    conversation = await _conversation(db)
    assert (await db.http("a_colleague", "GET", f"/support/conversations/{conversation}")).status_code == 403


async def test_other_tenants_attachment_cannot_be_downloaded_or_attached(support_db):
    db = support_db
    upload = await db.http("a_user", "POST", "/support/attachments", files={"file": ("shot.png", PNG, "image/png")})
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()["attachment_id"]
    assert (await db.http("b_user", "GET", f"/support/attachments/{attachment_id}")).status_code == 403
    response = await db.http("b_user", "POST", "/support/tickets", json={
        "request_id": new_request_id(), "subject": "Borrowed file", "category": "other",
        "description": "Trying to attach another tenant's upload.", "attachment_ids": [attachment_id]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "attachment_not_available"
    # The owner still can.
    ticket = await _ticket(db, attachment_ids=[attachment_id])
    assert ticket["messages"][0]["attachments"][0]["id"] == attachment_id
    redirect = await db.http("a_user", "GET", f"/support/attachments/{attachment_id}")
    assert redirect.status_code == 302


async def test_tenant_id_in_body_is_rejected_and_in_query_is_ignored(support_db):
    db = support_db
    response = await db.http("a_user", "POST", "/support/tickets", json={
        "request_id": new_request_id(), "subject": "Override attempt", "category": "other",
        "description": "Pretending to be tenant B.", "tenant_id": str(db.tenants["b"])})
    assert response.status_code == 422
    await _ticket(db, who="b_user")
    listing = await db.http("a_user", "GET", f"/support/tickets?tenant_id={db.tenants['b']}&scope=mine")
    assert listing.status_code == 200
    assert listing.json()["items"] == []


async def test_foreign_related_campaign_is_403(support_db):
    db = support_db
    foreign_campaign = await db.seed_campaign("b")
    response = await db.http("a_user", "POST", "/support/tickets", json={
        "request_id": new_request_id(), "subject": "Wrong campaign", "category": "calls_campaigns",
        "description": "Linking another tenant's campaign.", "related_campaign_id": str(foreign_campaign)})
    assert response.status_code == 403


async def test_team_list_needs_read_team(support_db):
    db = support_db
    colleague_ticket = await _ticket(db, who="a_colleague")
    assert (await db.http("a_user", "GET", "/support/tickets?scope=team")).status_code == 403
    team = await db.http("a_admin", "GET", "/support/tickets?scope=team")
    assert team.status_code == 200
    assert colleague_ticket["id"] in {item["id"] for item in team.json()["items"]}
    mine = await db.http("a_admin", "GET", "/support/tickets?scope=mine")
    assert colleague_ticket["id"] not in {item["id"] for item in mine.json()["items"]}


async def test_colleagues_ticket_is_readable_only_with_read_team_and_never_writable(support_db):
    db = support_db
    ticket = await _ticket(db, who="a_colleague")
    path = f"/support/tickets/{ticket['id']}"
    assert (await db.http("a_user", "GET", path)).status_code == 403
    admin_view = await db.http("a_admin", "GET", path)
    assert admin_view.status_code == 200
    assert admin_view.json()["allowed_actions"] == []
    reply = await db.http("a_admin", "POST", f"{path}/messages", json={"request_id": new_request_id(), "body": "x"})
    assert reply.status_code == 403


async def test_readonly_role_cannot_use_support(support_db):
    db = support_db
    assert (await db.http("a_readonly", "GET", "/support/tickets")).status_code == 403
    config = await db.http("a_readonly", "GET", "/support/config")
    assert config.status_code == 200
    assert config.json()["permissions"] == {"report": False, "read_team": False}


@pytest.mark.parametrize("who", ["a_user", "a_admin", "b_user", "a_readonly"])
async def test_non_platform_roles_are_denied_the_staff_console(support_db, who):
    db = support_db
    for method, path in (("GET", "/admin/support/tickets"), ("GET", "/admin/support/metrics"),
                         ("GET", "/admin/support/articles")):
        assert (await db.http(who, method, path)).status_code == 403, (who, path)
    ticket = await _ticket(db)
    patch = await db.http(who, "PATCH", f"/admin/support/tickets/{ticket['id']}",
                          json={"request_id": new_request_id(), "version": 1, "priority": "low"})
    assert patch.status_code == 403
