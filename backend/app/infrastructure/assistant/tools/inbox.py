"""Email READING tools for the assistant agent (Gmail connector).

Read-only companions to the existing `send_email` tool. They resolve the
tenant's active email connector via the shared `resolve_active_connector`
(canonical connector_accounts path) and call the connector's list/get methods.
No confirm card — reading mutates nothing.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)

_MAX_BODY_CHARS = 4000  # keep a single email body well within the LLM context


def _fmt_dt(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return str(value)


async def read_emails(
    tenant_id: str,
    db_client: Client,
    query: Optional[str] = None,
    unread_only: bool = False,
    max_results: int = 10,
) -> Dict[str, Any]:
    """List recent emails (subject/from/snippet) from the connected inbox.

    `query` is a Gmail search string (e.g. "from:jane@acme.com", "subject:demo").
    """
    logger.info("read_emails called tenant=%s query=%r unread=%s", str(tenant_id)[:8], query, unread_only)
    from app.services.connector_resolver import (
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, _cid, _provider = await resolve_active_connector(db_client, tenant_id, "email")
    except ConnectorNotConnectedError as exc:
        return {"success": False, "error": exc.message, "email_required": True}

    try:
        capped = max(1, min(int(max_results or 10), 25))
        messages = await connector.list_emails(max_results=capped, query=query, unread_only=bool(unread_only))
    except Exception as exc:
        logger.error("read_emails failed: %s", exc)
        return {"success": False, "error": "Couldn't read the inbox just now. Try reconnecting email in the Connectors page (left sidebar)."}

    emails = []
    for m in messages:
        snippet = (m.body or "").strip().replace("\r", " ").replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        emails.append({
            "id": m.id,
            "thread_id": m.thread_id,
            "from": m.from_email,
            "to": m.to,
            "subject": m.subject,
            "snippet": snippet,
            "sent_at": _fmt_dt(m.sent_at),
        })
    return {"success": True, "count": len(emails), "emails": emails}


async def read_email(
    tenant_id: str,
    db_client: Client,
    message_id: str,
) -> Dict[str, Any]:
    """Read one email's full content by its message id (from read_emails)."""
    if not (message_id or "").strip():
        return {"success": False, "error": "Need the email's message_id (get it from read_emails first)."}

    from app.services.connector_resolver import (
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, _cid, _provider = await resolve_active_connector(db_client, tenant_id, "email")
    except ConnectorNotConnectedError as exc:
        return {"success": False, "error": exc.message, "email_required": True}

    try:
        m = await connector.get_email(message_id.strip())
    except Exception as exc:
        logger.error("read_email failed: %s", exc)
        return {"success": False, "error": "Couldn't open that email — it may have been deleted, or email needs reconnecting."}

    body = (m.body or "").strip()
    truncated = len(body) > _MAX_BODY_CHARS
    if truncated:
        body = body[:_MAX_BODY_CHARS] + "…"
    return {
        "success": True,
        "email": {
            "id": m.id,
            "thread_id": m.thread_id,
            "from": m.from_email,
            "to": m.to,
            "cc": m.cc,
            "subject": m.subject,
            "body": body,
            "body_truncated": truncated,
            "sent_at": _fmt_dt(m.sent_at),
        },
    }
