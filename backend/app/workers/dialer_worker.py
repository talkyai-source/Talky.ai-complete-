"""
Dialer Worker
Background worker for processing outbound call jobs

Run as separate process:
    python -m app.workers.dialer_worker
"""
import asyncio
import logging
import os
import signal
from datetime import datetime
from typing import Optional, List
import json

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    import redis.asyncio as redis
    from supabase import create_client, Client
except ImportError as e:
    raise ImportError(f"Required dependency not installed: {e}")

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.models.calling_rules import CallingRules
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.scheduling_rules import SchedulingRuleEngine


logger = logging.getLogger(__name__)

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class DialerWorker:
    """
    Background worker for processing dialer jobs.
    
    Responsibilities:
    - Dequeue jobs from Redis
    - Check scheduling rules (time window, concurrent limits)
    - Initiate outbound calls via telephony provider
    - Handle call results and schedule retries
    
    Architecture:
    - Runs as separate process from FastAPI
    - Connects to same Redis and Supabase instances
    - Publishes call events for Voice Worker to handle
    """
    
    # Worker configuration
    POLL_INTERVAL = 1.0  # Seconds between queue checks when empty
    SCHEDULED_CHECK_INTERVAL = 60  # Seconds between scheduled job checks
    MAX_CONSECUTIVE_ERRORS = 10
    
    # API base URL for webhooks
    API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
    
    def __init__(self):
        self.queue_service = DialerQueueService()
        self.rules_engine = SchedulingRuleEngine()
        
        self.running = False
        self._supabase: Optional[Client] = None
        self._redis: Optional[redis.Redis] = None
        
        # Stats
        self._jobs_processed = 0
        self._jobs_failed = 0
        self._last_scheduled_check = datetime.utcnow()
    
    async def initialize(self) -> None:
        """Initialize connections to Redis and Supabase."""
        logger.info("Initializing Dialer Worker...")
        
        # Initialize queue service (Redis)
        await self.queue_service.initialize()
        
        # Initialize Supabase client
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
        
        if not supabase_url or not supabase_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        
        self._supabase = create_client(supabase_url, supabase_key)
        
        # Initialize separate Redis connection for pub/sub
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis = await redis.from_url(redis_url, decode_responses=True)
        
        logger.info("Dialer Worker initialized successfully")
    
    async def run(self) -> None:
        """
        Main worker loop.
        
        Continuously:
        1. Process any due scheduled retries
        2. Dequeue and process jobs
        3. Handle errors gracefully
        """
        await self.initialize()
        
        self.running = True
        consecutive_errors = 0
        
        logger.info("Dialer Worker started - listening for jobs")
        
        while self.running:
            try:
                # 1. Check for due scheduled jobs (periodically)
                if (datetime.utcnow() - self._last_scheduled_check).total_seconds() > self.SCHEDULED_CHECK_INTERVAL:
                    moved = await self.queue_service.process_scheduled_jobs()
                    if moved > 0:
                        logger.info(f"Moved {moved} scheduled jobs to queue")
                    self._last_scheduled_check = datetime.utcnow()
                
                # 2. Get active tenants
                tenant_ids = await self._get_active_tenant_ids()
                
                # 3. Dequeue next job
                job = await self.queue_service.dequeue_job(
                    tenant_ids=tenant_ids,
                    timeout=5
                )
                
                if job:
                    await self.process_job(job)
                    consecutive_errors = 0
                else:
                    # No jobs available, wait before checking again
                    await asyncio.sleep(self.POLL_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("Worker received cancellation signal")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Worker error ({consecutive_errors}): {e}", exc_info=True)
                
                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    logger.critical("Too many consecutive errors, stopping worker")
                    break
                
                await asyncio.sleep(min(5 * consecutive_errors, 60))
        
        await self.shutdown()
    
    async def process_job(self, job: DialerJob) -> None:
        """
        Process a single dialer job.
        
        Steps:
        1. Get tenant calling rules
        2. Check if we can make call now
        3. Initiate the call
        4. Create call record in database
        """
        logger.info(f"Processing job {job.job_id} for lead {job.lead_id} (attempt {job.attempt_number})")
        
        try:
            # 1. Get tenant calling rules
            rules = await self._get_tenant_rules(job.tenant_id)
            
            # 2. Get lead info for cooldown check
            lead_last_called = await self._get_lead_last_called(job.lead_id)
            
            # 3. Check scheduling rules
            can_call, reason = await self.rules_engine.can_make_call(
                tenant_id=job.tenant_id,
                campaign_id=job.campaign_id,
                rules=rules,
                lead_last_called=lead_last_called
            )
            
            if not can_call:
                logger.info(f"Cannot call now: {reason}")
                
                # Calculate delay until next window or retry
                if "time_window" in reason or "day" in reason.lower():
                    delay = self.rules_engine.get_delay_until_next_window(rules)
                else:
                    delay = 300  # 5 minutes for other reasons (concurrent limit, etc.)
                
                await self.queue_service.schedule_retry(job, delay_seconds=delay)
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
                return
            
            # 4. Register call start (for concurrent tracking)
            self.rules_engine.register_call_start(job.tenant_id, job.campaign_id)
            
            try:
                # 5. Initiate the call
                call_id = await self._make_call(job, rules)
                
                if call_id:
                    # 6. Create call record in database
                    await self._create_call_record(job, call_id)
                    
                    # 7. Update lead status to 'calling'
                    await self._update_lead_status(job.lead_id, "calling")
                    
                    # 8. Update job with call reference
                    job.call_id = call_id
                    job.status = JobStatus.PROCESSING
                    job.processed_at = datetime.utcnow()
                    await self._update_job_status(job.job_id, JobStatus.PROCESSING, call_id=call_id)
                    
                    # 9. Notify voice worker about new call
                    await self._publish_call_event(call_id, job)
                    
                    self._jobs_processed += 1
                    logger.info(f"Call initiated: {call_id} for job {job.job_id}")
                else:
                    raise Exception("No call_id returned from telephony provider")
                    
            finally:
                # Unregister call (will be re-registered when answered if needed)
                # For now, we track at initiation level
                pass
                
        except Exception as e:
            self._jobs_failed += 1
            logger.error(f"Failed to process job {job.job_id}: {e}", exc_info=True)
            
            # Record failure and potentially schedule retry
            job.last_error = str(e)
            job.last_outcome = CallOutcome.FAILED
            
            should_retry, retry_reason = job.should_retry(goal_achieved=False)
            
            if should_retry:
                await self.queue_service.schedule_retry(job, delay_seconds=job.RETRY_DELAY_SECONDS)
                await self._update_job_status(job.job_id, JobStatus.RETRY_SCHEDULED, error=str(e))
            else:
                await self.queue_service.mark_failed(job.job_id, str(e))
                await self._update_job_status(job.job_id, JobStatus.FAILED, error=str(e))
    
    async def _make_call(self, job: DialerJob, rules: CallingRules) -> Optional[str]:
        """
        Initiate an outbound call via telephony provider.
        
        Returns:
            call_id (UUID) if successful, None otherwise
        """
        # TODO: Integrate with actual Vonage/telephony provider
        # For now, log the call attempt
        
        logger.info(
            f"CALL INITIATION: {job.phone_number} "
            f"(campaign={job.campaign_id}, lead={job.lead_id})"
        )
        
        # In production, this would call the telephony provider:
        # from app.infrastructure.telephony.vonage_caller import VonageCaller
        # caller = VonageCaller()
        # call_id = await caller.make_call(
        #     to_number=job.phone_number,
        #     from_number=rules.caller_id or os.getenv("DEFAULT_CALLER_ID"),
        #     webhook_url=f"{self.API_BASE_URL}/api/v1/webhooks/vonage/answer",
        #     metadata={"job_id": job.job_id}
        # )
        
        # For now, generate a mock call_id
        import uuid
        call_id = str(uuid.uuid4())
        
        return call_id
    
    async def _get_active_tenant_ids(self) -> List[str]:
        """Get list of tenants with active/running campaigns."""
        try:
            response = self._supabase.table("campaigns").select(
                "id"
            ).in_("status", ["running", "active"]).execute()
            
            if response.data:
                # Get unique tenant IDs from campaigns (if tenant_id exists)
                # For now, return empty list to check all queues
                pass
            
            # Return None to scan all tenant queues
            return None
            
        except Exception as e:
            logger.error(f"Failed to get active tenants: {e}")
            return None
    
    async def _get_tenant_rules(self, tenant_id: str) -> CallingRules:
        """Get calling rules for a tenant."""
        try:
            response = self._supabase.table("tenants").select(
                "calling_rules"
            ).eq("id", tenant_id).single().execute()
            
            if response.data and response.data.get("calling_rules"):
                return CallingRules.from_dict(response.data["calling_rules"])
            
        except Exception as e:
            logger.warning(f"Failed to get tenant rules, using defaults: {e}")
        
        return CallingRules.default()
    
    async def _get_lead_last_called(self, lead_id: str) -> Optional[datetime]:
        """Get the last time a lead was called."""
        try:
            response = self._supabase.table("leads").select(
                "last_called_at"
            ).eq("id", lead_id).single().execute()
            
            if response.data and response.data.get("last_called_at"):
                return datetime.fromisoformat(response.data["last_called_at"].replace("Z", "+00:00"))
            
        except Exception as e:
            logger.warning(f"Failed to get lead last_called_at: {e}")
        
        return None
    
    async def _create_call_record(self, job: DialerJob, call_id: str) -> None:
        """Create a call record in the database."""
        try:
            call_data = {
                "id": call_id,
                "campaign_id": job.campaign_id,
                "lead_id": job.lead_id,
                "phone_number": job.phone_number,
                "status": "initiated",
                "created_at": datetime.utcnow().isoformat(),
                "dialer_job_id": job.job_id
            }
            
            self._supabase.table("calls").insert(call_data).execute()
            logger.debug(f"Created call record: {call_id}")
            
        except Exception as e:
            logger.error(f"Failed to create call record: {e}")
    
    async def _update_lead_status(self, lead_id: str, status: str) -> None:
        """Update lead status in database."""
        try:
            self._supabase.table("leads").update({
                "status": status,
                "last_called_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
            
        except Exception as e:
            logger.error(f"Failed to update lead status: {e}")
    
    async def _update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        call_id: Optional[str] = None,
        error: Optional[str] = None,
        reason: Optional[str] = None
    ) -> None:
        """Update job status in database."""
        try:
            update_data = {
                "status": status.value if hasattr(status, 'value') else status,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            if call_id:
                update_data["call_id"] = call_id
                update_data["processed_at"] = datetime.utcnow().isoformat()
            
            if error:
                update_data["last_error"] = error
            
            if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.GOAL_ACHIEVED]:
                update_data["completed_at"] = datetime.utcnow().isoformat()
            
            self._supabase.table("dialer_jobs").update(update_data).eq("id", job_id).execute()
            
        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
    
    async def _publish_call_event(self, call_id: str, job: DialerJob) -> None:
        """Publish call event for voice worker to pick up."""
        try:
            event = {
                "event": "call_initiated",
                "call_id": call_id,
                "job_id": job.job_id,
                "campaign_id": job.campaign_id,
                "lead_id": job.lead_id,
                "tenant_id": job.tenant_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self._redis.publish("voice:calls:active", json.dumps(event))
            logger.debug(f"Published call event for {call_id}")
            
        except Exception as e:
            logger.error(f"Failed to publish call event: {e}")
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Dialer Worker...")
        self.running = False
        
        # Close connections
        await self.queue_service.close()
        if self._redis:
            await self._redis.close()
        
        # Log final stats
        logger.info(
            f"Dialer Worker shutdown complete. "
            f"Processed: {self._jobs_processed}, Failed: {self._jobs_failed}"
        )
    
    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            "running": self.running,
            "jobs_processed": self._jobs_processed,
            "jobs_failed": self._jobs_failed,
            "active_calls": {
                tenant_id: count 
                for tenant_id, count in self.rules_engine._active_calls.items()
            }
        }


async def main():
    """Entry point for running dialer worker as separate process."""
    worker = DialerWorker()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        worker.running = False
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
