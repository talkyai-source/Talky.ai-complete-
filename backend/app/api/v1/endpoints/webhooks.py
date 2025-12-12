"""
Webhooks API Endpoints
Handles incoming webhooks from telephony providers (Vonage)

Updated for Dialer Engine - handles call status and retry logic
"""
import os
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from supabase import Client

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.services.queue_service import DialerQueueService
from app.api.v1.dependencies import get_supabase

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# Vonage status to our CallOutcome mapping
VONAGE_STATUS_MAP = {
    "started": None,  # Call initiated, not an outcome yet
    "ringing": None,  # Still ringing
    "answered": CallOutcome.ANSWERED,
    "completed": CallOutcome.GOAL_NOT_ACHIEVED,  # Default, may be updated
    "busy": CallOutcome.BUSY,
    "timeout": CallOutcome.NO_ANSWER,
    "failed": CallOutcome.FAILED,
    "rejected": CallOutcome.REJECTED,
    "unanswered": CallOutcome.NO_ANSWER,
    "cancelled": CallOutcome.FAILED,
    "machine": CallOutcome.VOICEMAIL,
}

# Outcomes that should trigger retry (busy, no answer, voicemail)
RETRYABLE_OUTCOMES = {
    CallOutcome.BUSY,
    CallOutcome.NO_ANSWER,
    CallOutcome.FAILED,
    CallOutcome.VOICEMAIL,
}

# Outcomes that should NOT retry
NON_RETRYABLE_OUTCOMES = {
    CallOutcome.SPAM,
    CallOutcome.INVALID,
    CallOutcome.UNAVAILABLE,
    CallOutcome.DISCONNECTED,
    CallOutcome.REJECTED,
    CallOutcome.GOAL_ACHIEVED,
}


class VonageAnswerResponse(BaseModel):
    """NCCO response for Vonage answer webhook"""
    action: str = "connect"


