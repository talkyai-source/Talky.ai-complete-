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
import os
from typing import List, Optional

from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)

# Minimum pg_trgm *word* similarity for the fuzzy fallback. We use
# word_similarity() (not similarity()) because the query is short and the node
# text is long: similarity() divides by the union of ALL the document's
# trigrams → always ~0, so it never fires. word_similarity() instead scores the
# query against the best-matching contiguous extent inside the text, which is
# exactly what catches an STT mishear ("warrantee" → the word "warranty").
# 0.35 comfortably catches single-word mishears without flooding on noise.
_WORD_SIM_FLOOR = float(os.getenv("KNOWLEDGE_WORD_SIM_FLOOR", "0.35"))


async def retrieve_knowledge(
    pool,
    tenant_id: str,
    campaign_id: str,
    query: str,
    k: int = 2,
) -> List[dict]:
    """Top-k knowledge nodes for `query` (a user transcript). Hybrid FTS + trgm.

    Tiered so precision wins but recall never silently drops a relevant node:
      tier 2  exact AND match  — every query lexeme present (websearch tsquery)
      tier 1  any-term match   — at least one lexeme present (AND tsquery OR'd)
      tier 0  fuzzy match only — word_similarity over the trigram column,
                                 for STT mishears the analyzer can't lexicalise
    Within a tier we sort by match strength, then owner priority, then hit_count.
    The OR fallback is what rescues conversational queries like "what areas do
    you cover" where the doc has "areas" but not "cover".
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        async with acquire_with_tenant(pool, tenant_id) as conn:
            rows = await conn.fetch(
                """
                WITH tq AS (
                    SELECT websearch_to_tsquery('english', $2) AS q_and,
                           NULLIF(replace(
                               websearch_to_tsquery('english', $2)::text,
                               ' & ', ' | '), '')::tsquery AS q_or
                )
                SELECT n.id, n.heading, n.summary, n.voice_answer, n.content,
                       ts_rank(n.search_tsv, tq.q_and) AS fts,
                       word_similarity($2, n.search_text) AS sim
                FROM campaign_knowledge_nodes n, tq
                WHERE n.campaign_id = $1
                  AND n.enabled
                  AND (
                       n.search_tsv @@ tq.q_and
                    OR (tq.q_or IS NOT NULL AND n.search_tsv @@ tq.q_or)
                    OR word_similarity($2, n.search_text) >= $4
                  )
                ORDER BY
                    CASE WHEN n.search_tsv @@ tq.q_and THEN 2
                         WHEN tq.q_or IS NOT NULL AND n.search_tsv @@ tq.q_or THEN 1
                         ELSE 0 END DESC,
                    GREATEST(
                        ts_rank(n.search_tsv, COALESCE(tq.q_or, tq.q_and)),
                        word_similarity($2, n.search_text)
                    ) DESC,
                    n.priority DESC,
                    n.hit_count DESC
                LIMIT $3
                """,
                campaign_id, q, k, _WORD_SIM_FLOOR,
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
