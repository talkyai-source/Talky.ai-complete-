"""
Campaign query and action tools for the assistant agent.
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class StartCampaignInput(BaseModel):
    """Input for start_campaign tool"""
    campaign_id: str


async def get_campaigns(
    tenant_id: str,
    db_client: Client,
    status: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get campaigns for the tenant.
    """
    try:
        query = db_client.table("campaigns").select(
            "id, name, status, goal, total_leads, calls_completed, calls_failed, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id)

        if status:
            query = query.eq("status", status)

        response = query.order("created_at", desc=True).limit(20).execute()

        return {
            "total_count": response.count,
            "campaigns": response.data
        }
    except Exception as e:
        logger.error(f"Error getting campaigns: {e}")
        return {"error": str(e)}


async def start_campaign(
    tenant_id: str,
    db_client: Client,
    campaign_id: str,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Start or resume a campaign.
    """
    try:
        # Verify campaign belongs to tenant
        campaign = db_client.table("campaigns").select(
            "id, name, status"
        ).eq("id", campaign_id).eq("tenant_id", tenant_id).single().execute()

        if not campaign.data:
            return {"success": False, "error": "Campaign not found"}

        current_status = campaign.data.get("status")
        if current_status == "running":
            return {"success": False, "error": "Campaign is already running"}

        # Update campaign status
        db_client.table("campaigns").update({
            "status": "running",
            "started_at": datetime.utcnow().isoformat() if current_status == "draft" else None
        }).eq("id", campaign_id).execute()

        # Log action
        db_client.table("assistant_actions").insert({
            "tenant_id": tenant_id,
            "type": "start_campaign",
            "status": "completed",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "campaign_id": campaign_id,
            "input_data": json.dumps({"campaign_id": campaign_id}),
            "output_data": json.dumps({"previous_status": current_status}),
            "completed_at": datetime.utcnow().isoformat()
        }).execute()

        return {
            "success": True,
            "message": f"Campaign '{campaign.data.get('name')}' has been started",
            "campaign_id": campaign_id
        }
    except Exception as e:
        logger.error(f"Error starting campaign: {e}")
        return {"success": False, "error": str(e)}
