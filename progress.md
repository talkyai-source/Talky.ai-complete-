# 📊 Talky.ai — Platform Progress & Architecture Document

**Document Version**: 2.0  
**Date**: February 17, 2026  
**Prepared For**: Presentation & Stakeholder Review  
**Status**: Active Development  

---

## 📋 Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Platform Overview & Vision](#2-platform-overview--vision)
3. [System Architecture](#3-system-architecture)
4. [Backend — Core Engine](#4-backend--core-engine)
5. [Voice Pipeline — Real-Time AI Conversation](#5-voice-pipeline--real-time-ai-conversation)
6. [Provider Integrations](#6-provider-integrations)
7. [Telephony & SIP Infrastructure](#7-telephony--sip-infrastructure)
8. [User Frontend (Talk-Leee)](#8-user-frontend-talk-leee)
9. [Admin Panel](#9-admin-panel)
10. [Database Architecture](#10-database-architecture)
11. [Multi-Tenant Architecture](#11-multi-tenant-architecture)
12. [AI Assistant & Agent System](#12-ai-assistant--agent-system)
13. [Connectors & Third-Party Integrations](#13-connectors--third-party-integrations)
14. [Campaign Management & Dialer Engine](#14-campaign-management--dialer-engine)
15. [Security, Compliance & Audit](#15-security-compliance--audit)
16. [Billing & Subscription System](#16-billing--subscription-system)
17. [Background Workers & Job Processing](#17-background-workers--job-processing)
18. [Deployment & DevOps](#18-deployment--devops)
19. [White Label Feature (Roadmap)](#19-white-label-feature-roadmap)
20. [Workflow Diagrams](#20-workflow-diagrams)
21. [Key Performance Metrics](#21-key-performance-metrics)
22. [Development Timeline Summary](#22-development-timeline-summary)
23. [Future Roadmap](#23-future-roadmap)

---

## 1. Executive Summary

**Talky.ai** is an enterprise-grade, AI-powered voice dialer platform that enables businesses to run intelligent, automated outbound calling campaigns at scale. The system conducts real-time, human-like conversations using a seamlessly integrated pipeline of Speech-to-Text (STT), Large Language Models (LLM), and Text-to-Speech (TTS) — all orchestrated through a modular, provider-agnostic backend architecture.

### Key Achievements to Date

| Milestone | Status |
|-----------|--------|
| Backend API (FastAPI) — 20+ endpoint groups | ✅ Complete |
| Real-Time Voice Pipeline (STT → LLM → TTS) | ✅ Complete |
| User-Facing Frontend (Next.js 15) | ✅ Complete |
| Admin Panel (React + Vite) | ✅ Complete |
| Multi-Tenant Architecture with RLS | ✅ Complete |
| FreeSWITCH / SIP / PBX Integration | ✅ Complete |
| AI Assistant Agent with Tool Calling | ✅ Complete |
| Connectors (CRM, Calendar, Drive, Email, SMS) | ✅ Complete |
| Campaign Dialer with Priority Queue | ✅ Complete |
| Billing & Subscription System | ✅ Complete |
| Voice Contract & Call Logging | ✅ Complete |
| Security (Encryption, Token Rotation, Audit) | ✅ Complete |
| White Label Feature | 📋 Planned |

### Platform in Numbers

| Metric | Value |
|--------|-------|
| Total Backend Endpoints | 60+ REST + WebSocket |
| Domain Models | 20+ Pydantic Models |
| Database Tables | 11 Core Tables with RLS |
| Background Workers | 3 (Dialer, Voice, Reminder) |
| Provider Integrations | 8+ (STT, TTS, LLM, Telephony, Storage) |
| Third-Party Connectors | 5 (CRM, Calendar, Drive, Email, SMS) |
| Frontend Pages | 17+ Dashboard Pages |
| Admin Panel Pages | 8 Management Pages |
| Test Files | 75+ Test Cases |

---

## 2. Platform Overview & Vision

### What Talky.ai Does

Talky.ai is not just a dialer — it is an **intelligent voice communication platform**. At its core, the system:

1. **Manages Outbound Campaigns** — Upload leads, configure AI agent behavior, set schedules, and launch campaigns with configurable concurrency.
2. **Conducts AI-Powered Conversations** — Each call is handled by an AI agent that listens (STT), thinks (LLM), and responds (TTS) in real-time with sub-500ms latency.
3. **Detects Intent & Takes Actions** — The AI can identify customer intent (book a meeting, request a callback, express interest) and take automated actions such as scheduling meetings or sending follow-up emails.
4. **Provides Full Analytics** — Every call is recorded, transcribed, analyzed for sentiment and outcomes, and surfaced through comprehensive dashboards.
5. **Supports Multi-Tenancy** — Each business operates in complete data isolation with Row-Level Security (RLS) at the database layer.

### Target Audience

- **Sales Teams** — Automated outreach with AI-guided qualification
- **Marketing Agencies** — Campaign management at scale across multiple clients
- **Healthcare Providers** — Appointment reminders and patient follow-ups
- **Financial Services** — Collections, reminders, and account verification
- **Enterprise Operations** — Any business requiring high-volume voice communication

---

## 3. System Architecture

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        TALKY.AI PLATFORM                                │
│                                                                         │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │  Talk-Leee   │   │   Admin Panel    │   │   White Label Portal   │  │
│  │  (Next.js)   │   │  (React+Vite)    │   │     (Planned)          │  │
│  │  User-Facing │   │  Platform Admin  │   │   Partner Dashboard    │  │
│  └──────┬───────┘   └────────┬─────────┘   └───────────┬────────────┘  │
│         │                    │                          │               │
│         └────────────────────┼──────────────────────────┘               │
│                              │                                          │
│                    ┌─────────▼──────────┐                               │
│                    │   FastAPI Backend   │                               │
│                    │   (Python 3.11+)   │                               │
│                    │                     │                               │
│                    │  ┌──────────────┐  │                               │
│                    │  │  API Router  │  │                               │
│                    │  │  20+ Groups  │  │                               │
│                    │  └──────┬───────┘  │                               │
│                    │         │          │                               │
│                    │  ┌──────▼───────┐  │                               │
│                    │  │   Domain     │  │                               │
│                    │  │  Services    │  │                               │
│                    │  └──────┬───────┘  │                               │
│                    │         │          │                               │
│                    │  ┌──────▼───────┐  │                               │
│                    │  │Infrastructure│  │                               │
│                    │  │  Providers   │  │                               │
│                    │  └──────────────┘  │                               │
│                    └────────┬───────────┘                               │
│                             │                                           │
│         ┌───────────────────┼───────────────────┐                      │
│         │                   │                   │                      │
│  ┌──────▼──────┐    ┌──────▼──────┐    ┌───────▼──────┐               │
│  │  Supabase   │    │    Redis    │    │  FreeSWITCH  │               │
│  │ (PostgreSQL)│    │  (Sessions) │    │   (SIP/RTP)  │               │
│  │  + Storage  │    │  + Queue    │    │  + Media GW  │               │
│  └─────────────┘    └─────────────┘    └──────────────┘               │
│                                                                         │
│         ┌───────────────────────────────────────┐                      │
│         │        External AI Providers           │                      │
│         │  ┌──────────┐ ┌───────┐ ┌──────────┐  │                      │
│         │  │ Deepgram │ │  Groq │ │ Cartesia │  │                      │
│         │  │  (STT)   │ │ (LLM) │ │  (TTS)   │  │                      │
│         │  └──────────┘ └───────┘ └──────────┘  │                      │
│         └───────────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### Three-Tier Component Breakdown

| Tier | Component | Technology |
|------|-----------|------------|
| **Frontend** | User Dashboard | Next.js 15 (App Router), TypeScript, Shadcn UI |
| **Frontend** | Admin Panel | React 18, Vite, TypeScript |
| **Backend** | API Server | FastAPI (Python), Uvicorn, ASGI |
| **Backend** | Background Workers | Python asyncio workers (Dialer, Voice, Reminder) |
| **Data** | Primary Database | PostgreSQL (Supabase) with RLS |
| **Data** | Session/Queue Store | Redis 7 (Alpine) |
| **Telephony** | Media Gateway | FreeSWITCH (Docker), SIP, RTP |
| **AI** | Speech-to-Text | Deepgram Flux (~260ms latency) |
| **AI** | Language Model | Groq (Llama 3.1 8B, 185 tokens/sec) |
| **AI** | Text-to-Speech | Cartesia Sonic 3 (90ms TTFA) |
| **Storage** | File Storage | Supabase Storage |

---

## 4. Backend — Core Engine

The backend is built with **FastAPI** and follows a **clean architecture** with clearly separated layers. The entire codebase is designed with the **provider pattern** — all external services implement abstract interfaces, allowing seamless swapping of providers without modifying business logic.

### Directory Structure

```
backend/app/
├── api/v1/                    # HTTP + WebSocket API Layer
│   ├── endpoints/             # 20+ endpoint modules
│   │   ├── admin/             # Admin-specific endpoints (7 modules)
│   │   ├── auth.py            # Authentication + rate limiting
│   │   ├── campaigns.py       # Campaign CRUD + lifecycle
│   │   ├── contacts.py        # Contact/lead management
│   │   ├── calls.py           # Call history + management
│   │   ├── recordings.py      # Recording management
│   │   ├── analytics.py       # Analytics endpoints
│   │   ├── billing.py         # Billing/subscription management
│   │   ├── connectors.py      # Third-party connector management
│   │   ├── meetings.py        # Meeting scheduling
│   │   ├── dashboard.py       # Dashboard data aggregation
│   │   ├── freeswitch_bridge.py # FreeSWITCH SIP/RTP bridge
│   │   ├── assistant_ws.py    # AI Assistant WebSocket
│   │   ├── ask_ai_ws.py       # Ask AI WebSocket interface
│   │   ├── ai_options.py      # AI Configuration REST API
│   │   ├── ai_options_ws.py   # AI Options WebSocket
│   │   └── webhooks.py        # External webhooks (Vonage, etc.)
│   ├── dependencies.py        # FastAPI dependency injection
│   └── routes.py              # Route aggregator
│
├── core/                      # Core Framework
│   ├── config.py              # Settings & environment management
│   ├── container.py           # DI Container (Supabase, Redis, Services)
│   ├── tenant_middleware.py   # Multi-tenant request middleware
│   └── validation.py         # Provider validation on startup
│
├── domain/                    # Business Logic (Provider-Independent)
│   ├── interfaces/            # Abstract provider contracts
│   │   ├── stt_provider.py    # Speech-to-Text interface
│   │   ├── tts_provider.py    # Text-to-Speech interface
│   │   ├── llm_provider.py    # Language Model interface
│   │   ├── telephony_provider.py  # Telephony interface
│   │   └── media_gateway.py   # Media gateway interface
│   ├── models/                # 20+ Domain models (Pydantic)
│   │   ├── campaign.py, call.py, lead.py, session.py
│   │   ├── voice_contract.py, voice_intent.py
│   │   ├── ai_config.py, agent_config.py
│   │   ├── connector.py, meeting.py, conversation.py
│   │   └── ... (20+ models)
│   ├── services/              # Core business services (20 modules)
│   └── repositories/          # Data access layer
│
├── infrastructure/            # Provider Implementations
│   ├── stt/                   # Deepgram, Deepgram Flux
│   ├── tts/                   # Cartesia, Deepgram TTS, Google TTS
│   ├── llm/                   # Groq (Llama 3.1)
│   ├── telephony/             # Vonage, FreeSWITCH, SIP, RTP
│   ├── connectors/            # CRM, Calendar, Drive, Email, SMS
│   ├── assistant/             # AI Agent + Tool calling
│   └── storage/               # Supabase storage
│
├── services/                  # Application-Level Services
│   ├── assistant_agent_service.py   # AI assistant orchestration
│   ├── audit_service.py             # Security audit logging
│   ├── crm_sync_service.py          # CRM synchronization
│   ├── drive_sync_service.py        # Drive file sync
│   ├── email_service.py             # Email sending
│   ├── meeting_service.py           # Meeting management
│   ├── sms_service.py               # SMS notifications
│   ├── quota_service.py             # Usage quota management
│   ├── token_rotation_service.py    # Token security
│   ├── replay_protection_service.py # Anti-replay security
│   └── connector_revocation_service.py # Connector token revocation
│
├── workers/                   # Background Job Processors
│   ├── dialer_worker.py       # Automated campaign dialing
│   ├── voice_worker.py        # Voice pipeline processing
│   └── reminder_worker.py     # Scheduled reminder delivery
│
└── utils/                     # Shared Utilities
```

### Core Domain Services (20 Modules)

| Service | Responsibility |
|---------|---------------|
| `voice_pipeline_service.py` | End-to-end voice pipeline orchestration (STT → LLM → TTS) |
| `voice_orchestrator.py` | Real-time voice session management and media coordination |
| `conversation_engine.py` | AI conversation state machine, context management |
| `call_service.py` | Call lifecycle management (initiate → active → complete) |
| `campaign_service.py` | Campaign CRUD, state transitions, lead management |
| `session_manager.py` | Redis-backed session management for active calls |
| `queue_service.py` | Priority queue with intelligent scheduling |
| `intent_detector.py` | Real-time customer intent classification |
| `prompt_manager.py` | Dynamic prompt construction for LLM |
| `post_call_analyzer.py` | Post-call analytics, sentiment, outcomes |
| `transcript_service.py` | Turn-by-turn transcript generation and storage |
| `recording_service.py` | Audio recording capture and storage |
| `billing_service.py` | Subscription management, usage tracking, invoicing |
| `scheduling_rules.py` | Time-window and DNC compliance for calling |
| `llm_guardrails.py` | Content safety filters, response validation |
| `latency_tracker.py` | End-to-end latency monitoring per pipeline stage |
| `email_template_manager.py` | Email template management and rendering |
| `sms_template_manager.py` | SMS template management and rendering |
| `global_ai_config.py` | Platform-wide AI configuration management |

---

## 5. Voice Pipeline — Real-Time AI Conversation

The voice pipeline is the **heart of Talky.ai** — it handles real-time, bidirectional audio streaming between callers and the AI agent. The pipeline is optimized for ultra-low latency to ensure natural conversational flow.

### Voice Pipeline Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                    VOICE PIPELINE FLOW                                │
│                                                                      │
│  CALLER                                                              │
│    │                                                                 │
│    │  ① RTP Audio (16kHz, Linear16)                                  │
│    ▼                                                                 │
│  ┌──────────────────┐                                                │
│  │   FreeSWITCH     │                                                │
│  │   Media Gateway   │  ← SIP Signaling from PBX (e.g., 3CX)       │
│  └────────┬─────────┘                                                │
│           │  ② WebSocket Binary Audio Stream                         │
│           ▼                                                          │
│  ┌──────────────────┐                                                │
│  │  Voice Pipeline  │                                                │
│  │   Service        │                                                │
│  │                  │                                                │
│  │  ┌─────────────────────────────────────────────────┐             │
│  │  │                                                 │             │
│  │  │  ③ STT (Deepgram Flux)        ~260ms            │             │
│  │  │     ↓ Transcript + Turn Detection               │             │
│  │  │                                                 │             │
│  │  │  ④ Intent Detection            ~50ms            │             │
│  │  │     ↓ Customer Intent + Action Plan             │             │
│  │  │                                                 │             │
│  │  │  ⑤ LLM (Groq Llama 3.1)       ~100ms           │             │
│  │  │     ↓ AI Response Text                          │             │
│  │  │                                                 │             │
│  │  │  ⑥ TTS (Cartesia Sonic 3)      ~90ms            │             │
│  │  │     ↓ Synthesized Audio                         │             │
│  │  │                                                 │             │
│  │  └─────────────────────────────────────────────────┘             │
│  │                  │                                                │
│  └──────────────────┘                                                │
│           │                                                          │
│           │  ⑦ Audio Response Stream                                 │
│           ▼                                                          │
│  ┌──────────────────┐                                                │
│  │   FreeSWITCH     │                                                │
│  │   → RTP to PBX   │                                                │
│  └──────────────────┘                                                │
│           │                                                          │
│           ▼                                                          │
│       CALLER hears AI response                                       │
│                                                                      │
│  ⏱️ Total Round-Trip Latency Target: < 500ms                        │
└──────────────────────────────────────────────────────────────────────┘
```

### Latency Breakdown

| Stage | Provider | Target Latency |
|-------|----------|---------------|
| Audio Capture → STT | Deepgram Flux | ~260ms |
| Turn Detection (End-of-Turn) | Deepgram Flux | EOT threshold 0.7 |
| Intent Detection | Custom NLP | ~50ms |
| LLM Response Generation | Groq (185 tok/s) | ~100ms |
| TTS Audio Synthesis (TTFA) | Cartesia Sonic 3 | ~90ms |
| **Total Pipeline Latency** | — | **< 500ms** |

### WebSocket Configuration

| Setting | Value |
|---------|-------|
| Max Concurrent Connections | 1,000 |
| Connection Timeout | 300 seconds |
| Heartbeat Interval | 30 seconds |
| Audio Chunk Size | 80ms |
| Max Message Size | 64 KB |
| Audio Encoding | Linear16, 16kHz |
| Audio Protocol | Binary WebSocket frames |

---

## 6. Provider Integrations

Talky.ai uses a **provider-agnostic architecture**. Every external service is abstracted behind an interface defined in `app/domain/interfaces/`. Providers can be swapped by editing `config/providers.yaml` — no code changes required.

### Currently Active Providers

| Category | Active Provider | Alternative Providers |
|----------|----------------|----------------------|
| **STT** (Speech-to-Text) | Deepgram Flux | Deepgram Standard |
| **TTS** (Text-to-Speech) | Cartesia Sonic 3 | Deepgram TTS, Google TTS, Google TTS Streaming |
| **LLM** (Language Model) | Groq (Llama 3.1 8B) | — (Extensible) |
| **Telephony** | Vonage + FreeSWITCH | SIP Gateway, Browser Media |
| **Storage** | Supabase | — (Extensible) |

### Provider Configuration (YAML-Driven)

```yaml
providers:
  stt:
    active: "flux"        # Ultra-low latency STT (~260ms)
  tts:
    active: "cartesia"    # Ultra-low latency TTS (90ms TTFA)
  llm:
    active: "groq"        # Ultra-fast inference (185 tokens/sec)
  telephony:
    active: "vonage"      # Enterprise telephony
  storage:
    active: "supabase"    # Managed storage
```

---

## 7. Telephony & SIP Infrastructure

### FreeSWITCH Integration

The platform uses **FreeSWITCH** as its media gateway for SIP/RTP audio handling. FreeSWITCH runs in a Docker container and bridges between the PBX (e.g., 3CX) and the AI voice pipeline.

```
┌────────────────────────────────────────────────────────────────┐
│                  TELEPHONY ARCHITECTURE                        │
│                                                                │
│  ┌──────────┐      SIP       ┌────────────────┐     WS       │
│  │   3CX    │ ◄────────────► │  FreeSWITCH    │ ◄──────────► │
│  │   PBX    │    Signaling   │  (Docker)       │   Audio      │
│  │          │                │  Host Network   │   Stream     │
│  └────┬─────┘                │  SIP Port: 5080 │              │
│       │                      │  RTP: 16384-    │   ┌─────────┐│
│       │ PSTN/SIP             │       32768     │   │ FastAPI  ││
│       │                      └────────────────┘   │ Backend  ││
│       ▼                                           └─────────┘│
│  ┌──────────┐                                                 │
│  │  Caller  │                                                 │
│  │  (Phone) │                                                 │
│  └──────────┘                                                 │
└────────────────────────────────────────────────────────────────┘
```

### Telephony Components

| Component | File | Purpose |
|-----------|------|---------|
| FreeSWITCH ESL Client | `freeswitch_esl.py` | Event Socket Layer communication |
| FreeSWITCH Audio Bridge | `freeswitch_audio_bridge.py` | Bidirectional audio bridging |
| RTP Media Gateway | `rtp_media_gateway.py` | RTP packet handling |
| SIP Media Gateway | `sip_media_gateway.py` | SIP signaling management |
| Vonage Media Gateway | `vonage_media_gateway.py` | Vonage NCCO-based calls |
| Browser Media Gateway | `browser_media_gateway.py` | WebRTC browser audio |
| FreeSWITCH Bridge API | `freeswitch_bridge.py` | REST API for FreeSWITCH control |

---

## 8. User Frontend (Talk-Leee)

The user-facing frontend is built with **Next.js 15** (App Router) and provides a comprehensive dashboard for managing campaigns, calls, contacts, analytics, and AI configuration.

### Frontend Pages & Features

| Page/Route | Description |
|------------|-------------|
| `/` (Home) | Landing page with Hero, Stats, Features, Packages, CTA |
| `/dashboard` | Main dashboard with real-time stats and overview |
| `/campaigns` | Campaign management (create, edit, start, pause, stop) |
| `/contacts` | Lead/contact management and import |
| `/calls` | Call history with recordings, transcripts, outcomes |
| `/recordings` | Audio recording browser with playback |
| `/analytics` | Campaign performance, call analytics, trends |
| `/ai-options` | AI agent configuration (voice, personality, prompt) |
| `/ai-voices` | Voice selection and preview |
| `/assistant` | AI Assistant with actions, meetings, reminders |
| `/connectors` | Third-party integration management |
| `/email` | Email campaign management |
| `/meetings` | Meeting scheduling and calendar view |
| `/reminders` | Reminder management and scheduling |
| `/notifications` | Notification center |
| `/settings` | Account, team, billing, and API settings |
| `/auth` | Login, signup, OTP verification |

### Frontend Technology Stack

| Technology | Purpose |
|-----------|---------|
| Next.js 15 | React framework with App Router |
| TypeScript | Type-safe development |
| Shadcn UI | Component library (primitives) |
| Zod | API response validation schema |
| Custom Fonts (Satoshi) | Brand typography |
| Playwright | E2E and visual testing |
| Storybook | Component development environment |

### API Integration

The frontend communicates with the backend through a centralized API client (`src/lib/api.ts`) with:
- **Zod schema validation** for all API responses
- **HTTP client** with automatic token management (`src/lib/http-client.ts`)
- **Auth context** with token lifecycle management
- **Feature-specific API modules** (dashboard-api, ai-options-api, backend-api, etc.)

---

## 9. Admin Panel

The Admin Panel is a **separate React + Vite application** providing platform administrators with full operational control and monitoring capabilities.

### Admin Panel Pages

| Page | Component | Features |
|------|-----------|----------|
| **Command Center** | `CommandCenterPage.tsx` | Live calls, queue depth, worker status, health overview, stats grid, top tenants |
| **Tenants** | `TenantsPage.tsx` | Tenant CRUD, quota management, suspension, plan assignment |
| **Calls** | `CallsPage.tsx` | Cross-tenant call monitoring, call detail drawer, filters |
| **Actions** | `ActionsPage.tsx` | AI action log, meeting bookings, emails sent, action detail drawer |
| **Connectors** | `ConnectorsPage.tsx` | Connector health, OAuth status, revocation, detail drawer |
| **Usage & Cost** | `UsageCostPage.tsx` | Usage breakdown by tenant, cost analysis, STT/TTS/LLM usage |
| **Incidents** | `IncidentsPage.tsx` | System incidents, error tracking, resolution status |
| **System Health** | `SystemHealthPage.tsx` | Service health monitoring, uptime, latency metrics |
| **Login** | `LoginPage.tsx` | Admin authentication with route guards |

### Admin Panel Components (25 Components)

| Component | Purpose |
|-----------|---------|
| `LiveCalls.tsx` / `LiveCallsTable.tsx` | Real-time active call monitoring |
| `QueueDepthChart.tsx` | Dialer queue visualization |
| `WorkerStatusTable.tsx` | Background worker health |
| `HealthOverviewCards.tsx` | System health at-a-glance |
| `StatsGrid.tsx` | Key platform metrics |
| `TenantsTable.tsx` | Tenant management with inline editing |
| `CallHistoryTable.tsx` / `CallDetailDrawer.tsx` | Call inspection |
| `ActionsTable.tsx` / `ActionDetailDrawer.tsx` | Action inspection |
| `ConnectorsTable.tsx` / `ConnectorDetailDrawer.tsx` | Connector management |
| `UsageBreakdownCard.tsx` | Per-tenant usage visualization |
| `TopTenantsList.tsx` / `TopTenantsPanel.tsx` | Highest-usage tenants |
| `QuotaUsage.tsx` | Quota consumption tracking |
| `SystemHealth.tsx` | Service-level health checks |
| `Sidebar.tsx` / `Header.tsx` / `Footer.tsx` | Layout components |
| `AdminRouteGuard.tsx` | Protected route wrapper |
| `ConfirmationModal.tsx` | Action confirmation dialogs |

### Admin Backend API Endpoints

| Endpoint Group | File | Key Endpoints |
|----------------|------|---------------|
| **Health** | `admin/health.py` | Service health, Redis, workers, provider status |
| **Tenants** | `admin/tenants.py` | CRUD, suspend, resume, quota override |
| **Calls** | `admin/calls.py` | Cross-tenant call search, statistics |
| **Actions** | `admin/actions.py` | Action log, meeting/email/SMS audit |
| **Connectors** | `admin/connectors.py` | Connector status, revocation, re-auth |
| **Usage** | `admin/usage.py` | Usage breakdown, cost calculation |
| **Base** | `admin/base.py` | Admin authentication, authorization |

---

## 10. Database Architecture

The platform uses **PostgreSQL via Supabase** with a comprehensive multi-tenant schema. Every tenant-scoped table includes a `tenant_id` column with proper foreign key constraints and Row-Level Security policies.

### Database Schema Diagram

```
┌───────────────────────────────────────────────────────────────────┐
│                    DATABASE SCHEMA (11 Tables)                     │
│                                                                   │
│  ┌──────────┐     ┌──────────────┐     ┌─────────────────────┐   │
│  │  plans   │◄────│   tenants    │◄────│   user_profiles     │   │
│  │ (pricing)│     │ (multi-ten.) │     │ (extends auth.users)│   │
│  └──────────┘     └──────┬───────┘     └─────────────────────┘   │
│                          │                                        │
│              ┌───────────┼───────────┐                           │
│              │           │           │                           │
│        ┌─────▼────┐ ┌───▼────┐ ┌───▼──────┐                    │
│        │campaigns │ │clients │ │dialer_   │                    │
│        │          │ │        │ │jobs      │                    │
│        └────┬─────┘ └────────┘ └──────────┘                    │
│             │                                                   │
│        ┌────▼────┐                                              │
│        │  leads  │                                              │
│        └────┬────┘                                              │
│             │                                                   │
│        ┌────▼────┐                                              │
│        │  calls  │                                              │
│        └────┬────┘                                              │
│             │                                                   │
│    ┌────────┼────────┬────────────┐                             │
│    │        │        │            │                             │
│  ┌─▼──────┐│  ┌─────▼────┐  ┌───▼────────┐                   │
│  │conversa││  │recordings│  │transcripts │                   │
│  │tions   ││  │          │  │            │                   │
│  └────────┘│  └──────────┘  └────────────┘                   │
│            │                                                   │
└───────────────────────────────────────────────────────────────────┘
```

### Core Tables

| Table | Records | Purpose |
|-------|---------|---------|
| `plans` | 3 (Basic, Professional, Enterprise) | Pricing packages |
| `tenants` | Per-organization | Business/organization accounts |
| `user_profiles` | Per-user | Extends Supabase auth.users |
| `campaigns` | Per-tenant | Outbound calling campaigns |
| `leads` | Per-campaign | Contact leads with priority |
| `calls` | Per-lead | Individual call records |
| `conversations` | Per-call | Conversation message history |
| `recordings` | Per-call | Audio recording references |
| `transcripts` | Per-call | Turn-by-turn transcript storage |
| `clients` | Per-tenant | CRM-style client management |
| `dialer_jobs` | Per-campaign | Dialer queue entries |

### Pricing Plans

| Plan | Price | Minutes | AI Agents | Concurrent Calls |
|------|-------|---------|-----------|-------------------|
| **Basic** | $29/mo | 300 | 1 | 1 |
| **Professional** | $79/mo | 1,500 | 3 | 3 |
| **Enterprise** | $199/mo | 5,000 | 10 | 10 |

---

## 11. Multi-Tenant Architecture

### Tenant Isolation Model

The multi-tenant architecture ensures **complete data isolation** at three levels:

1. **Database Level (RLS)** — Row-Level Security policies enforce that users can only access data within their own `tenant_id`. The service role bypasses RLS for admin operations.

2. **Application Level (Middleware)** — The `TenantMiddleware` in FastAPI extracts the tenant context from the authenticated user and injects it into every request.

3. **API Level (Dependencies)** — FastAPI dependencies enforce that tenant-scoped operations validate `tenant_id` ownership.

```
┌────────────────────────────────────────────────────┐
│              MULTI-TENANT DATA FLOW                 │
│                                                    │
│  Request ──► TenantMiddleware ──► tenant_id        │
│                                    │               │
│              ┌─────────────────────▼──────────┐    │
│              │  API Endpoint                  │    │
│              │  (validates tenant ownership)  │    │
│              └─────────────────────┬──────────┘    │
│                                    │               │
│              ┌─────────────────────▼──────────┐    │
│              │  Supabase Query                │    │
│              │  (RLS enforces tenant filter)  │    │
│              └────────────────────────────────┘    │
└────────────────────────────────────────────────────┘
```

---

## 12. AI Assistant & Agent System

The AI Assistant is a powerful agent system that goes beyond simple question-answering. It can take **real-world actions** on behalf of users through tool calling.

### Assistant Architecture

```
┌──────────────────────────────────────────────────────┐
│              AI ASSISTANT AGENT                       │
│                                                      │
│  User Message                                        │
│       │                                              │
│       ▼                                              │
│  ┌──────────────────┐                                │
│  │ Conversation      │                                │
│  │ Engine            │ ← Manages context & state      │
│  └────────┬─────────┘                                │
│           │                                          │
│           ▼                                          │
│  ┌──────────────────┐                                │
│  │ LLM (Groq)       │ ← Decides response + actions   │
│  │ + Tool Definitions│                                │
│  └────────┬─────────┘                                │
│           │                                          │
│      ┌────┴────────────────────────────┐             │
│      │        TOOL CALLING             │             │
│      │  ┌──────────┐  ┌────────────┐  │             │
│      │  │ Book     │  │ Send      │  │             │
│      │  │ Meeting  │  │ Email     │  │             │
│      │  └──────────┘  └────────────┘  │             │
│      │  ┌──────────┐  ┌────────────┐  │             │
│      │  │ Send     │  │ Create    │  │             │
│      │  │ SMS      │  │ Reminder  │  │             │
│      │  └──────────┘  └────────────┘  │             │
│      │  ┌──────────┐  ┌────────────┐  │             │
│      │  │ CRM Sync │  │ Drive     │  │             │
│      │  │          │  │ Upload    │  │             │
│      │  └──────────┘  └────────────┘  │             │
│      └─────────────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

### Available AI Tools

| Tool | Description | Connector Required |
|------|-------------|-------------------|
| Book Meeting | Schedule meetings via Google Calendar | Calendar |
| Send Email | Compose and send emails via Gmail | Email |
| Send SMS | Send text messages via Twilio | SMS |
| Create Reminder | Schedule follow-up reminders | Built-in |
| CRM Sync | Push call data to CRM (HubSpot, etc.) | CRM |
| Drive Upload | Upload recordings/transcripts to Google Drive | Drive |

---

## 13. Connectors & Third-Party Integrations

The connector system enables Talky.ai to integrate with external business tools. Each connector uses **OAuth 2.0** for secure authentication and supports automatic **token rotation** and **revocation**.

### Connector Architecture

| Connector | Provider | Features |
|-----------|----------|----------|
| **Calendar** | Google Calendar | Meeting scheduling, availability check, event creation |
| **CRM** | HubSpot (extensible) | Contact sync, deal updates, activity logging |
| **Drive** | Google Drive | Recording uploads, transcript storage, file management |
| **Email** | Gmail (OAuth) | Email sending, template management, audit logging |
| **SMS** | Twilio | SMS sending, template management, delivery tracking |

### Security Features

| Feature | Implementation |
|---------|---------------|
| OAuth 2.0 | Standard OAuth flow with PKCE |
| Token Encryption | AES encryption at rest |
| Token Rotation | Automatic refresh before expiry |
| Replay Protection | Nonce-based anti-replay service |
| Revocation | Manual/automatic token revocation |
| Audit Logging | Full audit trail for all connector actions |

---

## 14. Campaign Management & Dialer Engine

### Campaign Lifecycle

```
┌──────────────────────────────────────────────────────────────┐
│               CAMPAIGN STATE MACHINE                          │
│                                                              │
│  ┌────────┐    Start    ┌─────────┐    Complete  ┌────────┐ │
│  │ DRAFT  │ ──────────► │ ACTIVE  │ ───────────► │  DONE  │ │
│  └────────┘             └────┬────┘              └────────┘ │
│       ▲                      │                        ▲      │
│       │                   Pause                       │      │
│       │                      │                        │      │
│       │                 ┌────▼────┐    Resume     ┌───┘      │
│       │                 │ PAUSED  │ ──────────────┘          │
│       │                 └─────────┘                          │
│       │                                                      │
│       └──────── Reset / Clone ───────────────────────────────┘
└──────────────────────────────────────────────────────────────┘
```

### Dialer Queue System

The dialer uses a **priority-based queue** with intelligent scheduling:

| Feature | Description |
|---------|-------------|
| Priority Scoring | 1-10 scale, VIP leads get +2 boost |
| Scheduling Rules | Time windows, timezone awareness, DNC compliance |
| Retry Logic | Configurable retries with backoff (busy, no answer) |
| Concurrency Control | Per-tenant concurrent call limits |
| Goal Detection | Stops calling when campaign goal is achieved |
| Smart Scheduling | Respects minimum hours between re-calls |

### Dialer Job States

```
pending → processing → completed
                    ↓         ↓
              retry_scheduled  failed
                    ↓
               goal_achieved
                    ↓
              non_retryable
                    ↓
                 skipped
```

---

## 15. Security, Compliance & Audit

### Security Architecture

| Layer | Implementation |
|-------|---------------|
| **Authentication** | Supabase Auth (JWT), OTP verification |
| **Authorization** | Role-based (user, admin, white_label_admin) |
| **Rate Limiting** | SlowAPI with per-endpoint limits |
| **Data Isolation** | PostgreSQL RLS on all tenant tables |
| **Encryption** | AES token encryption for connectors |
| **Token Security** | Automatic rotation + replay protection |
| **Audit Trail** | Comprehensive audit logging service |
| **CORS** | Restricted to known origins |
| **Input Validation** | Pydantic models with strict validation |
| **Content Safety** | LLM guardrails for response filtering |

### Audit Service

The `AuditService` logs every significant action including:
- User authentication events
- Connector authorization/revocation
- Campaign lifecycle changes
- Call state transitions
- Admin actions (suspend, resume, quota changes)
- AI agent tool invocations

---

## 16. Billing & Subscription System

### Billing Architecture

```
┌────────────────────────────────────────────────────────┐
│                 BILLING SYSTEM                          │
│                                                        │
│  ┌──────────┐      ┌──────────────┐     ┌──────────┐ │
│  │  Plans   │ ────►│   Tenants    │ ───►│  Usage   │ │
│  │  (3 tiers)│      │  (subscribed)│     │ Tracking │ │
│  └──────────┘      └──────┬───────┘     └────┬─────┘ │
│                           │                   │       │
│                     ┌─────▼─────┐       ┌────▼─────┐ │
│                     │  Stripe   │       │  Quota   │ │
│                     │  Billing  │       │  Service │ │
│                     └───────────┘       └──────────┘ │
└────────────────────────────────────────────────────────┘
```

### Features

- **Three pricing tiers** (Basic $29, Professional $79, Enterprise $199)
- **Minutes-based billing** with overage tracking
- **Quota enforcement** at the API level via `QuotaService`
- **Stripe integration** for payment processing
- **Usage analytics** with per-service breakdown (STT, TTS, LLM)
- **Admin cost visibility** across all tenants

---

## 17. Background Workers & Job Processing

Three dedicated background workers handle asynchronous task processing:

| Worker | File | Purpose | Key Features |
|--------|------|---------|-------------|
| **Dialer Worker** | `dialer_worker.py` | Processes campaign call queue | Priority scheduling, concurrency control, retry logic |
| **Voice Worker** | `voice_worker.py` | Manages active voice sessions | Audio streaming, pipeline coordination, session lifecycle |
| **Reminder Worker** | `reminder_worker.py` | Delivers scheduled reminders | Multi-channel (voice, SMS, email), retry on failure |

### Worker Deployment (systemd)

All workers are deployed as **systemd services** with proper lifecycle management:

```
talky.target (group)
├── talky-api.service          (FastAPI server)
├── talky-dialer-worker.service (Campaign dialer)
├── talky-voice-worker.service  (Voice pipeline)
└── talky-reminder-worker.service (Reminders)
```

---

## 18. Deployment & DevOps

### Docker Architecture

```yaml
# Main docker-compose.yml
services:
  backend:       # FastAPI + Uvicorn (Port 8000)
  redis:         # Redis 7 Alpine (Port 6379)

# FreeSWITCH docker-compose
services:
  freeswitch:    # drachtio/freeswitch-mrf (Host Network)
                 # SIP: 5080, RTP: 16384-32768
```

### Infrastructure Components

| Component | Technology | Deployment |
|-----------|----------|------------|
| API Server | FastAPI + Uvicorn | Docker container / systemd |
| Frontend | Next.js 15 | Vercel / static hosting |
| Admin Panel | Vite + React | Static hosting |
| Database | PostgreSQL | Supabase (managed) |
| Cache/Queue | Redis 7 | Docker container |
| Media Gateway | FreeSWITCH | Docker (host network) |
| File Storage | Supabase Storage | Managed |

### Environment Configuration

The platform uses a **layered configuration** approach:

1. `.env` — API keys and secrets (Deepgram, Groq, Cartesia, Vonage, Supabase)
2. `config/providers.yaml` — Active provider selection
3. `config/development.yaml` — Development overrides
4. `config/production.yaml` — Production settings
5. `config/sip_config.yaml` — SIP/telephony settings

---

## 19. White Label Feature (Roadmap)

A comprehensive **White Label system** is planned as the next major feature, enabling reseller partnerships. This is documented in detail in `white_label.md`.

### Business Model

| Type | Pricing | Description |
|------|---------|-------------|
| Direct Users | $30/mo | Standard Talky.ai branding |
| White Label Partners | $25/user/mo | Custom branding, resell capability |
| Sub-Tenant (Partner's Client) | Varies | Managed by the partner |

### Planned Architecture

```
Platform Admin
    │
    ├── Direct Tenants ($30/mo each)
    │
    └── White Label Partner ($25/user bulk)
            │
            ├── Sub-Tenant Client 1
            ├── Sub-Tenant Client 2
            └── Sub-Tenant Client 3
```

### Implementation Scope (20 Working Days)

| Week | Focus | Deliverables |
|------|-------|-------------|
| Week 1 | Foundation | Database schema, backend models, API endpoints |
| Week 2 | Admin Panel | Partner management UI in admin panel |
| Week 3 | Partner Portal | Self-service partner dashboard |
| Week 4 | Integration | Billing, security testing, polish |

---

## 20. Workflow Diagrams

### Workflow 1: Outbound Campaign Execution

```
                        CAMPAIGN EXECUTION WORKFLOW

  ┌─────────┐     ┌──────────┐     ┌──────────────┐     ┌──────────┐
  │  Admin   │────►│ Campaign │────►│ Lead Import  │────►│  Queue   │
  │ Creates  │     │  Config  │     │ (CSV/Manual) │     │  Build   │
  │ Campaign │     │ AI Prompt│     │ Phone Numbers│     │ Priority │
  └─────────┘     │ Voice ID │     └──────────────┘     │ Sorted   │
                  │ Schedule │                          └────┬─────┘
                  └──────────┘                               │
                                                             │
                                                        Start Campaign
                                                             │
                                          ┌──────────────────▼──────┐
                                          │    DIALER WORKER        │
                                          │  ┌───────────────────┐  │
                                          │  │ Pick highest      │  │
                                          │  │ priority lead     │  │
                                          │  └────────┬──────────┘  │
                                          │           │             │
                                          │  ┌────────▼──────────┐  │
                                          │  │ Check scheduling  │  │
                                          │  │ rules & quotas    │  │
                                          │  └────────┬──────────┘  │
                                          │           │             │
                                          │  ┌────────▼──────────┐  │
                                          │  │ Initiate call via │  │
                                          │  │ telephony provider│  │
                                          │  └────────┬──────────┘  │
                                          │           │             │
                                          │  ┌────────▼──────────┐  │
                                          │  │ Voice Pipeline    │  │
                                          │  │ STT → LLM → TTS  │  │
                                          │  └────────┬──────────┘  │
                                          │           │             │
                                          │  ┌────────▼──────────┐  │
                                          │  │ Post-Call Analyze  │  │
                                          │  │ Transcript, Intent │  │
                                          │  │ Outcome, Sentiment │  │
                                          │  └────────┬──────────┘  │
                                          │           │             │
                                          │  ┌────────▼──────────┐  │
                                          │  │ Execute Actions   │  │
                                          │  │ (Book, Email, SMS)│  │
                                          │  └────────┬──────────┘  │
                                          │           │             │
                                          │     Next Lead ──────►  │
                                          └─────────────────────────┘
```

### Workflow 2: User Registration & Onboarding

```
  ┌────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ Signup │───►│ Email OTP │───►│ Verify   │───►│ Create   │───►│ Choose   │
  │ Form   │    │ Sent      │    │ OTP      │    │ Tenant   │    │ Plan     │
  └────────┘    └───────────┘    └──────────┘    └──────────┘    └────┬─────┘
                                                                      │
                                                                 ┌────▼─────┐
                                                                 │Dashboard │
                                                                 │ (Ready)  │
                                                                 └──────────┘
```

### Workflow 3: AI Intent Detection & Action Execution

```
  ┌──────────────────────────────────────────────────────────────────┐
  │              INTENT DETECTION & ACTION FLOW                      │
  │                                                                  │
  │  Caller says: "I'd like to schedule a meeting next Tuesday"     │
  │       │                                                          │
  │       ▼                                                          │
  │  ┌─────────────────┐                                             │
  │  │ STT Transcribes │ → "schedule a meeting next Tuesday"        │
  │  └────────┬────────┘                                             │
  │           │                                                      │
  │  ┌────────▼────────┐                                             │
  │  │ Intent Detector │ → Intent: BOOK_MEETING                     │
  │  │                 │   Confidence: 0.95                          │
  │  │                 │   Entities: {date: "next Tuesday"}          │
  │  └────────┬────────┘                                             │
  │           │                                                      │
  │  ┌────────▼────────┐                                             │
  │  │ LLM Generates   │ → "I'll schedule that for you.            │
  │  │ Response         │    What time works best?"                  │
  │  └────────┬────────┘                                             │
  │           │                                                      │
  │  ┌────────▼────────┐                                             │
  │  │ Action Plan     │ → Create Google Calendar event             │
  │  │ Created         │   Send confirmation email                  │
  │  │                 │   Log in CRM                                │
  │  └─────────────────┘                                             │
  │                                                                  │
  └──────────────────────────────────────────────────────────────────┘
```

---

## 21. Key Performance Metrics

### Voice Pipeline Performance

| Metric | Target | Current |
|--------|--------|---------|
| STT Latency (Deepgram Flux) | < 300ms | ~260ms ✅ |
| LLM Inference (Groq) | < 150ms | ~100ms ✅ |
| TTS First Audio (Cartesia) | < 100ms | ~90ms ✅ |
| Total Round-Trip | < 500ms | ~450ms ✅ |
| End-of-Turn Detection | > 0.7 confidence | 0.7 ✅ |
| Audio Chunk Size | 80ms | 80ms ✅ |

### Platform Scalability

| Metric | Specification |
|--------|--------------|
| Max Concurrent WebSocket Connections | 1,000 |
| Max Concurrent Calls (per Performance settings) | 100 |
| Max Sessions per Worker | 100 |
| Session TTL | 1 hour |
| Stale Session Timeout | 5 minutes |
| Redis Session Sync Interval | 5 seconds |
| Heartbeat Interval | 30 seconds |

### API Performance

| Metric | Target |
|--------|--------|
| API Response Time (REST) | < 200ms |
| WebSocket Connection Establishment | < 500ms |
| Health Check Response | < 50ms |
| Database Query (with RLS) | < 100ms |

---

## 22. Development Timeline Summary

### Completed Development Phases (44+ Days Documented)

| Phase | Days | Key Deliverables |
|-------|------|-----------------|
| **Foundation** | Days 1-5 | Project setup, core architecture, provider pattern, initial STT/TTS/LLM integration |
| **Campaign Engine** | Days 6-10 | Campaign CRUD, lead management, dialer queue, priority scheduling |
| **Voice Pipeline** | Days 11-15 | Real-time voice pipeline, WebSocket streaming, session management |
| **Analytics & Recording** | Days 16-18 | Call recording, transcription, post-call analysis, sentiment |
| **TTS Evolution** | Days 19-21 | TTS streaming, Google TTS migration, Cartesia integration |
| **AI Assistant** | Days 22-25 | AI agent system, tool calling, meeting booking, connectors |
| **Communications** | Days 26-28 | Email service, SMS service, timed communication, assistant agent service |
| **Advanced Intents** | Day 29 | Voice intent detection, action plans, automated response actions |
| **Connectors** | Day 30 | CRM sync, Google Drive sync, connector architecture |
| **Security** | Day 31 | Token rotation, replay protection, audit service, encryption |
| **Frontend Alignment** | Day 32 | Frontend-backend API alignment, dashboard integration |
| **SIP/PBX** | Days 33-35 | SIP integration, MicroSIP setup, FreeSWITCH on Linux+Windows |
| **AI Conversation** | Day 36 | Enhanced conversation engine, context management |
| **Vonage Pipeline** | Day 37 | Vonage telephony pipeline, webhook handling |
| **Architecture** | Day 38 | Architecture improvements, code refactoring |
| **Voice Contract** | Days 39-40 | Voice contract specification, call state model, event schema |
| **Voice Orchestrator** | Day 41 | Voice orchestrator implementation, media coordination |
| **Voice Cleanup** | Day 43 | Linux voice pipeline cleanup, service optimization |
| **Systemd Deployment** | Day 44 | Systemd services, production deployment configuration |

### Documentation Generated

Over **60+ detailed documentation files** have been created throughout development, covering:
- Daily implementation reports
- Architecture reviews
- API endpoint reports
- WebSocket protocol specifications
- Testing guides
- Provider integration guides
- Deployment guides

---

## 23. Future Roadmap

### Near-Term (Q1 2026)

| Feature | Priority | Estimated Duration |
|---------|----------|-------------------|
| White Label Feature | 🔴 High | 20 working days |
| Advanced Analytics Dashboard | 🟡 Medium | 5 days |
| Inbound Call Support | 🟡 Medium | 10 days |
| Multi-Language Support | 🟡 Medium | 5 days |

### Mid-Term (Q2 2026)

| Feature | Priority |
|---------|----------|
| Custom Voice Cloning | 🟡 Medium |
| Predictive Dialing (ML-based) | 🟡 Medium |
| Conversation Intelligence (Coaching) | 🔵 Low |
| Mobile App (React Native) | 🔵 Low |
| Zapier/Make.com Integration | 🟡 Medium |

### Long-Term Vision

| Feature | Priority |
|---------|----------|
| On-Premise Deployment Option | 🔵 Low |
| Marketplace for AI Agents | 🔵 Low |
| Real-Time Agent Transfer (AI → Human) | 🟡 Medium |
| Advanced Compliance (GDPR, TCPA) | 🔴 High |
| WebRTC Direct Browser Calling | 🟡 Medium |

---

## 📈 Summary Graph — Platform Growth

```
Feature Completion Over Time
━━━━━━━━━━━━━━━━━━━━━━━━━━━

100% ┤                                                         ●━━ Current
     │                                                    ●━━━━
 90% ┤                                               ●━━━━
     │                                          ●━━━━
 80% ┤                                     ●━━━━
     │                                ●━━━━
 70% ┤                           ●━━━━
     │                      ●━━━━
 60% ┤                 ●━━━━
     │            ●━━━━
 50% ┤       ●━━━━
     │  ●━━━━
 40% ┤━━
     │
     └──────────────────────────────────────────────────────────►
       D1-5   D6-10  D11-15 D16-20 D21-25 D26-30 D31-35 D36-44
       
       Foundation → Voice → Analytics → AI Agent → Security → SIP
```

```
Technology Stack Complexity
━━━━━━━━━━━━━━━━━━━━━━━━━━

  Backend Services    ████████████████████████  20 services
  Domain Models       ████████████████████████  20+ models
  API Endpoints       ████████████████████████████████████████████████  60+ endpoints
  Database Tables     ████████████████  11 tables
  Frontend Pages      ████████████████████████████  17+ pages
  Admin Pages         ████████████  8 pages
  Test Files          ████████████████████████████████████████████████████████████████████  75+ tests
  Workers             ████  3 workers
  Connectors          ████████  5 connectors
  Providers           ████████████  8+ providers
```

---

**Document End**  
*Prepared by the Talky.ai Development Team*  
*Last Updated: February 17, 2026*
