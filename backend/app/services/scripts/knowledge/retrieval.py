"""Vectorless retrieval over the campaign knowledge tree (P1/P2).

Two read paths, both tenant-scoped (RLS via acquire_with_tenant):

  retrieve_knowledge()  per-turn hybrid lookup — Postgres FTS *and* pg_trgm
                        fuzzy in one indexed query. The fuzzy half is what makes
                        it robust to STT mishears ("commitment" vs "committing").
  compact_tree()        renders the tree as an indented outline for `inline`
                        (full) or `map_retrieve` (skeleton: heading + summary).

Both are written to never raise into the voice turn — on any error they return
empty/"" so a knowledge hiccup can't break a call.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)

_TRGM_FLOOR = 0.18   # min trigram similarity to consider a fuzzy match


async def retrieve_knowledge(
    pool,
    tenant_id: str,
    campaign_id: str,
    query: str,
    k: int = 2,
) -> List[dict]:
    """Top-k knowledge nodes for `query` (a user transcript). Hybrid FTS+trgm."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        async with acquire_with_tenant(pool, tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT id, heading, summary, voice_answer, content,
                       ts_rank(search_tsv, websearch_to_tsquery('english', $2)) AS fts,
                       similarity(search_text, $2) AS sim
                FROM campaign_knowledge_nodes
                WHERE campaign_id = $1
                  AND enabled
                  AND (search_tsv @@ websearch_to_tsquery('english', $2)
                       OR similarity(search_text, $2) > $4)
                ORDER BY (0.7 * ts_rank(search_tsv, websearch_to_tsquery('english', $2))
                          + 0.3 * similarity(search_text, $2)
                          + 0.01 * priority) DESC
                LIMIT $3
                """,
                campaign_id, q, k, _TRGM_FLOOR,
            )
            if rows:
                # analytics: best-effort hit_count bump, same txn (cheap)
                await conn.execute(
                    "UPDATE campaign_knowledge_nodes SET hit_count = hit_count + 1 "
                    "WHERE id = ANY($1::uuid[])",
                    [r["id"] for r in rows],
                )
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("retrieve_knowledge failed campaign=%s: %s", str(campaign_id)[:12], exc)
        return []


async def compact_tree(
    pool,
    tenant_id: str,
    campaign_id: str,
    *,
    skeleton_only: bool = False,
    max_chars: int = 12000,
) -> str:
    """Render enabled nodes as an indented outline for inline injection.

    skeleton_only=True (map_retrieve): heading + summary only (the "table of
    contents"). False (inline): heading + summary/voice_answer + content.
    """
    try:
        async with acquire_with_tenant(pool, tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT depth, heading, summary, voice_answer, content
                FROM campaign_knowledge_nodes
                WHERE campaign_id = $1 AND enabled
                ORDER BY string_to_array(path, '.')::int[]
                """,
                campaign_id,
            )
    except Exception as exc:
        logger.warning("compact_tree failed campaign=%s: %s", str(campaign_id)[:12], exc)
        return ""

    lines: List[str] = []
    for r in rows:
        indent = "  " * max(0, int(r["depth"]))
        head = r["heading"]
        if skeleton_only:
            tail = f" — {r['summary']}" if r["summary"] else ""
            lines.append(f"{indent}- {head}{tail}")
        else:
            body = (r["voice_answer"] or r["summary"] or r["content"] or "").strip().replace("\n", " ")
            lines.append(f"{indent}- {head}: {body}" if body else f"{indent}- {head}")
    out = "\n".join(lines)
    return out[:max_chars]
