"""
Call query and action tools for the assistant agent.
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import date
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class InitiateCallInput(BaseModel):
    """Input for initiate_call tool"""
    phone_number: str
    campaign_id: Optional[str] = None


async def get_recent_calls(
    tenant_id: str,
    db_client: Client,
    today_only: bool = True,
    outcome: Optional[str] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Get recent calls for the tenant.
    """
    try:
        query = db_client.table("calls").select(
            "id, phone_number, status, outcome, goal_achieved, duration_seconds, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id)

        if today_only:
            today = date.today().isoformat()
            query = query.gte("created_at", f"{today}T00:00:00")

        if outcome:
            query = query.eq("outcome", outcome)

        response = query.order("created_at", desc=True).limit(limit).execute()

        return {
            "total_count": response.count,
            "calls": response.data
        }
    except Exception as e:
        logger.error(f"Error getting calls: {e}")
        return {"error": str(e)}


async def initiate_call(
    tenant_id: str,
    db_client: Client,
    phone_number: str,
    campaign_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Initiate an outbound call.
    """
    try:
        action_data = {
            "tenant_id": tenant_id,
            "type": "initiate_call",
            "status": "pending",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "input_data": json.dumps({
                "phone_number": phone_number,
                "campaign_id": campaign_id,
                "lead_id": lead_id
            })
        }

        action_response = db_client.table("assistant_actions").insert(action_data).execute()
        action_id = action_response.data[0]["id"] if action_response.data else None

        # TODO: Queue call via dialer worker

        return {
            "success": True,
            "action_id": action_id,
            "message": f"Call to {phone_number} has been queued",
            "phone_number": phone_number
        }
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        return {"success": False, "error": str(e)}
