-- =============================================================================
-- Talky.ai - Complete Unified Database Schema (PostgreSQL - No Supabase)
-- =============================================================================
--
-- This is the final consolidated schema for LOCAL PostgreSQL (managed via pgAdmin4).
-- All Supabase-specific SQL has been removed:
--   - No auth.users reference (user_profiles uses its own UUID primary key)
--   - No auth.uid() or auth.role() in RLS (RLS disabled; app enforces tenant isolation)
--   - No Supabase extensions (uuid-ossp kept as it's standard PostgreSQL)
--
-- Auth: Local JWT (PyJWT + bcrypt) via app/api/v1/endpoints/auth.py
--
-- To apply:
--   psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai -f complete_schema.sql
--
-- Generated: February 2026
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- SECTION 1: CORE TABLES
-- =============================================================================

-- 1.1 PLANS TABLE
CREATE TABLE IF NOT EXISTS plans (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    description TEXT,
    minutes INTEGER NOT NULL,
    agents INTEGER NOT NULL DEFAULT 1,
    concurrent_calls INTEGER NOT NULL DEFAULT 1,
    features JSONB DEFAULT '[]',
    not_included JSONB DEFAULT '[]',
    popular BOOLEAN DEFAULT false,
    stripe_price_id VARCHAR(100),
    stripe_product_id VARCHAR(100),
    billing_period VARCHAR(20) DEFAULT 'monthly',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 1.2 TENANTS TABLE
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_name VARCHAR(255) NOT NULL,
    plan_id VARCHAR(50) REFERENCES plans(id),
    minutes_allocated INTEGER NOT NULL DEFAULT 0,
    minutes_used INTEGER NOT NULL DEFAULT 0,
    calling_rules JSONB DEFAULT '{
        "time_window_start": "09:00",
        "time_window_end": "19:00",
        "timezone": "America/New_York",
        "allowed_days": [0, 1, 2, 3, 4],
        "max_concurrent_calls": 10,
        "retry_delay_seconds": 7200,
        "max_retry_attempts": 3,
        "enable_priority_override": true,
        "high_priority_threshold": 8,
        "skip_dnc": true,
        "min_hours_between_calls": 2
    }',
    stripe_customer_id VARCHAR(100),
    stripe_subscription_id VARCHAR(100),
    subscription_status VARCHAR(50) DEFAULT 'inactive',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_plan_id ON tenants(plan_id);
CREATE INDEX IF NOT EXISTS idx_tenants_subscription_status ON tenants(subscription_status);

-- 1.3 USER_PROFILES
-- NOTE: No longer references auth.users. Uses its own UUID primary key.
--       password_hash stores bcrypt hash for local auth.
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255),
    tenant_id UUID REFERENCES tenants(id),
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    password_hash TEXT,                          -- bcrypt hash (nullable for OAuth future)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant_id ON user_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);

-- =============================================================================
-- SECTION 2: DIALER PIPELINE
-- =============================================================================

