# Talky.ai Backend Documentation - Part 4
# Infrastructure Providers & Testing

## Table of Contents
1. [Infrastructure Architecture](#infrastructure-architecture)
2. [LLM Provider (Groq)](#llm-provider-groq)
3. [STT Provider (Deepgram)](#stt-provider-deepgram)
4. [TTS Provider (Cartesia)](#tts-provider-cartesia)
5. [Telephony Provider (Vonage)](#telephony-provider-vonage)
6. [Tests Overview](#tests-overview)
7. [Database Schemas](#database-schemas)
8. [Configuration Files](#configuration-files)
9. [File Cross-Reference](#file-cross-reference)

---

## Infrastructure Architecture

The infrastructure layer implements provider interfaces defined in the domain layer:

```
app/infrastructure/
├── llm/                    # Language Model providers
│   ├── factory.py          # LLM factory pattern
│   └── groq.py             # Groq implementation (159 lines)
│
├── stt/                    # Speech-to-Text providers
│   ├── factory.py          # STT factory pattern
│   ├── deepgram.py         # Deepgram basic
│   └── deepgram_flux.py    # Deepgram Flux streaming (221 lines)
│
├── tts/                    # Text-to-Speech providers
│   ├── factory.py          # TTS factory pattern
│   └── cartesia.py         # Cartesia Sonic implementation (140 lines)
│
├── telephony/              # Telephony providers
│   ├── factory.py          # Telephony factory
│   ├── vonage_caller.py    # Vonage call initiation
│   ├── vonage_media_gateway.py  # Vonage WebSocket
│   └── rtp_media_gateway.py     # RTP audio handling
│
└── storage/                # Storage providers
    └── (supabase integration)
```

---

## LLM Provider (Groq)

**File:** `app/infrastructure/llm/groq.py` (159 lines)

Ultra-fast LLM inference using Groq LPU architecture.

### Configuration

```python
class GroqLLMProvider(LLMProvider):
    """
    Recommended models for voice AI (Dec 2025):
    - llama-3.1-8b-instant: 560 t/s - Fastest, ideal for real-time
    - llama-3.3-70b-versatile: 280 t/s - Best quality/speed balance
    - llama-4-scout-17b-16e-instruct: 750 t/s - Preview, very fast
    """
    
    DEFAULT_STOP_SEQUENCES = ["User:", "Human:", "\n\n\n"]
    
    def __init__(self):
        self._client: Optional[AsyncGroq] = None
        self._model: str = "llama-3.3-70b-versatile"
        self._temperature: float = 0.6  # Lower for consistent responses
        self._max_tokens: int = 100      # Concise voice responses
```

### Initialize Method

```python
async def initialize(self, config: dict) -> None:
    """Initialize Groq client"""
    api_key = config.get("api_key") or os.getenv("GROQ_API_KEY")
    
    if not api_key:
        raise ValueError("Groq API key not found")
    
    self._client = AsyncGroq(api_key=api_key)
    
    self._model = config.get("model", "llama-3.3-70b-versatile")
    self._temperature = config.get("temperature", 0.6)
    self._max_tokens = config.get("max_tokens", 100)
```

### Streaming Chat Method

```python
async def stream_chat(
    self,
    messages: List[Message],
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs
) -> AsyncIterator[str]:
    """
    Stream chat completion tokens from Groq.
    
    Following Groq's official guidelines:
    - Temperature 0.2-0.4 for factual, 0.6-0.8 for conversational
    - top_p should be 1.0 when using temperature
    - Stop sequences prevent rambling
    
    Yields:
        str: Token/chunk of response
    """
    # Build messages array for Groq API
    groq_messages = []
    
    # System channel
    if system_prompt:
        groq_messages.append({
            "role": "system",
            "content": system_prompt
        })
    
    # User/Assistant channels
    for msg in messages:
        groq_messages.append({
            "role": msg.role.value,
            "content": msg.content
        })
    
    # Get stop sequences
    stop_sequences = kwargs.get("stop", self.DEFAULT_STOP_SEQUENCES)
    
    # Stream completion
    stream = await self._client.chat.completions.create(
        model=self._model,
        messages=groq_messages,
        temperature=temperature or self._temperature,
        max_tokens=max_tokens or self._max_tokens,
        stream=True,
        top_p=kwargs.get("top_p", 1.0),
        stop=stop_sequences,
        seed=kwargs.get("seed", None)
    )
    
    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
```

---

## STT Provider (Deepgram)

**File:** `app/infrastructure/stt/deepgram_flux.py` (221 lines)

Real-time speech-to-text with ultra-low latency (~260ms).

### Configuration

```python
class DeepgramFluxSTTProvider(STTProvider):
    """
    Deepgram Flux STT with intelligent turn detection.
    Uses SDK v5.3.0 with threading pattern.
    """
    
    def __init__(self):
        self._client: Optional[DeepgramClient] = None
        self._model: str = "flux-general-en"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
```

### Initialize Method

```python
async def initialize(self, config: dict) -> None:
    """Initialize Deepgram client"""
    # SDK v5 auto-loads API key from DEEPGRAM_API_KEY env var
    self._client = DeepgramClient()
    
    self._model = config.get("model", "flux-general-en")
    self._sample_rate = config.get("sample_rate", 16000)
    self._encoding = config.get("encoding", "linear16")
```

### Streaming Transcription

```python
async def stream_transcribe(
    self,
    audio_stream: AsyncIterator[AudioChunk],
    language: str = "en",
    context: Optional[str] = None
) -> AsyncIterator[TranscriptChunk]:
    """
    Stream audio to Deepgram Flux for real-time transcription.
    
    Uses threading pattern for SDK v5.3.0 compatibility:
    1. Sync context manager for Deepgram connection
    2. Background thread for listening
    3. Queue to bridge sync events to async generator
    """
    transcript_queue = queue.Queue()
    stop_event = threading.Event()
    error_container = []
    
    def sync_transcribe():
        """Run Deepgram in sync mode with threading"""
        with self._client.listen.v2.connect(
            model=self._model,
            encoding=self._encoding,
            sample_rate=self._sample_rate
        ) as connection:
            
            def on_message(message):
                if message.type == 'TurnInfo':
                    if getattr(message, 'event', None) == 'EndOfTurn':
                        # Signal end of turn
                        chunk = TranscriptChunk(
                            text="",
                            is_final=True,
                            confidence=1.0
                        )
                        transcript_queue.put(chunk)
                
                elif message.type == 'Results':
                    if message.channel.alternatives:
                        alt = message.channel.alternatives[0]
                        if alt.transcript:
                            chunk = TranscriptChunk(
                                text=alt.transcript,
                                is_final=True,
                                confidence=getattr(alt, 'confidence', None)
                            )
                            transcript_queue.put(chunk)
            
            connection.on(EventType.MESSAGE, on_message)
            
            # Start listening in background
            listen_thread = threading.Thread(
                target=connection.start_listening,
                daemon=True
            )
            listen_thread.start()
            
            # Send audio chunks
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def send_audio():
                async for audio_chunk in audio_stream:
                    if stop_event.is_set():
                        break
                    connection.send_media(audio_chunk.data)
            
            loop.run_until_complete(send_audio())
            loop.close()
        
        transcript_queue.put(None)  # Signal completion
    
    # Run in background thread
    transcribe_thread = threading.Thread(target=sync_transcribe, daemon=True)
    transcribe_thread.start()
    
    # Yield transcripts from queue
    while True:
        try:
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, transcript_queue.get, True, 0.1
            )
            if chunk is None:
                break
            yield chunk
        except queue.Empty:
            if not transcribe_thread.is_alive():
                break
```

---

## TTS Provider (Cartesia)

**File:** `app/infrastructure/tts/cartesia.py` (140 lines)

Ultra-low latency text-to-speech (~90ms) using Cartesia Sonic 3.

### Configuration

```python
class CartesiaTTSProvider(TTSProvider):
    """Cartesia Sonic 3 TTS with ultra-low latency (90ms)"""
    
    def __init__(self):
        self._client: Optional[AsyncCartesia] = None
        self._model_id: str = "sonic-3"
        self._voice_id: str = ""
        self._sample_rate: int = 16000
```

### Initialize Method

```python
async def initialize(self, config: dict) -> None:
    """Initialize Cartesia client"""
    api_key = config.get("api_key") or os.getenv("CARTESIA_API_KEY")
    
    if not api_key:
        raise ValueError("Cartesia API key not found")
    
    self._client = AsyncCartesia(api_key=api_key)
    
    self._model_id = config.get("model_id", "sonic-3")
    self._voice_id = config.get("voice_id", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")
    self._sample_rate = config.get("sample_rate", 16000)
```

### Streaming Synthesis

```python
async def stream_synthesize(
    self,
    text: str,
    voice_id: str,
    sample_rate: int = 16000,
    **kwargs
) -> AsyncIterator[AudioChunk]:
    """
    Stream synthesized audio using Cartesia Sonic 3.
    
    Args:
        text: Text to synthesize
        voice_id: Voice identifier
        sample_rate: 8000, 16000, 22050, 24000, 44100
        **kwargs: language, container, encoding
    
    Yields:
        AudioChunk: Streaming audio chunks
    """
    selected_voice_id = voice_id or self._voice_id
    
    language = kwargs.get("language", "en")
    container = kwargs.get("container", "raw")
    encoding = kwargs.get("encoding", "pcm_f32le")
    
    # Validate sample rate
    valid_rates = [8000, 16000, 22050, 24000, 44100]
    if sample_rate not in valid_rates:
        raise ValueError(f"Invalid sample rate {sample_rate}")
    
    # Use SSE streaming
    bytes_iter = self._client.tts.bytes(
        model_id=self._model_id,
        transcript=text,
        voice={
            "mode": "id",
            "id": selected_voice_id
        },
        language=language,
        output_format={
            "container": container,
            "sample_rate": sample_rate,
            "encoding": encoding
        }
    )
    
    async for chunk in bytes_iter:
        if chunk:
            yield AudioChunk(
                data=chunk,
                sample_rate=sample_rate,
                channels=1
            )
```

### Get Available Voices

```python
async def get_available_voices(self) -> List[Dict]:
    """Get list of available Cartesia voices"""
    voices = self._client.voices.list()
    
    voice_list = []
    for voice in voices:
        voice_list.append({
            "id": voice.id,
            "name": voice.name,
            "language": getattr(voice, 'language', "en"),
            "description": getattr(voice, 'description', "")
        })
    
    return voice_list
```

---

## Telephony Provider (Vonage)

**File:** `app/infrastructure/telephony/vonage_caller.py` (7,461 bytes)

Vonage Voice API integration for outbound calling.

### VonageCaller Class

```python
class VonageCaller:
    """
    Initiates outbound calls via Vonage Voice API.
    Returns call_uuid for tracking.
    """
    
    def __init__(self):
        self._application_id = os.getenv("VONAGE_APPLICATION_ID")
        self._private_key_path = os.getenv("VONAGE_PRIVATE_KEY_PATH")
        self._from_number = os.getenv("VONAGE_FROM_NUMBER")
    
    async def make_call(
        self,
        to_number: str,
        answer_url: str,
        event_url: str,
        **kwargs
    ) -> dict:
        """
        Initiate outbound call.
        
        Args:
            to_number: Destination phone number (E.164)
            answer_url: Webhook URL for NCCO
            event_url: Webhook URL for events
        
        Returns:
            {"call_uuid": "...", "status": "started"}
        """
```

### VonageMediaGateway

**File:** `app/infrastructure/telephony/vonage_media_gateway.py` (12,177 bytes)

```python
class VonageMediaGateway(MediaGateway):
    """
    WebSocket media gateway for Vonage call audio.
    
    Handles:
    - Receiving audio from Vonage (PCM 16-bit, 16kHz)
    - Sending AI audio back to caller
    - Audio queue management
    """
    
    async def connect(self, call_uuid: str, websocket: WebSocket) -> None:
        """Connect WebSocket for call audio"""
    
    async def receive_audio(self, call_uuid: str) -> bytes:
        """Receive audio chunk from call"""
    
    async def send_audio(self, call_uuid: str, audio_data: bytes) -> None:
        """Send audio chunk to call"""
    
    def get_audio_queue(self, call_uuid: str) -> asyncio.Queue:
        """Get audio input queue for call"""
```

---

## Tests Overview

### Test Directory Structure

```
tests/
├── __init__.py
├── unit/                     # Unit tests (13 files)
│   ├── test_api_endpoints.py      # API endpoint tests
│   ├── test_audio_utils.py        # Audio utility tests
│   ├── test_conversation_engine.py # Conversation engine tests
│   ├── test_core.py               # Core module tests
│   ├── test_day9.py               # Day 9 feature tests (25 tests)
│   ├── test_dialer_engine.py      # Dialer engine tests
│   ├── test_latency_tracker.py    # Latency tracking tests
│   ├── test_media_gateway.py      # Media gateway tests
│   ├── test_prompt_manager.py     # Prompt manager tests
│   ├── test_rtp_builder.py        # RTP builder tests
│   ├── test_session.py            # Session model tests
│   └── test_websocket_messages.py # WebSocket message tests
│
├── integration/              # Integration tests (10 files)
│   ├── test_day3_completion.py         # Day 3 completion tests
│   ├── test_day4_audio_pipeline.py     # Audio pipeline tests
│   ├── test_day5_groq_integration.py   # Groq integration tests
│   ├── test_deepgram_connection.py     # Deepgram connection tests
│   ├── test_dialer_integration.py      # Dialer integration tests
│   ├── test_text_to_voice.py           # TTS tests
│   ├── test_tts_streaming.py           # TTS streaming tests
│   ├── test_voice_pipeline.py          # Full pipeline tests
│   └── test_voice_pipeline_conversation.py # Conversation tests
│
└── mocks/                    # Test mocks (3 files)
    └── (mock implementations)
```

### Running Tests

```bash
# Run all tests
cd backend
python -m pytest tests/ -v

# Run unit tests only
python -m pytest tests/unit/ -v

# Run integration tests
python -m pytest tests/integration/ -v

# Run specific test file
python -m pytest tests/unit/test_day9.py -v

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=html
```

### Day 9 Test Suite

**File:** `tests/unit/test_day9.py` (25 tests)

```python
# Test Categories:

class TestPhoneNormalization:
    """7 tests for phone number normalization"""
    test_normalize_us_10_digit()
    test_normalize_us_11_digit_with_1()
    test_normalize_with_plus()
    test_normalize_with_formatting()
    test_normalize_with_spaces()
    test_normalize_invalid_empty()
    test_normalize_too_short()

class TestContactCreateModel:
    """4 tests for ContactCreate validation"""
    test_valid_contact()
    test_minimal_contact()
    test_phone_validation_removes_formatting()
    test_phone_too_short_fails()

class TestCampaignModel:
    """2 tests for Campaign model"""
    test_campaign_with_new_fields()
    test_campaign_optional_fields()

class TestLeadModel:
    """2 tests for Lead model"""
    test_lead_with_last_call_result()
    test_lead_default_last_call_result()

class TestBulkImportResponse:
    """1 test for CSV import response"""
    test_import_response_with_duplicates()

class TestCampaignContactEndpoints:
    """2 tests for endpoint signatures"""
    test_add_contact_validates_campaign_exists()
    test_list_contacts_has_pagination()

class TestCSVUpload:
    """2 tests for CSV upload"""
    test_normalize_phone_in_contacts()
    test_upload_endpoint_exists()

class TestDay9Checkpoints:
    """5 checkpoint verification tests"""
    test_checkpoint_1_campaign_model()   # goal, script_config, calling_config
    test_checkpoint_2_contact_model()    # last_call_result field
    test_checkpoint_3_campaign_api_endpoints()  # contact endpoints exist
    test_checkpoint_4_csv_upload_endpoint()     # CSV upload exists
    test_checkpoint_5_dialer_link()             # start_campaign uses DialerJob
```

---

## Database Schemas

### Schema Files Summary

| File | Lines | Purpose |
|------|-------|---------|
| `schema.sql` | ~230 | Core tables (campaigns, leads, calls, conversations) |
| `schema_dialer.sql` | ~130 | Dialer engine (dialer_jobs, job statuses) |
| `schema_update.sql` | ~195 | Extended tables (plans, tenants, recordings, clients) |
| `schema_day9.sql` | ~100 | Latest updates (goal, script_config, last_call_result) |

### Core Tables

```sql
-- campaigns: Campaign configuration
-- leads: Contact records for calling
-- calls: Individual call records
-- conversations: Conversation messages
-- dialer_jobs: Pending/scheduled call jobs

-- plans: Subscription plans
-- tenants: Multi-tenant organizations
-- user_profiles: User accounts
-- recordings: Call recording metadata
-- clients: Standalone client records
```

---

## Configuration Files

### config/development.yaml

```yaml
environment: development
debug: true

server:
  host: "0.0.0.0"
  port: 8000
  reload: true

logging:
  level: DEBUG
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

session:
  require_redis: false  # Allow memory storage in dev
```

### config/providers.yaml

```yaml
llm:
  provider: groq
  model: llama-3.3-70b-versatile
  temperature: 0.7
  max_tokens: 150

stt:
  provider: deepgram
  model: nova-2
  language: en-US
  smart_format: true

tts:
  provider: cartesia
  voice_id: sonic-professional
  sample_rate: 24000

telephony:
  provider: vonage
  answer_url: https://your-domain/webhooks/vonage/answer
  event_url: https://your-domain/webhooks/vonage/event
```

### .env.example

```bash
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-role-key

# Redis
REDIS_URL=redis://localhost:6379

# AI Providers
GROQ_API_KEY=gsk_...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...

# Vonage
VONAGE_API_KEY=...
VONAGE_API_SECRET=...
VONAGE_APPLICATION_ID=...
VONAGE_PRIVATE_KEY_PATH=./vonage_private.key
VONAGE_FROM_NUMBER=+1234567890

# Application
ENVIRONMENT=development
DEBUG=true
API_BASE_URL=http://localhost:8000
WEBSOCKET_HOST=localhost:8000
```

---

## File Cross-Reference

### How Files Connect

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FILE CONNECTIONS MAP                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Entry Point                                                                │
│  main.py ────────────────► routes.py ────────► endpoints/*.py               │
│      │                                                                      │
│      │ startup                                                              │
│      ▼                                                                      │
│  core/validation.py ◄──── core/config.py                                   │
│                                                                             │
│  Request Flow (Campaigns)                                                   │
│  endpoints/campaigns.py ──► domain/models/dialer_job.py                    │
│           │                                                                 │
│           ▼                                                                 │
│  domain/services/queue_service.py ───► Redis                               │
│                                                                             │
│  Worker Flow (Dialer)                                                       │
│  workers/dialer_worker.py ──► domain/services/queue_service.py             │
│           │                   domain/services/scheduling_rules.py           │
│           │                   domain/models/calling_rules.py                │
│           ▼                                                                 │
│  infrastructure/telephony/vonage_caller.py ──► Vonage API                  │
│                                                                             │
│  Webhook Flow                                                               │
│  Vonage ──► endpoints/webhooks.py ──► domain/models/dialer_job.py          │
│                    │                                                        │
│                    ▼                                                        │
│             domain/services/queue_service.py (schedule_retry)               │
│                                                                             │
│  Voice Pipeline Flow                                                        │
│  endpoints/websockets.py ──► domain/services/voice_pipeline_service.py     │
│                                      │                                      │
│           ┌──────────────────────────┼──────────────────────────┐          │
│           ▼                          ▼                          ▼          │
│  infrastructure/stt/     infrastructure/llm/     infrastructure/tts/       │
│  deepgram_flux.py        groq.py                 cartesia.py               │
│           │                          │                          │          │
│           ▼                          ▼                          ▼          │
│       Deepgram API              Groq API                Cartesia API       │
│                                                                             │
│  Session Flow                                                               │
│  domain/models/session.py ◄──► domain/services/session_manager.py          │
│                  │                        │                                 │
│                  ▼                        ▼                                 │
│          domain/models/              Redis (session storage)                │
│          conversation.py                                                    │
│          conversation_state.py                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Import Map

```python
# main.py imports:
from app.api.v1.routes import api_router
from app.core.validation import validate_providers_on_startup
from app.domain.services.session_manager import SessionManager

# routes.py imports:
from app.api.v1.endpoints import (
    campaigns, webhooks, websockets, auth, plans, 
    dashboard, analytics, calls, recordings, 
    contacts, clients, admin
)

# campaigns.py imports:
from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.queue_service import DialerQueueService
from app.api.v1.dependencies import get_supabase, get_current_user

# webhooks.py imports:
from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.services.queue_service import DialerQueueService
from app.api.v1.dependencies import get_supabase

# voice_pipeline_service.py imports:
from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.services.prompt_manager import PromptManager

# dialer_worker.py imports:
from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.models.calling_rules import CallingRules
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.scheduling_rules import SchedulingRuleEngine
```

---

## Summary Statistics

| Category | Count |
|----------|-------|
| Total Python Files | ~75 |
| API Endpoints | 14 router files |
| Domain Models | 11 files |
| Domain Services | 8 files |
| Infrastructure Providers | 12 files |
| Workers | 2 files |
| Unit Tests | 13 files |
| Integration Tests | 10 files |
| Database Schemas | 4 files |
| Config Files | 2 files |

---

## End of Documentation

This documentation set consists of 4 files:

1. **file_one.md** - Project Overview & Core Architecture
2. **file_two.md** - API Endpoints Reference
3. **file_three.md** - Domain Models & Services
4. **file_four.md** - Infrastructure Providers & Testing (this file)

All documentation is based on actual source code examination with no assumptions.
