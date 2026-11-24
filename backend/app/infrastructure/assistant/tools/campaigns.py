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
    confirm: bool = Field(False, description="Preview the start; the authenticated user applies it to dial.")


def _get_campaign_service(db_client: Client) -> CampaignService:
    """Use the same queue-backed domain service as the HTTP campaign endpoint."""
    from app.core.container import get_container

    container = get_container()
    queue_service = container.queue_service if container.is_initialized else None
    return CampaignService(db_client, queue_service=queue_service)


def campaign_names(tenant_id: str, db_client: Client, campaign_ids) -> Dict[str, str]:
    """Map this tenant's campaign ids to names so every row can say which
    campaign it belongs to. Ids of another tenant's campaigns are never named."""
    ids = sorted({str(i) for i in campaign_ids if i})
    if not ids:
        return {}
    response = db_client.table("campaigns").select("id, name").eq(
        "tenant_id", tenant_id
    ).in_("id", ids).execute()
    return {str(row["id"]): row.get("name") for row in (response.data or [])}


async def get_campaigns(
    tenant_id: str,
    db_client: Client,
    status: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get campaigns for the tenant, optionally only those whose name contains ``name``.
    """
    try:
        query = db_client.table("campaigns").select(
            "id, name, status, direction, goal, total_leads, calls_completed, calls_failed, created_at",
            count="exact"
        ).eq("tenant_id", tenant_id)

        if status:
            query = query.eq("status", status)
        if name and str(name).strip():
            escaped = str(name).strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.ilike("name", f"%{escaped}%")

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
    conversation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Start or resume a campaign. Without ``confirm`` this only previews: starting
    dials real people, so it runs only from an applied proposal or plan.
    """
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.get_campaign(campaign_id, tenant_id=tenant_id)
        refusal = outbound_campaign_refusal([campaign], include_success=True)
        if refusal:
            return refusal

        current_status = campaign.get("status")
        if not confirm:
            from app.domain.services.dialer.permission_gate import contact_permission_dial_gate_mode

            if contact_permission_dial_gate_mode() == "on":
                question = (
                    f"Start campaign '{campaign.get('name')}'? It will begin calling its contacts "
                    "whose list has a recorded permission to call; the others are held back "
                    "until permission is confirmed."
                )
            else:
                question = f"Start campaign '{campaign.get('name')}'? It will begin calling its contacts."
            return {
                "success": True, "status": "preview", "confirmation_allowed": False,
                "message": question,
                "preview": True,
                "changes": [
                    {"field": "Campaign", "before": None, "after": campaign.get("name") or campaign_id},
                    {"field": "Status", "before": current_status, "after": "running"},
                ],
                "_apply_args": {"campaign_id": campaign_id},
            }
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

        answer = {
            "success": True,
            "message": f"Campaign '{campaign.get('name')}' has been started",
            "campaign_id": campaign_id,
            "jobs_enqueued": started.jobs_enqueued,
        }
        withheld = int(getattr(started, "jobs_withheld", 0) or 0)
        if withheld:
            # The permission check held contacts back. ``notice`` is what the
            # confirm card shows and voice mode speaks beside "Applied".
            note = CampaignService._withheld_note(withheld).strip()
            if getattr(started, "leads_selected", None) == 0:
                # This start selected nobody: every pending contact was held back.
                # The campaign is running (they are queued once permission is
                # confirmed; retries already scheduled still go out), so do not
                # say calling started. ``jobs_enqueued == 0`` is NOT this test: a
                # large start and a resume report 0 while new calls go out.
                answer["message"] = (
                    f"Campaign '{campaign.get('name')}' is running, but no contact was queued. {note}"
                )
                answer["notice"] = f"No contact was queued. {note}"
            else:
                answer["message"] += f". {note}"
                answer["notice"] = note
            answer["jobs_withheld"] = withheld
        return answer
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
