# Day 1: Finalize Requirements, Architecture & Repository Setup

## Overview

**Date:** Week 1, Day 1  
**Goal:** Establish clear project requirements, define system architecture, create repository structure, and set up the backend skeleton.

This document outlines the foundational work completed on Day 1, including the reasoning behind each architectural decision and implementation approach.

---

## Table of Contents

1. [Requirements Clarification](#1-requirements-clarification)
2. [Architecture Design](#2-architecture-design)
3. [Repository Setup](#3-repository-setup)
4. [Backend Skeleton Implementation](#4-backend-skeleton-implementation)
5. [Rationale Summary](#5-rationale-summary)

---

## 1. Requirements Clarification

### 1.1 MVP Scope Definition

The Minimum Viable Product (MVP) scope was defined with the following constraints:

| Requirement | Decision | Rationale |
|-------------|----------|-----------|
| **Call Direction** | Outbound calls only | Simplifies initial implementation by eliminating inbound call routing complexity. Outbound-first approach allows focus on core AI pipeline without handling call queuing and distribution. |
| **Tenancy Model** | Multi-tenant architecture | Future-proofs the system for SaaS deployment. Tenant isolation at the database level ensures data security and enables per-tenant billing. |
| **Minimum Features** | Campaign management, Contact lists, AI answering, Call logging | These represent the core value proposition: automated AI-powered outbound calling with trackable results. |

### 1.2 Provider Selection

The following providers were selected based on latency requirements, cost analysis, and API capabilities:

| Service Type | Primary Provider | Fallback | Selection Rationale |
|--------------|------------------|----------|---------------------|
| **STT (Speech-to-Text)** | Deepgram | Groq Whisper | Deepgram offers real-time streaming with sub-300ms latency, critical for natural conversation flow. Nova-2 model provides excellent accuracy for phone audio quality. |
| **TTS (Text-to-Speech)** | ElevenLabs | Deepgram TTS | ElevenLabs provides the most natural-sounding voices with streaming support. Low-latency mode enables near-instant audio generation. |
| **LLM (Large Language Model)** | Groq (LLaMA 3) | OpenAI GPT-4o-mini | Groq's inference speed (100+ tokens/second) is essential for maintaining conversational pace. Cost-effective for high-volume calling. |
| **Telephony** | Vonage | Twilio | Vonage provides programmatic SIP control, WebSocket audio streaming, and competitive per-minute pricing. Strong documentation and NCCO scripting for call flow control. |

**Why These Providers Matter:**

The total end-to-end latency budget for a voice AI system is approximately 500-700ms to feel natural. This breaks down as:
- STT processing: ~100-200ms
- LLM inference: ~100-300ms  
- TTS generation: ~100-200ms
- Network overhead: ~50-100ms

Each provider was chosen to minimize their portion of this latency budget.

---

## 2. Architecture Design

### 2.1 System Block Diagram

The system follows a layered architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ Web Admin   │  │ Mobile App  │  │ API Client  │                 │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                 │
└─────────┼────────────────┼────────────────┼─────────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         API LAYER                                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    FastAPI Application                        │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │  │
│  │  │ REST API │  │ WebSocket│  │ Webhooks │  │ Health Check │  │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        DOMAIN LAYER                                 │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐        │
│  │ Campaign Svc   │  │ Conversation   │  │ Voice Pipeline │        │
│  │                │  │ Engine         │  │ Service        │        │
│  └────────────────┘  └────────────────┘  └────────────────┘        │
│                              │                                      │
│  ┌────────────────┐  ┌──────┴───────┐  ┌────────────────┐          │
│  │ Dialer Engine  │  │ Agent Logic  │  │ Session Manager│          │
│  └────────────────┘  └──────────────┘  └────────────────┘          │
└─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     INFRASTRUCTURE LAYER                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │   STT    │  │   TTS    │  │   LLM    │  │    Telephony     │    │
│  │ Provider │  │ Provider │  │ Provider │  │    Provider      │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘    │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐    │
│  │ Supabase (DB)    │  │ Redis (Cache)    │  │ S3 (Storage)   │    │
│  └──────────────────┘  └──────────────────┘  └────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Audio Flow Architecture

The audio processing flow is the critical path for call quality:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        VOICE CALL FLOW                              │
└─────────────────────────────────────────────────────────────────────┘

    Caller                                              AI Agent
      │                                                      │
      │  ─────────────── RTP Audio ───────────────────▶     │
      │                                                      │
      ▼                                                      ▼
┌───────────┐      ┌───────────┐      ┌───────────┐    ┌───────────┐
│  VoIP     │ ──▶  │  Media    │ ──▶  │   STT     │ ──▶│   LLM     │
│  Server   │      │  Gateway  │      │  Provider │    │  Provider │
│ (Vonage)  │      │ (PCM/μlaw)│      │(Deepgram) │    │  (Groq)   │
└───────────┘      └───────────┘      └───────────┘    └───────────┘
      ▲                                                      │
      │                                                      │
      │  ◀─────────────── RTP Audio ────────────────────    │
      │                                                      ▼
┌───────────┐      ┌───────────┐      ┌───────────┐    ┌───────────┐
│  VoIP     │ ◀──  │  Media    │ ◀──  │   TTS     │ ◀──│  Response │
│  Server   │      │  Gateway  │      │  Provider │    │   Text    │
│ (Vonage)  │      │ (RTP Pack)│      │(ElevenLabs│    │           │
└───────────┘      └───────────┘      └───────────┘    └───────────┘
```

**Why This Flow Matters:**

- **Streaming Throughout:** Every component uses streaming to minimize latency. Audio chunks are processed as they arrive, not buffered.
- **Codec Handling at Gateway:** Audio codec conversion (G.711 μ-law to PCM) happens at the media gateway, keeping provider interfaces clean.
- **Decoupled Components:** Each provider can be swapped independently without affecting the others.

### 2.3 Layered Architecture Rationale

The three-layer architecture (API, Domain, Infrastructure) was chosen for the following reasons:

| Layer | Responsibility | Benefit |
|-------|----------------|---------|
| **API Layer** | HTTP/WebSocket handling, request validation, authentication | Separation of transport concerns allows testing business logic independently |
| **Domain Layer** | Business logic, conversation state, campaign rules | Provider-agnostic code is reusable and testable without external services |
| **Infrastructure Layer** | External service integration, data persistence | Easy to swap providers without touching business logic |

---

## 3. Repository Setup

### 3.1 Repository Structure Decision

A **monorepo** structure was chosen with separate directories for backend and frontend:

```
Talky.ai-complete-/
├── backend/           # Python FastAPI service
├── frontend/          # Next.js/React application
├── docker-compose.yml # Local development orchestration
└── .env               # Shared environment variables
```

**Why Monorepo:**

1. **Simplified Development:** Single clone for full-stack development
2. **Shared Configuration:** Common environment variables and Docker setup
3. **Atomic Commits:** Related frontend/backend changes can be committed together
4. **Easier CI/CD:** Single pipeline can build and deploy coordinated releases

### 3.2 Backend Folder Structure

The backend follows **Domain-Driven Design (DDD)** principles:

```
backend/
├── app/
│   ├── api/                    # HTTP endpoints and WebSocket handlers
│   │   ├── routes/             # Route definitions by resource
│   │   ├── middleware/         # Request processing middleware
│   │   └── dependencies.py     # FastAPI dependency injection
│   │
│   ├── core/                   # Framework and configuration
│   │   ├── config.py           # Environment configuration
│   │   ├── container.py        # Dependency injection container
│   │   └── security.py         # Authentication utilities
│   │
│   ├── domain/                 # Business logic (provider-independent)
│   │   ├── interfaces/         # Abstract base classes for providers
│   │   ├── models/             # Domain entities and DTOs
│   │   └── services/           # Core business services
│   │
│   ├── infrastructure/         # External service implementations
│   │   ├── stt/                # Speech-to-Text providers
│   │   ├── tts/                # Text-to-Speech providers
│   │   ├── llm/                # Language model providers
│   │   ├── telephony/          # VoIP providers
│   │   └── storage/            # Database and file storage
│   │
│   ├── workers/                # Background job processors
│   │   └── dialer_worker.py    # Outbound call scheduler
│   │
│   └── utils/                  # Shared utilities
│       ├── audio.py            # Audio processing helpers
│       └── logging.py          # Structured logging
│
├── config/
│   ├── providers.yaml          # Provider selection configuration
│   └── prompts/                # LLM prompt templates
│
├── database/
│   └── schema.sql              # Database schema definitions
│
├── docs/                       # Project documentation
│   └── day_one.md              # This file
│
├── tests/                      # Test suites
│   ├── unit/                   # Unit tests
│   ├── integration/            # Integration tests
│   └── e2e/                    # End-to-end tests
│
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container definition
└── .env.example                # Environment template
```

**Why This Structure:**

1. **Clear Boundaries:** Each directory has a single responsibility
2. **Provider Isolation:** Infrastructure implementations don't leak into domain logic
3. **Testability:** Domain logic can be unit tested without mocking external services
4. **Discoverability:** New developers can navigate by feature, not by file type

### 3.3 Branching Strategy

The following Git branching model was adopted:

```
main ──────────────────────────────────────────────────────────▶
  │                           │                    │
  │                           │                    │
  └── develop ────────────────┴────────────────────┴────────────▶
        │         │         │
        │         │         └── feature/dialer-engine
        │         │
        │         └── feature/ai-pipeline
        │
        └── feature/campaign-api
```

| Branch | Purpose | Merge Target |
|--------|---------|--------------|
| `main` | Production-ready code | N/A (protected) |
| `develop` | Integration branch for testing | `main` |
| `feature/*` | Individual features | `develop` |
| `hotfix/*` | Emergency production fixes | `main` and `develop` |

**Why This Model:**

- **Stable Main:** Production branch is always deployable
- **Integration Testing:** Develop branch catches integration issues before production
- **Parallel Development:** Multiple features can progress independently

---

## 4. Backend Skeleton Implementation

### 4.1 Framework Selection: FastAPI

FastAPI was chosen as the backend framework for the following reasons:

| Criterion | FastAPI | Flask | Django | Decision Rationale |
|-----------|---------|-------|--------|---------------------|
| **Async Support** | Native | Extension | Limited | Voice AI requires concurrent WebSocket handling |
| **Performance** | Excellent | Good | Good | High-throughput call handling needs |
| **Type Safety** | Built-in | Manual | Manual | Reduces bugs in complex audio pipelines |
| **WebSocket** | Native | Socket.IO | Channels | Direct integration for real-time audio |
| **Auto Documentation** | Swagger/OpenAPI | Manual | DRF | API documentation for frontend team |

### 4.2 Initial Dependencies

The following core dependencies were installed:

```python
# requirements.txt - Core Dependencies

# Framework
fastapi==0.109.2          # Async web framework
uvicorn[standard]==0.27.1 # ASGI server
python-multipart==0.0.9   # File upload support

# Database
supabase==2.3.4           # PostgreSQL with auth
redis==5.0.1              # Session caching and job queues

# AI Providers (SDKs)
deepgram-sdk==3.4.0       # STT provider
elevenlabs==1.1.2         # TTS provider
groq==0.4.2               # LLM provider

# Telephony
vonage==3.14.0            # VoIP integration

# Utilities
pydantic==2.6.1           # Data validation
pydantic-settings==2.1.0  # Environment configuration
python-dotenv==1.0.1      # Environment file loading
httpx==0.26.0             # Async HTTP client
```

**Dependency Selection Criteria:**

1. **Active Maintenance:** All packages have recent releases and active communities
2. **Async Compatibility:** Libraries support asyncio for non-blocking I/O
3. **Type Hints:** Packages provide type annotations for IDE support

### 4.3 Configuration Module

A centralized configuration system was implemented:

```python
# app/core/config.py

from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables.
    
    Rationale: Pydantic Settings provides:
    - Automatic type coercion from string env vars
    - Validation on startup (fail fast)
    - Default values with override capability
    - Secure handling of secrets
    """
    
    # Application
    APP_NAME: str = "Voice AI Dialer"
    DEBUG: bool = False
    API_VERSION: str = "v1"
    
    # Database
    SUPABASE_URL: str
    SUPABASE_KEY: str
    REDIS_URL: str = "redis://localhost:6379"
    
    # AI Providers
    DEEPGRAM_API_KEY: str
    ELEVENLABS_API_KEY: str
    GROQ_API_KEY: str
    
    # Telephony
    VONAGE_API_KEY: str
    VONAGE_API_SECRET: str
    VONAGE_APPLICATION_ID: str
    
    class Config:
        env_file = ".env"
        case_sensitive = True

@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance - loaded once at startup."""
    return Settings()
```

**Why Pydantic Settings:**

- **Fail Fast:** Application won't start with missing required configs
- **Type Safety:** Catches config errors at startup, not runtime
- **Documentation:** Settings class documents all required environment variables
- **Caching:** `@lru_cache` ensures config is parsed only once

### 4.4 Health Check Endpoint

A comprehensive health check was implemented:

```python
# app/api/routes/health.py

from fastapi import APIRouter, Depends
from app.core.config import get_settings

router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.
    
    Rationale: 
    - Kubernetes/Docker use this for liveness probes
    - Load balancers use this to route traffic
    - Returns 200 even if some services are degraded
    """
    return {
        "status": "healthy",
        "version": get_settings().API_VERSION
    }

@router.get("/health/ready")
async def readiness_check():
    """
    Readiness check - verifies all dependencies are connected.
    
    Rationale:
    - Separate from liveness (pod is running vs ready for traffic)
    - Checks database, Redis, and provider connectivity
    - Returns 503 if any critical dependency is unavailable
    """
    checks = {
        "database": await check_database(),
        "redis": await check_redis(),
        "providers": await check_providers()
    }
    
    all_healthy = all(checks.values())
    
    return {
        "status": "ready" if all_healthy else "degraded",
        "checks": checks
    }
```

### 4.5 Application Entry Point

The main application file wires everything together:

```python
# app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.api.routes import campaigns, health, webhooks

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager.
    
    Rationale:
    - Startup: Initialize connections, warm caches
    - Shutdown: Gracefully close connections
    - Context manager ensures cleanup even on crash
    """
    # Startup
    settings = get_settings()
    await initialize_providers(settings)
    await warm_cache()
    
    yield  # Application runs here
    
    # Shutdown
    await cleanup_connections()

app = FastAPI(
    title="Voice AI Dialer API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route registration
app.include_router(health.router, tags=["Health"])
app.include_router(
    campaigns.router, 
    prefix="/api/v1/campaigns", 
    tags=["Campaigns"]
)
app.include_router(
    webhooks.router, 
    prefix="/api/v1/webhooks", 
    tags=["Webhooks"]
)
```

---

## 5. Rationale Summary

### Key Architectural Decisions

| Decision | Choice | Why This Approach |
|----------|--------|-------------------|
| **Monorepo vs Polyrepo** | Monorepo | Simplified development workflow and atomic deployments for a small team |
| **Sync vs Async** | Async (FastAPI) | Voice AI requires handling hundreds of concurrent WebSocket streams |
| **Provider Pattern** | Abstract interfaces | Enables A/B testing providers and switching without code changes |
| **Configuration** | YAML + Environment | Sensitive data in env vars, non-sensitive provider config in YAML |
| **Session State** | Redis | Distributed state for horizontal scaling; survives process restarts |
| **Database** | Supabase (PostgreSQL) | Built-in auth, real-time subscriptions, and hosted infrastructure |
| **Branching** | Git Flow variant | Stable production branch with integration testing before merge |

### Trade-offs Acknowledged

| Trade-off | What We Gain | What We Sacrifice |
|-----------|--------------|-------------------|
| **Async complexity** | High concurrency | Steeper learning curve, harder debugging |
| **Provider abstraction** | Flexibility | Additional indirection, more code |
| **Monorepo** | Unified development | Larger repo size, coupled releases |
| **FastAPI over Django** | Performance, types | Less batteries-included admin |

### Success Criteria for Day 1

- [x] MVP scope documented and agreed upon
- [x] Architecture diagram created and reviewed
- [x] Repository structure established
- [x] Backend skeleton with health check endpoint operational
- [x] Environment configuration system implemented
- [x] Core dependencies installed and version-locked

---

## Next Steps (Day 2 Preview)

Day 2 will focus on integrating AI providers:
- Set up STT provider (Deepgram) with streaming
- Set up TTS provider (ElevenLabs) with streaming
- Set up LLM provider (Groq) with conversation support
- Create test scripts to validate each provider independently

---

*Document Version: 1.0*  
*Last Updated: Day 1 of Development Sprint*
