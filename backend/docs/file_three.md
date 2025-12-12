# Talky.ai Backend Documentation - Part 3
# Domain Models & Services

## Table of Contents
1. [Domain Architecture](#domain-architecture)
2. [DialerJob Model](#dialerjob-model)
3. [CallSession Model](#callsession-model)
4. [CallingRules Model](#callingrules-model)
5. [Other Domain Models](#other-domain-models)
6. [Queue Service](#queue-service)
7. [Voice Pipeline Service](#voice-pipeline-service)
8. [Dialer Worker](#dialer-worker)

---

## Domain Architecture

The domain layer follows Domain-Driven Design principles:

```
app/domain/
├── interfaces/              # Abstract base classes (contracts)
│   ├── llm_provider.py      # LLM provider interface
│   ├── stt_provider.py      # STT provider interface
│   ├── tts_provider.py      # TTS provider interface
│   ├── telephony_provider.py # Telephony interface
│   └── media_gateway.py     # Media gateway interface
│
├── models/                  # Pydantic data models
│   ├── agent_config.py      # AI agent configuration
│   ├── call.py              # Call model
│   ├── calling_rules.py     # Scheduling rules
│   ├── campaign.py          # Campaign model
│   ├── conversation.py      # Conversation model
│   ├── conversation_state.py # State machine model
│   ├── dialer_job.py        # Dialer job model
│   ├── lead.py              # Lead/contact model
│   ├── session.py           # Call session model
│   └── websocket_messages.py # WebSocket message types
│
└── services/                # Business logic services
    ├── conversation_engine.py    # Conversation state machine
    ├── latency_tracker.py        # Performance tracking
    ├── prompt_manager.py         # LLM prompt templates
    ├── queue_service.py          # Redis job queue
    ├── scheduling_rules.py       # Call scheduling
    ├── session_manager.py        # Session lifecycle
    └── voice_pipeline_service.py # STT→LLM→TTS pipeline
```

---

## DialerJob Model

**File:** `app/domain/models/dialer_job.py` (177 lines)

Represents a single outbound call job in the dialer queue.

### Enums

```python
class JobStatus(str, Enum):
    """Status of a dialer job"""
    PENDING = "pending"           # Waiting in queue
    PROCESSING = "processing"     # Currently being processed
    COMPLETED = "completed"       # Successfully completed
    FAILED = "failed"             # Failed (retryable)
    RETRY_SCHEDULED = "retry_scheduled"  # Scheduled for retry
    SKIPPED = "skipped"           # Skipped (time window, limit)
    GOAL_ACHIEVED = "goal_achieved"  # Goal achieved
    NON_RETRYABLE = "non_retryable"  # Permanent failure

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

### Retry Constants

```python
# Module-level constants
RETRY_DELAY_SECONDS = 7200  # 2 hours between retries
MAX_ATTEMPTS = 3             # Maximum retry attempts

# Retryable outcomes (will trigger retry)
RETRYABLE_OUTCOMES = {"busy", "no_answer", "timeout", "failed"}

# Non-retryable outcomes (permanent failure)
NON_RETRYABLE_OUTCOMES = {"spam", "invalid", "unavailable", "disconnected", "rejected"}

# Success outcomes
GOAL_OUTCOMES = {"goal_achieved", "answered"}
```

### DialerJob Fields

```python
class DialerJob(BaseModel):
    # Identity
    job_id: str          # Unique job identifier (UUID)
    campaign_id: str     # Campaign this job belongs to
    lead_id: str         # Lead to call
    tenant_id: str       # Tenant for rule lookups
    
    # Call details
    phone_number: str    # Phone number to dial
    
    # Priority (1-10, higher = more urgent)
    # Priority >= 8 goes to priority queue
    priority: int = 5
    
    # Status tracking
    status: JobStatus = JobStatus.PENDING
    attempt_number: int = 1  # Current attempt (1-based)
    
    # Timing
    scheduled_at: datetime
    created_at: datetime
    processed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Result tracking
    last_outcome: Optional[CallOutcome] = None
    last_error: Optional[str] = None
    call_id: Optional[str] = None  # Reference to calls table
```

### Retry Logic Method

```python
def should_retry(self, goal_achieved: bool = False) -> tuple[bool, str]:
    """
    Determine if this job should be retried.
    
    Returns:
        (should_retry: bool, reason: str)
    """
    # Rule 1: Never retry if goal achieved
    if goal_achieved:
        return False, "goal_achieved"
    
    if self.last_outcome in GOAL_OUTCOMES:
        return False, f"goal_outcome_{self.last_outcome}"
    
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

### Redis Serialization

```python
def to_redis_dict(self) -> dict:
    """Serialize for Redis storage"""
    return {
        "job_id": self.job_id,
        "campaign_id": self.campaign_id,
        "lead_id": self.lead_id,
        "tenant_id": self.tenant_id,
        "phone_number": self.phone_number,
        "priority": self.priority,
        "status": self.status,
        "attempt_number": self.attempt_number,
        "scheduled_at": self.scheduled_at.isoformat(),
        "created_at": self.created_at.isoformat(),
        "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        "last_outcome": self.last_outcome,
        "last_error": self.last_error,
        "call_id": self.call_id
    }

@classmethod
def from_redis_dict(cls, data: dict) -> "DialerJob":
    """Deserialize from Redis storage"""
    for dt_field in ["scheduled_at", "created_at", "processed_at", "completed_at"]:
        if data.get(dt_field) and isinstance(data[dt_field], str):
            data[dt_field] = datetime.fromisoformat(data[dt_field])
    return cls(**data)
```

---

## CallSession Model

**File:** `app/domain/models/session.py` (219 lines)

Runtime state for an active call. Lives in memory + Redis during call.

### CallState Enum

```python
class CallState(str, Enum):
    """Call session state"""
    CONNECTING = "connecting"   # WebSocket connecting
    ACTIVE = "active"           # Call in progress
    LISTENING = "listening"     # Waiting for user speech
    PROCESSING = "processing"   # STT/LLM/TTS processing
    SPEAKING = "speaking"       # AI speaking
    ENDING = "ending"           # Graceful shutdown
    ENDED = "ended"             # Call completed
    ERROR = "error"             # Unrecoverable error
```

### LatencyMetric Model

```python
class LatencyMetric(BaseModel):
    """Single latency measurement"""
    component: str       # "stt", "llm", "tts", "total"
    latency_ms: float    # Latency in milliseconds
    timestamp: datetime
    turn_id: int         # Turn number
    success: bool = True
    error_message: Optional[str] = None
```

### CallSession Fields

```python
class CallSession(BaseModel):
    # Identity
    call_id: str          # Unique call identifier (UUID)
    campaign_id: str      # Campaign this call belongs to
    lead_id: str          # Lead/contact being called
    tenant_id: Optional[str] = None
    
    # Connection State
    vonage_call_uuid: str  # Vonage's call UUID
    state: CallState = CallState.CONNECTING
    
    # Conversation State
    conversation_history: List[Message] = []
    current_user_input: str = ""       # Accumulating transcript
    current_ai_response: str = ""      # Accumulating LLM output
    turn_id: int = 0
    
    # Streaming State
    stt_active: bool = False
    llm_active: bool = False
    tts_active: bool = False
    user_speaking: bool = False
    ai_speaking: bool = False
    
    # Timing & Metrics
    started_at: datetime
    last_activity_at: datetime
    total_user_speech_ms: int = 0
    total_ai_speech_ms: int = 0
    latency_measurements: List[LatencyMetric] = []
    
    # Configuration
    system_prompt: str     # AI system prompt from campaign
    voice_id: str          # TTS voice identifier
    language: str = "en"
    
    # Conversation Engine State
    conversation_state: ConversationState = ConversationState.GREETING
    conversation_context: ConversationContext
    agent_config: Optional[AgentConfig] = None
    
    # Runtime-Only Fields (NOT serialized to Redis)
    websocket: Optional[Any] = None
    audio_input_buffer: Optional[Any] = None
    audio_output_buffer: Optional[Any] = None
    transcript_buffer: Optional[Any] = None
```

### Session Methods

```python
def model_dump_redis(self) -> dict:
    """Serialize to dict for Redis storage (excludes runtime fields)"""
    return self.model_dump(
        exclude={'websocket', 'audio_input_buffer', 
                 'audio_output_buffer', 'transcript_buffer'},
        mode='json'
    )

@classmethod
def from_redis_dict(cls, data: dict, websocket=None) -> "CallSession":
    """Deserialize from Redis, recreating runtime fields"""
    # Convert datetime strings
    # Convert latency measurements
    # Convert conversation history
    session = cls(**data)
    
    # Recreate runtime fields
    session.websocket = websocket
    session.audio_input_buffer = asyncio.Queue(maxsize=100)
    session.audio_output_buffer = asyncio.Queue(maxsize=100)
    session.transcript_buffer = asyncio.Queue(maxsize=50)
    return session

def update_activity(self):
    """Update last activity timestamp"""
    self.last_activity_at = datetime.utcnow()

def add_latency_measurement(self, component: str, latency_ms: float, 
                            success: bool = True, error_message: str = None):
    """Add a latency measurement"""
    metric = LatencyMetric(
        component=component,
        latency_ms=latency_ms,
        turn_id=self.turn_id,
        success=success,
        error_message=error_message
    )
    self.latency_measurements.append(metric)

def is_stale(self, timeout_seconds: int = 300) -> bool:
    """Check if session has been inactive too long"""
    elapsed = (datetime.utcnow() - self.last_activity_at).total_seconds()
    return elapsed > timeout_seconds

def get_duration_seconds(self) -> float:
    """Get total call duration in seconds"""
    return (datetime.utcnow() - self.started_at).total_seconds()

def increment_turn(self):
    """Increment turn counter and reset current inputs"""
    self.turn_id += 1
    self.current_user_input = ""
    self.current_ai_response = ""

def get_average_latency(self, component: str = None) -> float:
    """Get average latency for a component or overall"""
    # Filters measurements by component and calculates average
```

---

## CallingRules Model

**File:** `app/domain/models/calling_rules.py` (192 lines)

Tenant-configurable rules for outbound calling. Stored as JSONB in database.

### CallingRules Fields

```python
class CallingRules(BaseModel):
    # Time Window Configuration
    time_window_start: str = "09:00"   # HH:MM format
    time_window_end: str = "19:00"     # HH:MM format
    timezone: str = "America/New_York"
    allowed_days: List[int] = [0, 1, 2, 3, 4]  # 0=Monday, 6=Sunday
    
    # Concurrency Limits
    max_concurrent_calls: int = 10     # 1-100
    
    # Retry Settings
    retry_delay_seconds: int = 7200    # 1800-86400 (30min-24hr)
    max_retry_attempts: int = 3        # 1-5
    
    # Priority Settings
    enable_priority_override: bool = True
    high_priority_threshold: int = 8   # 1-10
    
    # Lead Filters
    skip_dnc: bool = True              # Skip Do Not Call list
    min_hours_between_calls: int = 2   # 1-24
    
    # Caller ID
    caller_id: Optional[str] = None    # Default caller ID
```

### Time Window Check

```python
def is_within_time_window(self, check_time: datetime = None) -> tuple[bool, str]:
    """
    Check if current time is within the calling window.
    
    Returns:
        (is_allowed: bool, reason: str)
    """
    # Get timezone
    tz = pytz.timezone(self.timezone)
    
    if check_time is None:
        check_time = datetime.now(tz)
    
    # Check day of week (0=Monday)
    current_day = check_time.weekday()
    if current_day not in self.allowed_days:
        return False, f"calling_not_allowed_on_{day_names[current_day]}"
    
    # Parse time window
    start_hour, start_min = map(int, self.time_window_start.split(":"))
    end_hour, end_min = map(int, self.time_window_end.split(":"))
    
    start_time = time(start_hour, start_min)
    end_time = time(end_hour, end_min)
    current_time = check_time.time()
    
    # Check if within window
    if start_time <= current_time <= end_time:
        return True, "within_time_window"
    else:
        return False, f"outside_time_window_{self.time_window_start}_{self.time_window_end}"
```

### Next Window Calculation

```python
def get_next_window_start(self, from_time: datetime = None) -> datetime:
    """
    Get the next time the calling window opens.
    """
    tz = pytz.timezone(self.timezone)
    
    if from_time is None:
        from_time = datetime.now(tz)
    
    start_hour, start_min = map(int, self.time_window_start.split(":"))
    
    # Check if we can call today
    today_start = from_time.replace(hour=start_hour, minute=start_min)
    
    if from_time.weekday() in self.allowed_days and from_time < today_start:
        return today_start
    
    # Find next allowed day
    check_date = from_time.date()
    for _ in range(7):
        check_date = check_date + timedelta(days=1)
        if check_date.weekday() in self.allowed_days:
            next_window = datetime.combine(check_date, time(start_hour, start_min))
            return tz.localize(next_window)
    
    return today_start + timedelta(days=1)
```

---

## Other Domain Models

### Campaign Model

**File:** `app/domain/models/campaign.py`

```python
class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Campaign(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: CampaignStatus = CampaignStatus.DRAFT
    system_prompt: str
    voice_id: str
    max_concurrent_calls: int = 10
    retry_failed: bool = True
    max_retries: int = 3
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_leads: int = 0
    calls_completed: int = 0
    calls_failed: int = 0
    
    # Added fields
    goal: Optional[str] = None
    script_config: Optional[dict] = None
    calling_config: Optional[dict] = None
```

### Lead Model

**File:** `app/domain/models/lead.py`

```python
class Lead(BaseModel):
    id: str
    campaign_id: str
    phone_number: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    custom_fields: Dict[str, Any] = {}
    created_at: datetime
    last_called_at: Optional[datetime] = None
    call_attempts: int = 0
    status: str = "pending"   # pending, called, completed, dnc
    last_call_result: Optional[str] = "pending"  # Quick status lookup
```

### Conversation Models

**File:** `app/domain/models/conversation.py`

```python
class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

class Message(BaseModel):
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    audio_url: Optional[str] = None

class AudioChunk(BaseModel):
    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    duration_ms: Optional[float] = None

class TranscriptChunk(BaseModel):
    text: str
    is_final: bool = False
    confidence: Optional[float] = None
    speaker_id: Optional[str] = None
```

---

## Queue Service

**File:** `app/domain/services/queue_service.py` (407 lines)

Redis-based job queue with priority support.

### Queue Architecture

```
Redis Key Structure:
├── dialer:priority:queue       # High priority jobs (List, LIFO)
├── dialer:tenant:{id}:queue    # Tenant-specific queues (List, FIFO)
├── dialer:scheduled            # Delayed retries (Sorted Set)
├── dialer:processing           # Jobs being processed (Set)
└── dialer:stats                # Statistics (Hash)
```

### Key Methods

```python
class DialerQueueService:
    HIGH_PRIORITY_THRESHOLD = 8
    
    async def initialize(self) -> None:
        """Initialize Redis connection"""
        redis_url = self._config.get("redis_url", "redis://localhost:6379")
        self._redis = await redis.from_url(redis_url, decode_responses=True)
        await self._redis.ping()
    
    async def enqueue_job(self, job: DialerJob) -> bool:
        """
        Enqueue a dialer job.
        - Priority >= 8: Goes to priority queue (LPUSH for LIFO)
        - Priority < 8: Goes to tenant queue (RPUSH for FIFO)
        """
        job_data = json.dumps(job.to_redis_dict())
        
        if job.priority >= self.HIGH_PRIORITY_THRESHOLD:
            await self._redis.lpush(self.PRIORITY_QUEUE, job_data)
        else:
            queue_key = f"dialer:tenant:{job.tenant_id}:queue"
            await self._redis.rpush(queue_key, job_data)
        
        await self._redis.hincrby(self.STATS_KEY, "total_enqueued", 1)
        return True
    
    async def dequeue_job(self, tenant_ids: List[str] = None, 
                          timeout: int = 5) -> Optional[DialerJob]:
        """
        Dequeue next job to process.
        
        Priority order:
        1. Priority queue (checked first)
        2. Tenant queues (round-robin)
        """
        # 1. Check priority queue
        job_data = await self._redis.lpop(self.PRIORITY_QUEUE)
        if job_data:
            job = DialerJob.from_redis_dict(json.loads(job_data))
            await self._mark_processing(job.job_id)
            return job
        
        # 2. Check tenant queues
        if tenant_ids:
            for tenant_id in tenant_ids:
                queue_key = f"dialer:tenant:{tenant_id}:queue"
                job_data = await self._redis.lpop(queue_key)
                if job_data:
                    job = DialerJob.from_redis_dict(json.loads(job_data))
                    await self._mark_processing(job.job_id)
                    return job
        
        return None
    
    async def schedule_retry(self, job: DialerJob, 
                             delay_seconds: int = 7200) -> bool:
        """
        Schedule job for retry using Redis Sorted Set.
        Score = Unix timestamp when job should run.
        """
        retry_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
        job.attempt_number += 1
        job.scheduled_at = retry_at
        job.status = JobStatus.RETRY_SCHEDULED
        
        job_data = json.dumps(job.to_redis_dict())
        await self._redis.zadd(
            self.SCHEDULED_ZSET,
            {job_data: retry_at.timestamp()}
        )
        return True
    
    async def process_scheduled_jobs(self) -> int:
        """
        Move due scheduled jobs back to their queues.
        Returns number of jobs moved.
        """
        now = datetime.utcnow().timestamp()
        
        # Get all jobs due now
        due_jobs = await self._redis.zrangebyscore(
            self.SCHEDULED_ZSET,
            min=0,
            max=now
        )
        
        moved = 0
        for job_data in due_jobs:
            job = DialerJob.from_redis_dict(json.loads(job_data))
            job.status = JobStatus.PENDING
            
            await self.enqueue_job(job)
            await self._redis.zrem(self.SCHEDULED_ZSET, job_data)
            moved += 1
        
        return moved
    
    async def get_queue_stats(self) -> dict:
        """Get queue statistics"""
        # Returns counts for priority queue, tenant queues, scheduled, processing
```

---

## Voice Pipeline Service

**File:** `app/domain/services/voice_pipeline_service.py` (587 lines)

Orchestrates the full voice AI pipeline: STT → LLM → TTS

### Pipeline Architecture

```
Audio Input → Deepgram STT → Turn Detection → Groq LLM → Cartesia TTS → Audio Output
```

### Key Methods

```python
class VoicePipelineService:
    def __init__(
        self,
        stt_provider: DeepgramFluxSTTProvider,
        llm_provider: GroqLLMProvider,
        tts_provider: CartesiaTTSProvider,
        media_gateway: MediaGateway
    ):
        self.stt_provider = stt_provider
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.media_gateway = media_gateway
        self.prompt_manager = PromptManager()
        self._active_pipelines: dict[str, bool] = {}
    
    async def start_pipeline(self, session: CallSession, 
                             websocket: WebSocket = None) -> None:
        """Start voice pipeline for a call session"""
        call_id = session.call_id
        self._active_pipelines[call_id] = True
        
        session.state = CallState.ACTIVE
        session.stt_active = True
        
        audio_queue = self.media_gateway.get_audio_queue(call_id)
        await self.process_audio_stream(session, audio_queue, websocket)
    
    async def process_audio_stream(self, session: CallSession, 
                                   audio_queue: asyncio.Queue,
                                   websocket: WebSocket = None) -> None:
        """
        Process audio stream through STT pipeline.
        Creates async generator from queue and streams to Deepgram.
        """
        async def audio_stream() -> AsyncIterator[AudioChunk]:
            while self._active_pipelines.get(call_id, False):
                try:
                    audio_data = await asyncio.wait_for(
                        audio_queue.get(), timeout=0.1
                    )
                    yield AudioChunk(data=audio_data, sample_rate=16000, channels=1)
                except asyncio.TimeoutError:
                    continue
        
        async for transcript in self.stt_provider.stream_transcribe(
            audio_stream(), language="en"
        ):
            await self.handle_transcript(session, transcript, websocket)
    
    async def handle_transcript(self, session: CallSession, 
                                transcript: TranscriptChunk,
                                websocket: WebSocket = None) -> None:
        """
        Handle transcript chunk from STT.
        - Accumulates text
        - Detects end of turn
        - Triggers LLM response generation
        """
        # Add to current input
        session.current_user_input += " " + transcript.text
        
        # Check for end of turn
        if transcript.is_final:
            await self.generate_response(session, websocket)
    
    async def generate_response(self, session: CallSession, 
                                websocket: WebSocket = None) -> None:
        """
        Generate AI response using LLM and synthesize with TTS.
        """
        # Build messages list
        messages = session.conversation_history.copy()
        messages.append(Message(role=MessageRole.USER, content=session.current_user_input))
        
        # Generate LLM response (streaming)
        session.llm_active = True
        full_response = ""
        
        async for token in self.llm_provider.stream_chat(
            messages=messages,
            system_prompt=session.system_prompt,
            temperature=0.6,
            max_tokens=100
        ):
            full_response += token
            session.current_ai_response = full_response
        
        session.llm_active = False
        
        # Add to history
        session.conversation_history.append(
            Message(role=MessageRole.USER, content=session.current_user_input)
        )
        session.conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content=full_response)
        )
        
        # Synthesize speech
        await self.synthesize_and_play(session, full_response, websocket)
        
        # Increment turn
        session.increment_turn()
    
    async def synthesize_and_play(self, session: CallSession, 
                                  text: str,
                                  websocket: WebSocket = None) -> None:
        """
        Synthesize text to speech and stream to media gateway.
        """
        session.tts_active = True
        session.ai_speaking = True
        
        async for audio_chunk in self.tts_provider.stream_synthesize(
            text=text,
            voice_id=session.voice_id,
            sample_rate=16000
        ):
            await self.media_gateway.send_audio(session.call_id, audio_chunk.data)
        
        session.tts_active = False
        session.ai_speaking = False
```

---

## Dialer Worker

**File:** `app/workers/dialer_worker.py` (463 lines)

Background worker for processing outbound call jobs.

### Worker Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DIALER WORKER                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Runs as separate process from FastAPI                                      │
│  Connects to same Redis and Supabase instances                              │
│                                                                             │
│  Main Loop:                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  1. Check for due scheduled retries                                  │   │
│  │  2. Get active tenant IDs                                           │   │
│  │  3. Dequeue next job                                                │   │
│  │  4. Process job (check rules, initiate call)                        │   │
│  │  5. Handle result (success, retry, or fail)                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Configuration

```python
class DialerWorker:
    POLL_INTERVAL = 1.0          # Seconds between queue checks when empty
    SCHEDULED_CHECK_INTERVAL = 60  # Seconds between scheduled job checks
    MAX_CONSECUTIVE_ERRORS = 10   # Max errors before stopping
    API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
```

### Run Command

```bash
python -m app.workers.dialer_worker
```

### Key Methods

```python
async def initialize(self) -> None:
    """Initialize connections to Redis and Supabase"""
    await self.queue_service.initialize()
    
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    self._supabase = create_client(supabase_url, supabase_key)
    
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    self._redis = await redis.from_url(redis_url, decode_responses=True)

async def run(self) -> None:
    """Main worker loop"""
    await self.initialize()
    self.running = True
    
    while self.running:
        # 1. Check for due scheduled jobs
        if (datetime.utcnow() - self._last_scheduled_check).total_seconds() > 60:
            moved = await self.queue_service.process_scheduled_jobs()
            self._last_scheduled_check = datetime.utcnow()
        
        # 2. Get active tenants
        tenant_ids = await self._get_active_tenant_ids()
        
        # 3. Dequeue next job
        job = await self.queue_service.dequeue_job(tenant_ids=tenant_ids)
        
        if job:
            await self.process_job(job)
        else:
            await asyncio.sleep(self.POLL_INTERVAL)

async def process_job(self, job: DialerJob) -> None:
    """
    Process a single dialer job.
    
    Steps:
    1. Get tenant calling rules
    2. Check if we can make call now
    3. Initiate the call
    4. Create call record in database
    """
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
        # Calculate delay and reschedule
        if "time_window" in reason:
            delay = self.rules_engine.get_delay_until_next_window(rules)
        else:
            delay = 300  # 5 minutes for other reasons
        
        await self.queue_service.schedule_retry(job, delay_seconds=delay)
        await self._update_job_status(job.job_id, JobStatus.SKIPPED, reason=reason)
        return
    
    # 4. Register call start (for concurrent tracking)
    self.rules_engine.register_call_start(job.tenant_id, job.campaign_id)
    
    # 5. Initiate call via telephony provider
    # 6. Create call record in database
    # 7. Update job status
```

---

## Next File

Continue to **file_four.md** for:
- Infrastructure Providers (LLM, STT, TTS, Telephony)
- Tests documentation
- Configuration files
