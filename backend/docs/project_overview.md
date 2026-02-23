# Talky.ai - Complete AI Voice Dialer Platform

## Executive Summary

Talky.ai is an enterprise-grade AI-powered voice dialer platform that revolutionizes outbound calling campaigns through real-time conversational AI. The platform combines cutting-edge speech recognition, large language models, and text-to-speech synthesis to create natural, human-like phone conversations at scale.

### What Makes Talky.ai Unique

- **Sub-Second Response Time**: Total voice pipeline latency of 500-900ms enables natural conversation flow
- **Intelligent Barge-In**: Users can interrupt the AI mid-speech for truly interactive dialogues
- **Multi-Tenant Architecture**: Complete data isolation with Row-Level Security across 17+ database tables
- **Unified Assistant**: AI chatbot that can query data, book meetings, send emails, and trigger calls
- **Enterprise Integrations**: Native OAuth connections to Google, Microsoft, HubSpot, and more

---

## System Architecture

### High-Level Architecture Diagram

```
                                    ┌─────────────────────────────────────┐
                                    │           FRONTEND                   │
                                    │         (Next.js 15)                 │
                                    │                                      │
                                    │  Dashboard │ Campaigns │ Analytics  │
                                    │  Calls     │ Contacts  │ Recordings │
                                    │  AI Options│ Assistant │ Settings   │
                                    └──────────────────┬────────────────────┘
                                                       │
                                                       │ HTTPS / WebSocket
                                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                    BACKEND (FastAPI)                                  │
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │   Auth Layer    │  │   API Layer     │  │  WebSocket      │  │   Background    │  │
│  │  ─────────────  │  │  ─────────────  │  │  ─────────────  │  │   Workers       │  │
│  │ JWT Validation  │  │ REST Endpoints  │  │ Voice Pipeline  │  │  ─────────────  │  │
│  │ Tenant Extract  │  │ CRUD Operations │  │ Assistant Chat  │  │ Dialer Queue    │  │
│  │ RLS Enforcement │  │ File Upload     │  │ Real-time Audio │  │ Reminder Send   │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
│                                                                                       │
│  ┌───────────────────────────────────────────────────────────────────────────────┐   │
│  │                           VOICE PIPELINE                                       │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                     │   │
│  │  │     STT      │    │     LLM      │    │     TTS      │                     │   │
│  │  │  Deepgram    │───►│    Groq      │───►│  Google TTS  │                     │   │
│  │  │    Flux      │    │  Llama 3.3   │    │  Chirp 3 HD  │                     │   │
│  │  │  ~200-300ms  │    │  ~300-500ms  │    │   ~200ms     │                     │   │
│  │  └──────────────┘    └──────────────┘    └──────────────┘                     │   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                       │
│  ┌───────────────────────────────────────────────────────────────────────────────┐   │
│  │                         SERVICE LAYER                                          │   │
│  │                                                                                │   │
│  │  CampaignService │ CallService │ RecordingService │ TranscriptService         │   │
│  │  MeetingService  │ EmailService│ SMSService       │ AssistantAgentService     │   │
│  │  CRMSyncService  │ DriveSyncService │ QuotaService│ AuditService              │   │
│  │  TokenRotationService │ ReplayProtectionService   │ ConnectorRevocationService│   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────┘
                    │                              │                    │
                    ▼                              ▼                    ▼
        ┌───────────────────┐          ┌───────────────────┐  ┌───────────────────┐
        │    PostgreSQL     │          │      Redis        │  │  External APIs    │
        │    (Supabase)     │          │                   │  │                   │
        │  ───────────────  │          │  ───────────────  │  │  ───────────────  │
        │  17+ Tables       │          │  Job Queues       │  │  Stripe Billing   │
        │  RLS Policies     │          │  Session State    │  │  OAuth Providers  │
        │  Encrypted Data   │          │  Rate Limiting    │  │  Vonage Telephony │
        └───────────────────┘          └───────────────────┘  └───────────────────┘
```

### Technology Stack Details

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| **Backend Framework** | FastAPI | 0.100+ | Async REST API with automatic OpenAPI docs |
| **Database** | PostgreSQL | 15+ | Primary data store via Supabase |
| **Cache/Queue** | Redis | 7+ | Job queuing, session state, rate limiting |
| **Frontend** | Next.js | 15.5+ | React-based dashboard with SSR |
| **Authentication** | Supabase Auth | - | JWT tokens with OTP email verification |
| **STT Provider** | Deepgram | Flux API | Real-time streaming transcription |
| **LLM Provider** | Groq | Cloud API | Ultra-fast inference with Llama 3.3 |
| **TTS Provider** | Google Cloud | v1 API | High-quality Chirp 3 HD voices |
| **Telephony** | Vonage | v4 API | SIP trunking, SMS delivery |
| **Billing** | Stripe | v2 API | Subscriptions, metered billing |
| **Email** | Gmail API | v1 | OAuth-based email sending |
| **Calendar** | Google Calendar | v3 | Meeting booking with Meet links |
| **CRM** | HubSpot | v3 API | Contact sync, call logging |
| **Storage** | Google Drive | v3 | Recording/transcript backup |

