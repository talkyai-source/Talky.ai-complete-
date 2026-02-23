"""
Lead Repository
PostgreSQL implementation of lead data access.

Encapsulates all leads-table interactions, replacing direct PostgreSQL queries
in webhooks.py, calls.py, and campaign endpoints.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.postgres_adapter import Client

from app.domain.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class LeadRepository(BaseRepository[dict]):
    """
    Repository for lead records.
    
    Wraps PostgreSQL interactions for the `leads` table.
    """
    
    TABLE = "leads"
    
    def __init__(self, db_client: Client):
        self._db_client = db_client
    
    async def get_by_id(self, entity_id: str) -> Optional[dict]:
        """Get a lead by ID."""
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
        """List leads with optional filtering."""
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
        """Create a new lead record."""
        data.setdefault("created_at", datetime.utcnow().isoformat())
        data.setdefault("updated_at", datetime.utcnow().isoformat())
        response = self._db_client.table(self.TABLE).insert(data).execute()
        return response.data[0] if response.data else data
    
    async def update(self, entity_id: str, data: Dict[str, Any]) -> Optional[dict]:
        """Update a lead record."""
        data["updated_at"] = datetime.utcnow().isoformat()
        response = self._db_client.table(self.TABLE).update(data).eq(
            "id", entity_id
        ).execute()
        return response.data[0] if response.data else None
    
    async def delete(self, entity_id: str) -> bool:
        """Delete a lead record."""
        response = self._db_client.table(self.TABLE).delete().eq(
            "id", entity_id
        ).execute()
        return bool(response.data)
    
    # =========================================================================
    # Domain-Specific Queries
    # =========================================================================
    
    async def get_by_campaign(
        self,
        campaign_id: str,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[dict]:
        """Get leads for a campaign, optionally filtered by status."""
        filters = {"campaign_id": campaign_id}
        if status:
            filters["status"] = status
        return await self.list(filters=filters, limit=limit, offset=offset)
    
    async def update_call_result(
        self,
        lead_id: str,
        status: str,
        last_call_result: str,
        increment_attempts: bool = True
    ) -> Optional[dict]:
        """
        Update lead after a call — sets status, last_call_result,
        and optionally increments call_attempts.
        """
        # Get current attempts if incrementing
        current_attempts = 0
        if increment_attempts:
            lead = await self.get_by_id(lead_id)
            if lead:
                current_attempts = lead.get("call_attempts", 0)
        
        update_data = {
            "status": status,
            "last_call_result": last_call_result,
            "last_called_at": datetime.utcnow().isoformat(),
        }
        if increment_attempts:
            update_data["call_attempts"] = current_attempts + 1
        
        return await self.update(lead_id, update_data)
    
    async def mark_dnc(self, lead_id: str) -> Optional[dict]:
        """Mark a lead as Do Not Call."""
        return await self.update(lead_id, {"status": "dnc"})
    
    async def get_pending_count(self, campaign_id: str) -> int:
        """Get count of pending leads for a campaign."""
        response = self._db_client.table(self.TABLE).select(
            "*", count="exact"
        ).eq("campaign_id", campaign_id).eq("status", "pending").execute()
        return response.count or 0
