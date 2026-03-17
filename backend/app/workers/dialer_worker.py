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
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.core.dotenv_compat import load_dotenv

# Load environment variables
load_dotenv()

try:
    import redis.asyncio as redis
    import asyncpg
except ImportError as e:
    raise ImportError(f"Required dependency not installed: {e}")

from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.models.calling_rules import CallingRules
from app.domain.models.voice_contract import generate_talklee_call_id
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.scheduling_rules import SchedulingRuleEngine
from app.core.db import init_db_pool, close_db_pool, Database

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
    - Connects to same Redis and PostgreSQL instances
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
        self._db_pool: Optional[asyncpg.Pool] = None
        self._redis: Optional[redis.Redis] = None
        
        # Stats
        self._jobs_processed = 0
        self._jobs_failed = 0
        self._last_scheduled_check = datetime.utcnow()
    
    async def initialize(self) -> None:
        """Initialize connections to Redis and PostgreSQL."""
        logger.info("Initializing Dialer Worker...")
        
        # Initialize queue service (Redis)
        await self.queue_service.initialize()
        
        # Initialize PostgreSQL pool
        self._db_pool = await init_db_pool()
        
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
                    # 6. Create call record in database (with talklee_call_id and PSTN leg)
                    talklee_call_id, leg_id = await self._create_call_record(job, call_id)
                    
                    # 7. Update lead status to 'calling'
                    await self._update_lead_status(job.lead_id, "calling")
                    
                    # 8. Update job with call reference
                    job.call_id = call_id
                    job.status = JobStatus.PROCESSING
                    job.processed_at = datetime.utcnow()
                    await self._update_job_status(job.job_id, JobStatus.PROCESSING, call_id=call_id)
                    
                    # 9. Notify voice worker about new call (include talklee_call_id)
                    await self._publish_call_event(call_id, job, talklee_call_id)
                    
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
        Initiate an outbound call via the active telephony provider.

        Uses ``TelephonyProviderFactory`` to select the configured provider
        (SIP/Asterisk/FreeSWITCH or Vonage) at runtime.

        Returns:
            call_id (UUID) if successful, None otherwise
        """
        from app.infrastructure.telephony.provider_factory import TelephonyProviderFactory

        try:
            provider = await TelephonyProviderFactory.create()
            webhook_base = os.getenv("API_BASE_URL", "http://localhost:8000")
            caller_id = rules.caller_id if hasattr(rules, "caller_id") else "1001"

            call_id = await provider.originate_call(
                destination=job.phone_number,
                caller_id=caller_id,
                webhook_base_url=webhook_base,
                metadata={"campaign_id": job.campaign_id, "lead_id": job.lead_id},
            )
            # Store which provider was used so DB records are accurate
            self._last_provider_name = provider.name

            if call_id:
                logger.info(
                    f"CALL INITIATED via {provider.name}: {job.phone_number} "
                    f"call_id={call_id[:8]}... "
                    f"(campaign={job.campaign_id}, lead={job.lead_id})"
                )
            else:
                logger.warning(
                    f"CALL FAILED via {provider.name}: {job.phone_number} "
                    f"(campaign={job.campaign_id}, lead={job.lead_id})"
                )
            return call_id
        except Exception as e:
            logger.error(f"Originate error for {job.phone_number}: {e}")
            return None
    
    async def _get_active_tenant_ids(self) -> List[str]:
        """Get list of tenants with active/running campaigns."""
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT tenant_id FROM campaigns WHERE status IN ('running', 'active')"
                )
                if rows:
                    return [str(r["tenant_id"]) for r in rows]
            return None
            
        except Exception as e:
            logger.error(f"Failed to get active tenants: {e}")
            return None
    
    async def _get_tenant_rules(self, tenant_id: str) -> CallingRules:
        """Get calling rules for a tenant."""
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT calling_rules FROM tenants WHERE id = $1",
                    tenant_id
                )
                if row and row["calling_rules"]:
                    # asyncpg returns JSON/JSONB as string or dict depending on driver config
                    # assuming standard driver config (string/dict)
                    rules_data = row["calling_rules"]
                    if isinstance(rules_data, str):
                        rules_data = json.loads(rules_data)
                    return CallingRules.from_dict(rules_data)
            
        except Exception as e:
            logger.warning(f"Failed to get tenant rules, using defaults: {e}")
        
        return CallingRules.default()
    
    async def _get_lead_last_called(self, lead_id: str) -> Optional[datetime]:
        """Get the last time a lead was called."""
        try:
            async with self._db_pool.acquire() as conn:
                val = await conn.fetchval(
                    "SELECT last_called_at FROM leads WHERE id = $1",
                    lead_id
                )
                return val  # asyncpg returns appropriate datetime object
            
        except Exception as e:
            logger.warning(f"Failed to get lead last_called_at: {e}")
        
        return None
    
    async def _create_call_record(self, job: DialerJob, call_id: str) -> tuple[str, str]:
        """
        Create a call record in the database with talklee_call_id and PSTN leg.
        
        Returns:
            tuple: (talklee_call_id, leg_id)
        """
        talklee_call_id = generate_talklee_call_id()
        
        try:
            async with self._db_pool.acquire() as conn:
                # Create call record
                await conn.execute(
                    """
                    INSERT INTO calls (
                        id, talklee_call_id, campaign_id, lead_id, phone_number,
                        status, created_at, dialer_job_id, tenant_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8)
                    """,
                    call_id, talklee_call_id, job.campaign_id, job.lead_id,
                    job.phone_number, "initiated", job.job_id, job.tenant_id
                )
                logger.debug(f"Created call record: {call_id} with {talklee_call_id}")
                
                # Create PSTN leg
                # Using direct execution instead of CallEventRepository for simplicity now
                # or replicate behavior
                import uuid
                leg_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO call_legs (
                        id, call_id, talklee_call_id, leg_type, direction,
                        provider, to_number, status, metadata, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                    """,
                    leg_id, call_id, talklee_call_id, "pstn_outbound", "outbound",
                    getattr(self, "_last_provider_name", "sip"), job.phone_number, "initiated",
                    json.dumps({"job_id": job.job_id, "campaign_id": job.campaign_id})
                )
                
                logger.debug(f"Created PSTN leg: {leg_id}")
                
                # Log call initiated event
                await conn.execute(
                    """
                    INSERT INTO call_events (
                        call_id, talklee_call_id, leg_id, event_type, source,
                        event_data, new_state, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    """,
                    call_id, talklee_call_id, leg_id, "leg_started", "dialer_worker",
                    json.dumps({"leg_type": "pstn_outbound", "provider": getattr(self, "_last_provider_name", "sip")}),
                    "initiated"
                )
                
                return talklee_call_id, leg_id
            
        except Exception as e:
            logger.error(f"Failed to create call record: {e}")
            return talklee_call_id, ""
    
    async def _update_lead_status(self, lead_id: str, status: str) -> None:
        """Update lead status in database."""
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE leads SET status = $1, last_called_at = NOW()
                    WHERE id = $2
                    """,
                    status, lead_id
                )
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
            # Build update query dynamically or use simple execution
            status_val = status.value if hasattr(status, 'value') else status
            
            async with self._db_pool.acquire() as conn:
                db = Database(conn)
                data = {
                    "status": status_val,
                    "updated_at": datetime.utcnow()
                }
                if call_id:
                    data["call_id"] = call_id
                    data["processed_at"] = datetime.utcnow()
                if error:
                    data["last_error"] = error
                
                if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.GOAL_ACHIEVED]:
                    data["completed_at"] = datetime.utcnow()
                    
                await db.update("dialer_jobs", data, "id = $1", [job_id])
                
        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
    
    async def _publish_call_event(self, call_id: str, job: DialerJob, talklee_call_id: str) -> None:
        """Publish call event for voice worker to pick up."""
        try:
            event = {
                "event": "call_initiated",
                "call_id": call_id,
                "talklee_call_id": talklee_call_id,
                "job_id": job.job_id,
                "campaign_id": job.campaign_id,
                "lead_id": job.lead_id,
                "tenant_id": job.tenant_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self._redis.publish("voice:calls:active", json.dumps(event))
            logger.debug(f"Published call event for {call_id} ({talklee_call_id})")
            
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
        
        if self._db_pool:
            await close_db_pool()
        
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

    async def _heartbeat(self) -> None:
        """Log heartbeat periodically for systemd liveness monitoring."""
        # Using simple config access to avoid dependency issues during migration
        # from app.core.voice_config import get_voice_config
        # interval = get_voice_config().worker_heartbeat_interval
        interval = 60
        while self.running:
            logger.info(
                f"heartbeat: jobs_processed={self._jobs_processed}, "
                f"jobs_failed={self._jobs_failed}"
            )
            await asyncio.sleep(interval)


async def main():
    """Entry point for running dialer worker as separate process."""
    # Setup simple logging first
    logging.basicConfig(level=logging.INFO)
    
    worker = DialerWorker()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        worker.running = False
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