-- 2.1 CAMPAIGNS
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    system_prompt TEXT NOT NULL DEFAULT '',
    voice_id VARCHAR(100) NOT NULL DEFAULT 'default',
    max_concurrent_calls INTEGER DEFAULT 10,
    retry_failed BOOLEAN DEFAULT true,
    max_retries INTEGER DEFAULT 3,
    goal TEXT,
    script_config JSONB DEFAULT '{}',
    calling_config JSONB DEFAULT '{
        "caller_id": null,
        "priority_override": null,
        "retry_on_no_answer": true,
        "retry_on_busy": true
    }',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    total_leads INTEGER DEFAULT 0,
    calls_completed INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_id ON campaigns(tenant_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

-- 2.2 LEADS
CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    custom_fields JSONB DEFAULT '{}',
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    is_high_value BOOLEAN DEFAULT false,
    tags TEXT[] DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'pending',
    last_call_result VARCHAR(50) DEFAULT 'pending',
    call_attempts INTEGER DEFAULT 0,
    last_called_at TIMESTAMPTZ,
    crm_contact_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_tenant_id ON leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_campaign_id ON leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone_number ON leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_crm_contact_id ON leads(crm_contact_id) WHERE crm_contact_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_campaign_phone_unique ON leads(campaign_id, phone_number) WHERE status != 'deleted';

-- 2.3 CALLS
CREATE TABLE IF NOT EXISTS calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    external_call_uuid VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'initiated',
    outcome VARCHAR(100),
    goal_achieved BOOLEAN DEFAULT false,
    started_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    recording_url TEXT,
    transcript TEXT,
    transcript_json JSONB,
    summary TEXT,
    cost DECIMAL(10, 4),
    talklee_call_id VARCHAR(20) UNIQUE,
    crm_call_id TEXT,
    crm_note_id TEXT,
    crm_synced_at TIMESTAMPTZ,
    detected_intents JSONB DEFAULT '[]',
    action_plan_id UUID,
    action_results JSONB DEFAULT '{}',
    pending_recommendations TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_tenant_id ON calls(tenant_id);
CREATE INDEX IF NOT EXISTS idx_calls_campaign_id ON calls(campaign_id);
CREATE INDEX IF NOT EXISTS idx_calls_lead_id ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at);
CREATE INDEX IF NOT EXISTS idx_calls_external_uuid ON calls(external_call_uuid);
CREATE INDEX IF NOT EXISTS idx_calls_talklee_id ON calls(talklee_call_id) WHERE talklee_call_id IS NOT NULL;

-- 2.4 CONVERSATIONS
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    messages JSONB DEFAULT '[]',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_tenant_id ON conversations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conversations_call_id ON conversations(call_id);

-- 2.5 RECORDINGS
CREATE TABLE IF NOT EXISTS recordings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    mime_type VARCHAR(50) DEFAULT 'audio/wav',
    status VARCHAR(50) DEFAULT 'pending',
    drive_file_id TEXT,
    drive_web_link TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recordings_tenant_id ON recordings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_recordings_call_id ON recordings(call_id);

