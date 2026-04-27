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
import uuid
from datetime import datetime, timezone
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
        # Set to epoch so the very first loop iteration runs the scheduled check
        self._last_scheduled_check = datetime(2000, 1, 1, tzinfo=timezone.utc)

    async def initialize(self) -> None:
        """Initialize connections to Redis and PostgreSQL."""
        logger.info("Initializing Dialer Worker...")
        
        # Initialize queue service (Redis)
        await self.queue_service.initialize()
        
        # Initialize PostgreSQL pool — reuse the container's pool when running
        # inside FastAPI to avoid creating a second connection pool.
        try:
            from app.core.container import get_container
            container = get_container()
            if container.is_initialized and container.db_pool:
                self._db_pool = container.db_pool
                logger.info("Dialer Worker reusing container DB pool")
            else:
                self._db_pool = await init_db_pool()
                logger.info("Dialer Worker created standalone DB pool")
        except Exception:
            self._db_pool = await init_db_pool()
            logger.info("Dialer Worker created standalone DB pool (fallback)")
        
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
                # 1. Check for due scheduled jobs (every 10s)
                now_utc = datetime.now(timezone.utc)
                if self._last_scheduled_check.tzinfo is None:
                    self._last_scheduled_check = self._last_scheduled_check.replace(tzinfo=timezone.utc)
                if (now_utc - self._last_scheduled_check).total_seconds() > 10:
                    moved = await self.queue_service.process_scheduled_jobs()
                    if moved > 0:
                        logger.info(f"Moved {moved} scheduled jobs to queue")
                    self._last_scheduled_check = now_utc
                
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
            campaign_status = await self._get_campaign_status(job.campaign_id)
            if campaign_status not in {"running", "active"}:
                reason = f"campaign_not_runnable:{campaign_status or 'missing'}"
                logger.info(
                    "Skipping job %s because campaign %s is %s",
                    job.job_id,
                    job.campaign_id,
                    campaign_status or "missing",
                )
                await self.queue_service.mark_skipped(job.job_id, reason="campaign_stopped")
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, error=reason)
                return

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
                    logger.info(
                        f"Outside calling window (tz={rules.timezone}, "
                        f"window={rules.time_window_start}-{rules.time_window_end}, "
                        f"days={rules.allowed_days}). "
                        f"Retrying in {delay}s (~{delay/3600:.1f}h)"
                    )
                elif "lead_cooldown" in reason:
                    # The cooldown timestamp was set at call *origination* (not at answer)
                    # due to a now-fixed bug.  Clear it and re-enqueue immediately (bypassing
                    # the scheduled-set → 60-second wait round-trip).
                    logger.info(
                        f"Clearing stale last_called_at for lead {job.lead_id} "
                        f"(was set at origination, not at answer)"
                    )
                    await self._clear_lead_last_called(job.lead_id)
                    # Re-enqueue directly into the tenant queue for immediate pickup
                    job.attempt_number += 1
                    await self.queue_service.enqueue_job(job)
                    await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
                    return
                else:
                    delay = 300  # 5 minutes for other reasons (concurrent limit, etc.)

                await self.queue_service.schedule_retry(job, delay_seconds=delay)
                await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
                return
            
            # 4. Register call start (for concurrent tracking)
            self.rules_engine.register_call_start(job.tenant_id, job.campaign_id)
            
            try:
                # 5. Initiate the call via the provider/PBX.
                provider_call_id = await self._make_call(job, rules)

                if provider_call_id:
                    # 6. Create tracked DB records using an internal UUID plus provider call ID.
                    internal_call_id, talklee_call_id, leg_id = await self._create_call_record(job, provider_call_id)

                    # 7. Update lead status to 'calling'
                    await self._update_lead_status(job.lead_id, "calling")

                    # 8. Update job with the internal DB call UUID
                    job.call_id = internal_call_id
                    job.status = JobStatus.PROCESSING
                    job.processed_at = datetime.now(timezone.utc)
                    await self._update_job_status(job.job_id, JobStatus.PROCESSING, call_id=internal_call_id)

                    # 9. Voice worker notification DISABLED — telephony bridge
                    #    handles the full call lifecycle via ARI callbacks
                    #    (_on_ringing → warmup, _on_new_call → pipeline start).
                    #    Publishing here caused voice_worker to create DUPLICATE
                    #    dead pipelines (BrowserMediaGateway, no audio routed)
                    #    that wasted Deepgram WS connections and caused API-key
                    #    contention, adding 1-3s to the bridge's legitimate
                    #    ringing-phase STT/TTS warmup handshake.
                    # await self._publish_call_event(internal_call_id, job, talklee_call_id, provider_call_id)

                    self._jobs_processed += 1
                    logger.info(
                        "Call initiated: internal_call_id=%s provider_call_id=%s job=%s",
                        internal_call_id,
                        provider_call_id,
                        job.job_id,
                    )
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
                # Reset lead back to pending so it can be picked up again
                await self._update_lead_status(job.lead_id, "pending")
                await self.queue_service.schedule_retry(job, delay_seconds=job.RETRY_DELAY_SECONDS)
                await self._update_job_status(job.job_id, JobStatus.RETRY_SCHEDULED, error=str(e))
            else:
                # Max retries exceeded — mark lead as failed
                await self._update_lead_status(job.lead_id, "failed")
                await self.queue_service.mark_failed(job.job_id, str(e))
                await self._update_job_status(job.job_id, JobStatus.FAILED, error=str(e))
    
    async def _make_call(self, job: DialerJob, rules: CallingRules) -> Optional[str]:
        """
        Initiate an outbound call through the telephony bridge HTTP endpoint.

        Delegates to POST /api/v1/sip/telephony/call so the bridge's persistent
        ARI/ESL adapter owns the channel for its entire lifetime.  Creating a
        separate adapter here and disconnecting it after origination caused
        Asterisk to immediately hang up the channel (ARI drops all channels
        belonging to a disconnected app).

        Returns:
            provider call_id (Asterisk channel ID) if successful, None otherwise.
        """
        import aiohttp

        api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
        caller_id = getattr(rules, "caller_id", None) or os.getenv("DEFAULT_CALLER_ID", "1001")
        url = (
            f"{api_base}/api/v1/sip/telephony/call"
            f"?destination={job.phone_number}"
            f"&caller_id={caller_id}"
            f"&tenant_id={job.tenant_id}"
            f"&campaign_id={job.campaign_id}"
            f"&first_speaker={job.first_speaker}"
        )
        if job.agent_name:
            # urlencode to be safe — names can contain spaces or accents.
            from urllib.parse import quote
            url += f"&agent_name={quote(job.agent_name, safe='')}"

        try:
            headers = {"Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status not in (200, 202):
                        body = await resp.text()
                        logger.error(
                            "Telephony bridge rejected call: status=%s body=%s dest=%s",
                            resp.status, body[:200], job.phone_number,
                        )
                        return None

                    data = await resp.json()
                    call_id: Optional[str] = data.get("call_id")
                    self._last_provider_name = data.get("adapter", "asterisk")

                    if call_id:
                        logger.info(
                            "CALL INITIATED via bridge (%s): %s call_id=%s... "
                            "(campaign=%s, lead=%s)",
                            self._last_provider_name, job.phone_number,
                            call_id[:8], job.campaign_id, job.lead_id,
                        )
                    else:
                        logger.warning(
                            "CALL FAILED via bridge: %s (campaign=%s, lead=%s)",
                            job.phone_number, job.campaign_id, job.lead_id,
                        )
                    return call_id

        except Exception as e:
            logger.error("Originate error for %s: %s", job.phone_number, e)
            return None
    
    async def _get_active_tenant_ids(self) -> List[str]:
        """Get list of tenants with active/running campaigns."""
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT tenant_id FROM campaigns WHERE status IN ('running', 'active')"
                )
                return [str(r["tenant_id"]) for r in rows] if rows else []

        except Exception as e:
            logger.error(f"Failed to get active tenants: {e}")
            return []

    async def _get_campaign_status(self, campaign_id: str) -> Optional[str]:
        """Return campaign status so dequeued jobs can be revalidated before originate."""
        try:
            async with self._db_pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT status FROM campaigns WHERE id = $1",
                    campaign_id,
                )
        except Exception as e:
            logger.error(f"Failed to get campaign status for {campaign_id}: {e}")
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
    
    async def _create_call_record(self, job: DialerJob, provider_call_id: str) -> tuple[str, str, str]:
        """
        Create a call record in the database with separate internal and provider IDs.

        Returns:
            tuple: (internal_call_id, talklee_call_id, leg_id)
        """
        talklee_call_id = generate_talklee_call_id()
        internal_call_id = str(uuid.uuid4())

        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO calls (
                        id, tenant_id, campaign_id, lead_id, phone_number,
                        external_call_uuid, status, talklee_call_id, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    """,
                    internal_call_id,
                    job.tenant_id,
                    job.campaign_id,
                    job.lead_id,
                    job.phone_number,
                    provider_call_id,
                    "initiated",
                    talklee_call_id,
                )
                logger.debug(
                    "Created call record internal=%s provider=%s talklee=%s",
                    internal_call_id,
                    provider_call_id,
                    talklee_call_id,
                )

                leg_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO call_legs (
                        id, call_id, talklee_call_id, leg_type, direction,
                        provider, provider_leg_id, to_number, status, metadata, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                    """,
                    leg_id,
                    internal_call_id,
                    talklee_call_id,
                    "pstn_outbound",
                    "outbound",
                    getattr(self, "_last_provider_name", "sip"),
                    provider_call_id,
                    job.phone_number,
                    "initiated",
                    json.dumps({
                        "job_id": job.job_id,
                        "campaign_id": job.campaign_id,
                        "provider_call_id": provider_call_id,
                    }),
                )

                await conn.execute(
                    """
                    INSERT INTO call_events (
                        call_id, talklee_call_id, leg_id, event_type, source,
                        event_data, new_state, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    """,
                    internal_call_id,
                    talklee_call_id,
                    leg_id,
                    "leg_started",
                    "dialer_worker",
                    json.dumps({
                        "leg_type": "pstn_outbound",
                        "provider": getattr(self, "_last_provider_name", "sip"),
                        "provider_call_id": provider_call_id,
                    }),
                    "initiated",
                )

                return internal_call_id, talklee_call_id, leg_id

        except Exception as e:
            logger.error(f"Failed to create call record: {e}")
            return internal_call_id, talklee_call_id, ""
    
    async def _update_lead_status(self, lead_id: str, status: str) -> None:
        """Update lead status in database."""
        try:
            async with self._db_pool.acquire() as conn:
                if status in ("pending", "calling"):
                    # "pending"  — resetting for retry, keep last_called_at unchanged
                    # "calling"  — origination only, call not yet answered; setting
                    #              last_called_at here would poison the per-lead cooldown
                    #              and block all retries for 2 hours even if the call
                    #              never connected.  last_called_at is set on terminal
                    #              states (completed / failed) instead.
                    await conn.execute(
                        "UPDATE leads SET status = $1 WHERE id = $2",
                        status, lead_id
                    )
                else:
                    # Terminal / completion states (failed, completed, etc.) —
                    # record the timestamp so per-lead cooldown is enforced correctly.
                    await conn.execute(
                        """
                        UPDATE leads SET status = $1, last_called_at = NOW()
                        WHERE id = $2
                        """,
                        status, lead_id
                    )
        except Exception as e:
            logger.error(f"Failed to update lead status: {e}")

    async def _clear_lead_last_called(self, lead_id: str) -> None:
        """Clear last_called_at so a stale origination-time timestamp cannot block retries."""
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE leads SET last_called_at = NULL WHERE id = $1",
                    lead_id
                )
        except Exception as e:
            logger.error(f"Failed to clear lead last_called_at: {e}")

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
                    "updated_at": datetime.now(timezone.utc)
                }
                if call_id:
                    data["call_id"] = call_id
                    data["processed_at"] = datetime.now(timezone.utc)
                if error:
                    data["last_error"] = error
                
                if status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.GOAL_ACHIEVED]:
                    data["completed_at"] = datetime.now(timezone.utc)
                    
                await db.update("dialer_jobs", data, "id = $1", [job_id])
                
        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
    
    async def _publish_call_event(
        self,
        call_id: str,
        job: DialerJob,
        talklee_call_id: str,
        provider_call_id: str,
    ) -> None:
        """Publish call event for voice worker to pick up."""
        try:
            event = {
                "event": "call_initiated",
                "call_id": call_id,
                "talklee_call_id": talklee_call_id,
                "provider_call_id": provider_call_id,
                "job_id": job.job_id,
                "campaign_id": job.campaign_id,
                "lead_id": job.lead_id,
                "tenant_id": job.tenant_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            await self._redis.publish("voice:calls:active", json.dumps(event))
            logger.debug(
                "Published call event internal=%s provider=%s talklee=%s",
                call_id,
                provider_call_id,
                talklee_call_id,
            )

        except Exception as e:
            logger.error(f"Failed to publish call event: {e}")
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Dialer Worker...")
        self.running = False
        
        # Close connections
        await self.queue_service.close()
        if self._redis:
            await self._redis.aclose()
        
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
