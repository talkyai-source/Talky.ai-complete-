-- =============================================================================
-- Assistant Agent System Migration
-- =============================================================================
--
-- This migration adds the Assistant Agent system including:
--   1. connectors - External integration registry (calendar/email/crm/drive/sms)
--   2. connector_accounts - OAuth tokens (encrypted)
--   3. assistant_conversations - Chat history with context
--   4. assistant_actions - Action audit log
--   5. meetings - Booked calendar events
--   6. reminders - Scheduled reminders
--
-- Run this AFTER the base schema.sql has been applied.
--
-- Created: January 5, 2026
-- Project: Talky.ai Voice Dialer - Assistant Agent
-- =============================================================================

-- =============================================================================
-- SECTION 1: CONNECTORS TABLE
-- External integrations (calendar, email, CRM, drive, SMS providers)
-- =============================================================================

CREATE TABLE IF NOT EXISTS connectors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,  -- calendar, email, crm, drive, sms
    provider VARCHAR(50) NOT NULL,  -- google, microsoft, twilio, salesforce, etc.
    name VARCHAR(100),
    status VARCHAR(50) DEFAULT 'pending',  -- pending, active, error, disconnected
    config JSONB DEFAULT '{}',  -- Provider-specific configuration
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for connectors
CREATE INDEX IF NOT EXISTS idx_connectors_tenant_id ON connectors(tenant_id);
CREATE INDEX IF NOT EXISTS idx_connectors_type ON connectors(type);
CREATE INDEX IF NOT EXISTS idx_connectors_status ON connectors(status);
CREATE INDEX IF NOT EXISTS idx_connectors_provider ON connectors(provider);

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_connectors_updated_at ON connectors;
CREATE TRIGGER update_connectors_updated_at BEFORE UPDATE ON connectors
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Comments
COMMENT ON TABLE connectors IS 'External service integrations (calendar, email, CRM, etc.)';
COMMENT ON COLUMN connectors.type IS 'Type: calendar, email, crm, drive, sms';
COMMENT ON COLUMN connectors.provider IS 'Provider: google, microsoft, twilio, salesforce, etc.';
COMMENT ON COLUMN connectors.config IS 'Provider-specific configuration as JSONB';

-- =============================================================================
-- SECTION 2: CONNECTOR ACCOUNTS TABLE
-- OAuth tokens and credentials (encrypted)
-- =============================================================================

CREATE TABLE IF NOT EXISTS connector_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connector_id UUID NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_account_id VARCHAR(255),  -- Provider's account/user identifier
    access_token_encrypted TEXT,  -- Fernet-encrypted OAuth access token
    refresh_token_encrypted TEXT,  -- Fernet-encrypted OAuth refresh token
    token_expires_at TIMESTAMPTZ,
    scopes TEXT[],  -- Granted OAuth scopes
    account_email VARCHAR(255),  -- Connected account email (for display)
    status VARCHAR(50) DEFAULT 'active',  -- active, expired, revoked
    last_refreshed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for connector_accounts
CREATE INDEX IF NOT EXISTS idx_connector_accounts_tenant_id ON connector_accounts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_connector_accounts_connector_id ON connector_accounts(connector_id);
CREATE INDEX IF NOT EXISTS idx_connector_accounts_status ON connector_accounts(status);
CREATE INDEX IF NOT EXISTS idx_connector_accounts_expires ON connector_accounts(token_expires_at);

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_connector_accounts_updated_at ON connector_accounts;
CREATE TRIGGER update_connector_accounts_updated_at BEFORE UPDATE ON connector_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Comments
COMMENT ON TABLE connector_accounts IS 'OAuth tokens and credentials for connectors (encrypted)';
COMMENT ON COLUMN connector_accounts.access_token_encrypted IS 'Fernet-encrypted OAuth access token';
COMMENT ON COLUMN connector_accounts.refresh_token_encrypted IS 'Fernet-encrypted OAuth refresh token';

-- =============================================================================
-- SECTION 3: ASSISTANT CONVERSATIONS TABLE
-- Chat history with multi-turn context
-- =============================================================================

CREATE TABLE IF NOT EXISTS assistant_conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    title VARCHAR(255),  -- Auto-generated or user-defined title
    messages JSONB DEFAULT '[]',  -- Array of {role, content, timestamp, tool_calls}
    context JSONB DEFAULT '{}',  -- Accumulated context for multi-turn conversations
    status VARCHAR(50) DEFAULT 'active',  -- active, archived
    message_count INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for assistant_conversations
CREATE INDEX IF NOT EXISTS idx_assistant_conversations_tenant_id ON assistant_conversations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_assistant_conversations_user_id ON assistant_conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_assistant_conversations_status ON assistant_conversations(status);
CREATE INDEX IF NOT EXISTS idx_assistant_conversations_last_message ON assistant_conversations(last_message_at DESC);

