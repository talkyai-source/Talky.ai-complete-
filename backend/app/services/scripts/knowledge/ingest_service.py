"""Knowledge ingest orchestration (vectorless RAG, P1).

upload → store source → parse md→tree → LLM-enrich → insert nodes (with FTS
tsvector + plain search_text for pg_trgm) → compute the campaign's model-aware
knowledge_mode. Tenant-scoped via acquire_with_tenant (RLS).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.db_utils import acquire_with_tenant
from app.services.scripts.knowledge.budget import choose_mode, estimate_tokens
from app.services.scripts.knowledge.enricher import enrich_nodes
from app.services.scripts.knowledge.md_tree import parse_markdown_tree

logger = logging.getLogger(__name__)


def _search_text(heading: str, content: str, keywords, example_questions) -> str:
    parts = [heading, content, " ".join(keywords or []), " ".join(example_questions or [])]
    return " ".join(p for p in parts if p).strip()


async def ingest_markdown(
    pool,
    *,
    campaign_id: str,
    tenant_id: str,
    raw_md: str,
    filename: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Ingest one uploaded document into the campaign knowledge tree.

    Returns {source_id, node_count, token_count, mode}. The source row is left
    status='failed' (with error) if anything past insertion blows up.
    """
    nodes = parse_markdown_tree(raw_md)
    if not nodes:
        raise ValueError("Document has no usable content")

    # 1. create the source row (processing)
    async with acquire_with_tenant(pool, tenant_id) as conn:
        source_id = await conn.fetchval(
            """
            INSERT INTO campaign_knowledge_sources
                (campaign_id, tenant_id, filename, raw_md, status)
            VALUES ($1, $2, $3, $4, 'processing')
            RETURNING id
            """,
            campaign_id, tenant_id, filename, raw_md,
        )

    try:
        # 2. enrich (fail-soft — never raises)
        enrichments = await enrich_nodes(nodes)

        # 3. insert nodes; map list-index → uuid for parent_id (parents precede children)
        total_tokens = 0
        async with acquire_with_tenant(pool, tenant_id) as conn:
            index_to_id: dict[int, str] = {}
            for node in nodes:
                e = enrichments[node.index]
                stext = _search_text(node.heading, node.content, e.keywords, e.example_questions)
                total_tokens += estimate_tokens(node.heading + " " + node.content)
                parent_uuid = index_to_id.get(node.parent_index) if node.parent_index is not None else None
                new_id = await conn.fetchval(
                    """
                    INSERT INTO campaign_knowledge_nodes
                        (campaign_id, tenant_id, source_id, parent_id, depth, path, position,
                         heading, content, summary, voice_answer, keywords, example_questions,
                         search_text, search_tsv)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                            to_tsvector('english', $14))
                    RETURNING id
                    """,
                    campaign_id, tenant_id, source_id, parent_uuid, node.depth, node.path,
                    node.position, node.heading, node.content, e.summary or None,
                    e.voice_answer or None, e.keywords or None, e.example_questions or None,
                    stext,
                )
                index_to_id[node.index] = new_id

            # 4. mark source ready with its token count
            await conn.execute(
                "UPDATE campaign_knowledge_sources "
                "SET status='ready', token_count=$2, updated_at=NOW() WHERE id=$1",
                source_id, total_tokens,
            )

            # 5. recompute campaign knowledge_mode from ALL ready sources
            campaign_tokens = await conn.fetchval(
                "SELECT COALESCE(SUM(token_count),0) FROM campaign_knowledge_sources "
                "WHERE campaign_id=$1 AND status='ready'",
                campaign_id,
            )
            mode = choose_mode(int(campaign_tokens or 0), model)
            await conn.execute(
                "UPDATE campaigns SET knowledge_mode=$2, updated_at=NOW() WHERE id=$1",
                campaign_id, mode,
            )

        logger.info(
            "knowledge_ingested campaign=%s source=%s nodes=%d tokens=%d mode=%s",
            str(campaign_id)[:12], str(source_id)[:12], len(nodes), total_tokens, mode,
        )
        return {
            "source_id": str(source_id),
            "node_count": len(nodes),
            "token_count": total_tokens,
            "mode": mode,
        }
    except Exception as exc:
        logger.error("knowledge ingest failed campaign=%s: %s", str(campaign_id)[:12], exc, exc_info=True)
        try:
            async with acquire_with_tenant(pool, tenant_id) as conn:
                await conn.execute(
                    "UPDATE campaign_knowledge_sources SET status='failed', error=$2, updated_at=NOW() WHERE id=$1",
                    source_id, str(exc)[:500],
                )
        except Exception:
            pass
        raise