---

## Database Architecture

### Entity Relationship Overview

```
┌─────────────┐       ┌─────────────┐       ┌─────────────┐
│   tenants   │◄──────│user_profiles│       │    plans    │
│  (org root) │       │  (users)    │       │(subscription│
└──────┬──────┘       └─────────────┘       └──────┬──────┘
       │                                           │
       │ 1:N                                       │
       ▼                                           │
┌─────────────┐       ┌─────────────┐              │
│  campaigns  │◄──────│   leads     │              │
│  (outreach) │ 1:N   │ (contacts)  │              │
└──────┬──────┘       └──────┬──────┘              │
       │                     │                     │
       │ 1:N                 │ 1:N                 │
       ▼                     ▼                     │
┌─────────────┐       ┌─────────────┐              │
│    calls    │───────│conversations│              │
│  (history)  │ 1:1   │  (state)    │              │
└──────┬──────┘       └─────────────┘              │
       │                                           │
       │ 1:1                                       │
       ▼                                           │
┌─────────────┐       ┌─────────────┐              │
│ recordings  │       │ transcripts │              │
│ (audio)     │       │  (text)     │              │
└─────────────┘       └─────────────┘              │
                                                   │
┌─────────────┐       ┌─────────────┐              │
│ connectors  │◄──────│ connector_  │              │
│(definitions)│ 1:N   │  accounts   │              │
└─────────────┘       │(OAuth tokens│              │
                      └─────────────┘              │
                                                   │
┌─────────────┐       ┌─────────────┐       ┌──────▼──────┐
│  meetings   │       │  reminders  │       │subscriptions│
│ (calendar)  │       │ (scheduled) │       │ (billing)   │
└─────────────┘       └─────────────┘       └─────────────┘
```

### Complete Table Definitions

#### `tenants` - Organization Root Table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique tenant identifier |
| `name` | VARCHAR(255) | NOT NULL | Organization name |
| `business_name` | VARCHAR(255) | | Legal business name |
| `stripe_customer_id` | VARCHAR(255) | UNIQUE | Stripe customer reference |
| `stripe_subscription_id` | VARCHAR(255) | | Active subscription ID |
| `subscription_status` | VARCHAR(50) | DEFAULT 'trialing' | active, canceled, past_due |
| `plan_id` | UUID | FK plans(id) | Current subscription plan |
| `settings` | JSONB | DEFAULT '{}' | Tenant-specific configuration |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `user_profiles` - User Accounts

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, FK auth.users(id) | Supabase Auth user ID |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Organization membership |
| `email` | VARCHAR(255) | NOT NULL, UNIQUE | User email address |
| `name` | VARCHAR(255) | | Display name |
| `role` | VARCHAR(50) | DEFAULT 'user' | admin, user, viewer |
| `avatar_url` | TEXT | | Profile picture URL |
| `phone_number` | VARCHAR(50) | | Contact phone |
| `minutes_remaining` | INTEGER | DEFAULT 0 | Available call minutes |
| `last_login_at` | TIMESTAMPTZ | | Last successful login |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Account creation |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `campaigns` - Outreach Campaign Configuration

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Campaign identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `name` | VARCHAR(255) | NOT NULL | Campaign display name |
| `description` | TEXT | | Campaign purpose/notes |
| `status` | VARCHAR(50) | DEFAULT 'draft' | draft, active, paused, completed |
| `system_prompt` | TEXT | | AI agent instructions |
| `voice_id` | VARCHAR(100) | | Selected TTS voice |
| `llm_model` | VARCHAR(100) | | LLM model override |
| `max_call_duration` | INTEGER | DEFAULT 300 | Max seconds per call |
| `retry_attempts` | INTEGER | DEFAULT 3 | Failed call retry count |
| `priority` | INTEGER | DEFAULT 0 | Queue priority (higher = first) |
| `schedule_start` | TIME | | Daily calling window start |
| `schedule_end` | TIME | | Daily calling window end |
| `timezone` | VARCHAR(100) | DEFAULT 'UTC' | Campaign timezone |
| `total_leads` | INTEGER | DEFAULT 0 | Lead count cache |
| `called_leads` | INTEGER | DEFAULT 0 | Completed call count |
| `success_count` | INTEGER | DEFAULT 0 | Successful outcomes |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |
| `started_at` | TIMESTAMPTZ | | First call timestamp |
| `completed_at` | TIMESTAMPTZ | | Campaign completion |

