"""
Call query and action tools for the assistant agent.
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import date
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class InitiateCallInput(BaseModel):
    """Input for initiate_call tool"""
    phone_number: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    confirm: bool = Field(False, description="Preview the call; the authenticated user applies it to queue.")


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
    conversation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Initiate an outbound call.
    """
    job = None
    try:
        from app.core.db_utils import acquire_with_tenant
        from app.core.container import get_container
        from app.domain.models.dialer_job import DialerJob
        from app.domain.services.phone_number_normalizer import normalize_phone_for_capture
        if not campaign_id:
            return {"success": False, "error": "Select an active outbound campaign before placing a call."}
        phone = normalize_phone_for_capture(phone_number, None)
        pool = getattr(db_client, "pool", None)
        if pool is None:
            return {"success": False, "error": "The dialer database is unavailable."}
        # Lock the contact through the active-job check/insert so two distinct
        # approved dashboard proposals cannot originate simultaneous calls.
        async with acquire_with_tenant(pool, tenant_id) as conn:
            campaign = await conn.fetchrow("SELECT direction,status FROM campaigns WHERE id=$1::uuid AND tenant_id=$2::uuid FOR SHARE",campaign_id,tenant_id)
            if not campaign or campaign["direction"] != "outbound" or campaign["status"] not in {"running", "active"}:
                return {"success": False, "error": "The selected campaign must be an active outbound campaign."}
            lead = await conn.fetchrow("""
                SELECT id,phone_number,do_not_call,status FROM leads
                WHERE tenant_id=$1::uuid AND campaign_id=$2::uuid AND phone_number=$3
                  AND ($4::uuid IS NULL OR id=$4::uuid) FOR UPDATE
            """,tenant_id,campaign_id,phone,lead_id)
            if not lead or lead["do_not_call"] or lead["status"] == "deleted":
                return {"success": False, "error": "No callable contact matches this number in the selected campaign."}
            if await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM dialer_jobs WHERE tenant_id=$1::uuid AND lead_id=$2::uuid
                    AND status IN ('pending','queued','processing','calling','retry_scheduled'))
            """,tenant_id,str(lead["id"])):
                return {"success": False, "error": "This contact already has an active call job."}
            if not confirm:
                return {"success": True, "status": "preview", "confirmation_allowed": False,
                        "message": f"Call {phone} using the selected outbound campaign?",
                        "preview": True,
                        "changes": [
                            {"field": "Call number", "before": None, "after": phone},
                            {"field": "Campaign", "before": None, "after": campaign_id},
                            {"field": "Lead", "before": None, "after": str(lead["id"])},
                        ],
                        "_apply_args": {"phone_number": phone, "campaign_id": campaign_id,
                                        "lead_id": str(lead["id"])}}
            job = DialerJob(job_id=str(uuid4()),tenant_id=tenant_id,campaign_id=campaign_id,
                           lead_id=str(lead["id"]),phone_number=phone,scheduled_at=datetime.now(timezone.utc))
            await conn.execute("""
                INSERT INTO dialer_jobs (id,tenant_id,campaign_id,lead_id,phone_number,status,scheduled_at)
                VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'pending',$6)
            """,job.job_id,tenant_id,campaign_id,job.lead_id,phone,job.scheduled_at)
        queue = get_container().queue_service
        if not await queue.schedule_job_once(job,idempotency_key="assistant-call-"+job.job_id):
            return {"success":False,"status":"unknown","confirmation_allowed":False,"dialer_job_id":job.job_id,
                    "error":"Queue acceptance is unconfirmed. Review this job before placing another call."}
        async with acquire_with_tenant(pool,tenant_id) as conn:
            await conn.execute("UPDATE dialer_jobs SET status='queued' WHERE tenant_id=$1::uuid AND id=$2::uuid AND status='pending'",tenant_id,job.job_id)
        await queue.confirm_retry_once("assistant-call-"+job.job_id)
        return {"success":True,"status":"queued","confirmation_allowed":True,
                "dialer_job_id":job.job_id,"message":f"Call to {phone} has been queued.","phone_number":phone}
    except Exception as e:
        logger.error("Error initiating call: %s", type(e).__name__)
        return {"success": False, "status": "unknown" if confirm else "failed", "confirmation_allowed":False,
                "dialer_job_id": getattr(job, "job_id", None),
                "error": "Call scheduling could not be confirmed." if confirm else str(e)}
