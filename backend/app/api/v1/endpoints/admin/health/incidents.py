"""Durable platform incident reads and monotonic acknowledged/resolved transitions."""
from __future__ import annotations
import asyncio
import json
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import require_platform_admin_mutation
from .schemas import IncidentItem, IncidentListResponse

router = APIRouter()

@router.get("/incidents", response_model=IncidentListResponse)
async def list_incidents(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status: Literal["open", "acknowledged", "resolved"] | None = None,
    severity: Literal["critical", "warning", "info"] | None = None,
):
    try:
        async with acquire_with_tenant(db_client.pool, None, timeout=3.0) as conn:
            row = await asyncio.wait_for(conn.fetchrow("""
                WITH filtered AS (
                    SELECT * FROM incidents
                    WHERE ($1::text IS NULL OR status=$1) AND ($2::text IS NULL OR severity=$2)
                ), paged AS (
                    SELECT * FROM filtered ORDER BY triggered_at DESC, id DESC LIMIT $3 OFFSET $4
                )
                SELECT (SELECT COALESCE(json_agg(paged ORDER BY triggered_at DESC, id DESC),'[]'::json) FROM paged) AS items,
                       (SELECT COUNT(*) FROM filtered) AS total,
                       (SELECT COUNT(*) FROM incidents WHERE status='open') AS open_count,
                       (SELECT COUNT(*) FROM incidents WHERE status='open' AND severity='critical') AS critical_count
            """, status, severity, page_size, (page-1)*page_size), timeout=3.0)
        if row is None:
            raise RuntimeError("Missing incident observation")
        items = json.loads(row["items"]) if isinstance(row["items"], str) else row["items"]
        return IncidentListResponse(items=[IncidentItem(**item) for item in items], total=row["total"],
            page=page, page_size=page_size, open_count=row["open_count"], critical_count=row["critical_count"])
    except Exception:
        raise HTTPException(status_code=503, detail="Incident records could not be read; their state is unavailable.")

@router.get("/incidents/{incident_id}", response_model=IncidentItem)
async def get_incident(incident_id: UUID, admin_user: CurrentUser = Depends(require_platform_admin),
                       db_client: Client = Depends(get_db_client)):
    try:
        async with acquire_with_tenant(db_client.pool, None, timeout=3.0) as conn:
            row = await asyncio.wait_for(conn.fetchrow("SELECT * FROM incidents WHERE id=$1::uuid", str(incident_id)), timeout=3.0)
        if row is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return IncidentItem(**dict(row))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Incident record could not be read.")

async def _transition(incident_id: UUID, target: str, admin_user: CurrentUser, db_client: Client):
    try:
        async with acquire_with_tenant(db_client.pool, None, user_id=admin_user.id, timeout=3.0) as conn:
            await require_platform_admin_mutation(conn, admin_user.id)
            row = await asyncio.wait_for(conn.fetchrow("SELECT * FROM incidents WHERE id=$1::uuid FOR UPDATE", str(incident_id)), timeout=3.0)
            if row is None:
                raise HTTPException(status_code=404, detail="Incident not found")
            if target == "acknowledged" and row["status"] == "resolved":
                raise HTTPException(status_code=409, detail="A resolved incident cannot be acknowledged or reopened.")
            if row["status"] != target:
                if target == "acknowledged":
                    sql = "UPDATE incidents SET status='acknowledged', acknowledged_at=NOW(), acknowledged_by=$2::uuid WHERE id=$1::uuid AND status='open' RETURNING *"
                else:
                    sql = "UPDATE incidents SET status='resolved', resolved_at=NOW(), resolved_by=$2::uuid WHERE id=$1::uuid AND status IN ('open','acknowledged') RETURNING *"
                row = await asyncio.wait_for(conn.fetchrow(sql, str(incident_id), str(admin_user.id)), timeout=3.0)
                if row is None:
                    raise HTTPException(status_code=409, detail="Incident state changed; refresh before another action.")
            receipt = IncidentItem(**dict(row))
        # Publish only the committed, database-returned receipt. Exact replay
        # retains the first actor/timestamp and can never reopen a resolution.
        return {"success": True, "incident_id": receipt.id, "status": receipt.status,
                "message": f"Incident {receipt.status}", "acknowledged_at": receipt.acknowledged_at,
                "acknowledged_by": receipt.acknowledged_by,
                "resolved_at": receipt.resolved_at, "resolved_by": receipt.resolved_by}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Incident transition could not be confirmed. Refresh its record before retrying.")

@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge_incident(incident_id: UUID, admin_user: CurrentUser = Depends(require_platform_admin),
                               db_client: Client = Depends(get_db_client)):
    return await _transition(incident_id, "acknowledged", admin_user, db_client)

@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: UUID, admin_user: CurrentUser = Depends(require_platform_admin),
                           db_client: Client = Depends(get_db_client)):
    return await _transition(incident_id, "resolved", admin_user, db_client)