#### `leads` - Contact Records

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Lead identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `campaign_id` | UUID | FK campaigns(id) | Associated campaign |
| `phone_number` | VARCHAR(50) | NOT NULL | Primary contact number |
| `email` | VARCHAR(255) | | Email address |
| `first_name` | VARCHAR(100) | | First name |
| `last_name` | VARCHAR(100) | | Last name |
| `company` | VARCHAR(255) | | Company/organization |
| `title` | VARCHAR(100) | | Job title |
| `status` | VARCHAR(50) | DEFAULT 'pending' | pending, called, success, failed |
| `call_attempts` | INTEGER | DEFAULT 0 | Number of call attempts |
| `last_called_at` | TIMESTAMPTZ | | Last call attempt time |
| `next_call_at` | TIMESTAMPTZ | | Scheduled retry time |
| `outcome` | VARCHAR(50) | | Call result code |
| `notes` | TEXT | | Agent notes |
| `custom_fields` | JSONB | DEFAULT '{}' | Additional data |
| `crm_contact_id` | VARCHAR(255) | | External CRM reference |
| `timezone` | VARCHAR(100) | | Lead's timezone |
| `do_not_call` | BOOLEAN | DEFAULT false | DNC flag |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Import timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `calls` - Call History

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Call identifier |
| `external_uuid` | VARCHAR(255) | UNIQUE | Telephony provider ID |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `campaign_id` | UUID | FK campaigns(id) | Associated campaign |
| `lead_id` | UUID | FK leads(id) | Called lead |
| `phone_number` | VARCHAR(50) | NOT NULL | Dialed number |
| `direction` | VARCHAR(20) | DEFAULT 'outbound' | inbound, outbound |
| `status` | VARCHAR(50) | DEFAULT 'pending' | pending, ringing, in_progress, completed, failed |
| `outcome` | VARCHAR(50) | | success, declined, no_answer, voicemail, busy |
| `started_at` | TIMESTAMPTZ | | Call connect time |
| `ended_at` | TIMESTAMPTZ | | Call end time |
| `duration_seconds` | INTEGER | DEFAULT 0 | Call duration |
| `cost_cents` | INTEGER | DEFAULT 0 | Call cost in cents |
| `recording_url` | TEXT | | Signed recording URL |
| `transcript_preview` | TEXT | | First 500 chars of transcript |
| `sentiment` | VARCHAR(50) | | positive, neutral, negative |
| `detected_intents` | JSONB | DEFAULT '[]' | Post-call analysis results |
| `action_plan_id` | UUID | FK action_plans(id) | Triggered workflow |
| `action_results` | JSONB | DEFAULT '{}' | Workflow execution results |
| `pending_recommendations` | TEXT | | User-facing suggestions |
| `crm_call_id` | VARCHAR(255) | | HubSpot call engagement ID |
| `crm_note_id` | VARCHAR(255) | | HubSpot note ID |
| `crm_synced_at` | TIMESTAMPTZ | | CRM sync timestamp |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record creation |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `conversations` - Real-time State

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Conversation identifier |
| `call_id` | UUID | FK calls(id), UNIQUE | Associated call |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `state` | VARCHAR(50) | DEFAULT 'GREETING' | Current conversation state |
| `turn_count` | INTEGER | DEFAULT 0 | Number of exchanges |
| `history` | JSONB | DEFAULT '[]' | Full message history |
| `context` | JSONB | DEFAULT '{}' | Extracted entities/slots |
| `last_user_input` | TEXT | | Most recent user speech |
| `last_ai_response` | TEXT | | Most recent AI response |
| `outcome` | VARCHAR(50) | | Final conversation outcome |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Session start |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last activity |

#### `recordings` - Audio Storage

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Recording identifier |
| `call_id` | UUID | FK calls(id), UNIQUE | Associated call |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `storage_path` | TEXT | NOT NULL | Supabase Storage path |
| `file_size_bytes` | INTEGER | | Audio file size |
| `duration_seconds` | INTEGER | | Audio duration |
| `format` | VARCHAR(20) | DEFAULT 'wav' | Audio format |
| `sample_rate` | INTEGER | DEFAULT 16000 | Audio sample rate |
| `signed_url` | TEXT | | Temporary access URL |
| `signed_url_expires_at` | TIMESTAMPTZ | | URL expiration |
| `drive_file_id` | VARCHAR(255) | | Google Drive file ID |
| `drive_web_link` | TEXT | | Shareable Drive link |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Upload timestamp |

