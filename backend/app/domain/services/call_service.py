"""
Call Service
Domain service for call lifecycle management.

Extracts business logic from webhooks.py endpoints into a testable,
reusable service following the Domain-Driven Design pattern established
by CampaignService.
"""
import logging
from datetime import datetime
from typing import Optional

from app.core.postgres_adapter import Client

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.services.queue_service import DialerQueueService
from app.domain.repositories.call_repository import CallRepository
from app.domain.repositories.lead_repository import LeadRepository

logger = logging.getLogger(__name__)


# Retry configuration (shared with webhooks.py constants)
RETRY_DELAY_SECONDS = 7200   # 2 hours between retries
MAX_RETRY_ATTEMPTS = 3       # Maximum retry attempts per job

# Outcomes that should trigger retry
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


class CallService:
    """
    Domain service for call lifecycle management.
    
    Handles:
    - Call status updates (from telephony webhooks)
    - Lead status synchronization
    - Dialer job completion and retry logic
    - Goal achievement and spam marking
    """
    
    def __init__(
        self,
        db_client: Client,
        queue_service: Optional[DialerQueueService] = None,
        call_repo: Optional[CallRepository] = None,
        lead_repo: Optional[LeadRepository] = None,
    ):
        self._db_client = db_client
        self._queue_service = queue_service
        self._call_repo = call_repo or CallRepository(db_client)
        self._lead_repo = lead_repo or LeadRepository(db_client)
    
    # =========================================================================
    # Call Status Handling
    # =========================================================================
    
    async def handle_call_status(
        self,
        call_uuid: str,
        outcome: CallOutcome,
        duration: Optional[int] = None
    ) -> None:
        """
        Handle a call status update from the telephony provider.
        
        Uses the atomic RPC function (update_call_status) when available,
        falling back to sequential writes for backward compatibility.
        
        Steps performed:
        1. Update call record + lead status (atomic via RPC, or sequential)
        2. Handle dialer job completion and retry logic
        3. Update campaign counters
        
        Args:
            call_uuid: Unique call identifier from telephony provider
            outcome: The call outcome (answered, busy, failed, etc.)
            duration: Call duration in seconds (if available)
        """
        try:
            outcome_value = outcome.value if hasattr(outcome, 'value') else str(outcome)
            
            # Try atomic RPC first (steps 1+2: call + lead in one transaction)
            rpc_result = await self._try_atomic_update(call_uuid, outcome_value, duration)
            
            if rpc_result:
                # RPC succeeded — extract metadata for job/campaign handling
                job_id = rpc_result.get("job_id")
                campaign_id = rpc_result.get("campaign_id")
            else:
                # Fallback: sequential writes (RPC not deployed yet)
                job_id, campaign_id = await self._sequential_update(
                    call_uuid, outcome, outcome_value, duration
                )
            
            # Handle dialer job completion (always done app-side for retry logic)
            if job_id:
                await self._handle_job_completion(
                    job_id=job_id,
                    outcome=outcome,
                    campaign_id=campaign_id or "",
                    lead_id=rpc_result.get("lead_id", "") if rpc_result else ""
                )
            
            # Update campaign counters
            if campaign_id:
                self._update_campaign_counters(campaign_id, outcome)
            
            logger.info(f"Call {call_uuid} status updated: {outcome}")
            
            # --- Day 1: Event logging (additive, non-blocking) ---
            try:
                from app.domain.repositories.call_event_repository import CallEventRepository
                event_repo = CallEventRepository(self._db_client)
                await event_repo.log_event(
                    call_id=call_uuid,
                    event_type="state_change",
                    source="call_service",
                    event_data={
                        "outcome": outcome_value,
                        "duration": duration,
                        "campaign_id": campaign_id,
                    },
                    new_state=outcome_value,
                )
            except Exception as evt_err:
                logger.debug(f"Event logging failed (non-critical): {evt_err}")
            
        except Exception as e:
            logger.error(f"Error handling call status for {call_uuid}: {e}", exc_info=True)
    
    async def _try_atomic_update(
        self, call_uuid: str, outcome_value: str, duration: Optional[int]
    ) -> Optional[dict]:
        """
        Try to use the atomic RPC function for call+lead update.
        Returns the RPC result dict on success, None if RPC unavailable.
        """
        try:
            rpc_params = {
                "p_call_uuid": call_uuid,
                "p_outcome": outcome_value,
            }
            if duration is not None:
                rpc_params["p_duration"] = int(duration)
            
            response = self._db_client.rpc("update_call_status", rpc_params).execute()
            
            if response.data and response.data.get("found"):
                logger.debug(f"Atomic RPC update succeeded for call {call_uuid}")
                return response.data
            elif response.data and not response.data.get("found"):
                logger.warning(f"Call not found via RPC: {call_uuid}")
                return None
            return None
        except Exception as e:
            # RPC not available (migration not applied) — fall back silently
            logger.debug(f"RPC update_call_status not available, using fallback: {e}")
            return None
    
    async def _sequential_update(
        self, call_uuid: str, outcome: CallOutcome, outcome_value: str,
        duration: Optional[int]
    ) -> tuple:
        """
        Fallback: sequential writes for call + lead update.
        Returns (job_id, campaign_id) for downstream processing.
        """
        # Get call record via repository
        call = await self._call_repo.get_by_id(call_uuid)
        
        if not call:
            logger.warning(f"Call not found: {call_uuid}")
            return None, None
        
        job_id = call.get("dialer_job_id")
        campaign_id = call.get("campaign_id")
        lead_id = call.get("lead_id")
        
        # Update call record via repository
        call_update = {
            "status": "completed",
            "outcome": outcome_value,
            "ended_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        if duration:
            call_update["duration_seconds"] = int(duration)
        
        await self._call_repo.update(call_uuid, call_update)
        
        # Update lead status via repository
        if lead_id:
            await self._update_lead_status(lead_id, outcome)
        
        return job_id, campaign_id
    
    async def _update_lead_status(self, lead_id: str, outcome: CallOutcome) -> None:
        """Update lead status and call tracking fields based on call outcome."""
        lead_status = "called"
        last_call_result = outcome.value if hasattr(outcome, 'value') else str(outcome)
        
        if outcome == CallOutcome.ANSWERED:
            lead_status = "contacted"
        elif outcome == CallOutcome.GOAL_ACHIEVED:
            lead_status = "completed"
            last_call_result = "goal_achieved"
        elif outcome in NON_RETRYABLE_OUTCOMES:
            lead_status = "dnc"  # Do not call
        
        try:
            # Get current call_attempts first
            lead_data = self._db_client.table("leads").select("call_attempts").eq("id", lead_id).execute()
            current_attempts = lead_data.data[0].get("call_attempts", 0) if lead_data.data else 0
            
            self._db_client.table("leads").update({
                "status": lead_status,
                "last_call_result": last_call_result,
                "last_called_at": datetime.utcnow().isoformat(),
                "call_attempts": current_attempts + 1,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
        except Exception as e:
            logger.error(f"Failed to update lead {lead_id}: {e}")
    
    def _update_campaign_counters(self, campaign_id: str, outcome: CallOutcome) -> None:
        """Update campaign completion/failure counters via PostgreSQL RPC."""
        try:
            if outcome == CallOutcome.GOAL_ACHIEVED:
                self._db_client.rpc("increment_campaign_counter", {
                    "p_campaign_id": campaign_id,
                    "p_counter": "calls_completed"
                }).execute()
            elif outcome in NON_RETRYABLE_OUTCOMES:
                self._db_client.rpc("increment_campaign_counter", {
                    "p_campaign_id": campaign_id,
                    "p_counter": "calls_failed"
                }).execute()
        except Exception as e:
            logger.error(f"Failed to update campaign counters for {campaign_id}: {e}")
    
    # =========================================================================
    # Job Completion & Retry Logic
    # =========================================================================
    
    async def _handle_job_completion(
        self,
        job_id: str,
        outcome: CallOutcome,
        campaign_id: str,
        lead_id: str
    ) -> None:
        """
        Handle dialer job completion — decide retry or finalize.
        
        Retry rules:
        - Retry if: busy, no_answer, voicemail, failed AND under max attempts
        - Don't retry if: spam, invalid, unavailable, goal_achieved, max attempts
        - Retry delay: RETRY_DELAY_SECONDS (2 hours)
        - Max attempts: MAX_RETRY_ATTEMPTS (3)
        """
        try:
            # Get job details
            job_response = self._db_client.table("dialer_jobs").select("*").eq("id", job_id).execute()
            
            if not job_response.data:
                logger.warning(f"Dialer job not found: {job_id}")
                return
            
            job_data = job_response.data[0]
            attempt_number = job_data.get("attempt_number", 1)
            tenant_id = job_data.get("tenant_id", "default-tenant")
            
            # Determine final status and whether to retry
            goal_achieved = outcome == CallOutcome.GOAL_ACHIEVED
            should_retry = False
            
            if goal_achieved:
                final_status = JobStatus.GOAL_ACHIEVED
            elif outcome in NON_RETRYABLE_OUTCOMES:
                final_status = JobStatus.NON_RETRYABLE
            elif outcome in RETRYABLE_OUTCOMES and attempt_number < MAX_RETRY_ATTEMPTS:
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
            
            self._db_client.table("dialer_jobs").update(job_update).eq("id", job_id).execute()
            
            # Schedule retry if needed
            if should_retry:
                await self._schedule_retry(job_id, job_data, outcome, campaign_id, lead_id,
                                           tenant_id, attempt_number)
            
            logger.info(
                f"Job {job_id} completed: outcome={outcome}, "
                f"final_status={final_status}, retry={should_retry}"
            )
            
        except Exception as e:
            logger.error(f"Error handling job completion for {job_id}: {e}", exc_info=True)
    
    async def _schedule_retry(
        self,
        job_id: str,
        job_data: dict,
        outcome: CallOutcome,
        campaign_id: str,
        lead_id: str,
        tenant_id: str,
        attempt_number: int
    ) -> None:
        """Schedule a retry for a failed dialer job."""
        logger.info(f"Scheduling retry for job {job_id} (attempt {attempt_number + 1})")
        
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
        
        if self._queue_service:
            await self._queue_service.schedule_retry(retry_job, delay_seconds=RETRY_DELAY_SECONDS)
        else:
            logger.error(f"Cannot schedule retry for job {job_id}: queue service unavailable")
    
    # =========================================================================
    # Goal Achievement & Spam Marking
    # =========================================================================
    
    async def mark_goal_achieved(self, call_id: str) -> dict:
        """
        Mark a call as having achieved its goal.
        
        Updates both the call record and the associated dialer job to prevent
        future retry attempts.
        
        Args:
            call_id: The call UUID
            
        Returns:
            dict with confirmation message
        """
        # Update call
        self._db_client.table("calls").update({
            "goal_achieved": True,
            "outcome": CallOutcome.GOAL_ACHIEVED.value,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", call_id).execute()
        
        # Update dialer job if exists
        call_response = self._db_client.table("calls").select("dialer_job_id").eq("id", call_id).execute()
        if call_response.data and call_response.data[0].get("dialer_job_id"):
            job_id = call_response.data[0]["dialer_job_id"]
            self._db_client.table("dialer_jobs").update({
                "status": JobStatus.GOAL_ACHIEVED.value,
                "last_outcome": CallOutcome.GOAL_ACHIEVED.value,
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", job_id).execute()
        
        logger.info(f"Goal achieved for call {call_id}")
        return {"message": "Goal marked as achieved", "call_id": call_id}
    
    async def mark_as_spam(
        self,
        call_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        reason: str = "spam"
    ) -> dict:
        """
        Mark a call/lead as spam — prevents future calls.
        
        Args:
            call_id: Optional call UUID
            lead_id: Optional lead UUID (resolved from call if not provided)
            reason: Reason for marking (spam, invalid, unavailable, disconnected)
            
        Returns:
            dict with confirmation
        """
        outcome_map = {
            "spam": CallOutcome.SPAM,
            "invalid": CallOutcome.INVALID,
            "unavailable": CallOutcome.UNAVAILABLE,
            "disconnected": CallOutcome.DISCONNECTED
        }
        outcome = outcome_map.get(reason, CallOutcome.SPAM)
        
        if call_id:
            self._db_client.table("calls").update({
                "outcome": outcome.value,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", call_id).execute()
            
            # Get lead_id from call if not provided
            if not lead_id:
                call_response = self._db_client.table("calls").select("lead_id").eq("id", call_id).execute()
                if call_response.data:
                    lead_id = call_response.data[0].get("lead_id")
        
        if lead_id:
            self._db_client.table("leads").update({
                "status": "dnc",
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
        
        logger.info(f"Marked as {reason}: call={call_id}, lead={lead_id}")
        return {"message": f"Marked as {reason}", "call_id": call_id, "lead_id": lead_id}


def get_call_service(db_client: Client, queue_service: Optional[DialerQueueService] = None) -> CallService:
    """Factory function for dependency injection."""
    return CallService(db_client=db_client, queue_service=queue_service)