-- Comments
COMMENT ON TABLE assistant_conversations IS 'Chat history for assistant conversations with context';
COMMENT ON COLUMN assistant_conversations.messages IS 'JSONB array: [{role, content, timestamp, tool_calls}]';
COMMENT ON COLUMN assistant_conversations.context IS 'Accumulated context for multi-turn conversations';

-- =============================================================================
-- SECTION 4: ASSISTANT ACTIONS TABLE
-- Audit log for all actions triggered by the assistant
-- =============================================================================

CREATE TABLE IF NOT EXISTS assistant_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES assistant_conversations(id) ON DELETE SET NULL,
    user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
    call_id UUID REFERENCES calls(id) ON DELETE SET NULL,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    connector_id UUID REFERENCES connectors(id) ON DELETE SET NULL,
    type VARCHAR(50) NOT NULL,  -- send_email, send_sms, initiate_call, book_meeting, set_reminder, start_campaign
    status VARCHAR(50) DEFAULT 'pending',  -- pending, running, completed, failed, cancelled
    input_data JSONB,  -- Action input parameters
    output_data JSONB,  -- Action result/response
    error TEXT,  -- Error message if failed
    triggered_by VARCHAR(50),  -- chat, call_outcome, schedule, webhook
    scheduled_at TIMESTAMPTZ,  -- For scheduled actions
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER,  -- Execution duration in milliseconds
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for assistant_actions
CREATE INDEX IF NOT EXISTS idx_assistant_actions_tenant_id ON assistant_actions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_conversation_id ON assistant_actions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_type ON assistant_actions(type);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_status ON assistant_actions(status);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_triggered_by ON assistant_actions(triggered_by);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_created_at ON assistant_actions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_lead_id ON assistant_actions(lead_id);

-- Comments
COMMENT ON TABLE assistant_actions IS 'Audit log for all actions triggered by the assistant';
COMMENT ON COLUMN assistant_actions.type IS 'Action type: send_email, send_sms, initiate_call, book_meeting, set_reminder, start_campaign';
COMMENT ON COLUMN assistant_actions.triggered_by IS 'Trigger source: chat, call_outcome, schedule, webhook';
COMMENT ON COLUMN assistant_actions.input_data IS 'Action input parameters as JSONB';
COMMENT ON COLUMN assistant_actions.output_data IS 'Action result/response as JSONB';

-- =============================================================================
-- SECTION 5: MEETINGS TABLE
-- Calendar events booked through the assistant
-- =============================================================================

CREATE TABLE IF NOT EXISTS meetings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    call_id UUID REFERENCES calls(id) ON DELETE SET NULL,
    connector_id UUID REFERENCES connectors(id) ON DELETE SET NULL,
    action_id UUID REFERENCES assistant_actions(id) ON DELETE SET NULL,
    external_event_id VARCHAR(255),  -- Calendar provider's event ID
    title VARCHAR(255) NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    timezone VARCHAR(50) DEFAULT 'UTC',
    location TEXT,
    join_link TEXT,  -- Video conference link
    status VARCHAR(50) DEFAULT 'scheduled',  -- scheduled, confirmed, cancelled, completed, no_show
    attendees JSONB DEFAULT '[]',  -- Array of {email, name, status}
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for meetings
CREATE INDEX IF NOT EXISTS idx_meetings_tenant_id ON meetings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_meetings_lead_id ON meetings(lead_id);
CREATE INDEX IF NOT EXISTS idx_meetings_connector_id ON meetings(connector_id);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
CREATE INDEX IF NOT EXISTS idx_meetings_start_time ON meetings(start_time);
CREATE INDEX IF NOT EXISTS idx_meetings_external_event_id ON meetings(external_event_id);

-- Trigger for updated_at
DROP TRIGGER IF EXISTS update_meetings_updated_at ON meetings;
CREATE TRIGGER update_meetings_updated_at BEFORE UPDATE ON meetings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Comments
COMMENT ON TABLE meetings IS 'Calendar events booked through the assistant';
COMMENT ON COLUMN meetings.external_event_id IS 'Calendar provider event ID for syncing';
COMMENT ON COLUMN meetings.attendees IS 'JSONB array: [{email, name, status}]';

-- =============================================================================
-- SECTION 6: REMINDERS TABLE
-- Scheduled reminders (email, SMS, push)
-- =============================================================================

CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    meeting_id UUID REFERENCES meetings(id) ON DELETE CASCADE,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    action_id UUID REFERENCES assistant_actions(id) ON DELETE SET NULL,
    type VARCHAR(50) NOT NULL,  -- email, sms, push
    scheduled_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    status VARCHAR(50) DEFAULT 'pending',  -- pending, sent, failed, cancelled
    content JSONB,  -- Reminder content {subject, body, template}
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for reminders
CREATE INDEX IF NOT EXISTS idx_reminders_tenant_id ON reminders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_reminders_meeting_id ON reminders(meeting_id);
CREATE INDEX IF NOT EXISTS idx_reminders_lead_id ON reminders(lead_id);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
CREATE INDEX IF NOT EXISTS idx_reminders_scheduled_at ON reminders(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(status, scheduled_at) 
    WHERE status = 'pending';

-- Comments
COMMENT ON TABLE reminders IS 'Scheduled reminders for meetings and leads';
COMMENT ON COLUMN reminders.type IS 'Reminder type: email, sms, push';
COMMENT ON COLUMN reminders.content IS 'Reminder content as JSONB: {subject, body, template}';

-- =============================================================================
-- SECTION 7: ROW LEVEL SECURITY (RLS)
-- =============================================================================

-- Enable RLS on all new tables
ALTER TABLE connectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE assistant_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE assistant_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE reminders ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 7.1 CONNECTORS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view connectors in their tenant" ON connectors;
CREATE POLICY "Users can view connectors in their tenant" ON connectors
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage connectors in their tenant" ON connectors;
CREATE POLICY "Users can manage connectors in their tenant" ON connectors
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all connectors" ON connectors;
CREATE POLICY "Service role can manage all connectors" ON connectors
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 7.2 CONNECTOR ACCOUNTS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view connector_accounts in their tenant" ON connector_accounts;
CREATE POLICY "Users can view connector_accounts in their tenant" ON connector_accounts
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all connector_accounts" ON connector_accounts;
CREATE POLICY "Service role can manage all connector_accounts" ON connector_accounts
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 7.3 ASSISTANT CONVERSATIONS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view conversations in their tenant" ON assistant_conversations;
CREATE POLICY "Users can view conversations in their tenant" ON assistant_conversations
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage their own conversations" ON assistant_conversations;
CREATE POLICY "Users can manage their own conversations" ON assistant_conversations
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
        AND (user_id = auth.uid() OR user_id IS NULL)
    );

DROP POLICY IF EXISTS "Service role can manage all conversations" ON assistant_conversations;
CREATE POLICY "Service role can manage all conversations" ON assistant_conversations
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 7.4 ASSISTANT ACTIONS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view actions in their tenant" ON assistant_actions;
CREATE POLICY "Users can view actions in their tenant" ON assistant_actions
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all actions" ON assistant_actions;
CREATE POLICY "Service role can manage all actions" ON assistant_actions
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 7.5 MEETINGS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view meetings in their tenant" ON meetings;
CREATE POLICY "Users can view meetings in their tenant" ON meetings
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage meetings in their tenant" ON meetings;
CREATE POLICY "Users can manage meetings in their tenant" ON meetings
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all meetings" ON meetings;
CREATE POLICY "Service role can manage all meetings" ON meetings
    FOR ALL USING (auth.role() = 'service_role');

-- -----------------------------------------------------------------------------
-- 7.6 REMINDERS POLICIES
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view reminders in their tenant" ON reminders;
CREATE POLICY "Users can view reminders in their tenant" ON reminders
    FOR SELECT USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Users can manage reminders in their tenant" ON reminders;
CREATE POLICY "Users can manage reminders in their tenant" ON reminders
    FOR ALL USING (
        tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
    );

DROP POLICY IF EXISTS "Service role can manage all reminders" ON reminders;
CREATE POLICY "Service role can manage all reminders" ON reminders
    FOR ALL USING (auth.role() = 'service_role');

-- =============================================================================
-- SUCCESS NOTIFICATION
-- =============================================================================

DO $$
BEGIN
    RAISE NOTICE '=============================================================================';
    RAISE NOTICE 'Assistant Agent Migration completed successfully!';
    RAISE NOTICE '';
    RAISE NOTICE 'Tables created:';
    RAISE NOTICE '  - connectors: External integration registry';
    RAISE NOTICE '  - connector_accounts: OAuth tokens (encrypted)';
    RAISE NOTICE '  - assistant_conversations: Chat history with context';
    RAISE NOTICE '  - assistant_actions: Action audit log';
    RAISE NOTICE '  - meetings: Booked calendar events';
    RAISE NOTICE '  - reminders: Scheduled reminders';
    RAISE NOTICE '';
    RAISE NOTICE 'RLS policies enabled on all tables.';
    RAISE NOTICE '';
    RAISE NOTICE 'NEXT STEPS:';
    RAISE NOTICE '  1. Create domain models (connector.py, assistant_action.py, etc.)';
    RAISE NOTICE '  2. Implement LangGraph agent with tools';
    RAISE NOTICE '  3. Create WebSocket endpoint for chat';
    RAISE NOTICE '=============================================================================';
END $$;