#### `transcripts` - Conversation Text

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Transcript identifier |
| `call_id` | UUID | FK calls(id), UNIQUE | Associated call |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `full_text` | TEXT | | Complete transcript |
| `turns` | JSONB | DEFAULT '[]' | Speaker-labeled turns |
| `word_count` | INTEGER | | Total word count |
| `language` | VARCHAR(10) | DEFAULT 'en' | Detected language |
| `confidence` | FLOAT | | Average STT confidence |
| `drive_file_id` | VARCHAR(255) | | Google Drive file ID |
| `drive_web_link` | TEXT | | Shareable Drive link |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Processing timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `connector_accounts` - OAuth Tokens

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Account identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `connector_type` | VARCHAR(50) | NOT NULL | google_calendar, gmail, hubspot, etc. |
| `provider_account_id` | VARCHAR(255) | | Provider's user ID |
| `email` | VARCHAR(255) | | Connected account email |
| `access_token_encrypted` | TEXT | NOT NULL | Fernet-encrypted access token |
| `refresh_token_encrypted` | TEXT | | Fernet-encrypted refresh token |
| `token_expires_at` | TIMESTAMPTZ | | Access token expiration |
| `scopes` | TEXT[] | | Granted OAuth scopes |
| `status` | VARCHAR(50) | DEFAULT 'active' | active, expired, revoked |
| `token_last_rotated_at` | TIMESTAMPTZ | | Last refresh timestamp |
| `rotation_count` | INTEGER | DEFAULT 0 | Total refresh count |
| `revoked_at` | TIMESTAMPTZ | | Revocation timestamp |
| `revoked_reason` | TEXT | | user_requested, security, expired |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Connection timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `meetings` - Calendar Events

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Meeting identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `lead_id` | UUID | FK leads(id) | Associated lead |
| `call_id` | UUID | FK calls(id) | Originating call |
| `connector_account_id` | UUID | FK connector_accounts(id) | Calendar connection |
| `external_event_id` | VARCHAR(255) | | Google/Outlook event ID |
| `title` | VARCHAR(255) | NOT NULL | Meeting title |
| `description` | TEXT | | Meeting description |
| `start_time` | TIMESTAMPTZ | NOT NULL | Start timestamp |
| `end_time` | TIMESTAMPTZ | NOT NULL | End timestamp |
| `timezone` | VARCHAR(100) | DEFAULT 'UTC' | Event timezone |
| `location` | TEXT | | Physical location |
| `video_link` | TEXT | | Google Meet/Teams URL |
| `attendees` | JSONB | DEFAULT '[]' | Attendee emails |
| `status` | VARCHAR(50) | DEFAULT 'confirmed' | confirmed, cancelled, tentative |
| `reminder_sent` | BOOLEAN | DEFAULT false | Reminder delivery flag |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Booking timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `reminders` - Scheduled Notifications

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Reminder identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `meeting_id` | UUID | FK meetings(id) | Associated meeting |
| `lead_id` | UUID | FK leads(id) | Recipient lead |
| `reminder_type` | VARCHAR(50) | NOT NULL | 24h, 1h, 10m |
| `scheduled_at` | TIMESTAMPTZ | NOT NULL | Send time |
| `status` | VARCHAR(50) | DEFAULT 'pending' | pending, sent, failed, cancelled |
| `channel` | VARCHAR(20) | | sms, email |
| `external_message_id` | VARCHAR(255) | | Provider message ID |
| `retry_count` | INTEGER | DEFAULT 0 | Delivery attempts |
| `max_retries` | INTEGER | DEFAULT 3 | Max retry limit |
| `next_retry_at` | TIMESTAMPTZ | | Scheduled retry time |
| `last_error` | TEXT | | Error from last attempt |
| `idempotency_key` | VARCHAR(255) | UNIQUE | Duplicate prevention |
| `sent_at` | TIMESTAMPTZ | | Delivery timestamp |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `action_plans` - Multi-Step Workflows

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Plan identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `conversation_id` | UUID | FK assistant_conversations(id) | Triggering chat |
| `user_id` | UUID | FK user_profiles(id) | Requesting user |
| `intent` | TEXT | NOT NULL | Human-readable goal |
| `context` | JSONB | DEFAULT '{}' | Execution context |
| `actions` | JSONB | NOT NULL | Ordered action steps |
| `status` | VARCHAR(50) | DEFAULT 'pending' | pending, running, completed, failed |
| `current_step` | INTEGER | DEFAULT 0 | Execution progress |
| `step_results` | JSONB | DEFAULT '[]' | Per-step outcomes |
| `error` | TEXT | | Failure reason |
| `started_at` | TIMESTAMPTZ | | Execution start |
| `completed_at` | TIMESTAMPTZ | | Execution end |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `tenant_quotas` - Rate Limits

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Quota identifier |
| `tenant_id` | UUID | FK tenants(id), UNIQUE | Owning tenant |
| `emails_per_day` | INTEGER | DEFAULT 50 | Daily email limit |
| `sms_per_day` | INTEGER | DEFAULT 25 | Daily SMS limit |
| `calls_per_day` | INTEGER | DEFAULT 50 | Daily call limit |
| `meetings_per_day` | INTEGER | DEFAULT 10 | Daily meeting limit |
| `max_concurrent_connectors` | INTEGER | DEFAULT 5 | Connector limit |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last modification |

#### `assistant_actions` - Audit Log

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Action identifier |
| `tenant_id` | UUID | FK tenants(id), NOT NULL | Owning tenant |
| `user_id` | UUID | FK user_profiles(id) | Triggering user |
| `conversation_id` | UUID | FK assistant_conversations(id) | Chat context |
| `action_type` | VARCHAR(100) | NOT NULL | Tool name executed |
| `triggered_by` | VARCHAR(50) | | chat, voice, api, system |
| `input_data` | JSONB | DEFAULT '{}' | Sanitized input params |
| `output_data` | JSONB | DEFAULT '{}' | Action results |
| `outcome_status` | VARCHAR(50) | | success, failed, quota_exceeded |
| `error` | TEXT | | Error message if failed |
| `duration_ms` | INTEGER | | Execution time |
| `ip_address` | INET | | Client IP address |
| `user_agent` | TEXT | | Client user agent |
| `request_id` | UUID | | Correlation ID |
| `idempotency_key` | VARCHAR(255) | | Replay prevention |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Execution timestamp |

---

## Row-Level Security (RLS)

All tables enforce tenant isolation through PostgreSQL RLS policies:

```sql
-- Example RLS policy pattern applied to all tables
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;

-- SELECT policy: Users can only see their tenant's data
CREATE POLICY "Tenant isolation for select" ON campaigns
    FOR SELECT
    USING (
        tenant_id = (
            SELECT tenant_id 
            FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

-- INSERT policy: Users can only create in their tenant
CREATE POLICY "Tenant isolation for insert" ON campaigns
    FOR INSERT
    WITH CHECK (
        tenant_id = (
            SELECT tenant_id 
            FROM user_profiles 
            WHERE id = auth.uid()
        )
    );

-- Service role bypass for backend operations
CREATE POLICY "Service role bypass" ON campaigns
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
```

---

## Voice Pipeline Deep Dive

### Audio Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           REAL-TIME VOICE PIPELINE                               │
└─────────────────────────────────────────────────────────────────────────────────┘

User speaks into phone
        │
        ▼
┌───────────────────┐
│  Telephony (SIP)  │  Raw audio from carrier
│  16kHz, PCM 16-bit│
└────────┬──────────┘
         │
         ▼ WebSocket stream
┌───────────────────────────────────────────────────────────────────────────────┐
│                              STT PROCESSING                                    │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                        Deepgram Flux                                     │  │
│  │  • Real-time streaming transcription                                     │  │
│  │  • Intelligent EndOfTurn detection via TurnInfo events                   │  │
│  │  • StartOfTurn for barge-in detection                                    │  │
│  │  • Partial transcripts replaced by finals                                │  │
│  │  • Latency: 200-300ms from end of speech                                 │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────┬──────────────────────────────────────────┘
                                     │
                                     ▼ Finalized transcript text
┌───────────────────────────────────────────────────────────────────────────────┐
│                              LLM PROCESSING                                    │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                        Groq (Llama 3.3 70B)                              │  │
│  │                                                                          │  │
│  │  System Prompt:                                                          │  │
│  │  ┌────────────────────────────────────────────────────────────────────┐ │  │
│  │  │ You are an AI sales agent for {company}. Your goal is to           │ │  │
│  │  │ {campaign_objective}. Be conversational, empathetic, and concise.  │ │  │
│  │  │ Current state: {conversation_state}                                 │ │  │
│  │  │ Previous context: {extracted_entities}                              │ │  │
│  │  └────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                          │  │
│  │  Parameters:                                                             │  │
│  │  • temperature: 0.3 (consistent responses)                               │  │
│  │  • max_tokens: 150 (keep responses brief)                                │  │
│  │  • stop sequences: ["User:", "Human:"]                                   │  │
│  │                                                                          │  │
│  │  Latency: 300-500ms                                                      │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────┬──────────────────────────────────────────┘
                                     │
                                     ▼ Generated response text
┌───────────────────────────────────────────────────────────────────────────────┐
│                              TTS PROCESSING                                    │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                    Google Cloud TTS (Chirp 3 HD)                         │  │
│  │                                                                          │  │
│  │  Available Voices:                                                       │  │
│  │  ┌──────────────┬────────────────┬────────────────────────────────────┐ │  │
│  │  │ Voice ID     │ Gender         │ Description                        │ │  │
│  │  ├──────────────┼────────────────┼────────────────────────────────────┤ │  │
│  │  │ Orus         │ Male           │ Warm, professional (default)       │ │  │
│  │  │ Zephyr       │ Male           │ Energetic, friendly                │ │  │
│  │  │ Charon       │ Male           │ Deep, authoritative                │ │  │
│  │  │ Puck         │ Male           │ Youthful, casual                   │ │  │
│  │  │ Aoede        │ Female         │ Clear, professional                │ │  │
│  │  │ Kore         │ Female         │ Warm, empathetic                   │ │  │
│  │  │ Fenrir       │ Male           │ Confident, bold                    │ │  │
│  │  │ Leda         │ Female         │ Friendly, approachable             │ │  │
│  │  └──────────────┴────────────────┴────────────────────────────────────┘ │  │
│  │                                                                          │  │
│  │  Audio Format: LINEAR16, 24kHz → resampled to 16kHz for telephony        │  │
│  │  Streaming: Chunked delivery for low first-byte latency                  │  │
│  │  Latency: ~200ms to first audio                                          │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────┬──────────────────────────────────────────┘
                                     │
                                     ▼ PCM audio stream
┌───────────────────┐
│  Telephony (SIP)  │  Audio played to caller
│  16kHz, PCM 16-bit│
└───────────────────┘
```

### Barge-In Implementation

When a user starts speaking while AI audio is playing:

```python
# Deepgram Flux sends StartOfTurn event
async def handle_deepgram_event(event: dict):
    if event.get("type") == "TurnInfo":
        turn_info = event.get("turn_info", {})
        
        if turn_info.get("event") == "StartOfTurn":
            # User started speaking - interrupt TTS
            barge_in_signal.set()
            
            # Send interrupt signal to frontend
            await websocket.send_json({
                "type": "barge_in",
                "message": "User interrupted"
            })
            
            # Stop current TTS playback
            tts_cancel_event.set()
            
        elif turn_info.get("event") == "EndOfTurn":
            # User finished speaking - process transcript
            await process_final_transcript(transcript)
