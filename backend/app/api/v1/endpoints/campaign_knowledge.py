"""Campaign knowledge endpoints (vectorless RAG, P1.3).

Upload a .md/.txt knowledge doc for a campaign, view the parsed+enriched tree,
edit/disable nodes, and remove a source. Behind CAMPAIGN_KNOWLEDGE_ENABLED.
All tenant-scoped: a campaign is only touchable by its owning tenant.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client
from app.domain.services.campaign_knowledge_access import (
    CampaignKnowledgeAccessBusy,
    CampaignKnowledgeAccessDenied,
    CampaignKnowledgeAccessError,
    CampaignKnowledgeAccessUnavailable,
    CampaignKnowledgeNotFound,
    campaign_knowledge_access_lease,
)
from app.services.scripts.knowledge.ingest_service import (
    KnowledgeEnrichmentTimeoutError,
    KnowledgeNodeLimitError,
    create_processing_source,
    enrich_prepared_document,
    mark_source_failed_durably,
    persist_prepared_document,
    prepare_markdown,
    recover_stale_processing_sources,
)
from app.services.scripts.knowledge.retrieval import knowledge_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaign-knowledge"])

_MAX_UPLOAD_BYTES = int(os.getenv("KNOWLEDGE_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))  # 10 MB


def _require_enabled() -> None:
    if not knowledge_enabled():
        raise HTTPException(status_code=404, detail="Campaign knowledge is not enabled")


def _require_tenant(current_user: CurrentUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID required")
    return str(current_user.tenant_id)


def _invalidate_campaign_cache_fail_soft(tenant_id: str, campaign_id: str) -> None:
    """Invalidate only after a successful database commit."""

    try:
        from app.services.scripts.knowledge import cache as _kb_cache

        _kb_cache.invalidate_campaign(tenant_id, campaign_id)
    except Exception as exc:
        logger.debug(
            "knowledge cache invalidate failed campaign=%s: %s",
            campaign_id[:12],
            exc,
        )


@dataclass
class _KnowledgeMutationLease:
    conn: object
    tenant_id: str
    direction: str
    current_user: CurrentUser
    campaign_id: str
    mutate: bool = True
    cache_dirty: bool = False

    def mark_cache_dirty(self) -> None:
        self.cache_dirty = True


def _knowledge_access_http_error(exc: CampaignKnowledgeAccessError) -> HTTPException:
    if isinstance(exc, CampaignKnowledgeNotFound):
        return HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": "Campaign not found"},
        )
    if isinstance(exc, CampaignKnowledgeAccessDenied):
        return HTTPException(
            status_code=403,
            detail={
                "code": exc.code,
                "required": exc.required_permission,
            },
        )
    if isinstance(exc, CampaignKnowledgeAccessBusy):
        return HTTPException(
            status_code=503,
            headers={"Retry-After": "1"},
            detail={"code": exc.code},
        )
    if isinstance(exc, CampaignKnowledgeAccessUnavailable):
        return HTTPException(
            status_code=503,
            detail={"code": exc.code},
        )
    return HTTPException(
        status_code=503,
        detail={"code": "authorization_unavailable"},
    )


@asynccontextmanager
async def _http_knowledge_access(
    *,
    db_client: Client,
    current_user: CurrentUser,
    campaign_id: str,
    mutate: bool,
):
    """Map the shared domain lease to the stable HTTP error contract."""

    tenant_id = _require_tenant(current_user)
    try:
        async with campaign_knowledge_access_lease(
            db_client.pool,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            actor_user_id=str(current_user.id),
            mutate=mutate,
        ) as access:
            yield _KnowledgeMutationLease(
                conn=access.conn,
                tenant_id=access.tenant_id,
                direction=access.direction,
                current_user=current_user,
                campaign_id=access.campaign_id,
                mutate=access.mutate,
            )
    except CampaignKnowledgeAccessError as exc:
        raise _knowledge_access_http_error(exc) from exc


def _knowledge_access_dependency(*, mutate: bool):
    async def dependency(
        campaign_id: str,
        current_user: CurrentUser = Depends(get_current_user),
        db_client: Client = Depends(get_db_client),
    ) -> AsyncIterator[_KnowledgeMutationLease]:
        committed_lease: _KnowledgeMutationLease | None = None
        async with _http_knowledge_access(
            db_client=db_client,
            current_user=current_user,
            campaign_id=campaign_id,
            mutate=mutate,
        ) as active_lease:
            committed_lease = active_lease
            yield active_lease

        # Function-scoped dependencies exit (and commit) before response send.
        # A commit failure skips this line, so stale data is never evicted.
        if mutate and committed_lease is not None and committed_lease.cache_dirty:
            _invalidate_campaign_cache_fail_soft(
                committed_lease.tenant_id,
                committed_lease.campaign_id,
            )

    return dependency


_require_knowledge_read = _knowledge_access_dependency(mutate=False)
_require_knowledge_mutation = _knowledge_access_dependency(mutate=True)


@router.post(
    "/{campaign_id}/knowledge",
)
async def upload_knowledge(
    campaign_id: str,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Upload a .md/.txt document → parse → enrich → store as a knowledge tree."""
    _require_enabled()
    tenant_id = _require_tenant(current_user)

    name = (file.filename or "").lower()
    if not (name.endswith(".md") or name.endswith(".txt") or not name):
        raise HTTPException(status_code=415, detail="Only .md or .txt files are supported")

    # Authorize before loading/parsing provider work. This lease is short and
    # deliberately released before reading the upload.
    async with _http_knowledge_access(
        db_client=db_client,
        current_user=current_user,
        campaign_id=campaign_id,
        mutate=True,
    ):
        pass

    # Read at most one byte beyond the limit; reading the whole request first
    # would let a rejected oversized upload exhaust process memory.
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {_MAX_UPLOAD_BYTES} bytes)")
    try:
        raw_md = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text")
    if not raw_md.strip():
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        prepared = prepare_markdown(raw_md)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    source_id: str | None = None
    try:
        # Commit a visible processing record under a short, re-authorized
        # mutation lease. A cancellation after this point has durable state.
        async with _http_knowledge_access(
            db_client=db_client,
            current_user=current_user,
            campaign_id=campaign_id,
            mutate=True,
        ) as source_lease:
            await recover_stale_processing_sources(
                db_client.pool,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                conn=source_lease.conn,
            )
            source_id = await create_processing_source(
                db_client.pool,
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                raw_md=raw_md,
                filename=file.filename,
                conn=source_lease.conn,
            )

        # Provider work is bounded and runs with no pooled connection or
        # advisory lock, so a slow upload cannot starve unrelated requests or
        # block campaign conversion for its full duration.
        enrichments = await enrich_prepared_document(prepared)

        # Grants and campaign direction may have changed during enrichment.
        # Re-authorize, then publish every node + ready state atomically using
        # this exact connection before releasing the direction lease.
        async with _http_knowledge_access(
            db_client=db_client,
            current_user=current_user,
            campaign_id=campaign_id,
            mutate=True,
        ) as publish_lease:
            model = await publish_lease.conn.fetchval(
                "SELECT knowledge_model FROM campaigns "
                "WHERE id = $1 AND tenant_id = $2",
                campaign_id,
                tenant_id,
            )
            result = await persist_prepared_document(
                publish_lease.conn,
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
            failure_code = (
                "knowledge_ingest_cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "knowledge_enrichment_timeout"
                if isinstance(exc, KnowledgeEnrichmentTimeoutError)
                else str(exc)
            )
            try:
                cancelled_while_marking = await mark_source_failed_durably(
                    db_client.pool,
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    error=failure_code,
                )
            except Exception as mark_exc:  # stale recovery remains the fallback
                logger.error(
                    "knowledge upload failure marker failed campaign=%s type=%s",
                    campaign_id[:12],
                    type(mark_exc).__name__,
                    exc_info=True,
                )

        if isinstance(exc, asyncio.CancelledError) or cancelled_while_marking:
            raise asyncio.CancelledError() from exc
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, (KnowledgeNodeLimitError, ValueError)):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if isinstance(exc, KnowledgeEnrichmentTimeoutError):
            raise HTTPException(
                status_code=504,
                detail={"code": "knowledge_enrichment_timeout"},
            ) from exc
        logger.error(
            "knowledge upload failed campaign=%s type=%s",
            campaign_id[:12],
            type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Knowledge ingest failed") from exc

    # Publication and its outer authorization transaction have committed.
    _invalidate_campaign_cache_fail_soft(tenant_id, campaign_id)
    return result


@router.get("/{campaign_id}/knowledge")
async def get_knowledge(
    campaign_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    lease: _KnowledgeMutationLease = Depends(
        _require_knowledge_read,
        scope="function",
    ),
):
    """Return the campaign's knowledge tree (nested), its sources, and mode."""
    _require_enabled()
    tenant_id = lease.tenant_id
    conn = lease.conn
    mode = await conn.fetchval(
        "SELECT knowledge_mode FROM campaigns "
        "WHERE id = $1 AND tenant_id = $2",
        campaign_id,
        tenant_id,
    )
    sources = await conn.fetch(
        "SELECT id, filename, token_count, version, status, error, created_at "
        "FROM campaign_knowledge_sources "
        "WHERE campaign_id = $1 AND tenant_id = $2 ORDER BY created_at DESC",
        campaign_id,
        tenant_id,
    )
    nodes = await conn.fetch(
        "SELECT id, parent_id, depth, path, position, heading, content, summary, voice_answer, "
        "       keywords, example_questions, priority, hit_count, enabled "
        "FROM campaign_knowledge_nodes "
        "WHERE campaign_id = $1 AND tenant_id = $2 "
        "ORDER BY string_to_array(path, '.')::int[]",
        campaign_id,
        tenant_id,
    )

    # build nested tree from flat parent_id rows
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for r in nodes:
        d = dict(r)
        d["id"] = str(d["id"])
        d["parent_id"] = str(d["parent_id"]) if d["parent_id"] else None
        d["children"] = []
        by_id[d["id"]] = d
    for d in by_id.values():
        if d["parent_id"] and d["parent_id"] in by_id:
            by_id[d["parent_id"]]["children"].append(d)
        else:
            roots.append(d)

    return {
        "campaign_id": campaign_id,
        "knowledge_mode": mode,
        "sources": [dict(s) | {"id": str(s["id"])} for s in sources],
        "tree": roots,
    }


@router.patch(
    "/{campaign_id}/knowledge/nodes/{node_id}",
)
async def update_node(
    campaign_id: str,
    node_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    mutation: _KnowledgeMutationLease = Depends(
        _require_knowledge_mutation,
        scope="function",
    ),
):
    """Edit a node: enabled / priority / summary / voice_answer (owner tuning)."""
    _require_enabled()
    tenant_id = mutation.tenant_id
    allowed = {"enabled", "priority", "summary", "voice_answer", "heading", "content"}
    fields = {k: v for k, v in payload.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail=f"No editable fields (allowed: {sorted(allowed)})")

    conn = mutation.conn
    # $1/$2/$3 are node, campaign and tenant; edited values begin at $4.
    set_parts = [f"{k} = ${i + 4}" for i, k in enumerate(fields)]
    params = list(fields.values())
    # If heading/content changed, recompute search_text + tsvector so the
    # retriever reflects the edit (same shape ingest builds).
    if "heading" in fields or "content" in fields:
        cur = await conn.fetchrow(
            "SELECT heading, content, keywords, example_questions "
            "FROM campaign_knowledge_nodes "
            "WHERE id = $1 AND campaign_id = $2 AND tenant_id = $3",
            node_id,
            campaign_id,
            tenant_id,
        )
        if not cur:
            raise HTTPException(status_code=404, detail="Node not found")
        heading = fields.get("heading", cur["heading"]) or ""
        content = fields.get("content", cur["content"]) or ""
        kw = cur["keywords"] or []
        eq = cur["example_questions"] or []
        search_text = " ".join(
            p for p in [heading, content, " ".join(kw), " ".join(eq)] if p
        ).strip()
        idx = len(params) + 4
        params.append(search_text)
        set_parts.append(f"search_text = ${idx}")
        set_parts.append(f"search_tsv = to_tsvector('english', ${idx})")

    sets = ", ".join(set_parts)
    updated = await conn.fetchval(
        f"UPDATE campaign_knowledge_nodes SET {sets}, updated_at = NOW() "
        "WHERE id = $1 AND campaign_id = $2 AND tenant_id = $3 RETURNING id",
        node_id,
        campaign_id,
        tenant_id,
        *params,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Node not found")

    mutation.mark_cache_dirty()
    return {"id": str(updated), "updated": list(fields.keys())}


@router.post("/{campaign_id}/knowledge/test")
async def test_retrieval(
    campaign_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    lease: _KnowledgeMutationLease = Depends(
        _require_knowledge_read,
        scope="function",
    ),
):
    """Run the live retriever for a query and return the matched node(s).

    The owner's "test a question" tool — shows exactly what the agent would pull
    from the knowledge tree for a caller's question. Does NOT bump hit_count so
    trials don't inflate usage stats.
    """
    _require_enabled()
    tenant_id = lease.tenant_id

    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        k = max(1, min(int(payload.get("k", 3)), 5))
    except (TypeError, ValueError):
        k = 3

    from app.services.scripts.knowledge.retrieval import retrieve_knowledge

    hits = await retrieve_knowledge(
        db_client.pool,
        tenant_id,
        campaign_id,
        query,
        k=k,
        bump_hits=False,
        conn=lease.conn,
        raise_on_error=True,
    )
    return {
        "query": query,
        "hits": [
            {
                "id": str(h["id"]),
                "heading": h.get("heading"),
                "voice_answer": h.get("voice_answer"),
                "summary": h.get("summary"),
                "fts": h.get("fts"),
                "sim": h.get("sim"),
            }
            for h in hits
        ],
    }


@router.delete(
    "/{campaign_id}/knowledge/sources/{source_id}",
)
async def delete_source(
    campaign_id: str,
    source_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    mutation: _KnowledgeMutationLease = Depends(
        _require_knowledge_mutation,
        scope="function",
    ),
):
    """Delete a knowledge source (cascades its nodes) and recompute mode."""
    _require_enabled()
    tenant_id = mutation.tenant_id
    from app.services.scripts.knowledge.budget import choose_mode

    conn = mutation.conn
    deleted = await conn.fetchval(
        "DELETE FROM campaign_knowledge_sources "
        "WHERE id = $1 AND campaign_id = $2 AND tenant_id = $3 RETURNING id",
        source_id,
        campaign_id,
        tenant_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found")
    model = await conn.fetchval(
        "SELECT knowledge_model FROM campaigns "
        "WHERE id = $1 AND tenant_id = $2",
        campaign_id,
        tenant_id,
    )
    remaining = await conn.fetchval(
        "SELECT COALESCE(SUM(token_count),0) FROM campaign_knowledge_sources "
        "WHERE campaign_id = $1 AND tenant_id = $2 AND status = 'ready'",
        campaign_id,
        tenant_id,
    )
    mode = choose_mode(int(remaining or 0), model)
    updated = await conn.execute(
        "UPDATE campaigns SET knowledge_mode = $3, updated_at = NOW() "
        "WHERE id = $1 AND tenant_id = $2",
        campaign_id,
        tenant_id,
        mode,
    )
    if updated != "UPDATE 1":
        raise HTTPException(
            status_code=409,
            detail={"code": "campaign_write_conflict"},
        )

    mutation.mark_cache_dirty()
    return {"deleted": str(deleted), "knowledge_mode": mode}
