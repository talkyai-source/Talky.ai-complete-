"""Campaign knowledge endpoints (vectorless RAG, P1.3).

Upload a .md/.txt knowledge doc for a campaign, view the parsed+enriched tree,
edit/disable nodes, and remove a source. Behind CAMPAIGN_KNOWLEDGE_ENABLED.
All tenant-scoped: a campaign is only touchable by its owning tenant.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.services.scripts.knowledge import ingest_markdown
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


async def _assert_campaign_owned(pool, tenant_id: str, campaign_id: str) -> None:
    async with acquire_with_tenant(pool, tenant_id) as conn:
        owned = await conn.fetchval(
            "SELECT 1 FROM campaigns WHERE id = $1 AND tenant_id = $2",
            campaign_id, tenant_id,
        )
    if not owned:
        raise HTTPException(status_code=404, detail="Campaign not found")


@router.post("/{campaign_id}/knowledge")
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

    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {_MAX_UPLOAD_BYTES} bytes)")
    try:
        raw_md = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 text")
    if not raw_md.strip():
        raise HTTPException(status_code=400, detail="File is empty")

    await _assert_campaign_owned(db_client.pool, tenant_id, campaign_id)

    # Optional larger-context model for this campaign (drives inline budget).
    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        model = await conn.fetchval(
            "SELECT knowledge_model FROM campaigns WHERE id = $1", campaign_id,
        )

    try:
        result = await ingest_markdown(
            db_client.pool,
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            raw_md=raw_md,
            filename=file.filename,
            model=model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("knowledge upload failed campaign=%s: %s", campaign_id[:12], exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Knowledge ingest failed")
    return result


@router.get("/{campaign_id}/knowledge")
async def get_knowledge(
    campaign_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Return the campaign's knowledge tree (nested), its sources, and mode."""
    _require_enabled()
    tenant_id = _require_tenant(current_user)
    await _assert_campaign_owned(db_client.pool, tenant_id, campaign_id)

    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        mode = await conn.fetchval("SELECT knowledge_mode FROM campaigns WHERE id = $1", campaign_id)
        sources = await conn.fetch(
            "SELECT id, filename, token_count, version, status, error, created_at "
            "FROM campaign_knowledge_sources WHERE campaign_id = $1 ORDER BY created_at DESC",
            campaign_id,
        )
        nodes = await conn.fetch(
            "SELECT id, parent_id, depth, path, position, heading, summary, voice_answer, "
            "       keywords, example_questions, priority, hit_count, enabled "
            "FROM campaign_knowledge_nodes WHERE campaign_id = $1 "
            "ORDER BY string_to_array(path, '.')::int[]",
            campaign_id,
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


@router.patch("/{campaign_id}/knowledge/nodes/{node_id}")
async def update_node(
    campaign_id: str,
    node_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Edit a node: enabled / priority / summary / voice_answer (owner tuning)."""
    _require_enabled()
    tenant_id = _require_tenant(current_user)
    await _assert_campaign_owned(db_client.pool, tenant_id, campaign_id)

    allowed = {"enabled", "priority", "summary", "voice_answer", "heading", "content"}
    fields = {k: v for k, v in payload.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail=f"No editable fields (allowed: {sorted(allowed)})")

    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        set_parts = [f"{k} = ${i + 3}" for i, k in enumerate(fields)]
        params = list(fields.values())
        # If heading/content changed, recompute search_text + tsvector so the
        # retriever reflects the edit (same shape ingest builds).
        if "heading" in fields or "content" in fields:
            cur = await conn.fetchrow(
                "SELECT heading, content, keywords, example_questions "
                "FROM campaign_knowledge_nodes WHERE id = $1 AND campaign_id = $2",
                node_id, campaign_id,
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
            idx = len(params) + 3
            params.append(search_text)
            set_parts.append(f"search_text = ${idx}")
            set_parts.append(f"search_tsv = to_tsvector('english', ${idx})")

        sets = ", ".join(set_parts)
        updated = await conn.fetchval(
            f"UPDATE campaign_knowledge_nodes SET {sets}, updated_at = NOW() "
            "WHERE id = $1 AND campaign_id = $2 RETURNING id",
            node_id, campaign_id, *params,
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"id": str(updated), "updated": list(fields.keys())}


@router.post("/{campaign_id}/knowledge/test")
async def test_retrieval(
    campaign_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Run the live retriever for a query and return the matched node(s).

    The owner's "test a question" tool — shows exactly what the agent would pull
    from the knowledge tree for a caller's question. Does NOT bump hit_count so
    trials don't inflate usage stats.
    """
    _require_enabled()
    tenant_id = _require_tenant(current_user)
    await _assert_campaign_owned(db_client.pool, tenant_id, campaign_id)

    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        k = max(1, min(int(payload.get("k", 3)), 5))
    except (TypeError, ValueError):
        k = 3

    from app.services.scripts.knowledge.retrieval import retrieve_knowledge

    hits = await retrieve_knowledge(
        db_client.pool, tenant_id, campaign_id, query, k=k, bump_hits=False,
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


@router.delete("/{campaign_id}/knowledge/sources/{source_id}")
async def delete_source(
    campaign_id: str,
    source_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Delete a knowledge source (cascades its nodes) and recompute mode."""
    _require_enabled()
    tenant_id = _require_tenant(current_user)
    await _assert_campaign_owned(db_client.pool, tenant_id, campaign_id)

    from app.services.scripts.knowledge.budget import choose_mode

    async with acquire_with_tenant(db_client.pool, tenant_id) as conn:
        deleted = await conn.fetchval(
            "DELETE FROM campaign_knowledge_sources WHERE id = $1 AND campaign_id = $2 RETURNING id",
            source_id, campaign_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Source not found")
        model = await conn.fetchval("SELECT knowledge_model FROM campaigns WHERE id = $1", campaign_id)
        remaining = await conn.fetchval(
            "SELECT COALESCE(SUM(token_count),0) FROM campaign_knowledge_sources "
            "WHERE campaign_id = $1 AND status = 'ready'",
            campaign_id,
        )
        mode = choose_mode(int(remaining or 0), model)
        await conn.execute("UPDATE campaigns SET knowledge_mode = $2, updated_at = NOW() WHERE id = $1",
                           campaign_id, mode)
    return {"deleted": str(deleted), "knowledge_mode": mode}
