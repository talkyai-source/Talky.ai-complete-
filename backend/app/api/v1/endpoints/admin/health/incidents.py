"""Incident endpoints — list / get / acknowledge / resolve.

  GET  /admin/incidents
  GET  /admin/incidents/{id}
  POST /admin/incidents/{id}/acknowledge
  POST /admin/incidents/{id}/resolve
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from app.api.v1.dependencies import CurrentUser, get_db_client, require_platform_admin
from app.core.postgres_adapter import Client

from .schemas import IncidentItem, IncidentListResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/incidents", response_model=IncidentListResponse)
async def list_incidents(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status"),
    severity: Optional[str] = Query(None, description="Filter by severity")
):
    """
    List incidents with pagination and filters.
    """
    try:
        offset = (page - 1) * page_size

        query = db_client.table("incidents").select("*", count="exact")

        if status:
            query = query.eq("status", status)
        if severity:
            query = query.eq("severity", severity)

        response = query.order("triggered_at", desc=True).range(
            offset, offset + page_size - 1
        ).execute()
        if response.error:
            raise RuntimeError("Incident query failed")

        items = []
        for inc in jsonable_encoder(response.data or []):
            items.append(IncidentItem(
                id=inc["id"],
                title=inc["title"],
                severity=inc.get("severity", "info"),
                status=inc.get("status", "open"),
                description=inc.get("description"),
                triggered_at=inc.get("triggered_at") or inc.get("created_at", ""),
                acknowledged_at=inc.get("acknowledged_at"),
                acknowledged_by=inc.get("acknowledged_by"),
                resolved_at=inc.get("resolved_at"),
                resolved_by=inc.get("resolved_by")
            ))

        open_response = db_client.table("incidents").select(
            "id", count="exact"
        ).eq("status", "open").execute()
        if open_response.error:
            raise RuntimeError("Open incident count failed")

        critical_response = db_client.table("incidents").select(
            "id", count="exact"
        ).eq("severity", "critical").eq("status", "open").execute()
        if critical_response.error:
            raise RuntimeError("Critical incident count failed")

        return IncidentListResponse(
            items=items,
            total=response.count or 0,
            page=page,
            page_size=page_size,
            open_count=open_response.count or 0,
            critical_count=critical_response.count or 0
        )

    except Exception:
        logger.exception("Failed to list persisted incidents")
        raise HTTPException(
            status_code=503,
            detail="Incident data is temporarily unavailable"
        ) from None


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """Get incident details."""
    try:
        response = db_client.table("incidents").select("*").eq(
            "id", incident_id
        ).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Incident not found")

        return response.data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get incident: {str(e)}"
        )


@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """Mark incident as acknowledged."""
    try:
        now = datetime.utcnow().isoformat() + "Z"

        response = db_client.table("incidents").update({
            "status": "acknowledged",
            "acknowledged_at": now,
            "acknowledged_by": admin_user.id if hasattr(admin_user, 'id') else None
        }).eq("id", incident_id).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Incident not found")

        return {
            "success": True,
            "message": "Incident acknowledged",
            "incident_id": incident_id,
            "acknowledged_at": now
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to acknowledge incident: {str(e)}"
        )


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """Mark incident as resolved."""
    try:
        now = datetime.utcnow().isoformat() + "Z"

        response = db_client.table("incidents").update({
            "status": "resolved",
            "resolved_at": now,
            "resolved_by": admin_user.id if hasattr(admin_user, 'id') else None
        }).eq("id", incident_id).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Incident not found")

        return {
            "success": True,
            "message": "Incident resolved",
            "incident_id": incident_id,
            "resolved_at": now
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resolve incident: {str(e)}"
        )
