"""
Lead query tools for the assistant agent.
"""
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class GetLeadsInput(BaseModel):
    """Input for get_leads tool"""
    campaign_id: Optional[str] = Field(None, description="Filter by campaign ID")
    status: Optional[str] = Field(None, description="Filter by status (pending, completed, failed)")
    limit: int = Field(10, description="Maximum number of leads to return")


async def get_leads(
    tenant_id: str,
    db_client: Client,
    campaign_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Get leads for the tenant with optional filters.
    """
    try:
        query = db_client.table("leads").select(
            "id, phone_number, first_name, last_name, email, status, priority, call_attempts, last_call_result",
            count="exact"
        ).eq("tenant_id", tenant_id)

        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
        if status:
            query = query.eq("status", status)

        response = query.order("created_at", desc=True).limit(limit).execute()

        return {
            "total_count": response.count,
            "returned_count": len(response.data),
            "leads": response.data
        }
    except Exception as e:
        logger.error(f"Error getting leads: {e}")
        return {"error": str(e)}
