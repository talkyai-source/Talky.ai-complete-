"""GET /rbac/permissions — list available permissions, optionally filtered by resource."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.core.postgres_adapter import Client

from .schemas import PermissionResponse

router = APIRouter(tags=["rbac"])


@router.get("/permissions", response_model=List[PermissionResponse])
async def list_permissions(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    resource: Optional[str] = Query(None, description="Filter by resource"),
) -> List[PermissionResponse]:
    """List all available permissions."""
    async with db_client.pool.acquire() as conn:
        if resource:
            rows = await conn.fetch(
                """
                SELECT id, name, description, resource, action
                FROM permissions
                WHERE resource = $1
                ORDER BY resource, action
                """,
                resource,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, name, description, resource, action
                FROM permissions
                ORDER BY resource, action
                """
            )

    return [
        PermissionResponse(
            id=str(row["id"]),
            name=row["name"],
            description=row["description"],
            resource=row["resource"],
            action=row["action"],
        )
        for row in rows
    ]
