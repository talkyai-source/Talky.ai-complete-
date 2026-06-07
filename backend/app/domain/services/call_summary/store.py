"""Idempotent generate-and-store helper for call summaries.

Reads the transcript from the calls table, calls the summarizer, and
writes summary_json + headline back — all within a tenant-scoped
RLS-correct transaction.

Idempotency: if summary_json is already populated and force=False, the
existing value is returned immediately without re-summarizing.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.core.db_utils import acquire_with_tenant
from app.domain.services.call_summary.summarizer import summarize_transcript

logger = logging.getLogger(__name__)


async def generate_and_store(
    pool,
    tenant_id: str,
    call_id: str,
    *,
    force: bool = False,
) -> Optional[dict]:
    """Generate (if needed) and persist a structured call summary.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    tenant_id:
        UUID string of the owning tenant (used to set RLS context).
    call_id:
        UUID string of the call row to summarize.
    force:
        When True, re-summarize even if summary_json is already set.

    Returns
    -------
    dict | None
        The summary dict if a summary was generated or already existed,
        or None when the call row is missing or has no transcript.
    """
    async with acquire_with_tenant(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT transcript, summary_json FROM calls WHERE id = $1",
            call_id,
        )

    if row is None:
        logger.warning("call_summary store: call %s not found for tenant %s", call_id, tenant_id)
        return None

    # --- Idempotency check ---
    existing = row["summary_json"]
    if existing is not None and not force:
        # asyncpg may return JSONB as a str or as a dict depending on
        # whether a codec is registered.  Handle both shapes.
        if isinstance(existing, str):
            try:
                return json.loads(existing)
            except json.JSONDecodeError:
                logger.warning(
                    "call_summary store: summary_json for call %s is invalid JSON — re-generating",
                    call_id,
                )
        else:
            # Already a dict (asyncpg decoded it)
            return dict(existing)

    # --- Transcript check ---
    transcript_text: str = row["transcript"] or ""
    if not transcript_text.strip():
        logger.debug("call_summary store: call %s has no transcript — skipping", call_id)
        return None

    # --- Generate ---
    summary = await summarize_transcript(transcript_text)

    # --- Persist (tenant-scoped) ---
    async with acquire_with_tenant(pool, tenant_id) as conn:
        await conn.execute(
            """
            UPDATE calls
               SET summary_json = $2::jsonb,
                   summary      = $3,
                   updated_at   = NOW()
             WHERE id = $1
            """,
            call_id,
            json.dumps(summary),
            summary.get("headline", ""),
        )

    return summary
