"""Bounded, recoverable campaign-knowledge ingestion.

The public stages deliberately separate local parsing, durable job creation,
provider enrichment, and atomic publication. HTTP callers can therefore run
slow provider work without holding a database transaction or a campaign
direction lock, then publish through a caller-provided locked connection.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.db_utils import acquire_with_tenant
from app.services.scripts.knowledge.budget import choose_mode, estimate_tokens
from app.services.scripts.knowledge.enricher import NodeEnrichment, enrich_nodes
from app.services.scripts.knowledge.md_tree import ParsedNode, parse_markdown_tree

logger = logging.getLogger(__name__)

_DEFAULT_MAX_NODES = 256
_DEFAULT_ENRICH_BATCH_SIZE = 20
_DEFAULT_MAX_ENRICH_BATCHES = 16
_DEFAULT_ENRICH_TIMEOUT_SECONDS = 90.0
_DEFAULT_STALE_AFTER_SECONDS = 15 * 60
_DEFAULT_FAILURE_MARK_TIMEOUT_SECONDS = 5.0


class KnowledgeNodeLimitError(ValueError):
    """The document would create more bounded work than ingestion permits."""


class KnowledgeEnrichmentTimeoutError(TimeoutError):
    """The absolute enrichment deadline expired."""


class KnowledgeSourceStateError(RuntimeError):
    """A processing source disappeared or changed state before publication."""


@dataclass(frozen=True)
class PreparedKnowledgeDocument:
    """Deterministic local output, safe to retain outside a DB transaction."""

    nodes: tuple[ParsedNode, ...]
    token_count: int


def _positive_int_setting(name: str, default: int, override: int | None = None) -> int:
    raw: object = override if override is not None else os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float_setting(
    name: str,
    default: float,
    override: float | None = None,
) -> float:
    raw: object = override if override is not None else os.getenv(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


@asynccontextmanager
async def _tenant_connection(pool, tenant_id: str, existing_conn=None):
    """Reuse a caller connection or acquire one tenant-scoped transaction."""

    if existing_conn is not None:
        yield existing_conn
        return
    async with acquire_with_tenant(pool, tenant_id) as acquired:
        yield acquired


def _search_text(heading: str, content: str, keywords, example_questions) -> str:
    parts = [heading, content, " ".join(keywords or []), " ".join(example_questions or [])]
    return " ".join(part for part in parts if part).strip()


def prepare_markdown(
    raw_md: str,
    *,
    max_nodes: int | None = None,
) -> PreparedKnowledgeDocument:
    """Parse and validate Markdown locally, before creating a source row."""

    nodes = tuple(parse_markdown_tree(raw_md))
    if not nodes:
        raise ValueError("Document has no usable content")

    node_limit = _positive_int_setting(
        "KNOWLEDGE_INGEST_MAX_NODES",
        _DEFAULT_MAX_NODES,
        max_nodes,
    )
    if len(nodes) > node_limit:
        raise KnowledgeNodeLimitError(
            f"Document has {len(nodes)} nodes; maximum is {node_limit} nodes"
        )

    token_count = sum(
        estimate_tokens(f"{node.heading} {node.content}") for node in nodes
    )
    return PreparedKnowledgeDocument(nodes=nodes, token_count=token_count)


async def create_processing_source(
    pool,
    *,
    campaign_id: str,
    tenant_id: str,
    raw_md: str,
    filename: Optional[str] = None,
    conn=None,
) -> str:
    """Create a processing row.

    Without ``conn``, leaving this function commits the row and makes it
    visible before enrichment starts. With ``conn``, the caller owns commit.
    """

    async with _tenant_connection(pool, tenant_id, conn) as db:
        async with db.transaction():
            source_id = await db.fetchval(
                """
                INSERT INTO campaign_knowledge_sources
                    (campaign_id, tenant_id, filename, raw_md, status)
                VALUES ($1, $2, $3, $4, 'processing')
                RETURNING id
                """,
                campaign_id,
                tenant_id,
                filename,
                raw_md,
            )
    if source_id is None:
        raise RuntimeError("Knowledge source creation returned no id")
    return str(source_id)


async def enrich_prepared_document(
    prepared: PreparedKnowledgeDocument,
    *,
    timeout_seconds: float | None = None,
    batch_size: int | None = None,
    max_batches: int | None = None,
) -> list[NodeEnrichment]:
    """Enrich bounded batches under one absolute deadline, without DB work."""

    deadline = _positive_float_setting(
        "KNOWLEDGE_INGEST_ENRICH_TIMEOUT_SECONDS",
        _DEFAULT_ENRICH_TIMEOUT_SECONDS,
        timeout_seconds,
    )
    size = _positive_int_setting(
        "KNOWLEDGE_INGEST_ENRICH_BATCH_SIZE",
        _DEFAULT_ENRICH_BATCH_SIZE,
        batch_size,
    )
    batch_limit = _positive_int_setting(
        "KNOWLEDGE_INGEST_MAX_ENRICH_BATCHES",
        _DEFAULT_MAX_ENRICH_BATCHES,
        max_batches,
    )
    required_batches = (len(prepared.nodes) + size - 1) // size
    if required_batches > batch_limit:
        raise KnowledgeNodeLimitError(
            "Document exceeds enrichment batch cap "
            f"({required_batches} required; maximum {batch_limit})"
        )

    async def _run_batches() -> list[NodeEnrichment]:
        enriched: list[NodeEnrichment] = []
        for start in range(0, len(prepared.nodes), size):
            batch = list(prepared.nodes[start : start + size])
            batch_result = await enrich_nodes(batch)
            if len(batch_result) != len(batch):
                raise RuntimeError(
                    "Knowledge enricher returned a mismatched node count"
                )
            enriched.extend(batch_result)
        return enriched

    try:
        return await asyncio.wait_for(_run_batches(), timeout=deadline)
    except asyncio.TimeoutError as exc:
        raise KnowledgeEnrichmentTimeoutError(
            f"Knowledge enrichment exceeded its {deadline:g}s absolute timeout"
        ) from exc


async def persist_prepared_document(
    conn,
    *,
    campaign_id: str,
    tenant_id: str,
    source_id: str,
    prepared: PreparedKnowledgeDocument,
    enrichments: Sequence[NodeEnrichment],
    model: Optional[str] = None,
) -> dict:
    """Atomically publish nodes, ready state, and campaign knowledge mode."""

    if len(enrichments) != len(prepared.nodes):
        raise ValueError("Expected one enrichment result per knowledge node")

    # This is a savepoint when the caller already owns an authorization txn.
    async with conn.transaction():
        index_to_id: dict[int, str] = {}
        for node, enrichment in zip(prepared.nodes, enrichments, strict=True):
            parent_uuid = (
                index_to_id.get(node.parent_index)
                if node.parent_index is not None
                else None
            )
            search_text = _search_text(
                node.heading,
                node.content,
                enrichment.keywords,
                enrichment.example_questions,
            )
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
                campaign_id,
                tenant_id,
                source_id,
                parent_uuid,
                node.depth,
                node.path,
                node.position,
                node.heading,
                node.content,
                enrichment.summary or None,
                enrichment.voice_answer or None,
                enrichment.keywords or None,
                enrichment.example_questions or None,
                search_text,
            )
            index_to_id[node.index] = str(new_id)

        ready_source_id = await conn.fetchval(
            """
            UPDATE campaign_knowledge_sources
            SET status = 'ready', token_count = $4, error = NULL, updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2 AND campaign_id = $3
              AND status = 'processing'
            RETURNING id
            """,
            source_id,
            tenant_id,
            campaign_id,
            prepared.token_count,
        )
        if ready_source_id is None:
            raise KnowledgeSourceStateError(
                "Knowledge source is no longer in processing state"
            )

        campaign_tokens = await conn.fetchval(
            """
            SELECT COALESCE(SUM(token_count), 0)
            FROM campaign_knowledge_sources
            WHERE campaign_id = $1 AND tenant_id = $2 AND status = 'ready'
            """,
            campaign_id,
            tenant_id,
        )
        mode = choose_mode(int(campaign_tokens or 0), model)
        campaign_result = await conn.execute(
            """
            UPDATE campaigns
            SET knowledge_mode = $3, updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            """,
            campaign_id,
            tenant_id,
            mode,
        )
        if campaign_result == "UPDATE 0":
            raise KnowledgeSourceStateError("Campaign disappeared during publication")

    return {
        "source_id": str(source_id),
        "node_count": len(prepared.nodes),
        "token_count": prepared.token_count,
        "mode": mode,
    }


async def mark_source_failed(
    pool,
    *,
    campaign_id: str,
    tenant_id: str,
    source_id: str,
    error: str,
    conn=None,
) -> None:
    """Move a still-processing source to failed without masking its cause."""

    message = (error or "knowledge_ingest_failed")[:500]
    async with _tenant_connection(pool, tenant_id, conn) as db:
        await db.execute(
            """
            UPDATE campaign_knowledge_sources
            SET status = 'failed', error = $4, updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2 AND campaign_id = $3
              AND status = 'processing'
            """,
            source_id,
            tenant_id,
            campaign_id,
            message,
        )


async def recover_stale_processing_sources(
    pool,
    *,
    tenant_id: str,
    campaign_id: str | None = None,
    stale_after_seconds: int | None = None,
    conn=None,
) -> int:
    """Mark abandoned processing rows failed so they never remain ambiguous."""

    stale_after = _positive_int_setting(
        "KNOWLEDGE_INGEST_STALE_AFTER_SECONDS",
        _DEFAULT_STALE_AFTER_SECONDS,
        stale_after_seconds,
    )
    async with _tenant_connection(pool, tenant_id, conn) as db:
        if campaign_id is None:
            recovered = await db.fetchval(
                """
                WITH recovered AS (
                    UPDATE campaign_knowledge_sources
                    SET status = 'failed', error = 'stale_processing_recovered',
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND status = 'processing'
                      AND updated_at < NOW() - ($2::double precision * INTERVAL '1 second')
                    RETURNING 1
                )
                SELECT COUNT(*) FROM recovered
                """,
                tenant_id,
                stale_after,
            )
        else:
            recovered = await db.fetchval(
                """
                WITH recovered AS (
                    UPDATE campaign_knowledge_sources
                    SET status = 'failed', error = 'stale_processing_recovered',
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND campaign_id = $2
                      AND status = 'processing'
                      AND updated_at < NOW() - ($3::double precision * INTERVAL '1 second')
                    RETURNING 1
                )
                SELECT COUNT(*) FROM recovered
                """,
                tenant_id,
                campaign_id,
                stale_after,
            )
    return int(recovered or 0)


def _failure_message(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "knowledge_ingest_cancelled"
    if isinstance(exc, KnowledgeEnrichmentTimeoutError):
        return "knowledge_enrichment_timeout"
    return (str(exc) or type(exc).__name__)[:500]


async def _finish_failure_update(
    update: Awaitable[None],
) -> bool:
    """Finish the bounded status write even if this task is cancelled again.

    Returns whether another cancellation arrived while cleanup was in flight.
    The database work runs in a child task so shielding it does not merely
    abandon an unfinished coroutine when the request receives a second cancel.
    """

    cleanup = asyncio.create_task(
        asyncio.wait_for(update, timeout=_DEFAULT_FAILURE_MARK_TIMEOUT_SECONDS)
    )
    cancellation_seen = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancellation_seen = True
    cleanup.result()
    return cancellation_seen


async def mark_source_failed_durably(
    pool,
    *,
    campaign_id: str,
    tenant_id: str,
    source_id: str,
    error: str,
    conn=None,
) -> bool:
    """Commit a bounded failure marker despite repeated request cancellation.

    Returns ``True`` when another cancellation arrived while the marker was
    being written, allowing the caller to preserve cancellation semantics only
    after the database cleanup has finished or reached its absolute timeout.
    """

    return await _finish_failure_update(
        mark_source_failed(
            pool,
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            source_id=source_id,
            error=error,
            conn=conn,
        )
    )


def _invalidate_cache_fail_soft(tenant_id: str, campaign_id: str) -> None:
    try:
        from app.services.scripts.knowledge import cache as knowledge_cache

        knowledge_cache.invalidate_campaign(tenant_id, campaign_id)
    except Exception as exc:
        logger.debug(
            "knowledge cache invalidate failed campaign=%s: %s",
            str(campaign_id)[:12],
            exc,
        )


async def ingest_markdown(
    pool,
    *,
    campaign_id: str,
    tenant_id: str,
    raw_md: str,
    filename: Optional[str] = None,
    model: Optional[str] = None,
    conn=None,
    invalidate_cache: bool = True,
) -> dict:
    """Backward-compatible orchestration over the staged ingest primitives.

    The normal path owns short transactions, commits ``processing`` before
    enrichment, and invalidates cache only after its publication commit. The
    optional ``conn`` path preserves callers that already own a transaction;
    those callers own both commit and post-commit cache invalidation.
    """

    prepared = prepare_markdown(raw_md)
    source_id: str | None = None
    try:
        await recover_stale_processing_sources(
            pool,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            conn=conn,
        )
        source_id = await create_processing_source(
            pool,
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            raw_md=raw_md,
            filename=filename,
            conn=conn,
        )
        enrichments = await enrich_prepared_document(prepared)

        async with _tenant_connection(pool, tenant_id, conn) as db:
            result = await persist_prepared_document(
                db,
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                source_id=source_id,
                prepared=prepared,
                enrichments=enrichments,
                model=model,
            )
    except (Exception, asyncio.CancelledError) as exc:
        cancelled_while_marking = False
        if source_id is not None:
            try:
                cancelled_while_marking = await mark_source_failed_durably(
                    pool,
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    error=_failure_message(exc),
                    conn=conn,
                )
            except Exception as mark_exc:
                logger.error(
                    "knowledge ingest failure status update failed campaign=%s: %s",
                    str(campaign_id)[:12],
                    mark_exc,
                    exc_info=True,
                )
        logger.error(
            "knowledge ingest failed campaign=%s: %s",
            str(campaign_id)[:12],
            exc,
            exc_info=not isinstance(exc, asyncio.CancelledError),
        )
        if cancelled_while_marking and not isinstance(exc, asyncio.CancelledError):
            raise asyncio.CancelledError() from exc
        raise

    logger.info(
        "knowledge_ingested campaign=%s source=%s nodes=%d tokens=%d mode=%s",
        str(campaign_id)[:12],
        str(source_id)[:12],
        result["node_count"],
        result["token_count"],
        result["mode"],
    )
    if invalidate_cache and conn is None:
        _invalidate_cache_fail_soft(tenant_id, campaign_id)
    return result
