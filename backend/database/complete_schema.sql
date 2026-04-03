-- =============================================================================
-- Talky.ai - Complete Unified Database Schema (PostgreSQL - No Supabase)
-- =============================================================================
--
-- This is the final consolidated schema for LOCAL PostgreSQL (managed via pgAdmin4).
-- All Supabase-specific SQL has been removed:
--   - No auth.users reference (user_profiles uses its own UUID primary key)
--   - No auth.uid() or auth.role() in RLS (tenant policy tables use app.current_tenant_id context)
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
    
    -- Day 8: Suspension and White Label support
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'pending_deletion')),
    suspended_at TIMESTAMPTZ,
    suspended_by UUID REFERENCES user_profiles(id),
    suspension_reason TEXT,
    white_label_partner_id UUID, -- Will add foreign key after table creation
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_plan_id ON tenants(plan_id);
CREATE INDEX IF NOT EXISTS idx_tenants_subscription_status ON tenants(subscription_status);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_white_label_partner_id ON tenants(white_label_partner_id);

-- 1.3 USER_PROFILES
-- NOTE: No longer references auth.users. Uses its own UUID primary key.
--       password_hash stores bcrypt hash for local auth.
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255),
    tenant_id UUID REFERENCES tenants(id),
    role VARCHAR(50) NOT NULL DEFAULT 'user' CHECK (role IN ('platform_admin', 'partner_admin', 'tenant_admin', 'user', 'readonly')),
    password_hash TEXT,                          -- bcrypt hash (nullable for OAuth future)
    
    -- Security hardening columns
    account_locked_until TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    password_changed_at TIMESTAMPTZ,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    last_login_at TIMESTAMPTZ,
    mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,

    -- Day 3: Passkey denormalized count
    passkey_count INTEGER NOT NULL DEFAULT 0 CHECK (passkey_count >= 0),
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant_id ON user_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);
CREATE INDEX IF NOT EXISTS idx_user_profiles_has_passkey ON user_profiles(passkey_count) WHERE passkey_count > 0;
CREATE INDEX IF NOT EXISTS idx_user_profiles_is_active ON user_profiles (is_active) WHERE is_active = FALSE;
CREATE INDEX IF NOT EXISTS idx_user_profiles_mfa_enabled ON user_profiles (mfa_enabled) WHERE mfa_enabled = TRUE;