```

### Conversation State Machine

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        CONVERSATION STATE MACHINE                             │
└──────────────────────────────────────────────────────────────────────────────┘

                              ┌─────────────┐
                              │  GREETING   │
                              │ "Hello, I'm │
                              │  calling... │
                              └──────┬──────┘
                                     │
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
              ┌────────────┐  ┌────────────┐  ┌────────────┐
              │ DECLINED   │  │QUALIFICATION│  │ CALLBACK  │
              │ "No thanks"│  │ "Tell me   │  │ "Call me  │
              │            │  │  more..."  │  │  later"   │
              └──────┬─────┘  └──────┬─────┘  └──────┬────┘
                     │               │               │
                     ▼               ▼               ▼
              ┌────────────┐  ┌────────────┐  ┌────────────┐
              │  GOODBYE   │  │ OBJECTION  │  │ SCHEDULE  │
              │ "Thank you │  │ HANDLING   │  │ REMINDER  │
              │  goodbye"  │  │ "But what  │  │           │
              └────────────┘  │  about..." │  └────────────┘
                              └──────┬─────┘
                                     │
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
              ┌────────────┐  ┌────────────┐  ┌────────────┐
              │  CLOSING   │  │ TRANSFER   │  │ DECLINED  │
              │ "Great,    │  │ TO HUMAN   │  │           │
              │  let's..." │  │            │  │           │
              └──────┬─────┘  └────────────┘  └────────────┘
                     │
                     ▼
              ┌────────────┐
              │  SUCCESS   │
              │ Outcome:   │
              │ meeting    │
              │ booked     │
              └────────────┘
```

---

## API Documentation

### Authentication Endpoints

#### POST `/auth/register`
Register a new user and send OTP verification email.

**Request:**
```json
{
  "email": "user@example.com",
  "name": "John Doe",
  "business_name": "Acme Corp"
}
```

**Response (201):**
```json
{
  "message": "Verification code sent to user@example.com",
  "email": "user@example.com"
}
```

#### POST `/auth/verify-otp`
Verify OTP code and receive JWT tokens.

**Request:**
```json
{
  "email": "user@example.com",
  "token": "123456"
}
```

**Response (200):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "v1.MjAyNi0wMS0yMVQxNTozMDowMFo...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "name": "John Doe",
    "role": "admin",
    "tenant_id": "660e8400-e29b-41d4-a716-446655440001",
    "minutes_remaining": 100
  }
}
```

#### GET `/auth/me`
Get current authenticated user profile.

**Headers:**
```
Authorization: Bearer {access_token}
```

**Response (200):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "name": "John Doe",
  "business_name": "Acme Corp",
  "role": "admin",
  "tenant_id": "660e8400-e29b-41d4-a716-446655440001",
  "minutes_remaining": 87,
  "avatar_url": null,
  "created_at": "2026-01-15T10:30:00Z"
}
```

### Campaign Endpoints

#### GET `/campaigns`
List campaigns with pagination.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | integer | 1 | Page number |
| `limit` | integer | 20 | Items per page |
| `status` | string | null | Filter by status |

**Response (200):**
```json
{
  "campaigns": [
    {
      "id": "770e8400-e29b-41d4-a716-446655440002",
      "name": "Q1 Outreach",
      "status": "active",
      "total_leads": 500,
      "called_leads": 127,
      "success_count": 34,
      "success_rate": 26.77,
      "created_at": "2026-01-10T09:00:00Z",
      "started_at": "2026-01-12T14:00:00Z"
    }
  ],
  "total": 5,
  "page": 1,
  "limit": 20
}
```

#### POST `/campaigns/{id}/leads/import`
Bulk import leads from CSV file.

**Request (multipart/form-data):**
```
file: contacts.csv
skip_duplicates: true
```

**CSV Format:**
```csv
phone_number,email,first_name,last_name,company
+14155551234,john@example.com,John,Doe,Acme Corp
+14155555678,jane@example.com,Jane,Smith,Tech Inc
```

**Response (200):**
```json
{
  "total_rows": 150,
  "imported": 142,
  "duplicates_skipped": 5,
  "failed": 3,
  "errors": [
    {"row": 45, "phone": "invalid", "error": "Invalid phone number format"},
    {"row": 89, "phone": "+1415", "error": "Phone number too short"},
    {"row": 134, "error": "Missing required field: phone_number"}
  ],
  "message": "Successfully imported 142 of 150 contacts"
}
```

### Analytics Endpoints

