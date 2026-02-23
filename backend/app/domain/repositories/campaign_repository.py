"""
Campaign Repository
PostgreSQL implementation of campaign data access.

Encapsulates all campaigns-table interactions, replacing direct PostgreSQL queries
in campaigns.py endpoints.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.postgres_adapter import Client

from app.domain.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class CampaignRepository(BaseRepository[dict]):
    """
    Repository for campaign records.

    Wraps PostgreSQL interactions for the `campaigns` table.
    """

    TABLE = "campaigns"

    def __init__(self, db_client: Client):
        self._db_client = db_client

    async def get_by_id(self, entity_id: str) -> Optional[dict]:
        """Get a campaign by ID."""
        response = self._db_client.table(self.TABLE).select("*").eq(
            "id", entity_id
        ).execute()
        return response.data[0] if response.data else None

    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[dict]:
        """List campaigns with optional filtering."""
        query = self._db_client.table(self.TABLE).select("*")

        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)

        query = query.range(offset, offset + limit - 1).order(
            "created_at", desc=True
        )
        response = query.execute()
        return response.data or []

    async def create(self, data: Dict[str, Any]) -> dict:
        """Create a new campaign."""
        data.setdefault("created_at", datetime.utcnow().isoformat())
        response = self._db_client.table(self.TABLE).insert(data).execute()
        return response.data[0] if response.data else data

    async def update(self, entity_id: str, data: Dict[str, Any]) -> Optional[dict]:
        """Update a campaign."""
        data["updated_at"] = datetime.utcnow().isoformat()
        response = self._db_client.table(self.TABLE).update(data).eq(
            "id", entity_id
        ).execute()
        return response.data[0] if response.data else None

    async def delete(self, entity_id: str) -> bool:
        """Delete a campaign."""
        response = self._db_client.table(self.TABLE).delete().eq(
            "id", entity_id
        ).execute()
        return bool(response.data)

    # =========================================================================
    # Domain-Specific Queries
    # =========================================================================

    async def get_by_tenant(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> List[dict]:
        """Get campaigns for a specific tenant."""
        return await self.list(
            filters={"tenant_id": tenant_id}, limit=limit, offset=offset
        )

    async def update_status(
        self,
        campaign_id: str,
        status: str,
        **extra_fields: Any
    ) -> Optional[dict]:
        """Update campaign status with optional extra fields."""
        update_data: Dict[str, Any] = {"status": status}
        update_data.update(extra_fields)
        return await self.update(campaign_id, update_data)
