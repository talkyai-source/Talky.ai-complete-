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
from uuid import UUID

import httpx

from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from app.services.connector_resolver import ConnectorAuthorizationSnapshot, _authorization_snapshot_for_row
from app.infrastructure.assistant.tools.coercion import coerce_bool
from app.infrastructure.connectors.base import BaseConnector, ConnectorProviderError

logger = logging.getLogger(__name__)

_MAX_BODY_CHARS = 4000  # keep a single email body well within the LLM context
_EMAIL_OPERATION_TIMEOUT_SECONDS = 15.0
_EMAIL_HEALTH_TIMEOUT_SECONDS = 5.0
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
    from app.services.connector_resolver import ReviewedConnectorChanged

    if isinstance(exc, ReviewedConnectorChanged):
        return {
            "success": False,
            "error": (
                "The original email account could not be verified. Check the "
                "intended account in Connectors before reading again."
            ),
            "error_code": "email_account_changed",
        }
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
    """Retry once after a 401, retaining the initial connector and account."""
    from app.services.connector_resolver import (
        ConnectorNotConnectedError,
        ReviewedConnectorChanged,
        connector_identity,
        resolve_active_connector,
        verify_reviewed_connector,
    )

    # Freeze before the first provider await. OAuth reconnect inserts a new
    # authorization row; ordinary token refresh retains it. Gmail may have no
    # external subject ID, so that separate reviewed-effect proof is optional
    # here. Legacy objects without row proof can read, but cannot refresh/retry.
    original_authorization = getattr(connector, "_authorization_snapshot", None)
    initial = {
        "connector_id": connector_id,
        "provider": getattr(connector, "provider_name", None),
        "account_id": getattr(connector, "account_row_id", None),
    }
    retry_identity = (
        {key: value.strip() for key, value in initial.items()}
        if all(isinstance(value, str) and value.strip() for value in initial.values())
        else None
    )
    try:
        reviewed = connector_identity(connector, connector_id, initial["provider"])
    except ReviewedConnectorChanged:
        reviewed = None

    async def invoke(current: BaseConnector) -> _T:
        return await asyncio.wait_for(
            operation(current), timeout=_EMAIL_OPERATION_TIMEOUT_SECONDS
        )

    try:
        return await invoke(connector)
    except ConnectorProviderError as exc:
        if exc.category != "authentication":
            raise
        if retry_identity is None:
            raise ReviewedConnectorChanged(
                "The original email authorization cannot be verified for retry"
            ) from exc
        logger.info(
            "Gmail rejected access token; attempting one forced refresh tenant=%s",
            str(tenant_id)[:8],
        )
        try:
            refreshed, refreshed_id, _provider = await resolve_active_connector(
                db_client,
                tenant_id,
                "email",
                force_refresh=True,
                connector_id=retry_identity["connector_id"],
                provider=retry_identity["provider"],
                account_id=retry_identity["account_id"],
            )
        except ConnectorNotConnectedError as refresh_exc:
            if (
                refresh_exc.connector_id != retry_identity["connector_id"]
                or refresh_exc.reason == "account_unavailable"
            ):
                # This failure cannot establish the original account's health
                # and must not expire another connector in the outer handlers.
                raise ReviewedConnectorChanged(
                    "The original email connection is unavailable"
                ) from refresh_exc
            same_terminal_connector = (
                refresh_exc.connector_id == connector_id
                and refresh_exc.reason == "refresh_unavailable"
            )
            if same_terminal_connector and not refresh_exc.provider_confirmed:
                # No refresh was sent. Only the generation rejected by the
                # first read is evidence; a newer row lacking refresh is not.
                await _mark_email_authorization_expired(
                    db_client, tenant_id, connector_id,
                    authorization=original_authorization,
                )
            # Provider-confirmed refresh failures are handled once by the
            # outer read boundary, using the generation that refresh attempted.
            raise
        current = {
            "connector_id": refreshed_id,
            "provider": _provider,
            "account_id": getattr(refreshed, "account_row_id", None),
        }
        if current != retry_identity:
            raise ReviewedConnectorChanged("The original email authorization changed")
        if reviewed is not None:
            verify_reviewed_connector(refreshed, refreshed_id, _provider, reviewed)
        refreshed_authorization = getattr(refreshed, "_authorization_snapshot", None)
        try:
            return await invoke(refreshed)
        except ConnectorProviderError as retry_exc:
            if retry_exc.category == "authentication":
                await _mark_email_authorization_expired(
                    db_client, tenant_id, refreshed_id, authorization=refreshed_authorization
                )
            raise


