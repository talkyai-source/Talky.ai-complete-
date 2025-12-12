"""
Dialer Queue Service
Redis-based job queue with priority support
"""
import asyncio
import json
import logging
from typing import Optional, List
from datetime import datetime, timedelta

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
    - dialer:processing - Set of jobs currently being processed
    """
    
    # Redis key prefixes
    PRIORITY_QUEUE = "dialer:priority:queue"
    TENANT_QUEUE_PREFIX = "dialer:tenant:{tenant_id}:queue"
    SCHEDULED_ZSET = "dialer:scheduled"
    PROCESSING_SET = "dialer:processing"
    STATS_KEY = "dialer:stats"
    
    # Default priority threshold (8+ goes to priority queue)
    HIGH_PRIORITY_THRESHOLD = 8
    
    def __init__(self, redis_client=None):
        """
        Initialize queue service.
        
        Args:
            redis_client: Optional pre-configured Redis client
        """
        self._redis = redis_client
        self._config = ConfigManager()
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize Redis connection if not provided."""
        if self._redis is not None:
            self._initialized = True
            return
        
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available - queue service will not work")
            return
        
        try:
            redis_url = self._config.get("redis_url", "redis://localhost:6379")
            self._redis = await redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            await self._redis.ping()
            self._initialized = True
            logger.info(f"DialerQueueService connected to Redis: {redis_url}")
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
            # 1. Check priority queue first
            job_data = await self._redis.lpop(self.PRIORITY_QUEUE)
            if job_data:
                job = DialerJob.from_redis_dict(json.loads(job_data))
                await self._mark_processing(job.job_id)
                logger.info(f"Dequeued high-priority job {job.job_id}")
                return job
            
            # 2. Check tenant queues
            if tenant_ids:
                for tenant_id in tenant_ids:
                    queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=tenant_id)
                    job_data = await self._redis.lpop(queue_key)
                    if job_data:
                        job = DialerJob.from_redis_dict(json.loads(job_data))
                        await self._mark_processing(job.job_id)
                        logger.debug(f"Dequeued job {job.job_id} from tenant {tenant_id}")
                        return job
            else:
                # No specific tenants - scan for any tenant queue with jobs
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(
                        cursor,
                        match="dialer:tenant:*:queue",
                        count=10
                    )
                    for key in keys:
                        job_data = await self._redis.lpop(key)
                        if job_data:
                            job = DialerJob.from_redis_dict(json.loads(job_data))
                            await self._mark_processing(job.job_id)
                            return job
                    if cursor == 0:
                        break
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to dequeue job: {e}")
            return None
    
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
            job.scheduled_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
            
            # Calculate execution time as Unix timestamp
            execute_at = datetime.utcnow().timestamp() + delay_seconds
            
            job_data = json.dumps(job.to_redis_dict())
            await self._redis.zadd(self.SCHEDULED_ZSET, {job_data: execute_at})
            
            # Remove from processing set
            await self._redis.srem(self.PROCESSING_SET, job.job_id)
            
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
            now = datetime.utcnow().timestamp()
            
            # Get all jobs due for processing
            due_jobs = await self._redis.zrangebyscore(
                self.SCHEDULED_ZSET,
                0,
                now
            )
            
            count = 0
            for job_data in due_jobs:
                job = DialerJob.from_redis_dict(json.loads(job_data))
                job.status = JobStatus.PENDING
                
                # Remove from scheduled set
                await self._redis.zrem(self.SCHEDULED_ZSET, job_data)
                
                # Re-enqueue
                await self.enqueue_job(job)
                count += 1
            
            if count > 0:
                logger.info(f"Moved {count} scheduled jobs to queues")
            
            return count
            
        except Exception as e:
            logger.error(f"Failed to process scheduled jobs: {e}")
            return 0
    
    async def mark_completed(self, job_id: str, outcome: str = "completed") -> None:
        """Mark a job as completed."""
        await self._redis.srem(self.PROCESSING_SET, job_id)
        await self._redis.hincrby(self.STATS_KEY, "total_completed", 1)
        await self._redis.hincrby(self.STATS_KEY, f"outcome_{outcome}", 1)
        logger.debug(f"Job {job_id} marked completed: {outcome}")
    
    async def mark_failed(self, job_id: str, error: str) -> None:
        """Mark a job as failed."""
        await self._redis.srem(self.PROCESSING_SET, job_id)
        await self._redis.hincrby(self.STATS_KEY, "total_failed", 1)
        logger.debug(f"Job {job_id} marked failed: {error}")
    
    async def _mark_processing(self, job_id: str) -> None:
        """Mark a job as currently being processed."""
        await self._redis.sadd(self.PROCESSING_SET, job_id)
        await self._redis.hincrby(self.STATS_KEY, "total_dequeued", 1)
    
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
            processing_count = await self._redis.scard(self.PROCESSING_SET)
            
            # Get stats hash
            stats = await self._redis.hgetall(self.STATS_KEY) or {}
            
            return {
                "priority_queue_length": priority_len,
                "scheduled_jobs": scheduled_count,
                "processing_jobs": processing_count,
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
                
                # Clear scheduled and processing
                await self._redis.delete(self.SCHEDULED_ZSET)
                await self._redis.delete(self.PROCESSING_SET)
            
            logger.info(f"Cleared {count} jobs from queue")
            return count
            
        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")
            return 0
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._initialized = False