-- 1.4 SECURITY_SESSIONS
-- Stores server-side session records for instant revocation.
CREATE TABLE IF NOT EXISTS security_sessions (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    session_token_hash  TEXT        NOT NULL UNIQUE,
    ip_address          TEXT,
    user_agent          TEXT,
    
    -- Day 5: Session security enhancements
    device_fingerprint  TEXT,
    device_name         TEXT,
    device_type         TEXT        CHECK (device_type IN ('mobile', 'tablet', 'desktop', 'unknown')),
    browser             TEXT,
    os                  TEXT,
    bound_ip            TEXT,
    ip_binding_enforced BOOLEAN     NOT NULL DEFAULT FALSE,
    fingerprint_binding_enforced BOOLEAN NOT NULL DEFAULT FALSE,
    is_suspicious       BOOLEAN     NOT NULL DEFAULT FALSE,
    suspicious_reason   TEXT,
    suspicious_detected_at TIMESTAMPTZ,
    requires_verification BOOLEAN   NOT NULL DEFAULT FALSE,
    verified_at         TIMESTAMPTZ,
    session_number      INTEGER,
    mfa_verified        BOOLEAN     NOT NULL DEFAULT FALSE,
    
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked             BOOLEAN     NOT NULL DEFAULT FALSE,
    revoked_at          TIMESTAMPTZ,
    revoke_reason       TEXT,
    CONSTRAINT chk_revoked_at_consistency CHECK ((revoked = FALSE AND revoked_at IS NULL) OR (revoked = TRUE AND revoked_at IS NOT NULL)),
    CONSTRAINT chk_expires_after_created CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS idx_ss_token_lookup ON security_sessions (session_token_hash) WHERE revoked = FALSE;
CREATE INDEX IF NOT EXISTS idx_ss_user_active ON security_sessions (user_id, last_active_at DESC) WHERE revoked = FALSE;
CREATE INDEX IF NOT EXISTS idx_ss_mfa_verified ON security_sessions (user_id, mfa_verified) WHERE mfa_verified = TRUE;

-- 1.5 LOGIN_ATTEMPTS
-- Per-account lockout tracking.
CREATE TABLE IF NOT EXISTS login_attempts (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT        NOT NULL,
    user_id         UUID        REFERENCES user_profiles(id) ON DELETE SET NULL,
    ip_address      TEXT        NOT NULL,
    user_agent      TEXT,
    success         BOOLEAN     NOT NULL,
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_la_email_failures ON login_attempts (email, created_at DESC) WHERE success = FALSE;
CREATE INDEX IF NOT EXISTS idx_la_user_time ON login_attempts (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- 1.6 USER_MFA
-- Encrypted TOTP secrets.
CREATE TABLE IF NOT EXISTS user_mfa (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID        NOT NULL UNIQUE REFERENCES user_profiles(id) ON DELETE CASCADE,
    totp_secret_enc     TEXT        NOT NULL,
    enabled             BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_at         TIMESTAMPTZ,
    last_used_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_user_mfa_verified_before_enabled CHECK ((enabled = FALSE) OR (enabled = TRUE AND verified_at IS NOT NULL))
);

-- 1.7 RECOVERY_CODES
-- Single-use backup codes.
CREATE TABLE IF NOT EXISTS recovery_codes (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    code_hash       TEXT        NOT NULL UNIQUE,
    batch_id        UUID        NOT NULL,
    used            BOOLEAN     NOT NULL DEFAULT FALSE,
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_recovery_codes_used_at CHECK ((used = FALSE AND used_at IS NULL) OR (used = TRUE AND used_at IS NOT NULL))
);

-- 1.8 MFA_CHALLENGES
-- Ephemeral two-step login tokens.
CREATE TABLE IF NOT EXISTS mfa_challenges (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    challenge_hash      TEXT        NOT NULL UNIQUE,
    ip_address          TEXT,
    user_agent          TEXT,
    expires_at          TIMESTAMPTZ NOT NULL,
    used                BOOLEAN     NOT NULL DEFAULT FALSE,
    used_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_mfa_challenge_used_at CHECK ((used = FALSE AND used_at IS NULL) OR (used = TRUE AND used_at IS NOT NULL)),
    CONSTRAINT chk_mfa_challenge_expires_after_created CHECK (expires_at > created_at)
);

-- 1.9 USER_PASSKEYS
-- Registered FIDO2 / WebAuthn credentials (passkeys) per user.
CREATE TABLE IF NOT EXISTS user_passkeys (
    id                      UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    credential_id           TEXT        NOT NULL UNIQUE,
    credential_public_key   TEXT        NOT NULL,
    sign_count              BIGINT      NOT NULL DEFAULT 0 CHECK (sign_count >= 0),
    aaguid                  TEXT,
    device_type             TEXT        CHECK (device_type IN ('singleDevice', 'multiDevice')),
    backed_up               BOOLEAN     NOT NULL DEFAULT FALSE,
    transports              TEXT[],
    display_name            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_up_credential_id ON user_passkeys (credential_id);
CREATE INDEX IF NOT EXISTS idx_up_user_id ON user_passkeys (user_id, created_at DESC);

-- 1.10 WEBAUTHN_CHALLENGES
-- Ephemeral challenges for WebAuthn registration and authentication ceremonies.
CREATE TABLE IF NOT EXISTS webauthn_challenges (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    challenge       TEXT        NOT NULL,
    ceremony        TEXT        NOT NULL CHECK (ceremony IN ('registration', 'authentication')),
    user_id         UUID        REFERENCES user_profiles(id) ON DELETE CASCADE,
    ip_address      TEXT,
    user_agent      TEXT,
    expires_at      TIMESTAMPTZ NOT NULL,
    used            BOOLEAN     NOT NULL DEFAULT FALSE,
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_wc_used_at CHECK ((used = FALSE AND used_at IS NULL) OR (used = TRUE AND used_at IS NOT NULL)),
    CONSTRAINT chk_wc_expires_after_created CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS idx_wc_id_active ON webauthn_challenges (id) WHERE used = FALSE;
CREATE INDEX IF NOT EXISTS idx_wc_cleanup ON webauthn_challenges (expires_at) WHERE used = FALSE;
CREATE INDEX IF NOT EXISTS idx_wc_user_time ON webauthn_challenges (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- 1.11 ROLES
-- RBAC role definitions.
CREATE TABLE IF NOT EXISTS roles (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(50) NOT NULL UNIQUE,
    description     TEXT,
    level           INTEGER     NOT NULL CHECK (level > 0),
    is_system_role  BOOLEAN     NOT NULL DEFAULT FALSE,
    tenant_scoped   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_roles_level ON roles (level DESC);

-- 1.12 PERMISSIONS
-- Granular permissions.
CREATE TABLE IF NOT EXISTS permissions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    resource        VARCHAR(50) NOT NULL,
    action          VARCHAR(50) NOT NULL,
    is_system       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (resource, action)
);

-- 1.13 ROLE_PERMISSIONS
-- Junction table for role-permission assignment.
CREATE TABLE IF NOT EXISTS role_permissions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    role_id         UUID        NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id   UUID        NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (role_id, permission_id)
);

-- 1.14 TENANT_USERS
-- Junction table for tenant membership with role assignment.
CREATE TABLE IF NOT EXISTS tenant_users (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role_id         UUID        NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    is_primary      BOOLEAN     NOT NULL DEFAULT FALSE,
    status          VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('pending', 'active', 'suspended', 'removed')),
    invited_by      UUID        REFERENCES user_profiles(id) ON DELETE SET NULL,
    invited_at      TIMESTAMPTZ,
    joined_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_tu_user_id ON tenant_users (user_id);
CREATE INDEX IF NOT EXISTS idx_tu_tenant_id ON tenant_users (tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tu_user_primary ON tenant_users (user_id) WHERE is_primary = TRUE;

-- 1.15 USER_PERMISSIONS
-- Direct permission grants to users.
CREATE TABLE IF NOT EXISTS user_permissions (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID        NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    permission_id   UUID        NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    tenant_id       UUID        REFERENCES tenants(id) ON DELETE CASCADE,
    expires_at      TIMESTAMPTZ,
    reason          TEXT,
    granted_by      UUID        REFERENCES user_profiles(id) ON DELETE SET NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, permission_id, tenant_id)
);

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
    talklee_call_id VARCHAR(20),
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
CREATE INDEX IF NOT EXISTS idx_transcripts_talklee_id ON transcripts(talklee_call_id) WHERE talklee_call_id IS NOT NULL;
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
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
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

-- 6.6.2 TENANT SIP TRUNKS
CREATE TABLE IF NOT EXISTS tenant_sip_trunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    trunk_name VARCHAR(100) NOT NULL,
    sip_domain VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL DEFAULT 5060 CHECK (port BETWEEN 1 AND 65535),
    transport VARCHAR(8) NOT NULL DEFAULT 'udp' CHECK (transport IN ('udp', 'tcp', 'tls')),
    direction VARCHAR(10) NOT NULL DEFAULT 'both' CHECK (direction IN ('inbound', 'outbound', 'both')),
    auth_username VARCHAR(255),
    auth_password_encrypted TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_sip_trunks_auth_pair
        CHECK (
            (auth_username IS NULL AND auth_password_encrypted IS NULL) OR
            (auth_username IS NOT NULL AND auth_password_encrypted IS NOT NULL)
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_sip_trunks_tenant_name_unique
    ON tenant_sip_trunks(tenant_id, lower(trunk_name));
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_sip_trunks_tenant_id_id_unique
    ON tenant_sip_trunks(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_tenant_sip_trunks_tenant_active
    ON tenant_sip_trunks(tenant_id, is_active);

-- 6.6.3 TENANT CODEC POLICIES
CREATE TABLE IF NOT EXISTS tenant_codec_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    allowed_codecs TEXT[] NOT NULL DEFAULT ARRAY['PCMU', 'PCMA'],
    preferred_codec VARCHAR(20) NOT NULL DEFAULT 'PCMU',
    sample_rate_hz INTEGER NOT NULL DEFAULT 8000 CHECK (sample_rate_hz IN (8000, 16000, 24000, 48000)),
    ptime_ms INTEGER NOT NULL DEFAULT 20 CHECK (ptime_ms IN (10, 20, 30, 40, 60)),
    max_bitrate_kbps INTEGER CHECK (max_bitrate_kbps IS NULL OR max_bitrate_kbps > 0),
    jitter_buffer_ms INTEGER NOT NULL DEFAULT 60 CHECK (jitter_buffer_ms BETWEEN 0 AND 1000),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_codec_preferred_in_allowed
        CHECK (preferred_codec = ANY (allowed_codecs))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_codec_policies_tenant_name_unique
    ON tenant_codec_policies(tenant_id, lower(policy_name));
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_codec_policies_tenant_id_id_unique
    ON tenant_codec_policies(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_tenant_codec_policies_tenant_active
    ON tenant_codec_policies(tenant_id, is_active);

-- 6.6.4 TENANT ROUTE POLICIES
CREATE TABLE IF NOT EXISTS tenant_route_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    route_type VARCHAR(10) NOT NULL DEFAULT 'outbound' CHECK (route_type IN ('inbound', 'outbound')),
    priority INTEGER NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 10000),
    match_pattern TEXT NOT NULL,
    target_trunk_id UUID NOT NULL,
    codec_policy_id UUID,
    strip_digits INTEGER NOT NULL DEFAULT 0 CHECK (strip_digits BETWEEN 0 AND 15),
    prepend_digits VARCHAR(20),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_tenant_route_policies_trunk
        FOREIGN KEY (tenant_id, target_trunk_id)
        REFERENCES tenant_sip_trunks(tenant_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_tenant_route_policies_codec
        FOREIGN KEY (tenant_id, codec_policy_id)
        REFERENCES tenant_codec_policies(tenant_id, id)
        ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_route_policies_tenant_name_unique
    ON tenant_route_policies(tenant_id, lower(policy_name));
CREATE INDEX IF NOT EXISTS idx_tenant_route_policies_tenant_route_active_priority
    ON tenant_route_policies(tenant_id, route_type, is_active, priority);

-- 6.6.5 TENANT TELEPHONY IDEMPOTENCY
CREATE TABLE IF NOT EXISTS tenant_telephony_idempotency (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    operation VARCHAR(120) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    response_body JSONB,
    status_code INTEGER CHECK (status_code IS NULL OR status_code BETWEEN 100 AND 599),
    resource_type VARCHAR(64),
    resource_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    CONSTRAINT uq_tenant_telephony_idempotency
        UNIQUE (tenant_id, operation, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_idempotency_tenant_created
    ON tenant_telephony_idempotency(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_telephony_idempotency_expires
    ON tenant_telephony_idempotency(expires_at);

-- 6.6.6 TENANT RUNTIME POLICY VERSIONS
CREATE TABLE IF NOT EXISTS tenant_runtime_policy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_version INTEGER NOT NULL CHECK (policy_version > 0),
    source_hash CHAR(64) NOT NULL,
    schema_version VARCHAR(32) NOT NULL DEFAULT 'ws-g.v1',
    input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    compiled_artifact JSONB NOT NULL,
    validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    build_status VARCHAR(20) NOT NULL DEFAULT 'compiled'
        CHECK (build_status IN ('compiled', 'active', 'failed', 'superseded', 'rolled_back')),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    is_last_good BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    activated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    CONSTRAINT uq_tenant_runtime_policy_version UNIQUE (tenant_id, policy_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_runtime_policy_versions_tenant_active_unique
    ON tenant_runtime_policy_versions(tenant_id)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_versions_tenant_version
    ON tenant_runtime_policy_versions(tenant_id, policy_version DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_versions_tenant_last_good
    ON tenant_runtime_policy_versions(tenant_id, is_last_good, policy_version DESC);

-- 6.6.7 TENANT RUNTIME POLICY EVENTS
CREATE TABLE IF NOT EXISTS tenant_runtime_policy_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_version_id UUID NOT NULL REFERENCES tenant_runtime_policy_versions(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL CHECK (action IN ('activate', 'rollback')),
    stage VARCHAR(20) NOT NULL CHECK (stage IN ('precheck', 'apply', 'verify', 'commit', 'rollback')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_id VARCHAR(128),
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_events_tenant_created
    ON tenant_runtime_policy_events(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_runtime_policy_events_policy_version
    ON tenant_runtime_policy_events(policy_version_id, created_at DESC);

-- 6.6.8 TENANT SIP TRUST POLICIES
CREATE TABLE IF NOT EXISTS tenant_sip_trust_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    allowed_source_cidrs CIDR[] NOT NULL DEFAULT ARRAY[]::CIDR[],
    blocked_source_cidrs CIDR[] NOT NULL DEFAULT ARRAY[]::CIDR[],
    kamailio_group SMALLINT NOT NULL DEFAULT 1 CHECK (kamailio_group > 0),
    priority INTEGER NOT NULL DEFAULT 100 CHECK (priority BETWEEN 1 AND 10000),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tenant_sip_trust_has_source
        CHECK (cardinality(allowed_source_cidrs) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_sip_trust_policies_tenant_name_unique
    ON tenant_sip_trust_policies(tenant_id, lower(policy_name));
CREATE INDEX IF NOT EXISTS idx_tenant_sip_trust_policies_tenant_active
    ON tenant_sip_trust_policies(tenant_id, is_active, priority);

-- 6.6.9 TENANT TELEPHONY THRESHOLD POLICIES (WS-I)
CREATE TABLE IF NOT EXISTS tenant_telephony_threshold_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_name VARCHAR(100) NOT NULL,
    policy_scope VARCHAR(32) NOT NULL
        CHECK (policy_scope IN ('api_mutation', 'runtime_mutation', 'sip_edge')),
    metric_key VARCHAR(120) NOT NULL DEFAULT '*',
    window_seconds INTEGER NOT NULL DEFAULT 60 CHECK (window_seconds BETWEEN 1 AND 3600),
    warn_threshold INTEGER NOT NULL DEFAULT 20 CHECK (warn_threshold > 0),
    throttle_threshold INTEGER NOT NULL DEFAULT 30 CHECK (throttle_threshold > 0),
    block_threshold INTEGER NOT NULL DEFAULT 45 CHECK (block_threshold > 0),
    block_duration_seconds INTEGER NOT NULL DEFAULT 300 CHECK (block_duration_seconds BETWEEN 1 AND 86400),
    throttle_retry_seconds INTEGER NOT NULL DEFAULT 2 CHECK (throttle_retry_seconds BETWEEN 1 AND 60),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_telephony_threshold_policy UNIQUE (tenant_id, policy_scope, metric_key),
    CONSTRAINT chk_tenant_telephony_threshold_order
        CHECK (warn_threshold <= throttle_threshold AND throttle_threshold <= block_threshold)
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_threshold_scope_active
    ON tenant_telephony_threshold_policies(tenant_id, policy_scope, is_active);
CREATE INDEX IF NOT EXISTS idx_tenant_telephony_threshold_metric
    ON tenant_telephony_threshold_policies(tenant_id, policy_scope, metric_key);

INSERT INTO tenant_telephony_threshold_policies (
    tenant_id,
    policy_name,
    policy_scope,
    metric_key,
    window_seconds,
    warn_threshold,
    throttle_threshold,
    block_threshold,
    block_duration_seconds,
    throttle_retry_seconds,
    metadata
)
SELECT
    t.id,
    'api-default',
    'api_mutation',
    '*',
    60,
    20,
    30,
    45,
    300,
    2,
    '{"seeded_by":"ws-i"}'::jsonb
FROM tenants t
ON CONFLICT (tenant_id, policy_scope, metric_key) DO NOTHING;

INSERT INTO tenant_telephony_threshold_policies (
    tenant_id,
    policy_name,
    policy_scope,
    metric_key,
    window_seconds,
    warn_threshold,
    throttle_threshold,
    block_threshold,
    block_duration_seconds,
    throttle_retry_seconds,
    metadata
)
SELECT
    t.id,
    'runtime-default',
    'runtime_mutation',
    '*',
    60,
    10,
    15,
    20,
    300,
    2,
    '{"seeded_by":"ws-i"}'::jsonb
FROM tenants t
ON CONFLICT (tenant_id, policy_scope, metric_key) DO NOTHING;

-- 6.6.10 TENANT TELEPHONY QUOTA EVENTS (WS-I)
CREATE TABLE IF NOT EXISTS tenant_telephony_quota_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_id UUID REFERENCES tenant_telephony_threshold_policies(id) ON DELETE SET NULL,
    event_type VARCHAR(16) NOT NULL CHECK (event_type IN ('warn', 'throttle', 'block')),
    policy_scope VARCHAR(32) NOT NULL,
    metric_key VARCHAR(120) NOT NULL,
    counter_value BIGINT NOT NULL DEFAULT 0,
    threshold_value BIGINT,
    window_seconds INTEGER NOT NULL CHECK (window_seconds > 0),
    block_ttl_seconds INTEGER NOT NULL DEFAULT 0 CHECK (block_ttl_seconds >= 0),
    request_id VARCHAR(128),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_telephony_quota_events_tenant_created
    ON tenant_telephony_quota_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_telephony_quota_events_policy
    ON tenant_telephony_quota_events(policy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_telephony_quota_events_scope_metric
    ON tenant_telephony_quota_events(tenant_id, policy_scope, metric_key, created_at DESC);

-- 6.6.11 TENANT POLICY AUDIT LOG (WS-J)
CREATE TABLE IF NOT EXISTS tenant_policy_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    table_name VARCHAR(80) NOT NULL,
    record_id UUID,
    action VARCHAR(10) NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
    actor_user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    actor_type VARCHAR(16) NOT NULL DEFAULT 'system' CHECK (actor_type IN ('user', 'system')),
    request_id VARCHAR(128),
    correlation_id VARCHAR(128),
    before_payload JSONB,
    after_payload JSONB,
    changed_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    source VARCHAR(32) NOT NULL DEFAULT 'db_trigger',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retention_until TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '400 days')
);

CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_tenant_created
    ON tenant_policy_audit_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_tenant_table_created
    ON tenant_policy_audit_log(tenant_id, table_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_request_id
    ON tenant_policy_audit_log(request_id);
CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_retention_until
    ON tenant_policy_audit_log(retention_until);

CREATE OR REPLACE FUNCTION prevent_tenant_policy_audit_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'tenant_policy_audit_log is immutable';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION log_tenant_policy_mutation()
RETURNS TRIGGER AS $$
DECLARE
    event_tenant_id UUID;
    event_record_id UUID;
    before_data JSONB := NULL;
    after_data JSONB := NULL;
    actor_setting TEXT;
    actor_uuid UUID := NULL;
    request_id_setting TEXT;
    correlation_id_setting TEXT;
    merged_data JSONB;
    changed_cols TEXT[] := ARRAY[]::TEXT[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        event_tenant_id := NEW.tenant_id;
        event_record_id := NEW.id;
        after_data := to_jsonb(NEW);
    ELSIF TG_OP = 'UPDATE' THEN
        event_tenant_id := NEW.tenant_id;
        event_record_id := NEW.id;
        before_data := to_jsonb(OLD);
        after_data := to_jsonb(NEW);
    ELSIF TG_OP = 'DELETE' THEN
        event_tenant_id := OLD.tenant_id;
        event_record_id := OLD.id;
        before_data := to_jsonb(OLD);
    ELSE
        RAISE EXCEPTION 'Unsupported TG_OP: %', TG_OP;
    END IF;

    actor_setting := NULLIF(current_setting('app.current_user_id', true), '');
    IF actor_setting IS NOT NULL THEN
        BEGIN
            actor_uuid := actor_setting::UUID;
        EXCEPTION WHEN others THEN
            actor_uuid := NULL;
        END;
    END IF;

    request_id_setting := NULLIF(current_setting('app.current_request_id', true), '');
    correlation_id_setting := request_id_setting;

    merged_data := COALESCE(before_data, '{}'::jsonb) || COALESCE(after_data, '{}'::jsonb);
    SELECT COALESCE(array_agg(keys.key ORDER BY keys.key), ARRAY[]::TEXT[])
    INTO changed_cols
    FROM jsonb_object_keys(merged_data) AS keys(key)
    WHERE COALESCE(before_data -> keys.key, 'null'::jsonb)
        IS DISTINCT FROM COALESCE(after_data -> keys.key, 'null'::jsonb);

    INSERT INTO tenant_policy_audit_log (
        tenant_id,
        table_name,
        record_id,
        action,
        actor_user_id,
        actor_type,
        request_id,
        correlation_id,
        before_payload,
        after_payload,
        changed_fields,
        source
    )
    VALUES (
        event_tenant_id,
        TG_TABLE_NAME,
        event_record_id,
        TG_OP,
        actor_uuid,
        CASE WHEN actor_uuid IS NULL THEN 'system' ELSE 'user' END,
        request_id_setting,
        correlation_id_setting,
        before_data,
        after_data,
        changed_cols,
        'db_trigger'
    );

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prune_tenant_policy_audit_log(p_limit INTEGER DEFAULT 5000)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    WITH to_delete AS (
        SELECT id
        FROM tenant_policy_audit_log
        WHERE retention_until < NOW()
        ORDER BY retention_until ASC
        LIMIT GREATEST(COALESCE(p_limit, 0), 0)
    ),
    deleted AS (
        DELETE FROM tenant_policy_audit_log a
        USING to_delete d
        WHERE a.id = d.id
        RETURNING 1
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- 6.6.12 TENANT POLICY RLS
ALTER TABLE tenant_sip_trunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_codec_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_route_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_sip_trust_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_threshold_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_quota_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_policy_audit_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE tenant_sip_trunks FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_codec_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_route_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_idempotency FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_runtime_policy_events FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_sip_trust_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_threshold_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_telephony_quota_events FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_policy_audit_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_tenant_sip_trunks_select ON tenant_sip_trunks;
DROP POLICY IF EXISTS p_tenant_sip_trunks_insert ON tenant_sip_trunks;
DROP POLICY IF EXISTS p_tenant_sip_trunks_update ON tenant_sip_trunks;
DROP POLICY IF EXISTS p_tenant_sip_trunks_delete ON tenant_sip_trunks;
CREATE POLICY p_tenant_sip_trunks_select ON tenant_sip_trunks
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trunks_insert ON tenant_sip_trunks
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trunks_update ON tenant_sip_trunks
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trunks_delete ON tenant_sip_trunks
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_codec_policies_select ON tenant_codec_policies;
DROP POLICY IF EXISTS p_tenant_codec_policies_insert ON tenant_codec_policies;
DROP POLICY IF EXISTS p_tenant_codec_policies_update ON tenant_codec_policies;
DROP POLICY IF EXISTS p_tenant_codec_policies_delete ON tenant_codec_policies;
CREATE POLICY p_tenant_codec_policies_select ON tenant_codec_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_codec_policies_insert ON tenant_codec_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_codec_policies_update ON tenant_codec_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_codec_policies_delete ON tenant_codec_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_route_policies_select ON tenant_route_policies;
DROP POLICY IF EXISTS p_tenant_route_policies_insert ON tenant_route_policies;
DROP POLICY IF EXISTS p_tenant_route_policies_update ON tenant_route_policies;
DROP POLICY IF EXISTS p_tenant_route_policies_delete ON tenant_route_policies;
CREATE POLICY p_tenant_route_policies_select ON tenant_route_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_route_policies_insert ON tenant_route_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_route_policies_update ON tenant_route_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_route_policies_delete ON tenant_route_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_telephony_idempotency_select ON tenant_telephony_idempotency;
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_insert ON tenant_telephony_idempotency;
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_update ON tenant_telephony_idempotency;
DROP POLICY IF EXISTS p_tenant_telephony_idempotency_delete ON tenant_telephony_idempotency;
CREATE POLICY p_tenant_telephony_idempotency_select ON tenant_telephony_idempotency
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_idempotency_insert ON tenant_telephony_idempotency
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_idempotency_update ON tenant_telephony_idempotency
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_idempotency_delete ON tenant_telephony_idempotency
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_select ON tenant_runtime_policy_versions;
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_insert ON tenant_runtime_policy_versions;
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_update ON tenant_runtime_policy_versions;
DROP POLICY IF EXISTS p_tenant_runtime_policy_versions_delete ON tenant_runtime_policy_versions;
CREATE POLICY p_tenant_runtime_policy_versions_select ON tenant_runtime_policy_versions
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_versions_insert ON tenant_runtime_policy_versions
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_versions_update ON tenant_runtime_policy_versions
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_versions_delete ON tenant_runtime_policy_versions
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_runtime_policy_events_select ON tenant_runtime_policy_events;
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_insert ON tenant_runtime_policy_events;
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_update ON tenant_runtime_policy_events;
DROP POLICY IF EXISTS p_tenant_runtime_policy_events_delete ON tenant_runtime_policy_events;
CREATE POLICY p_tenant_runtime_policy_events_select ON tenant_runtime_policy_events
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_events_insert ON tenant_runtime_policy_events
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_events_update ON tenant_runtime_policy_events
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_runtime_policy_events_delete ON tenant_runtime_policy_events
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_sip_trust_policies_select ON tenant_sip_trust_policies;
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_insert ON tenant_sip_trust_policies;
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_update ON tenant_sip_trust_policies;
DROP POLICY IF EXISTS p_tenant_sip_trust_policies_delete ON tenant_sip_trust_policies;
CREATE POLICY p_tenant_sip_trust_policies_select ON tenant_sip_trust_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trust_policies_insert ON tenant_sip_trust_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trust_policies_update ON tenant_sip_trust_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_sip_trust_policies_delete ON tenant_sip_trust_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_select ON tenant_telephony_threshold_policies;
DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_insert ON tenant_telephony_threshold_policies;
DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_update ON tenant_telephony_threshold_policies;
DROP POLICY IF EXISTS p_tenant_telephony_threshold_policies_delete ON tenant_telephony_threshold_policies;
CREATE POLICY p_tenant_telephony_threshold_policies_select ON tenant_telephony_threshold_policies
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_threshold_policies_insert ON tenant_telephony_threshold_policies
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_threshold_policies_update ON tenant_telephony_threshold_policies
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_threshold_policies_delete ON tenant_telephony_threshold_policies
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_telephony_quota_events_select ON tenant_telephony_quota_events;
DROP POLICY IF EXISTS p_tenant_telephony_quota_events_insert ON tenant_telephony_quota_events;
DROP POLICY IF EXISTS p_tenant_telephony_quota_events_update ON tenant_telephony_quota_events;
DROP POLICY IF EXISTS p_tenant_telephony_quota_events_delete ON tenant_telephony_quota_events;
CREATE POLICY p_tenant_telephony_quota_events_select ON tenant_telephony_quota_events
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_quota_events_insert ON tenant_telephony_quota_events
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_quota_events_update ON tenant_telephony_quota_events
    FOR UPDATE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_telephony_quota_events_delete ON tenant_telephony_quota_events
    FOR DELETE
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS p_tenant_policy_audit_log_select ON tenant_policy_audit_log;
DROP POLICY IF EXISTS p_tenant_policy_audit_log_insert ON tenant_policy_audit_log;
DROP POLICY IF EXISTS p_tenant_policy_audit_log_update ON tenant_policy_audit_log;
DROP POLICY IF EXISTS p_tenant_policy_audit_log_delete ON tenant_policy_audit_log;
CREATE POLICY p_tenant_policy_audit_log_select ON tenant_policy_audit_log
    FOR SELECT
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_policy_audit_log_insert ON tenant_policy_audit_log
    FOR INSERT
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY p_tenant_policy_audit_log_update ON tenant_policy_audit_log
    FOR UPDATE
    USING (FALSE)
    WITH CHECK (FALSE);
CREATE POLICY p_tenant_policy_audit_log_delete ON tenant_policy_audit_log
    FOR DELETE
    USING (FALSE);

DROP TRIGGER IF EXISTS trg_prevent_tenant_policy_audit_log_update
    ON tenant_policy_audit_log;
CREATE TRIGGER trg_prevent_tenant_policy_audit_log_update
    BEFORE UPDATE ON tenant_policy_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_tenant_policy_audit_log_mutation();

DROP TRIGGER IF EXISTS trg_prevent_tenant_policy_audit_log_delete
    ON tenant_policy_audit_log;
CREATE TRIGGER trg_prevent_tenant_policy_audit_log_delete
    BEFORE DELETE ON tenant_policy_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_tenant_policy_audit_log_mutation();

DROP TRIGGER IF EXISTS trg_audit_tenant_sip_trunks ON tenant_sip_trunks;
CREATE TRIGGER trg_audit_tenant_sip_trunks
    AFTER INSERT OR UPDATE OR DELETE ON tenant_sip_trunks
    FOR EACH ROW
    EXECUTE FUNCTION log_tenant_policy_mutation();

DROP TRIGGER IF EXISTS trg_audit_tenant_codec_policies ON tenant_codec_policies;
CREATE TRIGGER trg_audit_tenant_codec_policies
    AFTER INSERT OR UPDATE OR DELETE ON tenant_codec_policies
    FOR EACH ROW
    EXECUTE FUNCTION log_tenant_policy_mutation();

DROP TRIGGER IF EXISTS trg_audit_tenant_route_policies ON tenant_route_policies;
CREATE TRIGGER trg_audit_tenant_route_policies
    AFTER INSERT OR UPDATE OR DELETE ON tenant_route_policies
    FOR EACH ROW
    EXECUTE FUNCTION log_tenant_policy_mutation();

DROP TRIGGER IF EXISTS trg_audit_tenant_sip_trust_policies ON tenant_sip_trust_policies;
CREATE TRIGGER trg_audit_tenant_sip_trust_policies
    AFTER INSERT OR UPDATE OR DELETE ON tenant_sip_trust_policies
    FOR EACH ROW
    EXECUTE FUNCTION log_tenant_policy_mutation();

DROP TRIGGER IF EXISTS trg_audit_tenant_runtime_policy_versions ON tenant_runtime_policy_versions;
CREATE TRIGGER trg_audit_tenant_runtime_policy_versions
    AFTER INSERT OR UPDATE OR DELETE ON tenant_runtime_policy_versions
    FOR EACH ROW
    EXECUTE FUNCTION log_tenant_policy_mutation();

DROP TRIGGER IF EXISTS trg_audit_tenant_telephony_threshold_policies ON tenant_telephony_threshold_policies;
CREATE TRIGGER trg_audit_tenant_telephony_threshold_policies
    AFTER INSERT OR UPDATE OR DELETE ON tenant_telephony_threshold_policies
    FOR EACH ROW
    EXECUTE FUNCTION log_tenant_policy_mutation();

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
-- SECTION 6.5: VOICE SECURITY & CALL GUARD (Day 7)
-- =============================================================================
-- Pre-call validation, rate limiting, concurrency enforcement, abuse detection.
-- Unified security gate for all outbound calls (REST + Dialer Worker).
-- =============================================================================

-- =============================================================================
-- TENANT CALL LIMITS
-- =============================================================================
-- Comprehensive per-tenant limits for voice operations including rate limits,
-- concurrency, spend caps, and geographic restrictions.
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenant_call_limits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Rate limits (rolling window)
    calls_per_minute INTEGER NOT NULL DEFAULT 60,
    calls_per_hour INTEGER NOT NULL DEFAULT 1000,
    calls_per_day INTEGER NOT NULL DEFAULT 10000,

    -- Concurrency
    max_concurrent_calls INTEGER NOT NULL DEFAULT 10,
    max_queue_size INTEGER NOT NULL DEFAULT 50,

    -- Usage limits
    monthly_minutes_allocated INTEGER NOT NULL DEFAULT 0,
    monthly_minutes_used INTEGER NOT NULL DEFAULT 0,
    monthly_spend_cap DECIMAL(12,2),
    monthly_spend_used DECIMAL(12,2) DEFAULT 0.00,

    -- Call restrictions
    max_call_duration_seconds INTEGER DEFAULT 3600, -- 1 hour
    min_call_interval_seconds INTEGER DEFAULT 300,  -- 5 min between calls to same number

    -- Geographic controls (ISO 3166-1 alpha-2 country codes)
    allowed_country_codes TEXT[] DEFAULT '{}',
    blocked_country_codes TEXT[] DEFAULT '{}',
    blocked_prefixes TEXT[] DEFAULT '{}',

    -- Feature flags (override plan features)
    features_enabled JSONB DEFAULT '{}',
    features_disabled JSONB DEFAULT '{}',

    -- Business hours (optional, format: '09:00' - '17:00')
    business_hours_start TIME,
    business_hours_end TIME,
    business_hours_timezone TEXT DEFAULT 'UTC',
    respect_business_hours BOOLEAN DEFAULT FALSE,

    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    effective_from TIMESTAMPTZ DEFAULT NOW(),
    effective_until TIMESTAMPTZ,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_by UUID,

    UNIQUE(tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_call_limits_tenant ON tenant_call_limits(tenant_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_tenant_call_limits_effective ON tenant_call_limits(tenant_id, effective_from, effective_until);

-- =============================================================================
-- PARTNER AGGREGATE LIMITS (Multi-tenant Reseller Controls)
-- =============================================================================
-- For partners/resellers managing multiple sub-tenants.
-- Enforces aggregate limits across all child tenants.
-- =============================================================================

CREATE TABLE IF NOT EXISTS partner_limits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    partner_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, -- Partner is also a tenant

    -- Tenant management
    max_tenants INTEGER NOT NULL DEFAULT 10,
    current_tenant_count INTEGER DEFAULT 0,

    -- Aggregate rate limits across all child tenants
    aggregate_calls_per_minute INTEGER NOT NULL DEFAULT 600,
    aggregate_calls_per_hour INTEGER NOT NULL DEFAULT 10000,
    aggregate_calls_per_day INTEGER NOT NULL DEFAULT 100000,
    aggregate_concurrent_calls INTEGER NOT NULL DEFAULT 100,

    -- Financial controls
    revenue_share_percent DECIMAL(5,2) DEFAULT 20.00,
    min_billing_amount DECIMAL(10,2) DEFAULT 100.00,
    max_billing_amount DECIMAL(12,2),

    -- Feature governance
    feature_whitelist JSONB DEFAULT '[]', -- If set, only these features allowed
    feature_blacklist JSONB DEFAULT '[]', -- These features never allowed

    -- Abuse detection sensitivity (0-100, higher = more sensitive)
    fraud_detection_sensitivity INTEGER DEFAULT 50 CHECK (fraud_detection_sensitivity BETWEEN 0 AND 100),

    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_by UUID,

    UNIQUE(partner_id)
);

CREATE INDEX IF NOT EXISTS idx_partner_limits_partner ON partner_limits(partner_id) WHERE is_active = TRUE;

-- =============================================================================
-- TENANT-PARTNER RELATIONSHIP (ALTER)
-- =============================================================================
-- Links child tenants to their partner for aggregate limit enforcement
-- =============================================================================

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS partner_id UUID REFERENCES tenants(id);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_partner BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_tenants_partner ON tenants(partner_id) WHERE partner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tenants_is_partner ON tenants(is_partner) WHERE is_partner = TRUE;

-- =============================================================================
-- ABUSE DETECTION RULES
-- =============================================================================
-- Configurable rules for real-time fraud and abuse detection.
-- Rules can be global (tenant_id = NULL) or tenant-specific.
-- =============================================================================

CREATE TABLE IF NOT EXISTS abuse_detection_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE, -- NULL = global rule

    rule_name TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK (rule_type IN (
        'velocity_spike',           -- Sudden volume increase
        'short_duration_pattern',   -- Many short calls
        'repeat_number',            -- Calling same number repeatedly
        'sequential_dialing',       -- War dialing
        'premium_rate',             -- Premium number abuse
        'international_spike',      -- Sudden international increase
        'after_hours',              -- Off-hours calling
        'geographic_impossibility', -- Physics-defying geography
        'account_hopping',          -- Rapid tenant switching
        'toll_fraud',               -- Known fraud patterns
        'wangiri',                  -- Missed call fraud pattern
        'irs_fraud'                 -- International Revenue Share Fraud
    )),

    -- Detection parameters (JSON for flexibility)
    parameters JSONB NOT NULL DEFAULT '{}',

    -- Thresholds
    warn_threshold INTEGER,
    block_threshold INTEGER,

    -- Actions
    action_on_trigger TEXT NOT NULL DEFAULT 'flag' CHECK (action_on_trigger IN (
        'flag',     -- Log for review
        'warn',     -- Alert but allow
        'throttle', -- Slow down
        'block',    -- Block calls
        'suspend'   -- Suspend tenant
    )),

    -- Time window for analysis (minutes)
    analysis_window_minutes INTEGER DEFAULT 60,

    -- Rule status
    is_active BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 100, -- Lower = higher priority

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    updated_by UUID
);

CREATE INDEX IF NOT EXISTS idx_abuse_rules_tenant ON abuse_detection_rules(tenant_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_abuse_rules_type ON abuse_detection_rules(rule_type, is_active);

-- =============================================================================
-- ABUSE EVENTS (AUDIT TRAIL)
-- =============================================================================
-- Records of detected abuse patterns and actions taken.
-- Used for fraud investigation and compliance reporting.
-- =============================================================================

CREATE TABLE IF NOT EXISTS abuse_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    partner_id UUID REFERENCES tenants(id), -- If applicable

    event_type TEXT NOT NULL, -- References abuse_detection_rules.rule_type
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),

    -- Detection details
    rule_id UUID REFERENCES abuse_detection_rules(id),
    trigger_value NUMERIC, -- The value that triggered (e.g., 150 calls when limit is 100)
    threshold_value NUMERIC, -- The threshold that was exceeded

    -- Context
    source_ip INET,
    phone_number_called VARCHAR(50),
    campaign_id UUID,
    call_id UUID,
    user_id UUID,

    -- Geographic info
    destination_country TEXT,
    destination_prefix TEXT,

    -- Financial impact (if applicable)
    estimated_cost_impact DECIMAL(12,2),

    -- Action taken
    action_taken TEXT NOT NULL CHECK (action_taken IN (
        'flagged',     -- Logged for review
        'warned',      -- Alert sent
        'throttled',   -- Rate limited
        'blocked',     -- Call blocked
        'suspended'    -- Tenant suspended
    )),
    action_details JSONB DEFAULT '{}',

    -- Auto-escalation
    auto_escalate_at TIMESTAMPTZ,
    escalation_level INTEGER DEFAULT 0,

    -- Resolution
    resolved_at TIMESTAMPTZ,
    resolved_by UUID,
    resolution_notes TEXT,
    false_positive BOOLEAN, -- Was this a false positive?

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_abuse_events_tenant ON abuse_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_abuse_events_type ON abuse_events(event_type, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_abuse_events_unresolved ON abuse_events(tenant_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_abuse_events_phone ON abuse_events(phone_number_called) WHERE phone_number_called IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_abuse_events_partner ON abuse_events(partner_id, created_at DESC) WHERE partner_id IS NOT NULL;

-- =============================================================================
-- CALL GUARD DECISIONS (AUDIT LOG)
-- =============================================================================
-- Records every call guard decision for compliance and debugging.
-- Critical for troubleshooting blocked calls and proving compliance.
-- =============================================================================

CREATE TABLE IF NOT EXISTS call_guard_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    partner_id UUID REFERENCES tenants(id),

    -- Request details
    call_id UUID,
    phone_number VARCHAR(50) NOT NULL,
    campaign_id UUID,
    user_id UUID,
    call_type TEXT DEFAULT 'outbound' CHECK (call_type IN ('outbound', 'inbound', 'transfer')),

    -- Decision
    decision TEXT NOT NULL CHECK (decision IN ('allow', 'block', 'queue', 'throttle')),

    -- Check results (JSON array of all checks performed)
    checks_performed JSONB NOT NULL DEFAULT '[]',

    -- Failed checks (if blocked)
    failed_checks JSONB DEFAULT '[]',

    -- Queue info (if queued)
    queue_position INTEGER,
    queue_wait_seconds INTEGER,

    -- Throttle info (if throttled)
    retry_after_seconds INTEGER,

    -- Timing
    total_latency_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_guard_decisions_tenant ON call_guard_decisions(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_guard_decisions_blocked ON call_guard_decisions(tenant_id, decision) WHERE decision != 'allow';
CREATE INDEX IF NOT EXISTS idx_call_guard_decisions_call ON call_guard_decisions(call_id) WHERE call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_call_guard_decisions_phone ON call_guard_decisions(phone_number);

-- =============================================================================
-- DO-NOT-CALL (DNC) LIST
-- =============================================================================
-- Per-tenant and global DNC lists for compliance.
-- =============================================================================

CREATE TABLE IF NOT EXISTS dnc_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE, -- NULL = global DNC

    phone_number VARCHAR(50) NOT NULL,
    normalized_number VARCHAR(50) NOT NULL, -- E.164 format

    -- DNC source/reason
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN (
        'manual',           -- Added by admin
        'customer_request', -- Customer asked not to be called
        'internal_list',    -- Company policy
        'government_list',  -- National DNC registry
        'litigation',       -- Legal hold
        'abuse_prevention'  -- Auto-added due to abuse
    )),

    -- Reason for DNC
    reason TEXT,

    -- Expiration (optional)
    expires_at TIMESTAMPTZ,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    UNIQUE(tenant_id, normalized_number)
);

CREATE INDEX IF NOT EXISTS idx_dnc_entries_tenant ON dnc_entries(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dnc_entries_number ON dnc_entries(normalized_number);
CREATE INDEX IF NOT EXISTS idx_dnc_entries_global ON dnc_entries(normalized_number) WHERE tenant_id IS NULL;

-- =============================================================================
-- SECTION 6.6: AUDIT LOGGING, SECURITY & SUSPENSION (Day 8)
-- =============================================================================

-- 1. WHITE_LABEL_PARTNERS
CREATE TABLE IF NOT EXISTS white_label_partners (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE, -- partner's own tenant
    company_name VARCHAR(255) NOT NULL,
    contact_email VARCHAR(255),
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'pending_deletion')),
    suspended_at TIMESTAMPTZ,
    suspended_by UUID REFERENCES user_profiles(id),
    suspension_reason TEXT,
    custom_domain VARCHAR(255),
    branding_config JSONB DEFAULT '{}',
    billing_config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_white_label_partners_status ON white_label_partners(status);

-- Add foreign key to tenants (already has the column from earlier Day 8 edit)
ALTER TABLE tenants 
    ADD CONSTRAINT fk_tenants_white_label_partner 
    FOREIGN KEY (white_label_partner_id) 
    REFERENCES white_label_partners(id) ON DELETE SET NULL;

-- 2. AUDIT_LOGS (Immutable security event log)
CREATE TABLE IF NOT EXISTS audit_logs (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    event_category VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL DEFAULT 'INFO',

    -- Actor
    actor_id UUID REFERENCES user_profiles(id),
    actor_type VARCHAR(20) NOT NULL DEFAULT 'user',
    actor_role VARCHAR(50),

    -- Target
    tenant_id UUID REFERENCES tenants(id),
    resource_type VARCHAR(50),
    resource_id UUID,

    -- Location/Device
    ip_address INET,
    user_agent TEXT,
    session_id UUID,
    device_fingerprint VARCHAR(64),
    country_code CHAR(2),

    -- Content
    action VARCHAR(100) NOT NULL,
    description TEXT,
    before_state JSONB,
    after_state JSONB,
    metadata JSONB,

    -- Integrity (tamper-evident)
    previous_hash VARCHAR(64),
    entry_hash VARCHAR(64),
    signature VARCHAR(128),

    -- Compliance
    compliance_tags VARCHAR(50)[],
    retention_until DATE NOT NULL DEFAULT (CURRENT_DATE + INTERVAL '365 days'),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_event_time ON audit_logs(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_id ON audit_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource ON audit_logs(resource_type, resource_id);

-- 3. SECURITY_EVENTS (High-priority alerts)
CREATE TABLE IF NOT EXISTS security_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Classification
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive', 'escalated')),

    -- Scope
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES user_profiles(id),
    session_id UUID,

    -- Detection
    detection_source VARCHAR(50) NOT NULL, 
    rule_id UUID, -- References abuse_detection_rules(id) if exists

    -- Details
    title VARCHAR(200) NOT NULL,
    description TEXT,
    evidence JSONB,

    -- Response
    assigned_to UUID REFERENCES user_profiles(id),
    resolved_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES user_profiles(id),
    resolution_notes TEXT,

    -- Automated response
    auto_action_taken VARCHAR(50),
    auto_action_success BOOLEAN,

    -- SLA tracking
    sla_deadline TIMESTAMPTZ,
    first_response_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_security_events_status ON security_events(status);
CREATE INDEX IF NOT EXISTS idx_security_events_severity ON security_events(severity);
CREATE INDEX IF NOT EXISTS idx_security_events_tenant ON security_events(tenant_id);

-- 4. SUSPENSION_EVENTS (Formal suspension history)
CREATE TABLE IF NOT EXISTS suspension_events (
    suspension_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Target
    target_type VARCHAR(20) NOT NULL CHECK (target_type IN ('user', 'tenant', 'partner')),
    target_id UUID NOT NULL,

    -- Suspension details
    suspension_type VARCHAR(30) NOT NULL, -- TEMPORARY, ADMIN, BILLING, ABUSE, COMPLIANCE, EMERGENCY
    reason_category VARCHAR(50) NOT NULL,
    reason_description TEXT NOT NULL,
    evidence JSONB,

    -- Timing
    suspended_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    suspended_until TIMESTAMPTZ, -- NULL = indefinite
    restored_at TIMESTAMPTZ,

    -- Actors
    suspended_by UUID REFERENCES user_profiles(id),
    restored_by UUID REFERENCES user_profiles(id),
    restore_reason TEXT,

    -- State tracking
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    propagated_services VARCHAR(50)[],
    propagation_confirmed_at TIMESTAMPTZ,

    -- Appeal workflow
    appeal_submitted_at TIMESTAMPTZ,
    appeal_reason TEXT,
    appeal_reviewed_by UUID REFERENCES user_profiles(id),
    appeal_decision VARCHAR(20), -- granted, denied, pending
    appeal_response TEXT,

    -- Audit reference
    audit_log_id UUID REFERENCES audit_logs(event_id)
);

CREATE INDEX IF NOT EXISTS idx_suspension_events_target ON suspension_events(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_suspension_events_active ON suspension_events(target_id, is_active) WHERE is_active = TRUE;

-- =============================================================================
-- CALL VELOCITY TRACKING (FOR PATTERN DETECTION)
-- =============================================================================
-- Lightweight table for tracking call velocity per tenant/number.
-- Used by abuse detection for real-time pattern analysis.
-- =============================================================================

CREATE TABLE IF NOT EXISTS call_velocity_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Time window (5-minute buckets for efficient aggregation)
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,

    -- Metrics
    total_calls INTEGER DEFAULT 0,
    unique_numbers INTEGER DEFAULT 0,
    international_calls INTEGER DEFAULT 0,
    premium_calls INTEGER DEFAULT 0,
    short_duration_calls INTEGER DEFAULT 0, -- < 10 seconds

    -- Top destinations (for pattern detection)
    top_destinations JSONB DEFAULT '[]', -- [{"country": "PK", "count": 50}, ...]

    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(tenant_id, window_start)
);

CREATE INDEX IF NOT EXISTS idx_call_velocity_tenant ON call_velocity_snapshots(tenant_id, window_start DESC);

-- =============================================================================
-- DEFAULT ABUSE DETECTION RULES (GLOBAL)
-- =============================================================================
-- Pre-populate with industry-standard rules based on CTIA/FCA guidance.
-- =============================================================================

INSERT INTO abuse_detection_rules (
    tenant_id,
    rule_name,
    rule_type,
    parameters,
    warn_threshold,
    block_threshold,
    action_on_trigger,
    analysis_window_minutes,
    priority
) VALUES
(NULL, 'Global Velocity Spike Detection', 'velocity_spike',
 '{"comparison_window_hours": 24, "spike_multiplier": 3.0, "min_baseline_calls": 10}'::jsonb,
 2, 5, 'throttle', 60, 10),
(NULL, 'Global Short Duration Pattern', 'short_duration_pattern',
 '{"duration_threshold_seconds": 10, "min_calls_in_window": 10, "window_minutes": 60}'::jsonb,
 5, 10, 'block', 60, 20),
(NULL, 'Global Repeat Number Detection', 'repeat_number',
 '{"max_calls_per_number": 3, "window_minutes": 60}'::jsonb,
 2, 3, 'block', 60, 30),
(NULL, 'Global Sequential Dialing Detection', 'sequential_dialing',
 '{"sequence_length": 5, "window_minutes": 30, "digit_variance_threshold": 2}'::jsonb,
 1, 2, 'block', 30, 40),
(NULL, 'Global Premium Rate Protection', 'premium_rate',
 '{"blocked_prefixes": ["+1900", "+4487", "+339", "+809"], "alert_on_first": true}'::jsonb,
 1, 3, 'block', 1440, 50),
(NULL, 'Global International Spike', 'international_spike',
 '{"comparison_window_hours": 24, "spike_multiplier": 5.0, "high_risk_countries": ["PK", "BD", "NG", "VN", "ID"]}'::jsonb,
 3, 5, 'throttle', 60, 60),
(NULL, 'Global After Hours Detection', 'after_hours',
 '{"allow_emergency": true}'::jsonb,
 10, 20, 'warn', 1440, 100),
(NULL, 'Global Toll Fraud Protection', 'toll_fraud',
 '{"known_fraud_patterns": ["wangiri", "irs_fraud"], "block_high_risk_destinations": true}'::jsonb,
 1, 2, 'block', 60, 5)
ON CONFLICT DO NOTHING;

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
        SELECT t.table_name
        FROM information_schema.tables t
        JOIN information_schema.columns c
          ON c.table_schema = t.table_schema
         AND c.table_name = t.table_name
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
          AND c.column_name = 'updated_at'
          AND t.table_name NOT IN ('call_events', 'recordings', 'invoices', 'usage_records')
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

-- Insert system roles
INSERT INTO roles (name, description, level, is_system_role, tenant_scoped) VALUES
    ('platform_admin', 'Full system access across all tenants.', 100, TRUE, FALSE),
    ('partner_admin', 'Access to multiple tenants within partner scope.', 80, TRUE, TRUE),
    ('tenant_admin', 'Full administrative access within a single tenant.', 60, TRUE, TRUE),
    ('user', 'Standard user within a tenant.', 40, TRUE, TRUE),
    ('readonly', 'View-only access within a tenant.', 20, TRUE, TRUE)
ON CONFLICT (name) DO NOTHING;

-- Insert system permissions
INSERT INTO permissions (name, description, resource, action, is_system) VALUES
    ('campaigns:create', 'Create new campaigns', 'campaigns', 'create', TRUE),
    ('campaigns:read', 'View campaigns and their data', 'campaigns', 'read', TRUE),
    ('campaigns:update', 'Modify existing campaigns', 'campaigns', 'update', TRUE),
    ('campaigns:delete', 'Delete campaigns', 'campaigns', 'delete', TRUE),
    ('campaigns:admin', 'Full administrative control over all campaigns', 'campaigns', 'admin', TRUE),
    ('users:create', 'Create new users within tenant', 'users', 'create', TRUE),
    ('users:read', 'View user profiles', 'users', 'read', TRUE),
    ('users:update', 'Update user profiles', 'users', 'update', TRUE),
    ('users:delete', 'Deactivate/delete users', 'users', 'delete', TRUE),
    ('users:manage', 'Manage user roles and permissions', 'users', 'manage', TRUE),
    ('tenants:read', 'View tenant information', 'tenants', 'read', TRUE),
    ('tenants:update', 'Update tenant settings', 'tenants', 'update', TRUE),
    ('tenants:admin', 'Full tenant administration', 'tenants', 'admin', TRUE),
    ('billing:read', 'View billing and usage information', 'billing', 'read', TRUE),
    ('billing:update', 'Modify billing settings', 'billing', 'update', TRUE),
    ('billing:admin', 'Full billing administration', 'billing', 'admin', TRUE),
    ('calls:create', 'Initiate calls', 'calls', 'create', TRUE),
    ('calls:read', 'View call history and recordings', 'calls', 'read', TRUE),
    ('calls:delete', 'Delete call records', 'calls', 'delete', TRUE),
    ('connectors:create', 'Add new connectors', 'connectors', 'create', TRUE),
    ('connectors:read', 'View connector configurations', 'connectors', 'read', TRUE),
    ('connectors:update', 'Modify connector settings', 'connectors', 'update', TRUE),
    ('connectors:delete', 'Remove connectors', 'connectors', 'delete', TRUE),
    ('analytics:read', 'View analytics and reports', 'analytics', 'read', TRUE),
    ('analytics:export', 'Export analytics data', 'analytics', 'export', TRUE),
    ('platform:admin', 'Full platform administration', 'platform', 'admin', TRUE),
    ('platform:tenants:manage', 'Manage all tenants', 'platform:tenants', 'manage', TRUE),
    ('platform:users:manage', 'Manage all users across tenants', 'platform:users', 'manage', TRUE),
    ('platform:settings:manage', 'Manage global platform settings', 'platform:settings', 'manage', TRUE)
ON CONFLICT (name) DO NOTHING;

-- Populate role_permissions
WITH role_ids AS (SELECT id, name FROM roles),
perm_ids AS (SELECT id, name, resource, action FROM permissions)
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM role_ids r CROSS JOIN perm_ids p
WHERE
    (r.name = 'readonly' AND (p.action = 'read' OR p.name = 'analytics:export')) OR
    (r.name = 'user' AND (p.action IN ('read', 'create', 'update') OR (p.resource = 'calls' AND p.action IN ('create', 'read')) OR p.name = 'analytics:export')) OR
    (r.name = 'tenant_admin' AND p.resource NOT LIKE 'platform:%') OR
    (r.name = 'partner_admin' AND (p.resource NOT LIKE 'platform:%' OR p.name = 'platform:tenants:read')) OR
    (r.name = 'platform_admin')
ON CONFLICT DO NOTHING;

-- =============================================================================
-- SECTION 10: VIEWS
-- =============================================================================

CREATE OR REPLACE VIEW user_effective_permissions AS
SELECT DISTINCT
    up.id AS user_id,
    p.id AS permission_id,
    p.name AS permission_name,
    p.resource,
    p.action,
    tu.tenant_id,
    r.name AS role_name,
    'role' AS grant_type
FROM user_profiles up
JOIN tenant_users tu ON tu.user_id = up.id AND tu.status = 'active'
JOIN roles r ON r.id = tu.role_id
JOIN role_permissions rp ON rp.role_id = r.id
JOIN permissions p ON p.id = rp.permission_id
UNION
SELECT
    up.id AS user_id,
    p.id AS permission_id,
    p.name AS permission_name,
    p.resource,
    p.action,
    up_perm.tenant_id,
    NULL AS role_name,
    'direct' AS grant_type
FROM user_profiles up
JOIN user_permissions up_perm ON up_perm.user_id = up.id
JOIN permissions p ON p.id = up_perm.permission_id
WHERE up_perm.expires_at IS NULL OR up_perm.expires_at > NOW();

CREATE OR REPLACE VIEW user_tenant_roles AS
SELECT
    up.id AS user_id,
    up.email,
    tu.tenant_id,
    t.business_name AS tenant_name,
    r.id AS role_id,
    r.name AS role_name,
    r.level AS role_level,
    tu.status,
    tu.is_primary
FROM user_profiles up
JOIN tenant_users tu ON tu.user_id = up.id
JOIN tenants t ON t.id = tu.tenant_id
JOIN roles r ON r.id = tu.role_id
WHERE tu.status IN ('active', 'pending');

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