async def _mark_email_authorization_expired(
    db_client: Client,
    tenant_id: str,
    connector_id: str,
    *,
    authorization: ConnectorAuthorizationSnapshot | None = None,
) -> bool:
    """Acknowledge expiry only for the still-current rejected authorization.

    False means no transition was acknowledged; an interrupted commit is not
    proof that no write occurred. No broad or synchronous fallback is permitted.
    """
    if (
        not isinstance(authorization, ConnectorAuthorizationSnapshot)
        or authorization.tenant_id != tenant_id
        or authorization.connector_id != connector_id
        or not getattr(db_client, "pool", None)
    ):
        return False
    try:
        tenant_uuid, connector_uuid = UUID(tenant_id), UUID(connector_id)
        account_uuid = UUID(authorization.account_row_id)
        async with asyncio.timeout(_EMAIL_HEALTH_TIMEOUT_SECONDS):
            async with acquire_with_tenant(
                db_client.pool, tenant_id, timeout=_EMAIL_HEALTH_TIMEOUT_SECONDS
            ) as conn:
                parent = await conn.fetchrow(
                    """SELECT id FROM connectors WHERE id=$1 AND tenant_id=$2
                       AND provider=$3 AND status='active' FOR UPDATE""",
                    connector_uuid,
                    tenant_uuid,
                    authorization.provider,
                )
                if parent is None:
                    return False
                # Parent lock blocks reconnect FK inserts. Lock every existing
                # account (including inactive rows), then rank afresh after any
                # waited-on refresh/activation commits; LIMIT locks are unsafe.
                await conn.fetch(
                    """SELECT id FROM connector_accounts WHERE connector_id=$1
                       AND tenant_id=$2 ORDER BY id FOR UPDATE""",
                    connector_uuid,
                    tenant_uuid,
                )
                active = await conn.fetch(
                    """SELECT id, access_token_encrypted, refresh_token_encrypted,
                              external_account_id, token_expires_at, last_refreshed_at
                       FROM connector_accounts WHERE connector_id=$1 AND tenant_id=$2
                       AND status='active' ORDER BY last_refreshed_at DESC LIMIT 2""",
                    connector_uuid,
                    tenant_uuid,
                )
                if not active or (
                    len(active) > 1
                    and active[0]["last_refreshed_at"] == active[1]["last_refreshed_at"]
                ):
                    return False
                current = _authorization_snapshot_for_row(
                    tenant_id, connector_id, authorization.provider, active[0]
                )
                if current != authorization:
                    return False
                account_ack = await conn.fetchval(
                    """UPDATE connector_accounts SET status='expired' WHERE id=$1
                       AND connector_id=$2 AND tenant_id=$3 AND status='active' RETURNING id""",
                    account_uuid,
                    connector_uuid,
                    tenant_uuid,
                )
                parent_ack = await conn.fetchval(
                    """UPDATE connectors SET status='expired' WHERE id=$1
                       AND tenant_id=$2 AND provider=$3 AND status='active' RETURNING id""",
                    connector_uuid,
                    tenant_uuid,
                    authorization.provider,
                )
                if account_ack is None or parent_ack is None:
                    raise RuntimeError("Authorization expiry was not acknowledged")
        return True
    except Exception as exc:
        logger.warning(
            "Could not acknowledge Gmail authorization expiry connector=%s type=%s",
            connector_id,
            type(exc).__name__,
        )
        return False


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
            await _mark_email_authorization_expired(
                db_client, tenant_id, exc.connector_id, authorization=exc.authorization_snapshot
            )
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
                # coerce, don't bool(): bool("false") is True
                unread_only=coerce_bool(unread_only),
            ),
        )
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message, "error_code": "email_lookup_error"}
    except ConnectorNotConnectedError as exc:
        if exc.connector_id and exc.provider_confirmed:
            await _mark_email_authorization_expired(
                db_client, tenant_id, exc.connector_id, authorization=exc.authorization_snapshot
            )
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
            await _mark_email_authorization_expired(
                db_client, tenant_id, exc.connector_id, authorization=exc.authorization_snapshot
            )
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
            await _mark_email_authorization_expired(
                db_client, tenant_id, exc.connector_id, authorization=exc.authorization_snapshot
            )
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
