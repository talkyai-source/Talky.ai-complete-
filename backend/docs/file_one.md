# Talky.ai Backend Documentation - Part 1
# Project Overview & Core Architecture

## Table of Contents
1. [Project Introduction](#project-introduction)
2. [Technology Stack](#technology-stack)
3. [Directory Structure](#directory-structure)
4. [Core Files](#core-files)
5. [Configuration Files](#configuration-files)
6. [Database Schemas](#database-schemas)

---

## Project Introduction

### What is Talky.ai?

Talky.ai is an enterprise-grade Voice AI platform for automated outbound calling campaigns. The system enables businesses to:

- Create and manage calling campaigns
- Upload contact lists via CSV
- Automatically dial contacts using the Vonage telephony provider
- Conduct AI-powered voice conversations using real-time STT → LLM → TTS pipeline
- Track call outcomes and campaign performance
- Handle smart retry logic for failed calls

### Key Capabilities

| Feature | Description |
|---------|-------------|
| Campaign Management | Create, start, pause, stop campaigns with goals and scripts |
| Contact Management | Add single contacts, bulk CSV upload with validation |
| AI Voice Conversations | Real-time conversation using Groq LLM, Deepgram STT, Cartesia TTS |
| Dialer Engine | Automated outbound calling with priority queues and scheduling |
| Call Tracking | Recording, transcription, outcome tracking, analytics |
| Multi-tenancy | Designed for tenant isolation (infrastructure ready) |

---

## Technology Stack

### Backend Framework

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TECHNOLOGY STACK                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Framework:      FastAPI (Python 3.10+)                                     │
│  Database:       Supabase (PostgreSQL)                                      │
│  Cache/Queue:    Redis (job queues, session storage)                        │
│  Authentication: JWT via Supabase Auth                                      │
│                                                                             │
│  AI Providers:                                                              │
│  ├── LLM:        Groq (llama-3.3-70b-versatile) - Ultra-fast inference     │
│  ├── STT:        Deepgram (Nova-2) - Real-time speech-to-text              │
│  └── TTS:        Cartesia (Sonic) - Low-latency text-to-speech             │
│                                                                             │
│  Telephony:      Vonage (Voice API + WebSocket audio)                       │
│  Storage:        Supabase Storage (call recordings)                         │
│                                                                             │
│  Testing:        Pytest with async support                                  │
│  Deployment:     Docker + Docker Compose                                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Provider Latency Targets

| Component | Target Latency | Provider |
|-----------|----------------|----------|
| STT (Speech-to-Text) | < 300ms | Deepgram Nova-2 |
| LLM (Language Model) | < 500ms | Groq (llama-3.3-70b) |
| TTS (Text-to-Speech) | < 200ms | Cartesia Sonic |
| End-to-End Response | < 1000ms | Total pipeline |

---

## Directory Structure

### Complete Project Tree

```
backend/
├── app/                          # Main application code
│   ├── __init__.py               # Package marker
│   ├── main.py                   # FastAPI application entry point
│   │
│   ├── api/                      # API layer
│   │   ├── __init__.py
│   │   └── v1/                   # API version 1
│   │       ├── __init__.py
│   │       ├── dependencies.py   # Shared dependencies (auth, supabase)
│   │       ├── routes.py         # API router consolidation
│   │       ├── endpoints/        # REST API endpoints
│   │       │   ├── admin.py      # Admin operations
│   │       │   ├── analytics.py  # Analytics & reporting
│   │       │   ├── auth.py       # Authentication (login, register)
│   │       │   ├── calls.py      # Call management
│   │       │   ├── campaigns.py  # Campaign CRUD + contact management
│   │       │   ├── clients.py    # Client management
│   │       │   ├── contacts.py   # Bulk contact import
│   │       │   ├── dashboard.py  # Dashboard data
│   │       │   ├── health.py     # Health check endpoint
│   │       │   ├── plans.py      # Subscription plans
│   │       │   ├── recordings.py # Recording management
│   │       │   ├── webhooks.py   # Vonage webhooks
│   │       │   └── websockets.py # Voice WebSocket endpoint
│   │       └── schemas/          # Pydantic schemas (empty)
│   │
│   ├── core/                     # Core utilities
│   │   ├── __init__.py
│   │   ├── config.py             # Configuration management
│   │   ├── container.py          # Dependency container (future DI)
│   │   ├── tenant_middleware.py  # Multi-tenant middleware
│   │   └── validation.py         # Provider validation at startup
│   │
│   ├── domain/                   # Business domain
│   │   ├── __init__.py
│   │   ├── interfaces/           # Abstract interfaces
│   │   │   ├── llm_provider.py   # LLM provider interface
│   │   │   ├── stt_provider.py   # STT provider interface
│   │   │   ├── tts_provider.py   # TTS provider interface
│   │   │   ├── telephony_provider.py  # Telephony interface
│   │   │   └── media_gateway.py  # Media gateway interface
│   │   │
│   │   ├── models/               # Pydantic domain models
│   │   │   ├── agent_config.py   # AI agent configuration
│   │   │   ├── call.py           # Call model
│   │   │   ├── calling_rules.py  # Scheduling rules
│   │   │   ├── campaign.py       # Campaign model
│   │   │   ├── conversation.py   # Conversation model
│   │   │   ├── conversation_state.py  # State machine model
│   │   │   ├── dialer_job.py     # Dialer job model
│   │   │   ├── lead.py           # Lead/contact model
│   │   │   ├── session.py        # Call session model
│   │   │   └── websocket_messages.py  # WebSocket message types
│   │   │
│   │   └── services/             # Business services
│   │       ├── conversation_engine.py   # Conversation state machine
│   │       ├── latency_tracker.py       # Performance tracking
│   │       ├── prompt_manager.py        # LLM prompt templates
│   │       ├── queue_service.py         # Redis job queue
│   │       ├── scheduling_rules.py      # Call scheduling
│   │       ├── session_manager.py       # Session lifecycle
│   │       └── voice_pipeline_service.py # STT→LLM→TTS pipeline
│   │
│   ├── infrastructure/           # External provider implementations
│   │   ├── __init__.py
│   │   ├── llm/                  # LLM providers
│   │   │   ├── factory.py        # LLM factory
│   │   │   └── groq.py           # Groq implementation
│   │   │
│   │   ├── stt/                  # STT providers
│   │   │   ├── factory.py        # STT factory
│   │   │   ├── deepgram.py       # Deepgram basic
│   │   │   └── deepgram_flux.py  # Deepgram streaming
│   │   │
│   │   ├── tts/                  # TTS providers
│   │   │   ├── factory.py        # TTS factory
│   │   │   └── cartesia.py       # Cartesia implementation
│   │   │
│   │   ├── telephony/            # Telephony providers
│   │   │   ├── factory.py        # Telephony factory
│   │   │   ├── vonage_caller.py  # Vonage call initiation
│   │   │   ├── vonage_media_gateway.py  # Vonage WebSocket
│   │   │   └── rtp_media_gateway.py     # RTP audio handling
│   │   │
│   │   └── storage/              # Storage providers
│   │       └── (supabase integration)
│   │
│   ├── utils/                    # Utility functions
│   │   └── audio_utils.py        # Audio processing utilities
│   │
│   └── workers/                  # Background workers
│       ├── __init__.py
│       ├── dialer_worker.py      # Outbound call processing
│       └── voice_worker.py       # Voice AI pipeline
│
├── config/                       # Configuration files
│   ├── development.yaml          # Dev environment config
│   └── providers.yaml            # Provider settings
│
├── database/                     # Database schemas
│   ├── schema.sql                # Core tables
│   ├── schema_dialer.sql         # Dialer engine tables
│   ├── schema_update.sql         # Additional tables
│   └── schema_day9.sql           # Latest updates
│
├── docs/                         # Documentation
│   └── (documentation files)
│
├── tests/                        # Test suites
│   ├── unit/                     # Unit tests
│   ├── integration/              # Integration tests
│   └── mocks/                    # Test mocks
│
├── .env.example                  # Environment template
├── .gitignore                    # Git ignore rules
├── Dockerfile                    # Docker build
├── README.md                     # Project readme
└── requirements.txt              # Python dependencies
```

---

## Core Files

### 1. main.py - Application Entry Point

**File:** `app/main.py`  
**Size:** ~3,800 bytes  
**Purpose:** FastAPI application initialization and configuration

```python
# Key Components in main.py:

# 1. FastAPI App Creation
app = FastAPI(
    title="Voice AI Backend",
    description="Production-ready voice AI with ultra-low latency",
    version="1.0.0"
)

# 2. CORS Configuration
app.add_middleware(CORSMiddleware, ...)

# 3. Router Registration
app.include_router(api_router, prefix="/api/v1")

# 4. Lifespan Events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Validate providers, init session manager
    # Shutdown: Cleanup resources
```

**Connections:**
- Imports routers from `app.api.v1.routes`
- Uses `SessionManager` from `app.domain.services.session_manager`
- Validates providers using `app.core.validation`

---

### 2. routes.py - API Router Consolidation

**File:** `app/api/v1/routes.py`  
**Size:** ~928 bytes  
**Purpose:** Combines all endpoint routers into a single API router

```python
# Router structure:
api_router = APIRouter()

# Included routers:
api_router.include_router(auth.router)        # /auth
api_router.include_router(plans.router)       # /plans
api_router.include_router(dashboard.router)   # /dashboard
api_router.include_router(analytics.router)   # /analytics
api_router.include_router(calls.router)       # /calls
api_router.include_router(recordings.router)  # /recordings
api_router.include_router(contacts.router)    # /contacts
api_router.include_router(clients.router)     # /clients
api_router.include_router(campaigns.router)   # /campaigns
api_router.include_router(admin.router)       # /admin
api_router.include_router(webhooks.router)    # /webhooks
api_router.include_router(websocket_router)   # WebSocket
api_router.include_router(health.router)      # /health
```

**Connections:**
- Imported by `main.py`
- Imports all endpoint files from `app.api.v1.endpoints`

---

### 3. dependencies.py - Shared API Dependencies

**File:** `app/api/v1/dependencies.py`  
**Size:** ~6,712 bytes  
**Purpose:** Dependency injection for API endpoints

```python
# Key Functions:

def get_supabase() -> Client:
    """Get Supabase client instance"""
    # Returns authenticated Supabase client

async def get_current_user(
    authorization: str = Header(...)
) -> CurrentUser:
    """Extract and validate JWT token"""
    # Validates JWT, returns user info

class CurrentUser:
    """User context from JWT"""
    user_id: str
    tenant_id: str
    email: str
    role: str
```

**Connections:**
- Used by all endpoint files via `Depends(get_supabase)` and `Depends(get_current_user)`
- Connects to Supabase using environment variables

---

## Configuration Files

### 1. config.py - Configuration Manager

**File:** `app/core/config.py`  
**Size:** ~4,535 bytes  
**Purpose:** Centralized configuration loading and management

```python
class ConfigManager:
    """Manages application configuration"""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self._config = {}
    
    def load(self) -> dict:
        """Load configuration from YAML files"""
        # Loads development.yaml and providers.yaml
        # Merges with environment variables
    
    def get_provider_config(self, provider_type: str) -> dict:
        """Get configuration for specific provider"""
```

**Configuration Hierarchy:**
1. YAML files (development.yaml, providers.yaml)
2. Environment variables (override YAML)
3. Hardcoded defaults (fallback)

---

### 2. development.yaml - Development Configuration

**File:** `config/development.yaml`  
**Size:** ~551 bytes  
**Purpose:** Development environment settings

```yaml
# Environment settings
environment: development
debug: true

# Server configuration
server:
  host: "0.0.0.0"
  port: 8000
  reload: true

# Logging
logging:
  level: DEBUG
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Session settings
session:
  require_redis: false  # Allow memory storage in dev
```

---

### 3. providers.yaml - Provider Settings

**File:** `config/providers.yaml`  
**Size:** ~2,680 bytes  
**Purpose:** AI provider configuration

```yaml
# LLM Configuration
llm:
  provider: groq
  model: llama-3.3-70b-versatile
  temperature: 0.7
  max_tokens: 150
  top_p: 0.9

# STT Configuration
stt:
  provider: deepgram
  model: nova-2
  language: en-US
  smart_format: true
  interim_results: true

# TTS Configuration
tts:
  provider: cartesia
  voice_id: sonic-professional
  sample_rate: 24000
  format: pcm_mulaw

# Telephony Configuration
telephony:
  provider: vonage
  answer_url: https://your-domain/webhooks/vonage/answer
  event_url: https://your-domain/webhooks/vonage/event
```

---

## Database Schemas

### Schema Files Overview

| File | Purpose | Key Tables |
|------|---------|------------|
| `schema.sql` | Core tables | campaigns, leads, calls, conversations |
| `schema_dialer.sql` | Dialer engine | dialer_jobs, job statuses |
| `schema_update.sql` | Extended tables | plans, tenants, recordings, clients |
| `schema_day9.sql` | Latest updates | New columns for campaigns/leads |

---

### 1. schema.sql - Core Database Schema

**File:** `database/schema.sql`  
**Size:** ~8,050 bytes  
**Purpose:** Core application tables

```sql
-- CAMPAIGNS TABLE
CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'draft',
    system_prompt TEXT NOT NULL,
    voice_id VARCHAR(100) DEFAULT 'en-US-Standard-A',
    max_concurrent_calls INTEGER DEFAULT 10,
    retry_failed BOOLEAN DEFAULT true,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    total_leads INTEGER DEFAULT 0,
    calls_completed INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0
);

-- LEADS TABLE
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    custom_fields JSONB DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'pending',
    call_attempts INTEGER DEFAULT 0,
    last_called_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- CALLS TABLE
CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID REFERENCES campaigns(id),
    lead_id UUID REFERENCES leads(id),
    phone_number VARCHAR(20) NOT NULL,
    status VARCHAR(50) DEFAULT 'initiated',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    recording_url TEXT,
    transcription TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- CONVERSATIONS TABLE
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    audio_url TEXT
);
```

**Entity Relationship:**
```
campaigns (1) ─────────► (N) leads
    │                        │
    │                        │
    ▼                        ▼
  (N) calls ◄───────────────┘
    │
    │
    ▼
  (N) conversations
```

---

### 2. schema_dialer.sql - Dialer Engine Schema

**File:** `database/schema_dialer.sql`  
**Size:** ~5,365 bytes  
**Purpose:** Dialer job management tables

```sql
-- DIALER_JOBS TABLE
CREATE TABLE dialer_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    tenant_id UUID,
    phone_number VARCHAR(20) NOT NULL,
    priority INTEGER DEFAULT 5,
    status VARCHAR(50) DEFAULT 'pending',
    attempt_number INTEGER DEFAULT 1,
    scheduled_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_outcome VARCHAR(50),
    last_error TEXT,
    call_id UUID REFERENCES calls(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add columns to campaigns
ALTER TABLE campaigns ADD COLUMN calling_config JSONB DEFAULT '{}';

-- Add columns to leads
ALTER TABLE leads ADD COLUMN priority INTEGER DEFAULT 5;
ALTER TABLE leads ADD COLUMN is_high_value BOOLEAN DEFAULT false;
ALTER TABLE leads ADD COLUMN tags TEXT[] DEFAULT '{}';

-- Add columns to calls
ALTER TABLE calls ADD COLUMN outcome VARCHAR(50);
ALTER TABLE calls ADD COLUMN goal_achieved BOOLEAN DEFAULT false;
ALTER TABLE calls ADD COLUMN dialer_job_id UUID REFERENCES dialer_jobs(id);

-- Indexes for performance
CREATE INDEX idx_dialer_jobs_status ON dialer_jobs(status);
CREATE INDEX idx_dialer_jobs_scheduled ON dialer_jobs(scheduled_at);
CREATE INDEX idx_dialer_jobs_campaign ON dialer_jobs(campaign_id);
```

---

### 3. schema_update.sql - Extended Tables

**File:** `database/schema_update.sql`  
**Size:** ~7,177 bytes  
**Purpose:** Multi-tenancy and additional features

```sql
-- PLANS TABLE (Subscription plans)
CREATE TABLE plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    price_monthly DECIMAL(10,2),
    features JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- TENANTS TABLE (Multi-tenancy)
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    plan_id UUID REFERENCES plans(id),
    status VARCHAR(50) DEFAULT 'active',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- USER_PROFILES TABLE
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    tenant_id UUID REFERENCES tenants(id),
    role VARCHAR(50) DEFAULT 'user',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RECORDINGS TABLE
CREATE TABLE recordings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    status VARCHAR(50) DEFAULT 'processing',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- CLIENTS TABLE (Separate from leads)
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id),
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(20),
    email VARCHAR(255),
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### 4. schema_day9.sql - Latest Updates

**File:** `database/schema_day9.sql`  
**Size:** ~4,555 bytes  
**Purpose:** Campaign and contact management enhancements

```sql
-- Add goal to campaigns
ALTER TABLE campaigns ADD COLUMN goal TEXT;

-- Add script_config JSONB to campaigns
ALTER TABLE campaigns ADD COLUMN script_config JSONB DEFAULT '{}';

-- Add last_call_result to leads
ALTER TABLE leads ADD COLUMN last_call_result VARCHAR(50) DEFAULT 'pending';

-- Index for efficient filtering
CREATE INDEX idx_leads_last_call_result ON leads(last_call_result);

-- Unique constraint for duplicate prevention
CREATE UNIQUE INDEX idx_leads_campaign_phone_unique 
ON leads(campaign_id, phone_number) 
WHERE status != 'deleted';
```

---

## Environment Variables

### Required Environment Variables

```bash
# Supabase Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# Redis Configuration
REDIS_URL=redis://localhost:6379

# AI Provider Keys
GROQ_API_KEY=gsk_your_groq_key
DEEPGRAM_API_KEY=your_deepgram_key
CARTESIA_API_KEY=your_cartesia_key

# Vonage Telephony
VONAGE_API_KEY=your_vonage_key
VONAGE_API_SECRET=your_vonage_secret
VONAGE_APPLICATION_ID=your_app_id
VONAGE_PRIVATE_KEY_PATH=./vonage_private.key
VONAGE_FROM_NUMBER=+1234567890

# Application Settings
ENVIRONMENT=development
DEBUG=true
```

---

## Next File

Continue to **file_two.md** for:
- API Endpoints detailed documentation
- Request/Response schemas
- Authentication flow
- Error handling patterns
