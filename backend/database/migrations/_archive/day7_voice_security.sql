-- =============================================================================
-- Day 7: Voice Security + Abuse Protection
-- =============================================================================
-- CTIA Anti-Fraud Best Practices compliant telephony security layer.
-- Implements unified Call Guard, abuse detection, and toll fraud prevention.
--
-- Dependencies:
--   - Day 1-6 security foundations
--   - WS-I Telephony Rate Limiter (tenant_telephony_threshold_policies)
--   - Day 9 Telephony Concurrency Limiter
--
-- References:
--   - CTIA Anti-Fraud Best Practices (Cellular Telecommunications Industry Association)
--   - OWASP Top 10 for Telephony
--   - Twilio Security Guidelines
--   - FCA (UK) Telecom Fraud Guidance (FG 19/6)
-- =============================================================================

-- =============================================================================
-- 1. TENANT CALL LIMITS
-- =============================================================================
-- Comprehensive per-tenant limits for voice operations including rate limits,
-- concurrency, spend caps, and geographic restrictions.
-- =============================================================================

CREATE TABLE tenant_call_limits (
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

CREATE INDEX idx_tenant_call_limits_tenant ON tenant_call_limits(tenant_id) WHERE is_active = TRUE;
CREATE INDEX idx_tenant_call_limits_effective ON tenant_call_limits(tenant_id, effective_from, effective_until);

-- =============================================================================
-- 2. PARTNER AGGREGATE LIMITS (Multi-tenant Reseller Controls)
-- =============================================================================
-- For partners/resellers managing multiple sub-tenants.
-- Enforces aggregate limits across all child tenants.
-- =============================================================================

CREATE TABLE partner_limits (
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

CREATE INDEX idx_partner_limits_partner ON partner_limits(partner_id) WHERE is_active = TRUE;

-- =============================================================================
-- 3. TENANT-PARTNER RELATIONSHIP
-- =============================================================================
-- Links child tenants to their partner for aggregate limit enforcement
-- =============================================================================

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS partner_id UUID REFERENCES tenants(id);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_partner BOOLEAN DEFAULT FALSE;

CREATE INDEX idx_tenants_partner ON tenants(partner_id) WHERE partner_id IS NOT NULL;
CREATE INDEX idx_tenants_is_partner ON tenants(is_partner) WHERE is_partner = TRUE;

-- =============================================================================
-- 4. ABUSE DETECTION RULES
-- =============================================================================
-- Configurable rules for real-time fraud and abuse detection.
-- Rules can be global (tenant_id = NULL) or tenant-specific.
-- =============================================================================

CREATE TABLE abuse_detection_rules (
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
    /*
    Example parameters by rule_type:

    velocity_spike: {
        "comparison_window_hours": 24,
        "spike_multiplier": 3.0,
        "min_baseline_calls": 10
    }

    short_duration_pattern: {
        "duration_threshold_seconds": 10,
        "min_calls_in_window": 5,
        "window_minutes": 60
    }

    repeat_number: {
        "max_calls_per_number": 3,
        "window_minutes": 60
    }

    sequential_dialing: {
        "sequence_length": 3,
        "window_minutes": 10,
        "digit_variance_threshold": 2
    }

    premium_rate: {
        "blocked_prefixes": ["+1900", "+4487", "+339"]
    }

    after_hours: {
        "allow_emergency": true
    }
    */

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

CREATE INDEX idx_abuse_rules_tenant ON abuse_detection_rules(tenant_id) WHERE is_active = TRUE;
CREATE INDEX idx_abuse_rules_type ON abuse_detection_rules(rule_type, is_active);

-- =============================================================================
-- 5. ABUSE EVENTS (AUDIT TRAIL)
-- =============================================================================
-- Records of detected abuse patterns and actions taken.
-- Used for fraud investigation and compliance reporting.
-- =============================================================================

CREATE TABLE abuse_events (
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

CREATE INDEX idx_abuse_events_tenant ON abuse_events(tenant_id, created_at DESC);
CREATE INDEX idx_abuse_events_type ON abuse_events(event_type, severity, created_at DESC);
CREATE INDEX idx_abuse_events_unresolved ON abuse_events(tenant_id) WHERE resolved_at IS NULL;
CREATE INDEX idx_abuse_events_phone ON abuse_events(phone_number_called) WHERE phone_number_called IS NOT NULL;
CREATE INDEX idx_abuse_events_partner ON abuse_events(partner_id, created_at DESC) WHERE partner_id IS NOT NULL;

-- =============================================================================
-- 6. CALL GUARD DECISIONS (AUDIT LOG)
-- =============================================================================
-- Records every call guard decision for compliance and debugging.
-- Critical for troubleshooting blocked calls and proving compliance.
-- =============================================================================

CREATE TABLE call_guard_decisions (
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
    /*
    [
      {"check": "tenant_active", "passed": true, "latency_ms": 5},
      {"check": "rate_limit", "passed": false, "reason": "60/min exceeded", "latency_ms": 12}
    ]
    */

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

CREATE INDEX idx_call_guard_decisions_tenant ON call_guard_decisions(tenant_id, created_at DESC);
CREATE INDEX idx_call_guard_decisions_blocked ON call_guard_decisions(tenant_id, decision) WHERE decision != 'allow';
CREATE INDEX idx_call_guard_decisions_call ON call_guard_decisions(call_id) WHERE call_id IS NOT NULL;
CREATE INDEX idx_call_guard_decisions_phone ON call_guard_decisions(phone_number);

-- =============================================================================
-- 7. DO-NOT-CALL (DNC) LIST
-- =============================================================================
-- Per-tenant and global DNC lists for compliance.
-- =============================================================================

CREATE TABLE dnc_entries (
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

CREATE INDEX idx_dnc_entries_tenant ON dnc_entries(tenant_id);
CREATE INDEX idx_dnc_entries_number ON dnc_entries(normalized_number);
CREATE INDEX idx_dnc_entries_global ON dnc_entries(normalized_number) WHERE tenant_id IS NULL;

-- =============================================================================
-- 8. CALL VELOCITY TRACKING (FOR PATTERN DETECTION)
-- =============================================================================
-- Lightweight table for tracking call velocity per tenant/number.
-- Used by abuse detection for real-time pattern analysis.
-- =============================================================================

CREATE TABLE call_velocity_snapshots (
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

CREATE INDEX idx_call_velocity_tenant ON call_velocity_snapshots(tenant_id, window_start DESC);

-- =============================================================================
-- 9. DEFAULT ABUSE DETECTION RULES (GLOBAL)
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
-- Velocity spike detection
(NULL, 'Global Velocity Spike Detection', 'velocity_spike',
 '{"comparison_window_hours": 24, "spike_multiplier": 3.0, "min_baseline_calls": 10}'::jsonb,
 2, 5, 'throttle', 60, 10),

-- Short duration pattern (call pumping)
(NULL, 'Global Short Duration Pattern', 'short_duration_pattern',
 '{"duration_threshold_seconds": 10, "min_calls_in_window": 10, "window_minutes": 60}'::jsonb,
 5, 10, 'block', 60, 20),

-- Repeat number (harassment)
(NULL, 'Global Repeat Number Detection', 'repeat_number',
 '{"max_calls_per_number": 3, "window_minutes": 60}'::jsonb,
 2, 3, 'block', 60, 30),

-- Sequential dialing (war dialing)
(NULL, 'Global Sequential Dialing Detection', 'sequential_dialing',
 '{"sequence_length": 5, "window_minutes": 30, "digit_variance_threshold": 2}'::jsonb,
 1, 2, 'block', 30, 40),

-- Premium rate fraud
(NULL, 'Global Premium Rate Protection', 'premium_rate',
 '{"blocked_prefixes": ["+1900", "+4487", "+339", "+809"], "alert_on_first": true}'::jsonb,
 1, 3, 'block', 1440, 50),

-- International spike
(NULL, 'Global International Spike', 'international_spike',
 '{"comparison_window_hours": 24, "spike_multiplier": 5.0, "high_risk_countries": ["PK", "BD", "NG", "VN", "ID"]}'::jsonb,
 3, 5, 'throttle', 60, 60),

-- After hours calling
(NULL, 'Global After Hours Detection', 'after_hours',
 '{"allow_emergency": true}'::jsonb,
 10, 20, 'warn', 1440, 100),

-- Toll fraud (IRSF)
(NULL, 'Global Toll Fraud Protection', 'toll_fraud',
 '{"known_fraud_patterns": ["wangiri", "irs_fraud"], "block_high_risk_destinations": true}'::jsonb,
 1, 2, 'block', 60, 5);

-- =============================================================================
-- 10. TRIGGERS FOR UPDATED_AT
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_tenant_call_limits_updated_at
    BEFORE UPDATE ON tenant_call_limits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_partner_limits_updated_at
    BEFORE UPDATE ON partner_limits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_abuse_detection_rules_updated_at
    BEFORE UPDATE ON abuse_detection_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 11. PARTITIONING SETUP (for high-volume tables)
-- =============================================================================
-- For production with high call volumes, partition by time ranges.
-- Uncomment when needed.

/*
-- Partition abuse_events by month
CREATE TABLE abuse_events_2026_03 PARTITION OF abuse_events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Partition call_guard_decisions by month
CREATE TABLE call_guard_decisions_2026_03 PARTITION OF call_guard_decisions
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Partition call_velocity_snapshots by month
CREATE TABLE call_velocity_snapshots_2026_03 PARTITION OF call_velocity_snapshots
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
*/

-- =============================================================================
-- 12. RETENTION POLICY COMMENTS
-- =============================================================================
-- Recommended retention:
--   - call_guard_decisions: 90 days (compliance)
--   - abuse_events: 2 years (fraud investigation)
--   - call_velocity_snapshots: 30 days (rolling analysis)
--   - dnc_entries: Permanent (compliance)
-- =============================================================================

COMMENT ON TABLE tenant_call_limits IS 'Per-tenant voice call limits and restrictions (Day 7)';
COMMENT ON TABLE partner_limits IS 'Multi-tenant partner/reseller aggregate limits (Day 7)';
COMMENT ON TABLE abuse_detection_rules IS 'Configurable fraud and abuse detection rules (Day 7)';
COMMENT ON TABLE abuse_events IS 'Audit trail of detected abuse patterns (Day 7)';
COMMENT ON TABLE call_guard_decisions IS 'Audit log of all call guard decisions (Day 7)';
COMMENT ON TABLE dnc_entries IS 'Do-not-call list entries per tenant and global (Day 7)';
COMMENT ON TABLE call_velocity_snapshots IS 'Call velocity metrics for pattern detection (Day 7)';
