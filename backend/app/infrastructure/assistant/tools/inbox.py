"""Email READING tools for the assistant agent (Gmail connector).

Read-only companions to the existing `send_email` tool. They resolve the
tenant's active email connector via the shared `resolve_active_connector`
(canonical connector_accounts path) and call the connector's list/get methods.
Reading message data is side-effect free; after a confirmed unrecoverable 401,
credential status is downgraded so the dashboard no longer claims it is healthy.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

import httpx

from app.core.postgres_adapter import Client
from app.infrastructure.connectors.base import BaseConnector, ConnectorProviderError

logger = logging.getLogger(__name__)

_MAX_BODY_CHARS = 4000  # keep a single email body well within the LLM context
_EMAIL_OPERATION_TIMEOUT_SECONDS = 15.0
_T = TypeVar("_T")


def _fmt_dt(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return str(value)


def _email_provider_error(exc: Exception, *, opening_message: bool = False) -> Dict[str, Any]:
    """Return an honest user-facing error without leaking provider details."""
    if isinstance(exc, ConnectorProviderError):
        logger.error(
            "Gmail provider failure operation=%s category=%s status=%s",
            exc.operation,
            exc.category,
            exc.status_code,
        )
        if exc.category == "authentication":
            return {
                "success": False,
                "error": "Gmail rejected the saved authorization. Please reconnect email from the Connectors page (left sidebar).",
                "email_required": True,
                "error_code": "email_authentication_failed",
            }
        if exc.category == "permission":
            return {
                "success": False,
                "error": "Gmail denied inbox access. Make sure the Gmail API is enabled and inbox-read permission was granted.",
                "error_code": "email_permission_denied",
            }
        if exc.category == "rate_limit":
            return {
                "success": False,
                "error": "Gmail is rate-limiting inbox requests right now. Please wait a moment and try again.",
                "error_code": "email_rate_limited",
            }
        if exc.category == "not_found" and opening_message:
            return {
                "success": False,
                "error": "That email is no longer available; it may have been moved or deleted.",
                "error_code": "email_not_found",
            }
        if exc.category in {"temporary", "configuration"}:
            message = (
                "Gmail is temporarily unavailable. Please try again in a moment."
                if exc.category == "temporary"
                else "Gmail access is not configured correctly. An administrator needs to check the Google integration."
            )
            return {"success": False, "error": message, "error_code": f"email_{exc.category}_error"}

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return {
            "success": False,
            "error": "Gmail took too long to respond. Please try again.",
            "error_code": "email_timeout",
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "success": False,
            "error": "Gmail could not be reached. Please try again in a moment.",
            "error_code": "email_network_error",
        }
    return {
        "success": False,
        "error": "Couldn't read the inbox just now. Please try again in a moment.",
        "error_code": "email_provider_error",
    }


async def _call_with_one_auth_refresh(
    connector: BaseConnector,
    *,
    connector_id: str,
    tenant_id: str,
    db_client: Client,
    operation: Callable[[BaseConnector], Awaitable[_T]],
) -> _T:
    """Retry exactly once with a freshly-issued access token after a 401."""

    async def invoke(current: BaseConnector) -> _T:
        return await asyncio.wait_for(
            operation(current), timeout=_EMAIL_OPERATION_TIMEOUT_SECONDS
        )

    try:
        return await invoke(connector)
    except ConnectorProviderError as exc:
        if exc.category != "authentication":
            raise
        logger.info(
            "Gmail rejected access token; attempting one forced refresh tenant=%s",
            str(tenant_id)[:8],
        )
        from app.services.connector_resolver import (
            ConnectorNotConnectedError,
            resolve_active_connector,
        )

        try:
            refreshed, refreshed_id, _provider = await resolve_active_connector(
                db_client,
                tenant_id,
                "email",
                force_refresh=True,
            )
        except ConnectorNotConnectedError as refresh_exc:
            same_terminal_connector = (
                refresh_exc.connector_id == connector_id
                and refresh_exc.reason == "refresh_unavailable"
            )
            if refresh_exc.provider_confirmed or same_terminal_connector:
                _mark_email_authorization_expired(
                    db_client, tenant_id, refresh_exc.connector_id or connector_id
                )
            raise
        try:
            return await invoke(refreshed)
        except ConnectorProviderError as retry_exc:
            if retry_exc.category == "authentication":
                _mark_email_authorization_expired(db_client, tenant_id, refreshed_id)
            raise


def _mark_email_authorization_expired(db_client: Client, tenant_id: str, connector_id: str) -> None:
    """Make the dashboard honest after a confirmed, unrecoverable auth failure."""
    try:
        account_response = (
            db_client.table("connector_accounts")
            .update({"status": "expired"})
            .eq("connector_id", connector_id)
            .eq("tenant_id", tenant_id)
            .eq("status", "active")
            .execute()
        )
        connector_response = (
            db_client.table("connectors")
            .update({"status": "expired"})
            .eq("id", connector_id)
            .eq("tenant_id", tenant_id)
            .eq("status", "active")
            .execute()
        )
        if getattr(account_response, "error", None) or getattr(connector_response, "error", None):
            logger.error("Could not persist expired Gmail status connector=%s", connector_id)
    except Exception as exc:
        logger.error("Could not persist expired Gmail status connector=%s: %s", connector_id, exc)


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
        ConnectorLookupError,
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, connector_id, _provider = await resolve_active_connector(db_client, tenant_id, "email")
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message, "error_code": "email_lookup_error"}
    except ConnectorNotConnectedError as exc:
        if exc.connector_id and exc.provider_confirmed:
            _mark_email_authorization_expired(db_client, tenant_id, exc.connector_id)
        return {
            "success": False,
            "error": exc.message,
            "email_required": True,
            "error_code": "email_not_connected",
        }
    except (ConnectorProviderError, httpx.RequestError, asyncio.TimeoutError, TimeoutError) as exc:
        return _email_provider_error(exc)

    try:
        capped = max(1, min(int(max_results or 10), 25))
        messages = await _call_with_one_auth_refresh(
            connector,
            connector_id=connector_id,
            tenant_id=tenant_id,
            db_client=db_client,
            operation=lambda current: current.list_emails(
                max_results=capped,
                query=query,
                unread_only=bool(unread_only),
            ),
        )
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message, "error_code": "email_lookup_error"}
    except ConnectorNotConnectedError as exc:
        if exc.connector_id and exc.provider_confirmed:
            _mark_email_authorization_expired(db_client, tenant_id, exc.connector_id)
        return {
            "success": False,
            "error": exc.message,
            "email_required": True,
            "error_code": "email_not_connected",
        }
    except (ConnectorProviderError, httpx.RequestError, asyncio.TimeoutError, TimeoutError) as exc:
        return _email_provider_error(exc)
    except Exception as exc:
        logger.error("read_emails failed type=%s", type(exc).__name__)
        return _email_provider_error(exc)

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
        ConnectorLookupError,
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, connector_id, _provider = await resolve_active_connector(db_client, tenant_id, "email")
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message, "error_code": "email_lookup_error"}
    except ConnectorNotConnectedError as exc:
        if exc.connector_id and exc.provider_confirmed:
            _mark_email_authorization_expired(db_client, tenant_id, exc.connector_id)
        return {
            "success": False,
            "error": exc.message,
            "email_required": True,
            "error_code": "email_not_connected",
        }
    except (ConnectorProviderError, httpx.RequestError, asyncio.TimeoutError, TimeoutError) as exc:
        return _email_provider_error(exc, opening_message=True)

    try:
        m = await _call_with_one_auth_refresh(
            connector,
            connector_id=connector_id,
            tenant_id=tenant_id,
            db_client=db_client,
            operation=lambda current: current.get_email(message_id.strip()),
        )
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message, "error_code": "email_lookup_error"}
    except ConnectorNotConnectedError as exc:
        if exc.connector_id and exc.provider_confirmed:
            _mark_email_authorization_expired(db_client, tenant_id, exc.connector_id)
        return {
            "success": False,
            "error": exc.message,
            "email_required": True,
            "error_code": "email_not_connected",
        }
    except (ConnectorProviderError, httpx.RequestError, asyncio.TimeoutError, TimeoutError) as exc:
        return _email_provider_error(exc, opening_message=True)
    except Exception as exc:
        logger.error("read_email failed type=%s", type(exc).__name__)
        return _email_provider_error(exc, opening_message=True)

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
