"""Calendar READ tool for the assistant agent (Google Calendar connector).

Read-only companion to the meeting-create/reminder tools: answers "what's on
my calendar?" via the tenant's active calendar connector (shared
``resolve_active_connector``). Timed events only — the connector skips
all-day events by design.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

from app.core.postgres_adapter import Client
from app.infrastructure.connectors.base import ConnectorProviderError

logger = logging.getLogger(__name__)


def _fmt_dt(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return str(value)


def _calendar_provider_error(exc: Exception) -> Dict[str, Any]:
    """Honest user-facing calendar errors — a Google rejection is NOT 'not connected'."""
    if isinstance(exc, ConnectorProviderError):
        logger.error(
            "Calendar provider failure operation=%s category=%s status=%s",
            exc.operation,
            exc.category,
            exc.status_code,
        )
        if exc.category == "authentication":
            return {
                "success": False,
                "error": "Google Calendar rejected the saved authorization. Please reconnect the calendar from the Connectors page (left sidebar).",
                "calendar_required": True,
                "error_code": "calendar_authentication_failed",
            }
        if exc.category == "permission":
            return {
                "success": False,
                "error": "Google Calendar denied access. Make sure the Google Calendar API is enabled in the Google Cloud project and calendar permission was granted.",
                "error_code": "calendar_permission_denied",
            }
        if exc.category == "rate_limit":
            return {
                "success": False,
                "error": "Google Calendar is rate-limiting requests right now. Please wait a moment and try again.",
                "error_code": "calendar_rate_limited",
            }
        if exc.category in {"temporary", "configuration"}:
            message = (
                "Google Calendar is temporarily unavailable. Please try again in a moment."
                if exc.category == "temporary"
                else "Calendar access is not configured correctly. An administrator needs to check the Google integration."
            )
            return {"success": False, "error": message, "error_code": f"calendar_{exc.category}_error"}

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return {
            "success": False,
            "error": "Google Calendar took too long to respond. Please try again.",
            "error_code": "calendar_timeout",
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "success": False,
            "error": "Google Calendar could not be reached. Please try again in a moment.",
            "error_code": "calendar_network_error",
        }
    return {
        "success": False,
        "error": "Couldn't read the calendar just now. Please try again in a moment.",
        "error_code": "calendar_provider_error",
    }


async def read_calendar_events(
    tenant_id: str,
    db_client: Client,
    days_ahead: int = 7,
    max_results: int = 10,
) -> Dict[str, Any]:
    """List upcoming events from the connected calendar (now → +days_ahead)."""
    logger.info(
        "read_calendar_events called tenant=%s days_ahead=%r", str(tenant_id)[:8], days_ahead
    )
    from app.services.connector_resolver import (
        ConnectorLookupError,
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, _cid, _provider = await resolve_active_connector(
            db_client, tenant_id, "calendar"
        )
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message, "error_code": "calendar_lookup_error"}
    except ConnectorNotConnectedError as exc:
        return {
            "success": False,
            "error": exc.message,
            "calendar_required": True,
            "error_code": "calendar_not_connected",
        }
    except (ConnectorProviderError, httpx.RequestError, asyncio.TimeoutError, TimeoutError) as exc:
        return _calendar_provider_error(exc)

    try:
        days = max(1, min(int(days_ahead or 7), 31))
    except (TypeError, ValueError):
        days = 7
    try:
        capped = max(1, min(int(max_results or 10), 25))
    except (TypeError, ValueError):
        capped = 10

    # The connector serialises with a trailing "Z", so it expects naive UTC.
    start = datetime.utcnow()
    end = start + timedelta(days=days)

    try:
        events = await asyncio.wait_for(
            connector.list_events(start, end, max_results=capped), timeout=15.0
        )
    except Exception as exc:
        logger.error("read_calendar_events failed type=%s", type(exc).__name__)
        return _calendar_provider_error(exc)

    out = []
    for ev in events:
        out.append(
            {
                "id": ev.id,
                "title": ev.title,
                "start_time": _fmt_dt(ev.start_time),
                "end_time": _fmt_dt(ev.end_time),
                "timezone": ev.timezone,
                "location": ev.location,
                "attendees": ev.attendees,
            }
        )
    return {
        "success": True,
        "count": len(out),
        "window_days": days,
        "events": out,
        "note": "Timed events only — all-day events are not included.",
    }
