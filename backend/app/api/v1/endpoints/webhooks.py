"""
Webhooks API Endpoints

Call lifecycle webhooks used by the dialer engine and voice pipeline.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends

from app.core.postgres_adapter import Client
from app.domain.models.dialer_job import CallOutcome
from app.api.v1.dependencies import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


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
