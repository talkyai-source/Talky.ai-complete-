from app.core.postgres_adapter import Client
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
import asyncpg  # migrated from db_client

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.services.queue_service import DialerQueueService
from app.api.v1.dependencies import get_db_client

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

# Retry configuration
RETRY_DELAY_SECONDS = 7200       # 2 hours between retries
MAX_RETRY_ATTEMPTS = 3           # Maximum retry attempts per job


class VonageAnswerResponse(BaseModel):
    """NCCO response for Vonage answer webhook"""
    action: str = "connect"


@router.post("/vonage/answer")
async def vonage_answer(
    request: Request,
    db_client: Client = Depends(get_db_client)
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
    db_client: Client = Depends(get_db_client)
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
        
        # --- Day 1: Log raw webhook event (additive, non-blocking) ---
        if call_uuid:
            try:
                from app.domain.repositories.call_event_repository import CallEventRepository
                event_repo = CallEventRepository(db_client)
                await event_repo.log_event(
                    call_id=call_uuid,
                    event_type="webhook_received",
                    source="vonage_webhook",
                    event_data={
                        "vonage_status": status,
                        "direction": direction,
                        "duration": duration,
                        "raw_keys": list(data.keys()),
                    },
                )
            except Exception:
                pass  # Non-critical — never block webhook processing
        
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
            db_client=db_client
        )
        
        return {"message": f"Event processed: {status}"}
        
    except Exception as e:
        logger.error(f"Error in vonage_event: {e}", exc_info=True)
        return {"message": "Event received (error processing)"}


async def handle_call_status(
    call_uuid: str,
    outcome: CallOutcome,
    duration: Optional[int],
    db_client: Client
) -> None:
    """
    Handle call status update — delegates to CallService.
    
    Kept as a module-level function for backward compatibility with
    vonage_event() which calls it directly.
    """
    from app.core.container import get_container
    container = get_container()
    try:
        call_service = container.call_service
        await call_service.handle_call_status(call_uuid, outcome, duration)
    except RuntimeError:
        # Fallback if container not fully initialized
        logger.error(f"CallService unavailable for call {call_uuid}, container not ready")


# NOTE: handle_job_completion logic has been moved to CallService._handle_job_completion().
# It is no longer called directly from this module — CallService handles it internally
# as part of handle_call_status().


@router.post("/vonage/rtc")
async def vonage_rtc(request: Request):
    """Handle Vonage RTC events"""
    data = await request.json()
    logger.debug(f"Vonage RTC event: {data}")
    return {"message": "RTC event received"}


@router.post("/call/goal-achieved")
async def mark_goal_achieved(
    request: Request,
    db_client: Client = Depends(get_db_client)
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
        
        from app.core.container import get_container
        call_service = get_container().call_service
        result = await call_service.mark_goal_achieved(call_id)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking goal achieved: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark goal achieved")


@router.post("/call/mark-spam")
async def mark_as_spam(
    request: Request,
    db_client: Client = Depends(get_db_client)
):
    """
    Mark a call/lead as spam - prevents future calls.
    
    Called when a number is identified as spam or invalid.
    """
    try:
        data = await request.json()
        call_id = data.get("call_id")
        lead_id = data.get("lead_id")
        reason = data.get("reason", "spam")
        
        from app.core.container import get_container
        call_service = get_container().call_service
        result = await call_service.mark_as_spam(
            call_id=call_id,
            lead_id=lead_id,
            reason=reason
        )
        return result
        
    except Exception as e:
        logger.error(f"Error marking as spam: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to mark as spam")

