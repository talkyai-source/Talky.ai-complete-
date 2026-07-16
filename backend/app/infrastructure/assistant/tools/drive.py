"""Google Drive READ tools for the assistant agent.

List/search files and read a text file's contents, via the tenant's active
drive connector (shared `resolve_active_connector`). Read-only — no writes, no
confirm card. Binary/large files are described but not dumped into the context.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.core.postgres_adapter import Client
from app.infrastructure.connectors.base import ConnectorProviderError

logger = logging.getLogger(__name__)


def _drive_provider_error(exc: Exception, *, opening_file: bool = False) -> Dict[str, Any]:
    """Honest user-facing Drive errors — a Google rejection is NOT 'not connected'."""
    if isinstance(exc, ConnectorProviderError):
        logger.error(
            "Drive provider failure operation=%s category=%s status=%s",
            exc.operation,
            exc.category,
            exc.status_code,
        )
        if exc.category == "authentication":
            return {
                "success": False,
                "error": "Google Drive rejected the saved authorization. Please reconnect Drive from the Connectors page (left sidebar).",
                "drive_required": True,
                "error_code": "drive_authentication_failed",
            }
        if exc.category == "permission":
            return {
                "success": False,
                "error": "Google Drive denied access. Make sure the Google Drive API is enabled in the Google Cloud project and Drive permission was granted.",
                "error_code": "drive_permission_denied",
            }
        if exc.category == "rate_limit":
            return {
                "success": False,
                "error": "Google Drive is rate-limiting requests right now. Please wait a moment and try again.",
                "error_code": "drive_rate_limited",
            }
        if exc.category == "not_found" and opening_file:
            return {
                "success": False,
                "error": "That file is no longer available; it may have been moved or deleted.",
                "error_code": "drive_not_found",
            }
        if exc.category in {"temporary", "configuration"}:
            message = (
                "Google Drive is temporarily unavailable. Please try again in a moment."
                if exc.category == "temporary"
                else "Drive access is not configured correctly. An administrator needs to check the Google integration."
            )
            return {"success": False, "error": message, "error_code": f"drive_{exc.category}_error"}

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return {
            "success": False,
            "error": "Google Drive took too long to respond. Please try again.",
            "error_code": "drive_timeout",
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "success": False,
            "error": "Google Drive could not be reached. Please try again in a moment.",
            "error_code": "drive_network_error",
        }
    return {
        "success": False,
        "error": "Couldn't read Drive just now. Please try again in a moment.",
        "error_code": "drive_provider_error",
    }

_MAX_TEXT_CHARS = 6000        # cap a file's returned text for the LLM context
_MAX_DOWNLOAD_BYTES = 512 * 1024  # never pull more than 512 KiB into memory

# MIME types we can meaningfully return as text.
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json",
    "application/xml",
    "application/csv",
    "application/rtf",
}

# Google-native files reject the plain media download (403 fileNotDownloadable)
# and must go through the EXPORT endpoint with a concrete target format.
_GOOGLE_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def _fmt_dt(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return str(value)


def _is_texty(mime: Optional[str]) -> bool:
    if not mime:
        return False
    if any(mime.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    return mime in _TEXT_MIME_EXACT or mime in _GOOGLE_EXPORT_MIME


async def drive_list_files(
    tenant_id: str,
    db_client: Client,
    query: Optional[str] = None,
    max_results: int = 20,
) -> Dict[str, Any]:
    """List/search files in the connected Google Drive.

    `query` is a plain search term matched against file names (the connector
    maps it to a Drive `name contains` query).
    """
    logger.info("drive_list_files called tenant=%s query=%r", str(tenant_id)[:8], query)
    from app.services.connector_resolver import (
        ConnectorLookupError,
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, _cid, _provider = await resolve_active_connector(db_client, tenant_id, "drive")
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message}
    except ConnectorNotConnectedError as exc:
        return {"success": False, "error": exc.message, "drive_required": True}

    try:
        capped = max(1, min(int(max_results or 20), 50))
        files = await connector.list_files(query=query, max_results=capped)
    except Exception as exc:
        logger.error("drive_list_files failed type=%s", type(exc).__name__)
        return _drive_provider_error(exc)

    out = []
    for f in files:
        out.append({
            "id": f.id,
            "name": f.name,
            "mime_type": f.mime_type,
            "is_folder": f.is_folder,
            "size": f.size,
            "web_link": f.web_link,
            "modified_at": _fmt_dt(f.modified_at),
            "readable_as_text": _is_texty(f.mime_type),
        })
    return {"success": True, "count": len(out), "files": out}


async def drive_read_file(
    tenant_id: str,
    db_client: Client,
    file_id: str,
) -> Dict[str, Any]:
    """Read a text-like file's contents by id (from drive_list_files).

    Non-text or oversized files return metadata + a link instead of content.
    """
    if not (file_id or "").strip():
        return {"success": False, "error": "Need the file_id (get it from drive_list_files first)."}

    from app.services.connector_resolver import (
        ConnectorLookupError,
        ConnectorNotConnectedError,
        resolve_active_connector,
    )

    try:
        connector, _cid, _provider = await resolve_active_connector(db_client, tenant_id, "drive")
    except ConnectorLookupError as exc:
        return {"success": False, "error": exc.message}
    except ConnectorNotConnectedError as exc:
        return {"success": False, "error": exc.message, "drive_required": True}

    try:
        meta = await connector.get_file(file_id.strip())
    except Exception as exc:
        logger.error("drive_read_file metadata failed type=%s", type(exc).__name__)
        return _drive_provider_error(exc, opening_file=True)

    if meta.is_folder:
        return {"success": False, "error": f"'{meta.name}' is a folder, not a file. Use drive_list_files to see what's inside."}

    base = {
        "id": meta.id,
        "name": meta.name,
        "mime_type": meta.mime_type,
        "size": meta.size,
        "web_link": meta.web_link,
        "modified_at": _fmt_dt(meta.modified_at),
    }

    if not _is_texty(meta.mime_type):
        return {
            "success": True,
            "file": base,
            "content": None,
            "note": f"'{meta.name}' isn't a text file — I can't read its contents, but here's the link.",
        }
    if meta.size and int(meta.size) > _MAX_DOWNLOAD_BYTES:
        return {
            "success": True,
            "file": base,
            "content": None,
            "note": f"'{meta.name}' is too large to read inline ({meta.size} bytes). Open it via the link.",
        }

    try:
        export_mime = _GOOGLE_EXPORT_MIME.get(meta.mime_type or "")
        if export_mime:
            # Google-native file: must use the export endpoint (plain download
            # returns 403 fileNotDownloadable). Providers without export support
            # gracefully return the link instead.
            export = getattr(connector, "export_file", None)
            if not callable(export):
                return {
                    "success": True,
                    "file": base,
                    "content": None,
                    "note": f"'{meta.name}' is a Google-native document this connector can't export — open it via the link.",
                }
            raw = await export(file_id.strip(), export_mime)
        else:
            raw = await connector.download_file(file_id.strip())
    except Exception as exc:
        logger.error("drive_read_file download failed type=%s", type(exc).__name__)
        return _drive_provider_error(exc, opening_file=True)

    text = ""
    try:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception:
        text = ""
    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS] + "…"

    return {"success": True, "file": base, "content": text, "content_truncated": truncated}
