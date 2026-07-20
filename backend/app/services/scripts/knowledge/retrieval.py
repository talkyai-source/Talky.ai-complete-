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


def _truncate_on_boundary(text: str, max_chars: int) -> str:
    """Trim ``text`` to ``<= max_chars`` WITHOUT cutting a fact mid-line.

    Char-slicing a rendered KB block halves a number/email and lops the tail
    off a sentence. Instead we cut at the last SAFE boundary at or under the
    limit — preferring a line break, then a sentence terminator, then a word
    gap — so whatever survives is a whole, readable fact. The ``max_chars//2``
    floors stop us returning a useless sliver when the only boundary sits right
    at the start.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    half = max_chars // 2
    nl = window.rfind("\n")
    if nl >= half:
        return window[:nl].rstrip()
    best = -1
    for sep in (". ", "! ", "? ", "; ", ": "):
        best = max(best, window.rfind(sep))
    if best >= half:
        return window[: best + 1].rstrip()
    sp = window.rfind(" ")
    if sp >= half:
        return window[:sp].rstrip()
    return window.rstrip()


def render_node_answer(node: dict, *, max_chars: Optional[int] = None) -> str:
    """Render a matched knowledge node SOURCE-FIRST for the voice model.

    The FACT must come from the node's own ``content`` — the source text that
    FTS/pg_trgm actually matched, and retrieval can match a fact ANYWHERE in
    the node — NOT from the enricher's ``voice_answer``, which only summarises
    the TOP of the node. Leading with ``voice_answer`` silently drops any fact
    below the first sentence (the "KB was bad even on the realtime model" bug).

    So we LEAD with the source ``content`` and only fall back to
    voice_answer/summary when the node has no source text. When there is room
    we append the short spoken ``voice_answer`` as phrasing help, but the fact
    is always grounded in the source.
    """
    source = (node.get("content") or "").strip()
    phrasing = (node.get("voice_answer") or "").strip()
    summary = (node.get("summary") or "").strip()

    if not source:
        # No source text on this node — the enrichment is all we have.
        body = phrasing or summary
        if body and max_chars is not None:
            body = _truncate_on_boundary(body, max_chars)
        return body

    body = source
    # Append the spoken phrasing as a natural-wording hint, but only when it
    # adds wording the source doesn't already contain and there's budget for it.
    if phrasing and phrasing.lower() not in source.lower():
        candidate = f"{source}\n{phrasing}"
        if max_chars is None or len(candidate) <= max_chars:
            body = candidate
    if max_chars is not None and len(body) > max_chars:
        body = _truncate_on_boundary(body, max_chars)
    return body


def knowledge_enabled() -> bool:
    """Master gate for the campaign-knowledge layer (CAMPAIGN_KNOWLEDGE_ENABLED).

    The whole vectorless-RAG feature — ingest endpoints, pre-warm injection,
    and per-turn retrieval — is dark until this is flipped on, so it can ship
    disabled and be rolled out per environment without a redeploy.
    """
    return os.getenv("CAMPAIGN_KNOWLEDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


async def retrieve_knowledge(
    pool,
    tenant_id: str,
    campaign_id: str,
    query: str,
    k: int = 2,
    bump_hits: bool = False,
    acquire_timeout: Optional[float] = None,
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
    # SECURITY — fail closed: a missing/empty tenant must NOT reach
    # acquire_with_tenant, which treats tenant_id=None as an RLS BYPASS
    # (cross-tenant read). No KB caller legitimately wants a cross-tenant read
    # (every caller — per-turn, realtime, admin diagnostics — is tenant-scoped),
    # so we decline here. This is the shared choke point, so it also protects
    # the per-turn path (turn_streamer passes session.tenant_id, which could be
    # None) without that module needing to change.
    if tenant_id is None or not str(tenant_id).strip():
        logger.warning(
            "retrieve_knowledge blocked: missing tenant (campaign=%s) — refusing "
            "RLS-bypass cross-tenant read", str(campaign_id)[:12],
        )
        return []
    try:
        # acquire_timeout (Case 3): bound the pool wait so a saturated pool
        # fails fast into the caller's retrieve budget instead of queueing
        # unbounded behind it. None preserves the previous (unbounded) behavior
        # for non-voice callers that don't pass it.
        async with acquire_with_tenant(pool, tenant_id, timeout=acquire_timeout) as conn:
            rows = await conn.fetch(
                # `cand` bounds how many campaign nodes reach the expensive
                # ts_rank/word_similarity ranking (Case 3 defensive cap). The set
                # is already campaign-scoped (small), so the 200 cap is a
                # backstop for a pathologically large knowledge base, not a
                # behavior change for normal campaigns; it prefilters on the
                # cheap match predicate and orders by priority/hit_count so the
                # nodes most likely to win are the ones that survive the cap.
                """
                WITH tq AS (
                    SELECT websearch_to_tsquery('english', $2) AS q_and,
                           NULLIF(replace(
                               websearch_to_tsquery('english', $2)::text,
                               ' & ', ' | '), '')::tsquery AS q_or
                ),
                cand AS (
                    SELECT n.id, n.heading, n.summary, n.voice_answer, n.content,
                           n.search_tsv, n.search_text, n.priority, n.hit_count
                    FROM campaign_knowledge_nodes n, tq
                    WHERE n.campaign_id = $1
                      AND n.enabled
                      AND (
                           n.search_tsv @@ tq.q_and
                        OR (tq.q_or IS NOT NULL AND n.search_tsv @@ tq.q_or)
                        OR word_similarity($2, n.search_text) >= $4
                      )
                    ORDER BY n.priority DESC, n.hit_count DESC
                    LIMIT 200
                )
                SELECT c.id, c.heading, c.summary, c.voice_answer, c.content,
                       ts_rank(c.search_tsv, tq.q_and) AS fts,
                       word_similarity($2, c.search_text) AS sim
                FROM cand c, tq
                ORDER BY
                    CASE WHEN c.search_tsv @@ tq.q_and THEN 2
                         WHEN tq.q_or IS NOT NULL AND c.search_tsv @@ tq.q_or THEN 1
                         ELSE 0 END DESC,
                    GREATEST(
                        ts_rank(c.search_tsv, COALESCE(tq.q_or, tq.q_and)),
                        word_similarity($2, c.search_text)
                    ) DESC,
                    c.priority DESC,
                    c.hit_count DESC
                LIMIT $3
                """,
                campaign_id, q, k, _WORD_SIM_FLOOR,
            )
            if rows and bump_hits:
                # analytics: best-effort hit_count bump, same txn (cheap).
                # Skipped for the owner's "test a question" panel so trials
                # don't inflate the usage stats.
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
    contents"). False (inline): heading + SOURCE content (fact-complete), with
    the spoken voice_answer appended as phrasing help (see render_node_answer).

    Budgeting is done at NODE granularity: nodes are emitted WHOLE, best-first
    (priority/path order), until ``max_chars`` is reached, and the remaining
    nodes are dropped as whole units with a loud log. We never char-slice the
    joined string — that would cut a fact mid-line and silently swallow later
    topics. Only if the very FIRST node alone exceeds the budget do we emit a
    boundary-truncated slice of it (so the call is never left with zero KB).
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

    blocks: List[str] = []
    used = 0
    dropped = 0
    for i, r in enumerate(rows):
        indent = "  " * max(0, int(r["depth"]))
        head = (r["heading"] or "").strip()
        if skeleton_only:
            tail = f" — {r['summary']}" if r["summary"] else ""
            block = f"{indent}- {head}{tail}"
        else:
            body = render_node_answer(dict(r))
            if body:
                # Keep the outline readable: indent continuation lines beneath
                # the bullet instead of collapsing newlines into one long line
                # (collapsing would also defeat the line-boundary truncation).
                body = body.replace("\n", f"\n{indent}  ")
                block = f"{indent}- {head}: {body}" if head else f"{indent}- {body}"
            else:
                block = f"{indent}- {head}"
        sep = 1 if blocks else 0  # the "\n".join adds one char between blocks
        if used + sep + len(block) > max_chars:
            if not blocks:
                # First node alone blows the budget: emit a boundary-truncated
                # slice (never zero knowledge), then stop.
                blocks.append(_truncate_on_boundary(block, max_chars))
                dropped = len(rows) - 1
            else:
                dropped = len(rows) - i
            break
        blocks.append(block)
        used += sep + len(block)

    if dropped > 0:
        logger.warning(
            "compact_tree budget: dropped %d trailing node(s) over max_chars=%d "
            "campaign=%s skeleton=%s (topics omitted from injection)",
            dropped, max_chars, str(campaign_id)[:12], skeleton_only,
        )
    return "\n".join(blocks)
