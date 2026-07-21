"""
Dialer Queue Service
Redis-based job queue with priority support
"""
import asyncio
import json
import logging
import random
import time
from typing import Optional, List
from datetime import datetime, timedelta, timezone

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.core.config import ConfigManager

logger = logging.getLogger(__name__)


class DialerQueueService:
    """
    Redis-based job queue for the dialer engine.
    
    Uses Redis Lists for FIFO queuing with priority support:
    - High priority jobs (>= threshold) go to priority queue
    - Normal jobs go to tenant-specific queues
    - Scheduled retries use Redis Sorted Sets
    
    Queue Keys:
    - dialer:priority:queue - High priority jobs (checked first)
    - dialer:tenant:{id}:queue - Tenant-specific FIFO queues
    - dialer:scheduled - Sorted set for delayed retries
    - dialer:processing - ZSET of in-flight jobs, scored by start time (age-out)
    """

    # Redis key prefixes
    PRIORITY_QUEUE = "dialer:priority:queue"
    TENANT_QUEUE_PREFIX = "dialer:tenant:{tenant_id}:queue"
    SCHEDULED_ZSET = "dialer:scheduled"
    PROCESSING_ZSET = "dialer:processing"
    # Durable in-flight store (BUG 1 crash-safety). A job is moved OUT of its
    # queue and INTO this list in a SINGLE atomic LMOVE, so a worker that dies
    # anywhere after the pop still leaves the payload here — never in limbo.
    # INFLIGHT_HASH indexes job_id -> payload so a terminal mark (which only
    # knows the job_id) can LREM the exact list entry, and so a paused/quota
    # skip can re-enqueue the original job non-destructively (BUG 2).
    INFLIGHT_LIST = "dialer:inflight"
    INFLIGHT_HASH = "dialer:inflight:payloads"
    STATS_KEY = "dialer:stats"

    # Skip reasons that are NOT terminal: the campaign is merely paused, or the
    # tenant is temporarily out of plan minutes. A lead in one of these states
    # must SURVIVE (be re-deferred), never be turned into a terminal SKIPPED
    # that a resume / top-up can't recover (BUG 2).
    NON_TERMINAL_SKIP_REASONS = frozenset({"campaign_stopped", "out_of_minutes"})

    # Default priority threshold (8+ goes to priority queue)
    HIGH_PRIORITY_THRESHOLD = 8

    @staticmethod
    def _pause_redefer_seconds() -> int:
        """Delay before a paused/quota-blocked lead is retried. Long enough to
        avoid tight churn when a paused campaign shares a tenant with an active
        one, short enough that a resume / minutes top-up continues promptly."""
        import os
        try:
            return max(5, int(os.getenv("DIALER_PAUSE_REDEFER_S", "120")))
        except (TypeError, ValueError):
            return 120
    
    def __init__(self, redis_client=None):
        """
        Initialize queue service.
        
        Args:
            redis_client: Optional pre-configured Redis client
        """
        self._redis = redis_client
        self._config = ConfigManager()
        self._initialized = False
    
    async def _migrate_processing_key(self) -> None:
        """One-time: the in-flight tracker used to be a plain SET; it's now a
        timestamped ZSET. A pre-existing SET at the key would WRONGTYPE every
        zadd, so drop it — its members are stale-by-definition after a redeploy
        (and this is exactly the pile-up we're clearing)."""
        try:
            if self._redis is None:
                return
            ktype = await self._redis.type(self.PROCESSING_ZSET)
            if ktype not in ("zset", "none"):
                dropped = await self._redis.delete(self.PROCESSING_ZSET)
                logger.info(
                    "migrated dialer:processing (%s -> zset), dropped %s stale key(s)",
                    ktype, dropped,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("processing-key migration check failed: %s", exc)

    async def initialize(self) -> None:
        """Initialize Redis connection if not provided."""
        if self._redis is not None:
            self._initialized = True
            await self._migrate_processing_key()
            return
        
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available - queue service will not work")
            return
        
        try:
            # Env var wins — systemd's EnvironmentFile puts authenticated
            # REDIS_URL on the process, and the YAML's flat "redis_url" key
            # never existed (config nests it as redis.url), so the old
            # `self._config.get("redis_url", ...)` always fell back to the
            # unauthenticated localhost default.
            import os
            redis_url = os.getenv("REDIS_URL") or self._config.get(
                "redis.url",
                self._config.get("redis_url", "redis://localhost:6379"),
            )
            self._redis = await redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            await self._redis.ping()
            self._initialized = True
            await self._migrate_processing_key()
            from app.core.log_redact import redact_redis_url
            logger.info(
                "DialerQueueService connected to Redis: %s",
                redact_redis_url(redis_url),
            )
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    async def enqueue_job(self, job: DialerJob) -> bool:
        """
        Enqueue a dialer job.
        
        High priority jobs (>= threshold) go to the priority queue.
        Normal jobs go to tenant-specific FIFO queues.
        
        Args:
            job: DialerJob to enqueue
            
        Returns:
            True if enqueued successfully
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            job_data = json.dumps(job.to_redis_dict())
            
            if job.priority >= self.HIGH_PRIORITY_THRESHOLD:
                # High priority - goes to priority queue
                await self._redis.lpush(self.PRIORITY_QUEUE, job_data)
                logger.info(f"Enqueued high-priority job {job.job_id} (priority={job.priority})")
            else:
                # Normal priority - tenant-specific queue (RPUSH for FIFO)
                queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=job.tenant_id)
                await self._redis.rpush(queue_key, job_data)
                logger.debug(f"Enqueued job {job.job_id} to tenant queue")
            
            # Update stats
            await self._redis.hincrby(self.STATS_KEY, "total_enqueued", 1)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to enqueue job {job.job_id}: {e}")
            return False
    
    async def dequeue_job(
        self,
        tenant_ids: Optional[List[str]] = None,
        timeout: int = 5
    ) -> Optional[DialerJob]:
        """
        Dequeue the next job to process.
        
        Priority order:
        1. Priority queue (high-priority jobs)
        2. Tenant queues (round-robin through provided tenant_ids)
        
        Args:
            tenant_ids: List of tenant IDs to check (None = all)
            timeout: Max seconds to wait for a job
            
        Returns:
            DialerJob or None if no jobs available
        """
        if not self._initialized:
            await self.initialize()

        try:
            # FAIL-SAFE (BUG 2): an explicit EMPTY tenant list means "there are
            # NO active tenants this tick" → dequeue NOTHING. Only `None` (the
            # argument was not supplied) means "scan every queue". The dialer
            # worker passes `[]` when no campaign is running/active OR when the
            # active-tenant DB lookup failed, so we must fail SAFE here and never
            # drain paused/idle/quota-blocked queues (which would then be turned
            # into terminal SKIPPED jobs downstream and lost). This gate also
            # protects the priority queue, whose jobs likewise belong to
            # campaigns that are all paused when no tenant is active.
            if tenant_ids is not None and len(tenant_ids) == 0:
                return None

            # 1. Priority queue first (atomic, crash-safe move).
            job = await self._pop_and_track(self.PRIORITY_QUEUE)
            if job is not None:
                logger.info(f"Dequeued high-priority job {job.job_id}")
                return job

            # 2. Tenant queues.
            if tenant_ids is not None:
                for tenant_id in tenant_ids:
                    queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=tenant_id)
                    job = await self._pop_and_track(queue_key)
                    if job is not None:
                        logger.debug(f"Dequeued job {job.job_id} from tenant {tenant_id}")
                        return job
            else:
                # Explicit "scan all" (tenant_ids is None) — kept for tests /
                # callers that really do want every queue.
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(
                        cursor,
                        match="dialer:tenant:*:queue",
                        count=10
                    )
                    for key in keys:
                        job = await self._pop_and_track(key)
                        if job is not None:
                            return job
                    if cursor == 0:
                        break

            return None

        except Exception as e:
            logger.error(f"Failed to dequeue job: {e}")
            return None

    async def _pop_and_track(self, source_key: str) -> Optional[DialerJob]:
        """Crash-safe dequeue from one queue (BUG 1).

        The payload is moved OUT of ``source_key`` and INTO the durable
        ``INFLIGHT_LIST`` with a single atomic ``LMOVE``. Before this fix the
        code did ``LPOP`` (which DELETES the only copy) and only THEN wrote the
        processing marker — a worker death, a deserialization error, or a Redis
        hiccup in that gap left the job gone from every structure while the lead
        stayed 'pending' forever. With the atomic move, a crash at ANY point
        after the pop leaves the payload in ``INFLIGHT_LIST``, where
        ``reap_stale_processing`` reclaims it (re-enqueues it) on the next tick.

        The ZSET timestamp + payload-hash written by ``_mark_processing`` are
        pure bookkeeping: their absence is exactly what marks a list entry as a
        crash-orphan to be reclaimed, so a crash between the move and the mark is
        self-healing rather than a lost job.
        """
        payload = await self._redis.lmove(
            source_key, self.INFLIGHT_LIST, "LEFT", "RIGHT"
        )
        if not payload:
            return None
        try:
            job = DialerJob.from_redis_dict(json.loads(payload))
        except Exception as exc:  # noqa: BLE001
            # Un-decodable payload can never reach a terminal mark, so it would
            # wedge the inflight list forever — drop it loudly instead.
            logger.error("dequeue: dropping undecodable inflight payload: %s", exc)
            try:
                await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
            except Exception:  # noqa: BLE001
                pass
            return None
        await self._mark_processing(job.job_id, payload)
        return job
    
    async def schedule_retry(self, job: DialerJob, delay_seconds: int = 7200) -> bool:
        """
        Schedule a job for retry after a delay.
        
        Args:
            job: Job to retry
            delay_seconds: Delay before retry (default: 2 hours)
            
        Returns:
            True if scheduled successfully
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            # Update job for retry
            job.attempt_number += 1
            job.status = JobStatus.RETRY_SCHEDULED

            # De-synchronise the herd: when a whole batch is throttled/queued at
            # once it would otherwise re-fire in lockstep after exactly the same
            # delay, re-burst, and re-trip the limiter. Spread re-enqueue over a
            # small jitter window (0..min(25% of delay, 15s)) so retries fan out
            # instead of stampeding.
            jitter = random.uniform(0, min(delay_seconds * 0.25, 15.0))
            effective_delay = delay_seconds + jitter
            job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=effective_delay)

            # Calculate execution time as Unix timestamp
            execute_at = datetime.now(timezone.utc).timestamp() + effective_delay
            
            job_data = json.dumps(job.to_redis_dict())
            await self._redis.zadd(self.SCHEDULED_ZSET, {job_data: execute_at})

            # Rescheduled → no longer in flight. Drop the durable inflight copy
            # (list + payload index) AND the processing-ZSET marker so the job
            # isn't reclaimed as a crash-orphan while it waits in the schedule.
            await self._untrack_inflight(job.job_id)
            await self._redis.zrem(self.PROCESSING_ZSET, job.job_id)
            
            logger.info(
                f"Scheduled retry for job {job.job_id} "
                f"(attempt {job.attempt_number}) in {delay_seconds}s"
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to schedule retry for job {job.job_id}: {e}")
            return False
    
    async def process_scheduled_jobs(self) -> int:
        """
        Move due scheduled jobs back to their queues.
        
        Should be called periodically by the worker.
        
        Returns:
            Number of jobs moved
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            now = datetime.now(timezone.utc).timestamp()
            
            # Get all jobs due for processing
            due_jobs = await self._redis.zrangebyscore(
                self.SCHEDULED_ZSET,
                0,
                now
            )
            
            count = 0
            for job_data in due_jobs:
                # CLAIM the job by removing it from the scheduled set FIRST and
                # only proceeding if WE were the remover. ZREM returns the number
                # of members actually removed, so exactly one caller can win —
                # two workers (or a double tick) can never both promote the same
                # entry and double-dial the lead.
                claimed = await self._redis.zrem(self.SCHEDULED_ZSET, job_data)
                if not claimed:
                    continue  # already promoted / cleared by someone else

                job = DialerJob.from_redis_dict(json.loads(job_data))
                job.status = JobStatus.PENDING

                # Re-enqueue. If this FAILS after we've claimed (removed) the
                # entry, put it straight back into the scheduled set so the lead
                # is never lost — the next tick retries the promotion. This
                # closes the old ZREM-before-enqueue lose-it window: the job is
                # only ever absent from BOTH structures for the duration of one
                # in-process enqueue call, and any failure re-adds it.
                enqueued = await self.enqueue_job(job)
                if not enqueued:
                    await self._redis.zadd(self.SCHEDULED_ZSET, {job_data: now})
                    logger.warning(
                        "process_scheduled_jobs: re-enqueue failed for job %s — "
                        "restored to scheduled set (not lost)", job.job_id,
                    )
                    continue
                count += 1
            
            if count > 0:
                logger.info(f"Moved {count} scheduled jobs to queues")
            
            return count
            
        except Exception as e:
            logger.error(f"Failed to process scheduled jobs: {e}")
            return 0
    
    async def mark_completed(self, job_id: str, outcome: str = "completed") -> None:
        """Mark a job as completed (removes it from the in-flight tracking)."""
        await self._untrack_inflight(job_id)
        await self._redis.zrem(self.PROCESSING_ZSET, job_id)
        await self._redis.hincrby(self.STATS_KEY, "total_completed", 1)
        await self._redis.hincrby(self.STATS_KEY, f"outcome_{outcome}", 1)
        logger.debug(f"Job {job_id} marked completed: {outcome}")

    async def mark_failed(self, job_id: str, error: str) -> None:
        """Mark a job as failed (removes it from the in-flight tracking)."""
        await self._untrack_inflight(job_id)
        await self._redis.zrem(self.PROCESSING_ZSET, job_id)
        await self._redis.hincrby(self.STATS_KEY, "total_failed", 1)
        logger.debug(f"Job {job_id} marked failed: {error}")

    async def mark_skipped(self, job_id: str, reason: str = "skipped") -> None:
        """Skip a dequeued job without treating it as a failure.

        BUG 2 fail-safe: if the skip is only because the campaign is PAUSED or
        the tenant is temporarily OUT OF MINUTES (``NON_TERMINAL_SKIP_REASONS``),
        the lead must NOT be dropped — pausing/topping-up must be able to
        continue where it stopped. In that case we RE-DEFER the original job
        (from its durable inflight copy) into the scheduled set instead of
        destroying it. Any other reason is a genuine terminal skip.
        """
        if reason in self.NON_TERMINAL_SKIP_REASONS:
            if await self._redefer_inflight(job_id, reason):
                # Counted as skipped-this-tick for stats, but the LEAD lives on
                # in the scheduled set and will be retried after the delay.
                await self._redis.hincrby(self.STATS_KEY, "total_skipped", 1)
                await self._redis.hincrby(self.STATS_KEY, f"outcome_{reason}", 1)
                return
            # No inflight copy to re-defer (e.g. a genuinely STOPPED campaign
            # whose queue was already purged by clear_campaign_jobs) → fall
            # through to a plain terminal skip.
        await self._untrack_inflight(job_id)
        await self._redis.zrem(self.PROCESSING_ZSET, job_id)
        await self._redis.hincrby(self.STATS_KEY, "total_skipped", 1)
        await self._redis.hincrby(self.STATS_KEY, f"outcome_{reason}", 1)
        logger.debug(f"Job {job_id} marked skipped: {reason}")

    async def _mark_processing(self, job_id: str, payload: str) -> None:
        """Record a just-moved job as in flight.

        Writes the payload index (job_id → payload) BEFORE the processing-ZSET
        timestamp, so that whenever a job is "tracked" (present in the ZSET) its
        payload is guaranteed retrievable for a later LREM. The ZSET score is the
        pop time so a job that never reaches a terminal mark (e.g. a
        successfully-originated call whose end-of-call finalisation ran without a
        wired queue_service) is AGED OUT by ``reap_stale_processing`` instead of
        leaking. A job present in ``INFLIGHT_LIST`` but ABSENT from the ZSET is a
        crash-orphan (died in the pop→mark gap) and is reclaimed, not aged out.
        """
        await self._redis.hset(self.INFLIGHT_HASH, job_id, payload)
        await self._redis.zadd(self.PROCESSING_ZSET, {job_id: time.time()})
        await self._redis.hincrby(self.STATS_KEY, "total_dequeued", 1)

    async def _untrack_inflight(self, job_id: str) -> None:
        """Remove a job's durable inflight copy (list entry) + payload index.
        Tolerant of a missing index (older in-flight jobs from before this
        upgrade, or an already-cleaned entry)."""
        try:
            payload = await self._redis.hget(self.INFLIGHT_HASH, job_id)
            if payload is not None:
                await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
                await self._redis.hdel(self.INFLIGHT_HASH, job_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("untrack_inflight failed job=%s err=%s", job_id, exc)

    async def _redefer_inflight(self, job_id: str, reason: str) -> bool:
        """Non-destructively re-schedule a paused/quota-blocked job.

        Reads the ORIGINAL payload from the inflight index, re-schedules it into
        the scheduled ZSET after a short delay (WITHOUT bumping attempt_number —
        this is a defer, not a retry, so it never burns the retry budget), and
        clears the inflight tracking. Returns True if the job was re-deferred,
        False if there was no inflight copy to recover.
        """
        try:
            payload = await self._redis.hget(self.INFLIGHT_HASH, job_id)
            if not payload:
                return False
            job = DialerJob.from_redis_dict(json.loads(payload))
            job.status = JobStatus.PENDING
            delay = self._pause_redefer_seconds()
            execute_at = datetime.now(timezone.utc).timestamp() + delay
            new_payload = json.dumps(job.to_redis_dict())
            await self._redis.zadd(self.SCHEDULED_ZSET, {new_payload: execute_at})
            # Only now drop the inflight copy — the lead is safely staged in the
            # scheduled set, so there is no window where it exists nowhere.
            await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
            await self._redis.hdel(self.INFLIGHT_HASH, job_id)
            await self._redis.zrem(self.PROCESSING_ZSET, job_id)
            logger.info(
                "re-deferred job %s (%s) for %ss — lead preserved, NOT "
                "terminal-skipped", job_id, reason, delay,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("redefer_inflight failed job=%s err=%s", job_id, exc)
            return False

    async def reap_stale_processing(self, max_age_seconds: int = 900) -> int:
        """Self-heal the in-flight tracking. Two jobs in one pass:

        1. RECLAIM crash-orphans (BUG 1 recovery): a payload sitting in
           ``INFLIGHT_LIST`` whose job_id is NOT in the processing ZSET was moved
           out of its queue but died before the mark landed (the pop→mark gap).
           It never dialed, so it is re-enqueued — the lead is recovered, not
           lost. Success-path jobs are ALWAYS in the ZSET (marked at dequeue),
           so this can never re-dial a live/answered call.

        2. AGE OUT tracked zombies (hygiene): a ZSET member older than the max
           plausible job lifetime (default 15 min ≫ ring + hard call ceiling)
           whose call ended without a terminal mark. Evicted (and its inflight
           copy cleaned) — never re-dialed. O(log n + m); safe every tick.

        Returns the total number of entries reclaimed + evicted.
        """
        if not self._redis:
            return 0
        reclaimed = 0
        try:
            reclaimed = await self._reclaim_untracked_inflight()
        except Exception as exc:  # noqa: BLE001
            logger.warning("inflight reclaim failed: %s", exc)
        try:
            cutoff = time.time() - int(max_age_seconds)
            stale_ids = await self._redis.zrangebyscore(self.PROCESSING_ZSET, 0, cutoff)
            for job_id in (stale_ids or []):
                await self._untrack_inflight(job_id)
                await self._redis.zrem(self.PROCESSING_ZSET, job_id)
            n = len(stale_ids or [])
            if n:
                logger.info(
                    "reaper: evicted %d stale dialer:processing member(s) (>%ss)",
                    n, max_age_seconds,
                )
            return reclaimed + n
        except Exception as exc:  # noqa: BLE001 — never let hygiene break dispatch
            logger.warning("reap_stale_processing failed: %s", exc)
            return reclaimed

    async def _reclaim_untracked_inflight(self) -> int:
        """Re-enqueue inflight payloads that were moved out of a queue but never
        marked in the processing ZSET (BUG 1 crash-orphans). See
        ``reap_stale_processing`` for why this is safe against re-dialing live
        calls. Returns the number reclaimed."""
        entries = await self._redis.lrange(self.INFLIGHT_LIST, 0, -1)
        if not entries:
            return 0
        reclaimed = 0
        for payload in entries:
            try:
                data = json.loads(payload)
                job_id = data.get("job_id")
            except Exception:  # noqa: BLE001
                # Corrupt entry — evict so it can't wedge the list.
                await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
                continue
            if not job_id:
                await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
                continue
            # Tracked (mark landed) → leave it to the age-out path.
            if await self._redis.zscore(self.PROCESSING_ZSET, job_id) is not None:
                continue
            # Untracked → the pop→mark gap crash. Never dialed; re-enqueue it.
            try:
                job = DialerJob.from_redis_dict(data)
            except Exception as exc:  # noqa: BLE001
                await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
                logger.error("reclaim: dropping undecodable inflight job: %s", exc)
                continue
            await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
            await self._redis.hdel(self.INFLIGHT_HASH, job_id)
            await self.enqueue_job(job)
            reclaimed += 1
            logger.warning(
                "reclaimed crash-orphaned job %s from inflight → re-enqueued "
                "(recovered a job lost in the pop→mark gap)", job_id,
            )
        return reclaimed
    
    async def get_queue_stats(self) -> dict:
        """Get queue statistics."""
        if not self._initialized:
            await self.initialize()
        
        try:
            # Get priority queue length
            priority_len = await self._redis.llen(self.PRIORITY_QUEUE)
            
            # Get scheduled jobs count
            scheduled_count = await self._redis.zcard(self.SCHEDULED_ZSET)
            
            # Get processing count
            processing_count = await self._redis.zcard(self.PROCESSING_ZSET)

            # Durable in-flight copies (crash-recoverable payloads).
            inflight_count = await self._redis.llen(self.INFLIGHT_LIST)

            # Get stats hash
            stats = await self._redis.hgetall(self.STATS_KEY) or {}

            return {
                "priority_queue_length": priority_len,
                "scheduled_jobs": scheduled_count,
                "processing_jobs": processing_count,
                "inflight_jobs": inflight_count,
                "total_enqueued": int(stats.get("total_enqueued", 0)),
                "total_dequeued": int(stats.get("total_dequeued", 0)),
                "total_completed": int(stats.get("total_completed", 0)),
                "total_failed": int(stats.get("total_failed", 0))
            }
            
        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {}
    
    async def get_queue_length(self, tenant_id: Optional[str] = None) -> int:
        """Get queue length for a tenant or total."""
        if not self._initialized:
            await self.initialize()
        
        try:
            if tenant_id:
                queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=tenant_id)
                return await self._redis.llen(queue_key)
            else:
                # Total across all queues
                priority_len = await self._redis.llen(self.PRIORITY_QUEUE)
                
                # Scan tenant queues
                total = priority_len
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(
                        cursor,
                        match="dialer:tenant:*:queue",
                        count=10
                    )
                    for key in keys:
                        total += await self._redis.llen(key)
                    if cursor == 0:
                        break
                
                return total
                
        except Exception as e:
            logger.error(f"Failed to get queue length: {e}")
            return 0
    
    async def clear_queue(self, tenant_id: Optional[str] = None) -> int:
        """
        Clear queue (for testing/debugging).
        
        Args:
            tenant_id: Clear only this tenant's queue (None = clear all)
            
        Returns:
            Number of jobs cleared
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            count = 0
            
            if tenant_id:
                queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=tenant_id)
                count = await self._redis.llen(queue_key)
                await self._redis.delete(queue_key)
            else:
                # Clear all queues
                count = await self._redis.llen(self.PRIORITY_QUEUE)
                await self._redis.delete(self.PRIORITY_QUEUE)
                
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(
                        cursor,
                        match="dialer:tenant:*:queue",
                        count=10
                    )
                    for key in keys:
                        count += await self._redis.llen(key)
                        await self._redis.delete(key)
                    if cursor == 0:
                        break
                
                # Clear scheduled, processing, and the durable inflight store.
                await self._redis.delete(self.SCHEDULED_ZSET)
                await self._redis.delete(self.PROCESSING_ZSET)
                await self._redis.delete(self.INFLIGHT_LIST)
                await self._redis.delete(self.INFLIGHT_HASH)

            logger.info(f"Cleared {count} jobs from queue")
            return count
            
        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")
            return 0

    async def clear_campaign_jobs(self, campaign_id: str) -> int:
        """
        Remove queued and scheduled jobs for a specific campaign.

        Also purges the campaign's DURABLE INFLIGHT copies so that a job which
        was dequeued moments before a STOP cannot be re-deferred back into the
        schedule by ``mark_skipped`` (which would otherwise cycle a
        stopped-campaign lead forever). Purging inflight here means the
        subsequent ``mark_skipped(campaign_stopped)`` finds no copy and takes the
        plain terminal path.
        """
        if not self._initialized:
            await self.initialize()

        try:
            removed = 0
            removed += await self._remove_campaign_jobs_from_list(self.PRIORITY_QUEUE, campaign_id)

            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor,
                    match="dialer:tenant:*:queue",
                    count=50,
                )
                for key in keys:
                    removed += await self._remove_campaign_jobs_from_list(key, campaign_id)
                if cursor == 0:
                    break

            removed += await self._remove_campaign_jobs_from_scheduled(campaign_id)
            await self._remove_campaign_jobs_from_inflight(campaign_id)

            logger.info("Cleared %s queued jobs for campaign %s", removed, campaign_id)
            return removed
        except Exception as e:
            logger.error(f"Failed to clear campaign jobs for {campaign_id}: {e}")
            return 0

    async def _remove_campaign_jobs_from_list(self, key: str, campaign_id: str) -> int:
        """Filter one Redis list in place, preserving job order."""
        entries = await self._redis.lrange(key, 0, -1)
        if not entries:
            return 0

        kept_entries: list[str] = []
        removed = 0
        for entry in entries:
            try:
                payload = json.loads(entry)
            except Exception:
                kept_entries.append(entry)
                continue

            if payload.get("campaign_id") == campaign_id:
                removed += 1
            else:
                kept_entries.append(entry)

        if removed:
            await self._redis.delete(key)
            if kept_entries:
                await self._redis.rpush(key, *kept_entries)

        return removed

    async def _remove_campaign_jobs_from_scheduled(self, campaign_id: str) -> int:
        """Remove scheduled retry entries for one campaign."""
        entries = await self._redis.zrange(self.SCHEDULED_ZSET, 0, -1)
        if not entries:
            return 0

        removed = 0
        for entry in entries:
            try:
                payload = json.loads(entry)
            except Exception:
                continue
            if payload.get("campaign_id") == campaign_id:
                await self._redis.zrem(self.SCHEDULED_ZSET, entry)
                removed += 1

        return removed

    async def _remove_campaign_jobs_from_inflight(self, campaign_id: str) -> int:
        """Drop a campaign's durable inflight copies (list entry + payload index
        + processing marker). Used on STOP so an already-dequeued job can't be
        re-deferred and cycle forever."""
        removed = 0
        try:
            index = await self._redis.hgetall(self.INFLIGHT_HASH) or {}
            if not isinstance(index, dict):
                return removed
            for job_id, payload in index.items():
                try:
                    if json.loads(payload).get("campaign_id") != campaign_id:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                await self._redis.lrem(self.INFLIGHT_LIST, 0, payload)
                await self._redis.hdel(self.INFLIGHT_HASH, job_id)
                await self._redis.zrem(self.PROCESSING_ZSET, job_id)
                removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("remove_campaign_inflight failed campaign=%s err=%s", campaign_id, exc)
        return removed

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._initialized = False
