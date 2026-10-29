"""Rows -> API contract shapes.

Customer shapes never include internal notes, staff identities, diagnostics,
SLA data or other tenants' data. Staff shapes add those on top.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from app.domain.services.support import lifecycle
from app.domain.services.support.priority import sla_breached
from app.domain.services.support.vocabulary import ticket_display_number

SUPPORT_TEAM_NAME = "Talk-Lee Support"
SYSTEM_NAME = "Talk-Lee"


def attachments_by_message(attachments: Iterable[Mapping[str, Any]]) -> dict[Any, list[dict]]:
    grouped: dict[Any, list[dict]] = defaultdict(list)
    for item in attachments:
        grouped[item["message_id"]].append({
            "id": item["id"], "filename": item["filename"],
            "size_bytes": item["size_bytes"], "content_type": item["content_type"],
        })
    return grouped


def customer_message(row: Mapping[str, Any], attachments: Sequence[dict]) -> dict:
    if row["author_type"] == "staff":
        name = SUPPORT_TEAM_NAME
    elif row["author_type"] == "system":
        name = SYSTEM_NAME
    else:
        name = row.get("author_name") or "Customer"
    return {"id": row["id"], "author_type": row["author_type"], "author_name": name, "body": row["body"],
            "attachments": list(attachments), "created_at": row["created_at"]}


def staff_message(row: Mapping[str, Any], attachments: Sequence[dict]) -> dict:
    message = customer_message(row, attachments)
    if row["author_type"] == "staff":
        message["author_name"] = row.get("author_name") or SUPPORT_TEAM_NAME
    message["is_internal"] = bool(row["is_internal"])
    return message


def _related(ticket: Mapping[str, Any]) -> dict:
    related: dict[str, Any] = {}
    if ticket.get("related_campaign_id"):
        related["campaign_id"] = ticket["related_campaign_id"]
    if ticket.get("related_call_id"):
        related["call_id"] = ticket["related_call_id"]
    return related


def _csat(ticket: Mapping[str, Any]) -> dict | None:
    if ticket.get("csat_score") is None:
        return None
    return {"score": ticket["csat_score"], "comment": ticket.get("csat_comment")}


def customer_ticket(ticket: Mapping[str, Any], messages: Sequence[Mapping[str, Any]],
                    attachments: Sequence[Mapping[str, Any]], *, viewer_id: str, now: datetime) -> dict:
    grouped = attachments_by_message(attachments)
    is_requester = str(ticket.get("requester_user_id")) == str(viewer_id)
    return {
        "id": ticket["id"],
        "number": ticket_display_number(ticket["number"]),
        "subject": ticket["subject"],
        "category": ticket["category"],
        "priority": ticket["priority"],
        "status": ticket["status"],
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "requester": {"id": ticket.get("requester_user_id"), "name": ticket.get("requester_name")},
        "related": _related(ticket),
        "messages": [customer_message(m, grouped.get(m["id"], [])) for m in messages if not m["is_internal"]],
        "csat": _csat(ticket),
        "allowed_actions": lifecycle.allowed_actions(ticket, is_requester=is_requester, now=now),
    }


def staff_ticket(ticket: Mapping[str, Any], messages: Sequence[Mapping[str, Any]],
                 attachments: Sequence[Mapping[str, Any]], *, now: datetime,
                 tenant: Mapping[str, Any] | None = None, assignee: Mapping[str, Any] | None = None,
                 conversation: Sequence[dict] = (), events: Sequence[Mapping[str, Any]] = ()) -> dict:
    grouped = attachments_by_message(attachments)
    return {
        "id": ticket["id"],
        "number": ticket_display_number(ticket["number"]),
        "subject": ticket["subject"],
        "category": ticket["category"],
        "priority": ticket["priority"],
        "status": ticket["status"],
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "requester": {"id": ticket.get("requester_user_id"), "name": ticket.get("requester_name"),
                      "email": ticket.get("requester_email")},
        "related": _related(ticket),
        "messages": [staff_message(m, grouped.get(m["id"], [])) for m in messages],
        "csat": _csat(ticket),
        "allowed_actions": [],
        "version": ticket["version"],
        "tenant": dict(tenant) if tenant else None,
        "assignee": dict(assignee) if assignee else None,
        "sla": {
            "first_response_due_at": ticket["first_response_due_at"],
            "resolution_due_at": ticket["resolution_due_at"],
            "first_responded_at": ticket.get("first_responded_at"),
            "breached": sla_breached(ticket, now),
        },
        "conversation": list(conversation),
        "diagnostics": ticket.get("diagnostics") or {},
        "events": [{"id": e["id"], "type": e["type"], "from_value": e["from_value"], "to_value": e["to_value"],
                    "actor_id": e["actor_user_id"], "actor_name": e.get("actor_name"), "created_at": e["created_at"]}
                   for e in events],
    }


def list_item(row: Mapping[str, Any]) -> dict:
    return {"id": row["id"], "number": ticket_display_number(row["number"]), "subject": row["subject"],
            "status": row["status"], "priority": row["priority"], "category": row["category"],
            "updated_at": row["updated_at"], "unread": bool(row.get("unread"))}


def bot_message(row: Mapping[str, Any]) -> dict:
    sources = row.get("sources") or []
    return {"id": row["id"], "role": row["role"], "content": row["content"],
            "sources": [{"slug": s["slug"], "title": s["title"], "url": s["url"]} for s in sources],
            "created_at": row["created_at"]}