-- 2.6 TRANSCRIPTS
CREATE TABLE IF NOT EXISTS transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    turns JSONB NOT NULL DEFAULT '[]',
    full_text TEXT,
    word_count INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    user_word_count INTEGER DEFAULT 0,
    assistant_word_count INTEGER DEFAULT 0,
    duration_seconds INTEGER,
    drive_file_id TEXT,
    drive_web_link TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transcripts_tenant_id ON transcripts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_call_id ON transcripts(call_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_full_text_search
ON transcripts USING gin(to_tsvector('english', COALESCE(full_text, '')));

-- =============================================================================
-- SECTION 3: VOICE CONTRACT & TRACING
-- =============================================================================

-- 3.1 CALL LEGS
CREATE TABLE IF NOT EXISTS call_legs (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id          UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    talklee_call_id  VARCHAR(20),
    leg_type         VARCHAR(30) NOT NULL,
    direction        VARCHAR(10) NOT NULL DEFAULT 'outbound',
    provider         VARCHAR(30) NOT NULL DEFAULT 'vonage',
    provider_leg_id  VARCHAR(100),
    from_number      VARCHAR(20),
    to_number        VARCHAR(20),
    status           VARCHAR(30) NOT NULL DEFAULT 'initiated',
    started_at       TIMESTAMPTZ,
    answered_at      TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    duration_seconds INTEGER,
    metadata         JSONB DEFAULT '{}',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_legs_call_id ON call_legs(call_id);

-- 3.2 CALL EVENTS (Append-only)
CREATE TABLE IF NOT EXISTS call_events (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id          UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    talklee_call_id  VARCHAR(20),
    leg_id           UUID REFERENCES call_legs(id) ON DELETE SET NULL,
    event_type       VARCHAR(30) NOT NULL,
    previous_state   VARCHAR(30),
    new_state        VARCHAR(30),
    event_data       JSONB DEFAULT '{}',
    source           VARCHAR(50) NOT NULL DEFAULT 'system',
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_events_call_id ON call_events(call_id);
CREATE INDEX IF NOT EXISTS idx_call_events_talklee_id ON call_events(talklee_call_id) WHERE talklee_call_id IS NOT NULL;

-- =============================================================================
-- SECTION 4: ASSISTANT & AGENT SYSTEM
-- =============================================================================

-- 4.1 CONNECTORS
CREATE TABLE IF NOT EXISTS connectors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    name VARCHAR(100),
    status VARCHAR(50) DEFAULT 'pending',
    config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connectors_tenant_id ON connectors(tenant_id);

-- 4.2 CONNECTOR ACCOUNTS
CREATE TABLE IF NOT EXISTS connector_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connector_id UUID NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_account_id VARCHAR(255),
    access_token_encrypted TEXT,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMPTZ,
    scopes TEXT[],
    account_email VARCHAR(255),
    status VARCHAR(50) DEFAULT 'active',
    last_refreshed_at TIMESTAMPTZ,
    token_last_rotated_at TIMESTAMPTZ,
    rotation_count INTEGER DEFAULT 0,
    revoked_at TIMESTAMPTZ,
    revoked_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4.3 ASSISTANT CONVERSATIONS
CREATE TABLE IF NOT EXISTS assistant_conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    title VARCHAR(255),
    messages JSONB DEFAULT '[]',
    context JSONB DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'active',
    message_count INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4.4 ASSISTANT ACTIONS (Audit Log)
CREATE TABLE IF NOT EXISTS assistant_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES assistant_conversations(id) ON DELETE SET NULL,
    user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    call_id UUID REFERENCES calls(id) ON DELETE SET NULL,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    connector_id UUID REFERENCES connectors(id) ON DELETE SET NULL,
    type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    input_data JSONB,
    output_data JSONB,
    error TEXT,
    triggered_by VARCHAR(50),
    scheduled_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER,
    ip_address INET,
    user_agent TEXT,
    request_id UUID,
    outcome_status VARCHAR(50),
    idempotency_key VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_actions_idempotency
ON assistant_actions(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- 4.5 ACTION PLANS
CREATE TABLE IF NOT EXISTS action_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID,
    user_id UUID REFERENCES user_profiles(id),
    intent TEXT NOT NULL,
    context JSONB DEFAULT '{}',
    actions JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(50) DEFAULT 'pending',
    current_step INTEGER DEFAULT 0,
    step_results JSONB DEFAULT '[]',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Forward reference fix: calls.action_plan_id → action_plans
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_calls_action_plan'
        AND table_name = 'calls'
    ) THEN
        ALTER TABLE calls ADD CONSTRAINT fk_calls_action_plan
            FOREIGN KEY (action_plan_id) REFERENCES action_plans(id);
    END IF;
END $$;

-- =============================================================================
-- SECTION 5: SCHEDULING & REMINDERS
-- =============================================================================

-- 5.1 MEETINGS
CREATE TABLE IF NOT EXISTS meetings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    call_id UUID REFERENCES calls(id) ON DELETE SET NULL,
    connector_id UUID REFERENCES connectors(id) ON DELETE SET NULL,
    action_id UUID REFERENCES assistant_actions(id) ON DELETE SET NULL,
    external_event_id VARCHAR(255),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    timezone VARCHAR(50) DEFAULT 'UTC',
    location TEXT,
    join_link TEXT,
    status VARCHAR(50) DEFAULT 'scheduled',
    attendees JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5.2 REMINDERS
CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    meeting_id UUID REFERENCES meetings(id) ON DELETE CASCADE,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    action_id UUID REFERENCES assistant_actions(id) ON DELETE SET NULL,
    type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    scheduled_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    content JSONB,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    idempotency_key VARCHAR(255),
    max_retries INTEGER DEFAULT 3,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    channel VARCHAR(20),
    external_message_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_idempotency_key
ON reminders(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- =============================================================================
-- SECTION 6: GOVERNANCE & SETTINGS
-- =============================================================================

-- 6.1 TENANT QUOTAS
CREATE TABLE IF NOT EXISTS tenant_quotas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    emails_per_day INTEGER DEFAULT 50,
    sms_per_day INTEGER DEFAULT 25,
    calls_per_day INTEGER DEFAULT 50,
    meetings_per_day INTEGER DEFAULT 10,
    max_concurrent_connectors INTEGER DEFAULT 5,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6.2 TENANT QUOTA USAGE
CREATE TABLE IF NOT EXISTS tenant_quota_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
    emails_sent INTEGER DEFAULT 0,
    sms_sent INTEGER DEFAULT 0,
    calls_initiated INTEGER DEFAULT 0,
    meetings_booked INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, usage_date)
);

-- 6.3 SUBSCRIPTIONS
CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_subscription_id VARCHAR(100) NOT NULL UNIQUE,
    stripe_customer_id VARCHAR(100) NOT NULL,
    plan_id VARCHAR(50) REFERENCES plans(id),
    status VARCHAR(50) NOT NULL DEFAULT 'incomplete',
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ,
    trial_start TIMESTAMPTZ,
    trial_end TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6.4 INVOICES
CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_invoice_id VARCHAR(100) NOT NULL UNIQUE,
    stripe_subscription_id VARCHAR(100),
    amount_due INTEGER NOT NULL,
    amount_paid INTEGER NOT NULL DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'usd',
    status VARCHAR(50) NOT NULL,
    invoice_pdf TEXT,
    hosted_invoice_url TEXT,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    due_date TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6.5 USAGE RECORDS
CREATE TABLE IF NOT EXISTS usage_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
    usage_type VARCHAR(50) NOT NULL DEFAULT 'minutes',
    quantity INTEGER NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    reported_to_stripe BOOLEAN DEFAULT false,
    stripe_usage_record_id VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6.6 TENANT SETTINGS
CREATE TABLE IF NOT EXISTS tenant_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    auto_actions_enabled BOOLEAN DEFAULT FALSE,
    drive_root_folder_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6.6.1 TENANT AI PROVIDER CONFIGS
CREATE TABLE IF NOT EXISTS tenant_ai_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    llm_provider VARCHAR(50) NOT NULL DEFAULT 'groq',
    llm_model TEXT NOT NULL DEFAULT 'llama-3.3-70b-versatile',
    llm_temperature DOUBLE PRECISION NOT NULL DEFAULT 0.6,
    llm_max_tokens INTEGER NOT NULL DEFAULT 150,
    stt_provider VARCHAR(50) NOT NULL DEFAULT 'deepgram',
    stt_model TEXT NOT NULL DEFAULT 'nova-3',
    stt_language VARCHAR(16) NOT NULL DEFAULT 'en',
    tts_provider VARCHAR(50) NOT NULL DEFAULT 'google',
    tts_model TEXT NOT NULL DEFAULT 'Chirp3-HD',
    tts_voice_id TEXT NOT NULL DEFAULT 'en-US-Chirp3-HD-Leda',
    tts_sample_rate INTEGER NOT NULL DEFAULT 24000,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_ai_configs_tenant_id ON tenant_ai_configs(tenant_id);

-- 6.7 CLIENTS
CREATE TABLE IF NOT EXISTS clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    phone VARCHAR(20),
    email VARCHAR(255),
    tags JSONB DEFAULT '[]',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clients_tenant_id ON clients(tenant_id);

-- 6.8 DIALER JOBS
CREATE TABLE IF NOT EXISTS dialer_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    call_id UUID REFERENCES calls(id),
    phone_number VARCHAR(20) NOT NULL,
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    status VARCHAR(50) DEFAULT 'pending',
    attempt_number INTEGER DEFAULT 1,
    scheduled_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_outcome VARCHAR(50),
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dialer_jobs_tenant_id ON dialer_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_status ON dialer_jobs(status);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_priority ON dialer_jobs(priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_dialer_jobs_queue ON dialer_jobs(tenant_id, status, priority DESC, scheduled_at);

-- =============================================================================
-- SECTION 7: FUNCTIONS & PROCEDURES
-- =============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Atomic Call Status Update (called from app via asyncpg)
CREATE OR REPLACE FUNCTION update_call_status(
    p_call_uuid UUID,
    p_outcome TEXT,
    p_duration INT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
AS $$
DECLARE
    v_call RECORD;
    v_lead_status TEXT;
BEGIN
    UPDATE calls SET
        status = 'completed',
        outcome = p_outcome,
        duration_seconds = COALESCE(p_duration, duration_seconds),
        ended_at = NOW(),
        updated_at = NOW()
    WHERE id = p_call_uuid
    RETURNING id, lead_id, action_plan_id, campaign_id
    INTO v_call;

    IF v_call.id IS NULL THEN
        RETURN json_build_object('found', false, 'call_id', p_call_uuid);
    END IF;

    CASE p_outcome
        WHEN 'answered' THEN v_lead_status := 'contacted';
        WHEN 'goal_achieved' THEN v_lead_status := 'completed';
        WHEN 'spam', 'invalid', 'unavailable', 'disconnected', 'rejected' THEN v_lead_status := 'dnc';
        ELSE v_lead_status := 'called';
    END CASE;

    IF v_call.lead_id IS NOT NULL THEN
        UPDATE leads SET
            status = v_lead_status,
            last_call_result = p_outcome,
            last_called_at = NOW(),
            call_attempts = COALESCE(call_attempts, 0) + 1,
            updated_at = NOW()
        WHERE id = v_call.lead_id;
    END IF;

    RETURN json_build_object(
        'found', true,
        'call_id', v_call.id,
        'lead_id', v_call.lead_id,
        'campaign_id', v_call.campaign_id,
        'outcome', p_outcome,
        'lead_status', v_lead_status
    );
END;
$$;

-- =============================================================================
-- SECTION 8: TRIGGERS
-- =============================================================================

DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_type = 'BASE TABLE'
        AND table_name NOT IN ('call_events', 'recordings', 'invoices', 'usage_records')
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS update_%I_updated_at ON %I', t, t);
        EXECUTE format(
            'CREATE TRIGGER update_%I_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()',
            t, t
        );
    END LOOP;
END $$;

-- =============================================================================
-- SECTION 9: DEFAULT DATA
-- =============================================================================

INSERT INTO plans (id, name, price, description, minutes, agents, concurrent_calls, features, not_included, popular) VALUES
('basic', 'Basic', 29, 'Perfect for startups.', 300, 1, 1,
 '["300 minutes/month", "1 AI agent", "Basic analytics", "Email support"]'::jsonb,
 '["API access", "Custom voices", "Priority support"]'::jsonb, false),
('professional', 'Professional', 79, 'Ideal for growing businesses.', 1500, 3, 3,
 '["1500 minutes/month", "3 AI agents", "Advanced analytics", "Priority support", "Custom voices"]'::jsonb,
 '["API access", "White-label"]'::jsonb, true),
('enterprise', 'Enterprise', 199, 'For large scale operations.', 5000, 10, 10,
 '["5000 minutes/month", "10 AI agents", "Full analytics", "24/7 support", "API access", "White-label"]'::jsonb,
 '[]'::jsonb, false)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- SUCCESS
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'Talky.ai PostgreSQL Schema Applied Successfully!';
    RAISE NOTICE 'No Supabase dependencies. Auth via local JWT + bcrypt.';
    RAISE NOTICE '==========================================================';
END $$;