#### GET `/analytics/series`
Get call analytics time series data.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from` | date | 30 days ago | Start date (YYYY-MM-DD) |
| `to` | date | today | End date (YYYY-MM-DD) |
| `group_by` | string | day | Aggregation: day, week, month |

**Response (200):**
```json
{
  "series": [
    {
      "date": "2026-01-15",
      "total_calls": 45,
      "answered": 32,
      "voicemail": 8,
      "failed": 5,
      "avg_duration_seconds": 127,
      "success_count": 12
    },
    {
      "date": "2026-01-16",
      "total_calls": 52,
      "answered": 41,
      "voicemail": 6,
      "failed": 5,
      "avg_duration_seconds": 143,
      "success_count": 18
    }
  ],
  "summary": {
    "total_calls": 97,
    "total_answered": 73,
    "answer_rate": 75.26,
    "total_success": 30,
    "conversion_rate": 41.10
  }
}
```

### AI Options Endpoints

#### GET `/ai-options/tts/voices`
List available TTS voices.

**Response (200):**
```json
{
  "voices": [
    {
      "id": "en-US-Chirp3-HD-Orus",
      "name": "Orus",
      "provider": "google",
      "language": "en-US",
      "gender": "male",
      "description": "Warm, professional male voice",
      "is_default": true,
      "preview_url": "/api/v1/ai-options/tts/preview?voice_id=en-US-Chirp3-HD-Orus"
    },
    {
      "id": "en-US-Chirp3-HD-Aoede",
      "name": "Aoede",
      "provider": "google",
      "language": "en-US",
      "gender": "female",
      "description": "Clear, professional female voice",
      "is_default": false
    }
  ]
}
```

#### POST `/ai-options/benchmark`
Run latency benchmark for AI pipeline.

**Response (200):**
```json
{
  "stt_latency_ms": 245,
  "llm_latency_ms": 412,
  "tts_latency_ms": 187,
  "total_pipeline_ms": 844,
  "timestamp": "2026-01-21T15:30:00Z",
  "test_input": "Hello, how can I help you today?",
  "test_output": "I'd be happy to help! What questions do you have?"
}
```

### Meeting Endpoints

#### GET `/meetings/availability`
Check calendar availability.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date` | date | Yes | Date to check (YYYY-MM-DD) |
| `duration_minutes` | integer | No | Meeting duration (default: 30) |
| `timezone` | string | No | Timezone (default: UTC) |

**Response (200):**
```json
{
  "date": "2026-01-22",
  "timezone": "America/New_York",
  "available_slots": [
    {"start": "09:00", "end": "09:30"},
    {"start": "10:00", "end": "10:30"},
    {"start": "14:00", "end": "14:30"},
    {"start": "15:30", "end": "16:00"}
  ],
  "busy_periods": [
    {"start": "09:30", "end": "10:00", "title": "Team Standup"},
    {"start": "12:00", "end": "13:00", "title": "Lunch"}
  ]
}
```

#### POST `/meetings`
Book a new meeting.

**Request:**
```json
{
  "title": "Product Demo Call",
  "description": "30-minute product demonstration",
  "start_time": "2026-01-22T14:00:00Z",
  "end_time": "2026-01-22T14:30:00Z",
  "timezone": "America/New_York",
  "attendees": ["prospect@example.com"],
  "lead_id": "880e8400-e29b-41d4-a716-446655440003",
  "include_video_link": true
}
```

**Response (201):**
```json
{
  "id": "990e8400-e29b-41d4-a716-446655440004",
  "external_event_id": "abc123xyz",
  "title": "Product Demo Call",
  "start_time": "2026-01-22T14:00:00Z",
  "end_time": "2026-01-22T14:30:00Z",
  "video_link": "https://meet.google.com/abc-defg-hij",
  "attendees": ["prospect@example.com"],
  "status": "confirmed",
  "reminders_scheduled": ["24h", "1h", "10m"]
}
```

### WebSocket: Voice Pipeline

#### Connect: `/ws/voice/{call_uuid}`
Real-time bidirectional audio streaming for voice calls.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tenant_id` | UUID | Yes | Tenant identifier |
| `campaign_id` | UUID | No | Campaign context |
| `lead_id` | UUID | No | Lead context |
| `phone_number` | string | Yes | Caller phone number |

**Client -> Server Messages:**
```json
// Audio chunk (binary)
{
  "type": "audio",
  "data": "<base64 encoded PCM 16kHz mono>"
}

// Control message
{
  "type": "end_call"
}
```

**Server -> Client Messages:**
```json
// Transcript update
{
  "type": "transcript",
  "is_final": false,
  "text": "Hello, I'm interested in...",
  "confidence": 0.94
}

// AI response audio
{
  "type": "audio",
  "data": "<base64 encoded PCM 16kHz mono>"
}

// Barge-in detected
{
  "type": "barge_in",
  "message": "User interrupted"
}

// TTS interrupted
{
  "type": "tts_interrupted"
}

// Call ended
{
  "type": "call_ended",
  "duration_seconds": 127,
  "outcome": "success"
}
```

### WebSocket: Assistant Chat

#### Connect: `/assistant/chat`
Real-time AI assistant chat interface.

**Client -> Server Messages:**
```json
{
  "type": "message",
  "content": "Show me today's call statistics"
}
```

**Server -> Client Messages:**
```json
// Thinking indicator
{
  "type": "thinking",
  "message": "Analyzing call data..."
}

// Tool execution
{
  "type": "tool_call",
  "tool": "get_dashboard_stats",
  "status": "executing"
}

// Streaming response
{
  "type": "response_chunk",
  "content": "Here are today's statistics:\n\n"
}

// Final response
{
  "type": "response_complete",
  "content": "Here are today's statistics:\n\n- Total Calls: 45\n- Answered: 32 (71%)\n- Success Rate: 28%\n\nWould you like me to break this down by campaign?"
}

