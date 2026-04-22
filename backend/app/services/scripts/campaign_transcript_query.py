"""Campaign-scoped 'calls + transcripts' query used by the Script Card UI.

One DB round-trip for the page (plus one count for the total). Filters out
partial STT turns at formatting time so the UI never has to understand
Deepgram's eager/update/end_of_turn taxonomy.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List
from uuid import UUID

from app.services.scripts.transcript_formatting import format_transcript_turns

logger = logging.getLogger(__name__)


def _coerce_turns(raw: Any) -> List[Dict[str, Any]]:
    """transcript_json comes back from asyncpg as str or list depending on the
    driver config. Normalise to list[dict] before passing to the formatter."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(raw, list):
        return raw
    return []


async def fetch_campaign_transcripts(
    *,
    pool,
    tenant_id,
    campaign_id: str,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Return one page of calls for a campaign plus each call's transcript turns.

    Args:
        pool: asyncpg pool (container.db_pool).
        tenant_id: UUID of the tenant.
        campaign_id: UUID of the campaign (string form).
        page: 1-indexed page number (caller must clamp; default 1).
        page_size: items per page (caller must clamp; default 20).

    Returns:
        {"items": [...], "page": int, "page_size": int, "total": int}

    Raises:
        ValueError: when campaign_id or tenant_id is not a valid UUID.
    """
    offset = max(0, (max(page, 1) - 1) * max(page_size, 1))
    tenant_uuid = tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id))
    try:
        campaign_uuid = UUID(str(campaign_id))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid campaign_id: {campaign_id!r}") from exc

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id,
                   c.phone_number,
                   c.created_at,
                   c.duration_seconds,
                   c.outcome,
                   c.transcript_json
            FROM calls c
            WHERE c.tenant_id = $1
              AND c.campaign_id = $2
            ORDER BY c.created_at DESC
            LIMIT $3 OFFSET $4
            """,
            tenant_uuid, campaign_uuid, page_size, offset,
        )
        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM calls c
            WHERE c.tenant_id = $1 AND c.campaign_id = $2
            """,
            tenant_uuid, campaign_uuid,
        )

    items: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row["created_at"]
        raw_turns = _coerce_turns(row["transcript_json"])
        items.append({
            "call_id": str(row["id"]),
            "to_number": row["phone_number"] or "",
            "started_at": (
                created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at) if created_at is not None else ""
            ),
            "duration_seconds": row["duration_seconds"],
            "outcome": row["outcome"],
            "turns": format_transcript_turns(raw_turns),
        })

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
    }
