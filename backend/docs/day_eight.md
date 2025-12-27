# Day 8: Dialer Engine - Queue Management & Retry Logic

## Overview

**Date:** Week 2, Day 8  
**Goal:** Build the dialer engine with Redis-based job queuing, priority handling, and smart retry logic.

This document covers the dialer job model, queue service implementation, priority-based routing, and intelligent retry mechanisms.

---

## Table of Contents

1. [Dialer Architecture Overview](#1-dialer-architecture-overview)
2. [Dialer Job Model](#2-dialer-job-model)
3. [Queue Service Implementation](#3-queue-service-implementation)
4. [Priority-Based Routing](#4-priority-based-routing)
5. [Retry Logic](#5-retry-logic)
6. [Webhook Integration](#6-webhook-integration)
7. [Test Results & Verification](#7-test-results--verification)
8. [Rationale Summary](#8-rationale-summary)

---

## 1. Dialer Architecture Overview

### 1.1 High-Level Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Campaign   │     │    Redis     │     │   Dialer     │
│   Start API  │────►│    Queues    │────►│   Worker     │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                     ┌────────────────────────────┘
                     │
                     ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Vonage     │────►│   Webhook    │────►│   Retry      │
│   Call       │     │   Handler    │     │   Logic      │
└──────────────┘     └──────────────┘     └──────────────┘
```

### 1.2 Redis Queue Structure

| Key Pattern | Data Type | Purpose |
|-------------|-----------|---------|
| `dialer:priority:queue` | List | High-priority jobs (checked first) |
| `dialer:tenant:{id}:queue` | List | Tenant-specific FIFO queues |
| `dialer:scheduled` | Sorted Set | Delayed retry jobs |
| `dialer:processing` | Set | Jobs currently being processed |
| `dialer:stats` | Hash | Queue statistics |

---

## 2. Dialer Job Model

### 2.1 Job Status Enum

**File: `app/domain/models/dialer_job.py`**

```python
class JobStatus(str, Enum):
    """Status of a dialer job"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    SKIPPED = "skipped"           # Time window, limit reached
    GOAL_ACHIEVED = "goal_achieved"
    NON_RETRYABLE = "non_retryable"  # Spam, invalid, etc.
```

### 2.2 Call Outcome Enum

```python
class CallOutcome(str, Enum):
    """Outcome of a call attempt"""
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SPAM = "spam"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    DISCONNECTED = "disconnected"
    GOAL_ACHIEVED = "goal_achieved"
    GOAL_NOT_ACHIEVED = "goal_not_achieved"
    VOICEMAIL = "voicemail"
    REJECTED = "rejected"
```

### 2.3 DialerJob Model

```python
class DialerJob(BaseModel):
    """Represents a single outbound call job."""
    
    # Identity
    job_id: str                    # Unique job identifier (UUID)
    campaign_id: str               # Campaign this job belongs to
    lead_id: str                   # Lead to call
    tenant_id: str                 # Tenant for rule lookups
    
    # Call details
    phone_number: str              # Phone number to dial
    
    # Priority (1-10, higher = more urgent)
    priority: int = Field(default=5, ge=1, le=10)
    
    # Status tracking
    status: JobStatus = JobStatus.PENDING
    attempt_number: int = Field(default=1, ge=1)
    
    # Timing
    scheduled_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Result tracking
    last_outcome: Optional[CallOutcome] = None
    last_error: Optional[str] = None
    call_id: Optional[str] = None
```

### 2.4 Redis Serialization

```python
def to_redis_dict(self) -> dict:
    """Serialize for Redis storage."""
    return {
        "job_id": self.job_id,
        "campaign_id": self.campaign_id,
        "lead_id": self.lead_id,
        "tenant_id": self.tenant_id,
        "phone_number": self.phone_number,
        "priority": self.priority,
        "status": self.status.value,
        "attempt_number": self.attempt_number,
        "scheduled_at": self.scheduled_at.isoformat(),
        "last_outcome": self.last_outcome.value if self.last_outcome else None
    }

@classmethod
def from_redis_dict(cls, data: dict) -> "DialerJob":
    """Deserialize from Redis storage."""
    for dt_field in ["scheduled_at", "created_at", "processed_at"]:
        if data.get(dt_field) and isinstance(data[dt_field], str):
            data[dt_field] = datetime.fromisoformat(data[dt_field])
    return cls(**data)
```

---

## 3. Queue Service Implementation

### 3.1 Service Class

**File: `app/domain/services/queue_service.py`**

```python
class DialerQueueService:
    """
    Redis-based job queue for the dialer engine.
    
    Uses Redis Lists for FIFO queuing with priority support.
    """
    
    PRIORITY_QUEUE = "dialer:priority:queue"
    TENANT_QUEUE_PREFIX = "dialer:tenant:{tenant_id}:queue"
    SCHEDULED_ZSET = "dialer:scheduled"
    PROCESSING_SET = "dialer:processing"
    
    HIGH_PRIORITY_THRESHOLD = 8  # Priority >= 8 goes to priority queue
    
    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._initialized = False
```

### 3.2 Initialize Connection

```python
async def initialize(self) -> None:
    """Initialize Redis connection."""
    if not REDIS_AVAILABLE:
        logger.warning("Redis not available")
        return
    
    redis_url = self._config.get("redis_url", "redis://localhost:6379")
    self._redis = await redis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True
    )
    await self._redis.ping()
    self._initialized = True
```

### 3.3 Enqueue Job

```python
async def enqueue_job(self, job: DialerJob) -> bool:
    """Enqueue a dialer job with priority routing."""
    
    job_data = json.dumps(job.to_redis_dict())
    
    if job.priority >= self.HIGH_PRIORITY_THRESHOLD:
        # High priority - goes to priority queue (LPUSH for stack behavior)
        await self._redis.lpush(self.PRIORITY_QUEUE, job_data)
        logger.info(f"Enqueued high-priority job {job.job_id}")
    else:
        # Normal priority - tenant-specific queue (RPUSH for FIFO)
        queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=job.tenant_id)
        await self._redis.rpush(queue_key, job_data)
    
    await self._redis.hincrby(self.STATS_KEY, "total_enqueued", 1)
    return True
```

### 3.4 Dequeue Job

```python
async def dequeue_job(self, tenant_ids: Optional[List[str]] = None) -> Optional[DialerJob]:
    """
    Dequeue next job with priority:
    1. Priority queue (high-priority jobs)
    2. Tenant queues (round-robin)
    """
    # 1. Check priority queue first
    job_data = await self._redis.lpop(self.PRIORITY_QUEUE)
    if job_data:
        job = DialerJob.from_redis_dict(json.loads(job_data))
        await self._mark_processing(job.job_id)
        return job
    
    # 2. Check tenant queues
    if tenant_ids:
        for tenant_id in tenant_ids:
            queue_key = self.TENANT_QUEUE_PREFIX.format(tenant_id=tenant_id)
            job_data = await self._redis.lpop(queue_key)
            if job_data:
                job = DialerJob.from_redis_dict(json.loads(job_data))
                await self._mark_processing(job.job_id)
                return job
    
    return None
```

---

## 4. Priority-Based Routing

### 4.1 Priority Calculation

```python
# In campaigns.py - start_campaign endpoint
for lead in leads:
    base_priority = lead.get("priority", 5)
    
    # High-value customers get priority boost
    if lead.get("is_high_value"):
        base_priority = min(base_priority + 2, 10)
    
    # Urgent tags get priority boost
    lead_tags = lead.get("tags", []) or []
    if "urgent" in lead_tags or "appointment" in lead_tags:
        base_priority = min(base_priority + 1, 10)
    
    job = DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        lead_id=lead["id"],
        phone_number=lead["phone_number"],
        priority=base_priority
    )
    
    await queue_service.enqueue_job(job)
```

### 4.2 Priority Queue Behavior

| Priority | Queue | Processing Order |
|----------|-------|------------------|
| 8-10 | Priority Queue | First (LIFO within priority) |
| 1-7 | Tenant Queue | Second (FIFO per tenant) |

---

## 5. Retry Logic

### 5.1 Retry Constants

```python
RETRY_DELAY_SECONDS = 7200  # 2 hours between retries
MAX_ATTEMPTS = 3

# Outcomes that trigger retry
RETRYABLE_OUTCOMES = {"busy", "no_answer", "timeout", "failed", "voicemail"}

# Outcomes that should NOT retry
NON_RETRYABLE_OUTCOMES = {"spam", "invalid", "unavailable", "disconnected", "rejected"}

# Success outcomes
GOAL_OUTCOMES = {"goal_achieved", "answered"}
```

### 5.2 Retry Decision Logic

```python
def should_retry(self, goal_achieved: bool = False) -> tuple[bool, str]:
    """Determine if this job should be retried."""
    
    # Rule 1: Never retry if goal achieved
    if goal_achieved or self.last_outcome in GOAL_OUTCOMES:
        return False, "goal_achieved"
    
    # Rule 2: Never retry spam/invalid/unavailable
    if self.last_outcome in NON_RETRYABLE_OUTCOMES:
        return False, f"non_retryable_{self.last_outcome}"
    
    # Rule 3: Max attempts reached
    if self.attempt_number >= MAX_ATTEMPTS:
        return False, "max_attempts_reached"
    
    # Rule 4: Retry only busy/no-pickup/timeout
    if self.last_outcome in RETRYABLE_OUTCOMES:
        return True, f"retrying_{self.last_outcome}"
    
    return False, f"unknown_outcome_{self.last_outcome}"
```

### 5.3 Schedule Retry

```python
async def schedule_retry(self, job: DialerJob, delay_seconds: int = 7200) -> bool:
    """Schedule a job for retry after delay."""
    
    job.attempt_number += 1
    job.status = JobStatus.RETRY_SCHEDULED
    job.scheduled_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
    
    # Calculate execution time as Unix timestamp
    execute_at = datetime.utcnow().timestamp() + delay_seconds
    
    job_data = json.dumps(job.to_redis_dict())
    await self._redis.zadd(self.SCHEDULED_ZSET, {job_data: execute_at})
    
    # Remove from processing set
    await self._redis.srem(self.PROCESSING_SET, job.job_id)
    
    logger.info(f"Scheduled retry for job {job.job_id} (attempt {job.attempt_number})")
    return True
```

### 5.4 Process Scheduled Jobs

```python
async def process_scheduled_jobs(self) -> int:
    """Move due scheduled jobs back to their queues."""
    
    now = datetime.utcnow().timestamp()
    
    # Get all jobs due for processing
    due_jobs = await self._redis.zrangebyscore(self.SCHEDULED_ZSET, 0, now)
    
    count = 0
    for job_data in due_jobs:
        job = DialerJob.from_redis_dict(json.loads(job_data))
        job.status = JobStatus.PENDING
        
        # Remove from scheduled set
        await self._redis.zrem(self.SCHEDULED_ZSET, job_data)
        
        # Re-enqueue
        await self.enqueue_job(job)
        count += 1
    
    return count
```

---

## 6. Webhook Integration

### 6.1 Status Mapping

```python
VONAGE_STATUS_MAP = {
    "answered": CallOutcome.ANSWERED,
    "completed": CallOutcome.GOAL_NOT_ACHIEVED,
    "busy": CallOutcome.BUSY,
    "timeout": CallOutcome.NO_ANSWER,
    "failed": CallOutcome.FAILED,
    "rejected": CallOutcome.REJECTED,
    "machine": CallOutcome.VOICEMAIL,
}

RETRYABLE_OUTCOMES = {
    CallOutcome.BUSY,
    CallOutcome.NO_ANSWER,
    CallOutcome.FAILED,
    CallOutcome.VOICEMAIL,
}

NON_RETRYABLE_OUTCOMES = {
    CallOutcome.SPAM,
    CallOutcome.INVALID,
    CallOutcome.UNAVAILABLE,
    CallOutcome.REJECTED,
}
```

### 6.2 Job Completion Handler

```python
async def handle_job_completion(job_id, outcome, campaign_id, lead_id, supabase):
    """Handle dialer job completion - decide retry or complete."""
    
    job_data = supabase.table("dialer_jobs").select("*").eq("id", job_id).execute()
    attempt_number = job_data.data[0].get("attempt_number", 1)
    
    # Determine if we should retry
    should_retry = False
    final_status = JobStatus.COMPLETED
    
    if outcome == CallOutcome.GOAL_ACHIEVED:
        final_status = JobStatus.GOAL_ACHIEVED
    elif outcome in NON_RETRYABLE_OUTCOMES:
        final_status = JobStatus.NON_RETRYABLE
    elif outcome in RETRYABLE_OUTCOMES and attempt_number < MAX_ATTEMPTS:
        should_retry = True
        final_status = JobStatus.RETRY_SCHEDULED
    else:
        final_status = JobStatus.FAILED
    
    # Update job in database
    supabase.table("dialer_jobs").update({
        "status": final_status.value,
        "last_outcome": outcome.value
    }).eq("id", job_id).execute()
    
    # Schedule retry if needed
    if should_retry:
        queue_service = DialerQueueService()
        await queue_service.schedule_retry(job, delay_seconds=7200)
```

---

## 7. Test Results & Verification

### 7.1 Queue Service Tests

```
tests/unit/test_queue_service.py

TestDialerQueueService
  test_enqueue_job PASSED
  test_enqueue_high_priority_job PASSED
  test_dequeue_priority_first PASSED
  test_dequeue_tenant_queue PASSED
  test_schedule_retry PASSED
  test_process_scheduled_jobs PASSED
  test_queue_stats PASSED

==================== 7 passed in 0.35s ====================
```

### 7.2 Retry Logic Tests

```
tests/unit/test_dialer_job.py

TestDialerJob
  test_should_retry_on_busy PASSED
  test_should_retry_on_no_answer PASSED
  test_should_not_retry_on_goal_achieved PASSED
  test_should_not_retry_on_spam PASSED
  test_should_not_retry_after_max_attempts PASSED
  test_redis_serialization PASSED

==================== 6 passed in 0.12s ====================
```

### 7.3 Integration Test Output

```
======================================================================
  DIALER ENGINE INTEGRATION TEST
======================================================================

Testing Queue Operations...
  - Enqueue 100 jobs: 0.45s
  - Priority routing: PASSED (high priority dequeued first)
  - Tenant isolation: PASSED

Testing Retry Logic...
  - Busy -> Retry scheduled: PASSED
  - No answer -> Retry scheduled: PASSED
  - Spam -> No retry: PASSED
  - Max attempts -> No retry: PASSED

Testing Scheduled Jobs...
  - Schedule 10 retries: PASSED
  - Process due jobs: PASSED (all moved to queues)

Queue Stats:
  - Total enqueued: 110
  - Total completed: 85
  - Total failed: 15
  - Scheduled retries: 10

======================================================================
  ALL TESTS PASSED
======================================================================
```

---

## 8. Rationale Summary

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Queue Backend | Redis | Fast, persistent, supports sorted sets for scheduling |
| Priority Handling | Separate queue | Priority jobs never wait behind normal jobs |
| Retry Delay | 2 hours | Avoid harassing users, different time context |
| Max Attempts | 3 | Balance between persistence and annoyance |
| Tenant Isolation | Separate queues | Fair processing, prevents queue hogging |

### Retry Strategy

| Outcome | Action | Reason |
|---------|--------|--------|
| Busy | Retry in 2h | User may be available later |
| No Answer | Retry in 2h | Different time of day |
| Voicemail | Retry in 2h | May reach person directly |
| Spam | No retry | Number flagged as spam |
| Invalid | No retry | Wrong number |
| Goal Achieved | No retry | Success, no need |

### Files Created/Modified

| File | Purpose |
|------|---------|
| `app/domain/models/dialer_job.py` | Job model with retry logic |
| `app/domain/services/queue_service.py` | Redis queue operations |
| `app/api/v1/endpoints/webhooks.py` | Status handling and retry triggers |
| `app/api/v1/endpoints/campaigns.py` | Job enqueueing on campaign start |

---

*Document Version: 1.0*  
*Last Updated: Day 8 of Development Sprint*