// Action confirmation required
{
  "type": "action_confirmation",
  "action": "send_email",
  "parameters": {
    "to": ["prospect@example.com"],
    "subject": "Follow-up from our call"
  },
  "message": "I'm ready to send this email. Should I proceed?"
}
```

---

## Environment Configuration

### Complete `.env` Template

```bash
# =============================================================================
# TALKY.AI ENVIRONMENT CONFIGURATION
# =============================================================================

# -----------------------------------------------------------------------------
# Application Settings
# -----------------------------------------------------------------------------
ENVIRONMENT=development                    # development, staging, production
API_BASE_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
DEBUG=true

# -----------------------------------------------------------------------------
# Supabase (Database & Auth)
# -----------------------------------------------------------------------------
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_JWT_SECRET=your-jwt-secret-from-supabase-dashboard

# -----------------------------------------------------------------------------
# Redis (Queue & Cache)
# -----------------------------------------------------------------------------
REDIS_URL=redis://localhost:6379
REDIS_PASSWORD=                            # Optional, for production

# -----------------------------------------------------------------------------
# AI Providers
# -----------------------------------------------------------------------------
# Deepgram (Speech-to-Text)
DEEPGRAM_API_KEY=your-deepgram-api-key

# Groq (LLM)
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=llama-3.3-70b-versatile

# Google Cloud TTS
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# -----------------------------------------------------------------------------
# Telephony (Vonage)
# -----------------------------------------------------------------------------
VONAGE_API_KEY=your-vonage-api-key
VONAGE_API_SECRET=your-vonage-api-secret
VONAGE_APP_ID=your-vonage-app-id
VONAGE_PRIVATE_KEY_PATH=/path/to/private.key
VONAGE_FROM_NUMBER=+14155551234            # SMS-capable number

# -----------------------------------------------------------------------------
# Stripe (Billing)
# -----------------------------------------------------------------------------
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_MOCK_MODE=false                     # true for development

# -----------------------------------------------------------------------------
# OAuth Connectors
# -----------------------------------------------------------------------------
# Encryption key for storing OAuth tokens (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
CONNECTOR_ENCRYPTION_KEY=your-fernet-encryption-key

# For key rotation (comma-separated old keys)
CONNECTOR_ENCRYPTION_KEYS_OLD=

# Google OAuth (Calendar, Gmail, Drive)
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Microsoft OAuth (Outlook, Teams)
MICROSOFT_CLIENT_ID=your-microsoft-client-id
MICROSOFT_CLIENT_SECRET=your-microsoft-client-secret

# HubSpot OAuth (CRM)
HUBSPOT_CLIENT_ID=your-hubspot-client-id
HUBSPOT_CLIENT_SECRET=your-hubspot-client-secret

# -----------------------------------------------------------------------------
# SMTP Fallback (Optional)
# -----------------------------------------------------------------------------
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=noreply@yourdomain.com
SMTP_FROM_NAME=Talky AI
SMTP_USE_TLS=true
```

---

## Deployment Guide

### Backend Deployment

```bash
# Clone repository
git clone https://github.com/your-org/talky-ai.git
cd talky-ai/backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: .\venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Run database migrations
psql $DATABASE_URL -f database/schema.sql
psql $DATABASE_URL -f database/migrations/add_stripe_billing.sql
psql $DATABASE_URL -f database/migrations/add_assistant_agent.sql
psql $DATABASE_URL -f database/migrations/add_security_features.sql

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Frontend Deployment

```bash
cd talky-ai/frontend

# Install dependencies
npm install

# Build for production
npm run build

# Start production server
npm start
```

### Background Workers

```bash
# Reminder worker (SMS/email notifications)
python -m app.workers.reminder_worker &

# Dialer worker (call queue processing)
python -m app.workers.dialer_worker &

# Token rotation (OAuth refresh)
python -m app.workers.token_rotation_worker &
```

---

## Testing

### Run All Tests

```bash
cd backend

# Unit tests
python -m pytest tests/unit/ -v

# Integration tests
python -m pytest tests/integration/ -v

# Full test suite with coverage
python -m pytest tests/ -v --cov=app --cov-report=html
```

### Test Categories

| Test File | Coverage |
|-----------|----------|
| `test_conversation_engine.py` | State machine transitions |
| `test_sms_service.py` | SMS template rendering |
| `test_assistant_agent_service.py` | Multi-step workflows |
| `test_post_call_analyzer.py` | Intent detection patterns |
| `test_quota_service.py` | Rate limiting logic |
| `test_audit_service.py` | Audit log creation |
| `test_replay_protection_service.py` | Idempotency validation |
| `test_crm_sync_service.py` | HubSpot integration |
| `test_drive_sync_service.py` | Google Drive uploads |

---



### Debug Logging

```python
# Enable debug logging in .env
DEBUG=true
LOG_LEVEL=DEBUG

# View real-time logs
tail -f logs/app.log

# Filter by component
grep "VoicePipeline" logs/app.log
grep "AssistantAgent" logs/app.log
```

---

*Last Updated: January 21, 2026*
*Version: 1.0.0*
