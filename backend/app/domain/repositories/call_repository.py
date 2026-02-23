"""
Call Repository
PostgreSQL implementation of call data access.

Encapsulates all calls-table interactions, replacing direct PostgreSQL queries
scattered across webhooks.py, calls.py, and call_service.py.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.postgres_adapter import Client

from app.domain.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class CallRepository(BaseRepository[dict]):
    """
    Repository for call records.
    
    Wraps PostgreSQL interactions for the `calls` table,
    providing a clean interface for CallService and endpoints.
    """
    
    TABLE = "calls"
    
    def __init__(self, db_client: Client):
        self._db_client = db_client
    
    async def get_by_id(self, entity_id: str) -> Optional[dict]:
        """Get a call by its UUID."""
        response = self._db_client.table(self.TABLE).select(
            "*, dialer_job_id, campaign_id, lead_id"
        ).eq("id", entity_id).execute()
        return response.data[0] if response.data else None
    
    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[dict]:
        """
        List calls with optional filtering.
        
        Supported filter keys:
        - campaign_id: filter by campaign
        - tenant_id: filter by tenant
        - status: filter by call status
        - outcome: filter by call outcome
        """
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
        """Create a new call record."""
        data.setdefault("created_at", datetime.utcnow().isoformat())
        data.setdefault("updated_at", datetime.utcnow().isoformat())
        response = self._db_client.table(self.TABLE).insert(data).execute()
        return response.data[0] if response.data else data
    
    async def update(self, entity_id: str, data: Dict[str, Any]) -> Optional[dict]:
        """Update a call record."""
        data["updated_at"] = datetime.utcnow().isoformat()
        response = self._db_client.table(self.TABLE).update(data).eq(
            "id", entity_id
        ).execute()
        return response.data[0] if response.data else None
    
    async def delete(self, entity_id: str) -> bool:
        """Delete a call record (rarely used)."""
        response = self._db_client.table(self.TABLE).delete().eq(
            "id", entity_id
        ).execute()
        return bool(response.data)
    
    # =========================================================================
    # Domain-Specific Queries
    # =========================================================================
    
    async def get_by_campaign(
        self, campaign_id: str, limit: int = 100, offset: int = 0
    ) -> List[dict]:
        """Get calls for a specific campaign."""
        return await self.list(
            filters={"campaign_id": campaign_id},
            limit=limit,
            offset=offset
        )
    
    async def get_by_lead(self, lead_id: str) -> List[dict]:
        """Get all calls for a specific lead."""
        return await self.list(filters={"lead_id": lead_id})
    
    async def update_status(
        self,
        call_id: str,
        status: str,
        outcome: str,
        duration: Optional[int] = None
    ) -> Optional[dict]:
        """Update call status and outcome."""
        update_data = {
            "status": status,
            "outcome": outcome,
            "ended_at": datetime.utcnow().isoformat(),
        }
        if duration is not None:
            update_data["duration_seconds"] = int(duration)
        
        return await self.update(call_id, update_data)
    
    async def mark_goal_achieved(self, call_id: str) -> Optional[dict]:
        """Mark a call as having achieved its goal."""
        return await self.update(call_id, {
            "goal_achieved": True,
            "outcome": "goal_achieved",
        })
    
    async def get_call_count(
        self,
        tenant_id: str,
        campaign_id: Optional[str] = None
    ) -> int:
        """Get count of calls, optionally filtered by campaign."""
        query = self._db_client.table(self.TABLE).select(
            "*", count="exact"
        ).eq("tenant_id", tenant_id)
        
        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
        
        response = query.execute()
        return response.count or 0