@router.post("/vonage/answer")
async def vonage_answer(
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """
    Handle Vonage call answer webhook.
    
    Called when an outbound call is initiated. Returns NCCO to connect
    the call to a WebSocket for voice processing.
    """
    try:
        data = await request.json()
        
        call_uuid = data.get("uuid")
        to_number = data.get("to")
        from_number = data.get("from")
        
        logger.info(f"Vonage answer webhook: call_uuid={call_uuid}, to={to_number}")
        
        # Get WebSocket URL for voice processing
        ws_host = os.getenv("WEBSOCKET_HOST", "localhost:8000")
        ws_url = f"wss://{ws_host}/api/v1/ws/voice/{call_uuid}"
        
        # Return NCCO to connect call to WebSocket
        ncco = [
            {
                "action": "connect",
                "eventUrl": [f"{os.getenv('API_BASE_URL', 'http://localhost:8000')}/api/v1/webhooks/vonage/event"],
                "from": from_number,
                "endpoint": [
                    {
                        "type": "websocket",
                        "uri": ws_url,
                        "content-type": "audio/l16;rate=16000",
                        "headers": {
                            "call_uuid": call_uuid
                        }
                    }
                ]
            }
        ]
        
        return ncco
        
    except Exception as e:
        logger.error(f"Error in vonage_answer: {e}", exc_info=True)
        # Return empty NCCO on error
        return []


@router.post("/vonage/event")
async def vonage_event(
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """
    Handle Vonage call events.
    
    Processes call status changes:
    - answered: Call was picked up
    - completed: Call ended normally
    - busy: Line was busy
    - failed: Call failed
    - timeout/unanswered: No answer
    
    Updates call record, lead status, and triggers retry if needed.
    """
    try:
        data = await request.json()
        
        call_uuid = data.get("uuid") or data.get("conversation_uuid")
        status = data.get("status")
        direction = data.get("direction")
        duration = data.get("duration")
        
        logger.info(
            f"Vonage event: call_uuid={call_uuid}, status={status}, "
            f"direction={direction}, duration={duration}"
        )
        
        if not call_uuid or not status:
            return {"message": "Event received (missing data)"}
        
        # Map Vonage status to our outcome
        outcome = VONAGE_STATUS_MAP.get(status)
        
        if outcome is None:
            # Status that doesn't need processing (ringing, started)
            return {"message": f"Event received: {status}"}
        
        # Process the call status
        await handle_call_status(
            call_uuid=call_uuid,
            outcome=outcome,
            duration=duration,
            supabase=supabase
        )
        
        return {"message": f"Event processed: {status}"}
        
    except Exception as e:
        logger.error(f"Error in vonage_event: {e}", exc_info=True)
        return {"message": "Event received (error processing)"}


async def handle_call_status(
    call_uuid: str,
    outcome: CallOutcome,
    duration: Optional[int],
    supabase: Client
) -> None:
    """
    Handle call status update.
    
    1. Update call record in database
    2. Update lead status
    3. Update dialer job status
    4. Trigger retry if needed
    """
    try:
        # 1. Get call record
        call_response = supabase.table("calls").select(
            "*, dialer_job_id, campaign_id, lead_id"
        ).eq("id", call_uuid).execute()
        
        if not call_response.data:
            logger.warning(f"Call not found: {call_uuid}")
            return
        
        call = call_response.data[0]
        job_id = call.get("dialer_job_id")
        campaign_id = call.get("campaign_id")
        lead_id = call.get("lead_id")
        
        # 2. Update call record
        call_update = {
            "status": "completed",
            "outcome": outcome.value if hasattr(outcome, 'value') else str(outcome),
            "ended_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        if duration:
            call_update["duration_seconds"] = int(duration)
        
        supabase.table("calls").update(call_update).eq("id", call_uuid).execute()
        
        # 3. Update lead status and last_call_result
        lead_status = "called"
        # Map outcome to readable last_call_result
        last_call_result = outcome.value if hasattr(outcome, 'value') else str(outcome)
        
        if outcome == CallOutcome.ANSWERED:
            lead_status = "contacted"
        elif outcome == CallOutcome.GOAL_ACHIEVED:
            lead_status = "completed"
            last_call_result = "goal_achieved"
        elif outcome in NON_RETRYABLE_OUTCOMES:
            lead_status = "dnc"  # Do not call
        
        # Day 9: Update lead with last_call_result for quick status lookup
        try:
            # Get current call_attempts first
            lead_data = supabase.table("leads").select("call_attempts").eq("id", lead_id).execute()
            current_attempts = lead_data.data[0].get("call_attempts", 0) if lead_data.data else 0
            
            supabase.table("leads").update({
                "status": lead_status,
                "last_call_result": last_call_result,
                "last_called_at": datetime.utcnow().isoformat(),
                "call_attempts": current_attempts + 1,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
        except Exception as lead_update_error:
            logger.error(f"Failed to update lead {lead_id}: {lead_update_error}")
        
        # 4. Handle dialer job
        if job_id:
            await handle_job_completion(
                job_id=job_id,
                outcome=outcome,
                campaign_id=campaign_id,
                lead_id=lead_id,
                supabase=supabase
            )
        
        # 5. Update campaign counters
        if campaign_id:
            if outcome == CallOutcome.GOAL_ACHIEVED:
                supabase.rpc("increment_campaign_counter", {
                    "p_campaign_id": campaign_id,
                    "p_counter": "calls_completed"
                }).execute()
            elif outcome in NON_RETRYABLE_OUTCOMES:
                supabase.rpc("increment_campaign_counter", {
                    "p_campaign_id": campaign_id,
                    "p_counter": "calls_failed"
                }).execute()
        
        logger.info(f"Call {call_uuid} status updated: {outcome}")
        
    except Exception as e:
        logger.error(f"Error handling call status: {e}", exc_info=True)


async def handle_job_completion(
    job_id: str,
    outcome: CallOutcome,
    campaign_id: str,
    lead_id: str,
    supabase: Client
) -> None:
    """
    Handle dialer job completion - decide retry or complete.
    
    Retry logic:
    - Retry if: busy, no_answer, voicemail, failed
    - Don't retry if: spam, invalid, unavailable, goal_achieved, max attempts
    - Retry delay: 2 hours
    - Max attempts: 3
    """
    try:
        # Get job details
        job_response = supabase.table("dialer_jobs").select("*").eq("id", job_id).execute()
        
        if not job_response.data:
            logger.warning(f"Dialer job not found: {job_id}")
            return
        
        job_data = job_response.data[0]
        attempt_number = job_data.get("attempt_number", 1)
        tenant_id = job_data.get("tenant_id", "default-tenant")
        max_attempts = 3  # Could be from tenant config
        
        # Check if goal was achieved
        goal_achieved = outcome == CallOutcome.GOAL_ACHIEVED
        
        # Determine if we should retry
        should_retry = False
        final_status = JobStatus.COMPLETED
        
        if goal_achieved:
            final_status = JobStatus.GOAL_ACHIEVED
        elif outcome in NON_RETRYABLE_OUTCOMES:
            final_status = JobStatus.NON_RETRYABLE
        elif outcome in RETRYABLE_OUTCOMES and attempt_number < max_attempts:
            should_retry = True
            final_status = JobStatus.RETRY_SCHEDULED
        else:
            final_status = JobStatus.FAILED
        
        # Update job in database
        job_update = {
            "status": final_status.value if hasattr(final_status, 'value') else str(final_status),
            "last_outcome": outcome.value if hasattr(outcome, 'value') else str(outcome),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        if not should_retry:
            job_update["completed_at"] = datetime.utcnow().isoformat()
        
        supabase.table("dialer_jobs").update(job_update).eq("id", job_id).execute()
        
        # Schedule retry if needed
        if should_retry:
            logger.info(f"Scheduling retry for job {job_id} (attempt {attempt_number + 1})")
            
            # Create new job for retry with 2 hour delay
            retry_job = DialerJob(
                job_id=job_id,
                campaign_id=campaign_id,
                lead_id=lead_id,
                tenant_id=tenant_id,
                phone_number=job_data.get("phone_number", ""),
                priority=job_data.get("priority", 5),
                status=JobStatus.RETRY_SCHEDULED,
                attempt_number=attempt_number + 1,
                last_outcome=outcome
            )
            
            # Enqueue retry
            queue_service = DialerQueueService()
            await queue_service.initialize()
            await queue_service.schedule_retry(retry_job, delay_seconds=7200)  # 2 hours
            await queue_service.close()
        
        logger.info(
            f"Job {job_id} completed: outcome={outcome}, "
            f"final_status={final_status}, retry={should_retry}"
        )
        
    except Exception as e:
        logger.error(f"Error handling job completion: {e}", exc_info=True)


@router.post("/vonage/rtc")
async def vonage_rtc(request: Request):
    """Handle Vonage RTC events"""
    data = await request.json()
    logger.debug(f"Vonage RTC event: {data}")
    return {"message": "RTC event received"}


@router.post("/call/goal-achieved")
async def mark_goal_achieved(
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """
    Mark a call as having achieved its goal.
    
    Called by the voice pipeline when conversation goal is reached.
    This prevents future retry attempts.
    """
    try:
        data = await request.json()
        call_id = data.get("call_id")
        
        if not call_id:
            raise HTTPException(status_code=400, detail="call_id required")
        
        # Update call
        supabase.table("calls").update({
            "goal_achieved": True,
            "outcome": CallOutcome.GOAL_ACHIEVED.value,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", call_id).execute()
        
        # Update dialer job
        call_response = supabase.table("calls").select("dialer_job_id").eq("id", call_id).execute()
        if call_response.data and call_response.data[0].get("dialer_job_id"):
            job_id = call_response.data[0]["dialer_job_id"]
            supabase.table("dialer_jobs").update({
                "status": JobStatus.GOAL_ACHIEVED.value,
                "last_outcome": CallOutcome.GOAL_ACHIEVED.value,
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", job_id).execute()
        
        logger.info(f"Goal achieved for call {call_id}")
        
        return {"message": "Goal marked as achieved", "call_id": call_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking goal achieved: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/call/mark-spam")
async def mark_as_spam(
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """
    Mark a call/lead as spam - prevents future calls.
    
    Called when a number is identified as spam or invalid.
    """
    try:
        data = await request.json()
        call_id = data.get("call_id")
        lead_id = data.get("lead_id")
        reason = data.get("reason", "spam")  # spam, invalid, unavailable
        
        # Determine outcome
        outcome_map = {
            "spam": CallOutcome.SPAM,
            "invalid": CallOutcome.INVALID,
            "unavailable": CallOutcome.UNAVAILABLE,
            "disconnected": CallOutcome.DISCONNECTED
        }
        outcome = outcome_map.get(reason, CallOutcome.SPAM)
        
        if call_id:
            supabase.table("calls").update({
                "outcome": outcome.value,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", call_id).execute()
            
            # Get lead_id from call if not provided
            if not lead_id:
                call_response = supabase.table("calls").select("lead_id").eq("id", call_id).execute()
                if call_response.data:
                    lead_id = call_response.data[0].get("lead_id")
        
        if lead_id:
            supabase.table("leads").update({
                "status": "dnc",  # Do not call
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
        
        logger.info(f"Marked as {reason}: call={call_id}, lead={lead_id}")
        
        return {"message": f"Marked as {reason}", "call_id": call_id, "lead_id": lead_id}
        
    except Exception as e:
        logger.error(f"Error marking as spam: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

