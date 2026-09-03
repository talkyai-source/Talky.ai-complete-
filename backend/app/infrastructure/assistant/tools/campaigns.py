"""
Campaign query and action tools for the assistant agent.
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client
from app.domain.services.campaign_service import (
    CampaignError,
    CampaignNotFoundError,
    CampaignService,
    CampaignStateError,
)
from app.infrastructure.assistant.tools.campaign_direction import (
    outbound_campaign_refusal,
)

logger = logging.getLogger(__name__)


class StartCampaignInput(BaseModel):
    """Input for start_campaign tool"""
    campaign_id: str


def _get_campaign_service(db_client: Client) -> CampaignService:
    """Use the same queue-backed domain service as the HTTP campaign endpoint."""
    from app.core.container import get_container

    container = get_container()
    queue_service = container.queue_service if container.is_initialized else None
    return CampaignService(db_client, queue_service=queue_service)


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
            "id, name, status, direction, goal, total_leads, calls_completed, calls_failed, created_at",
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
        service = _get_campaign_service(db_client)
        campaign = await service.get_campaign(campaign_id, tenant_id=tenant_id)
        refusal = outbound_campaign_refusal([campaign], include_success=True)
        if refusal:
            return refusal

        current_status = campaign.get("status")
        started = await service.start_campaign(campaign_id, tenant_id=tenant_id)
        if not started.success:
            return {
                "success": False,
                "error": started.error or started.message,
                "campaign_id": campaign_id,
            }

        # Log action
        db_client.table("assistant_actions").insert({
            "tenant_id": tenant_id,
            "type": "start_campaign",
            "status": "completed",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "campaign_id": campaign_id,
            "input_data": json.dumps({"campaign_id": campaign_id}),
            "output_data": json.dumps({
                "previous_status": current_status,
                "jobs_enqueued": started.jobs_enqueued,
            }),
            "completed_at": datetime.utcnow().isoformat()
        }).execute()

        return {
            "success": True,
            "message": f"Campaign '{campaign.get('name')}' has been started",
            "campaign_id": campaign_id,
            "jobs_enqueued": started.jobs_enqueued,
        }
    except CampaignNotFoundError:
        return {"success": False, "error": "Campaign not found"}
    except CampaignStateError as exc:
        if "inbound campaign" in exc.message.lower():
            refusal = outbound_campaign_refusal(
                [{"id": campaign_id, "direction": "inbound"}],
                include_success=True,
            )
            if refusal:
                return refusal
        return {"success": False, "error": exc.message}
    except CampaignError as exc:
        return {"success": False, "error": exc.message}
    except Exception as e:
        logger.error(f"Error starting campaign: {e}")
        return {"success": False, "error": str(e)}
