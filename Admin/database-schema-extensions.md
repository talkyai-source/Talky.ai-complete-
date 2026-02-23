# Admin Panel Database Schema Extensions

## Overview
This document contains the SQL schema extensions required for the Talky.ai admin panel functionality. These extensions build upon the existing database schema to support comprehensive administrative features.

## Schema Extensions

### 1. Admin Audit Log Table

```sql
-- Comprehensive audit trail for all admin actions
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    action_type VARCHAR(100) NOT NULL,
    target_type VARCHAR(50), -- tenant, user, system, provider
    target_id UUID,
    target_description TEXT,
    action_details JSONB DEFAULT '{}',
    previous_values JSONB DEFAULT '{}',
    new_values JSONB DEFAULT '{}',
    ip_address INET,
    user_agent TEXT,
    session_id VARCHAR(255),
    request_id UUID,
    outcome_status VARCHAR(50) DEFAULT 'success', -- success, failed, partial
    error_message TEXT,
    execution_time_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin_user_id ON admin_audit_log(admin_user_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action_type ON admin_audit_log(action_type);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target_type ON admin_audit_log(target_type);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at ON admin_audit_log(created_at DESC);
```

### 2. System Alerts Table

```sql
-- System-wide alerts and notifications for admins
CREATE TABLE IF NOT EXISTS system_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_type VARCHAR(50) NOT NULL, -- system_error, security, performance, provider_down
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    category VARCHAR(50) NOT NULL, -- availability, security, performance, capacity
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    affected_services TEXT[] DEFAULT '{}',
    affected_tenants UUID[] DEFAULT '{}',
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by UUID REFERENCES auth.users(id),
    acknowledged_at TIMESTAMPTZ,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_by UUID REFERENCES auth.users(id),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_system_alerts_severity ON system_alerts(severity);
CREATE INDEX IF NOT EXISTS idx_system_alerts_acknowledged ON system_alerts(acknowledged);
CREATE INDEX IF NOT EXISTS idx_system_alerts_resolved ON system_alerts(resolved);
CREATE INDEX IF NOT EXISTS idx_system_alerts_created_at ON system_alerts(created_at DESC);
```

### 3. Provider Health Monitoring Table

```sql
-- Real-time health status of all service providers
CREATE TABLE IF NOT EXISTS provider_health (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_type VARCHAR(50) NOT NULL, -- stt, tts, llm, telephony, storage
    provider_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('healthy', 'degraded', 'down', 'maintenance')),
    status_message TEXT,
    latency_ms INTEGER,
    error_rate DECIMAL(5,2) DEFAULT 0 CHECK (error_rate >= 0 AND error_rate <= 100),
    success_rate DECIMAL(5,2) DEFAULT 100 CHECK (success_rate >= 0 AND success_rate <= 100),
    total_requests BIGINT DEFAULT 0,
    successful_requests BIGINT DEFAULT 0,
    failed_requests BIGINT DEFAULT 0,
    last_successful_request TIMESTAMPTZ,
    last_failed_request TIMESTAMPTZ,
    last_error_message TEXT,
    health_check_config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider_type, provider_name)
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_provider_health_provider_type ON provider_health(provider_type);
CREATE INDEX IF NOT EXISTS idx_provider_health_status ON provider_health(status);
CREATE INDEX IF NOT EXISTS idx_provider_health_updated_at ON provider_health(updated_at DESC);
```

### 4. Admin Sessions Table

```sql
-- Track admin user sessions for security monitoring
CREATE TABLE IF NOT EXISTS admin_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_token VARCHAR(255) NOT NULL UNIQUE,
    ip_address INET NOT NULL,
    user_agent TEXT,
    login_method VARCHAR(50), -- password, oauth, 2fa
    two_factor_verified BOOLEAN DEFAULT FALSE,
    session_metadata JSONB DEFAULT '{}',
    last_activity TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    revoked_at TIMESTAMPTZ,
    revoked_reason VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_admin_sessions_user_id ON admin_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires_at ON admin_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_last_activity ON admin_sessions(last_activity DESC);
```

### 5. System Metrics Table

```sql
-- Store system performance metrics for analytics
CREATE TABLE IF NOT EXISTS system_metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    metric_name VARCHAR(100) NOT NULL,
    metric_type VARCHAR(50) NOT NULL, -- counter, gauge, histogram
    metric_value DECIMAL(15,6) NOT NULL,
    metric_unit VARCHAR(20), -- requests, milliseconds, percentage, bytes
    metric_labels JSONB DEFAULT '{}',
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    provider_type VARCHAR(50),
    provider_name VARCHAR(100),
    collection_timestamp TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_system_metrics_metric_name ON system_metrics(metric_name);
CREATE INDEX IF NOT EXISTS idx_system_metrics_collection_timestamp ON system_metrics(collection_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_metrics_tenant_id ON system_metrics(tenant_id);
```

### 6. Admin Notifications Table

```sql
-- Admin-specific notifications and announcements
CREATE TABLE IF NOT EXISTS admin_notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    notification_type VARCHAR(50) NOT NULL, -- info, warning, critical, maintenance
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    action_url VARCHAR(500),
    action_text VARCHAR(100),
    target_audience VARCHAR(50) DEFAULT 'all', -- all, super_admin, support
    priority INTEGER DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    read_by UUID[] DEFAULT '{}',
    dismissed_by UUID[] DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_admin_notifications_type ON admin_notifications(notification_type);
CREATE INDEX IF NOT EXISTS idx_admin_notifications_priority ON admin_notifications(priority DESC);
CREATE INDEX IF NOT EXISTS idx_admin_notifications_expires_at ON admin_notifications(expires_at);
```

## Modifications to Existing Tables

### 1. Tenants Table Extensions

```sql
-- Add suspension and admin fields to tenants
ALTER TABLE tenants 
ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'pending_deletion')),
ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS suspended_by UUID REFERENCES auth.users(id),
ADD COLUMN IF NOT EXISTS suspension_reason TEXT,
ADD COLUMN IF NOT EXISTS suspend_until TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS admin_notes TEXT,
ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- Create indexes for new columns
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_suspended_at ON tenants(suspended_at);
```

### 2. User Profiles Table Extensions

```sql
-- Add admin fields to user_profiles
ALTER TABLE user_profiles 
ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'pending_deletion')),
ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS suspended_by UUID REFERENCES auth.users(id),
ADD COLUMN IF NOT EXISTS suspension_reason TEXT,
ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS login_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS failed_login_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_login_ip INET,
ADD COLUMN IF NOT EXISTS last_login_user_agent TEXT,
ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS two_factor_secret VARCHAR(255),
ADD COLUMN IF NOT EXISTS admin_notes TEXT,
ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- Create indexes for new columns
CREATE INDEX IF NOT EXISTS idx_user_profiles_status ON user_profiles(status);
CREATE INDEX IF NOT EXISTS idx_user_profiles_last_active ON user_profiles(last_active_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_profiles_two_factor ON user_profiles(two_factor_enabled);
```

## Row Level Security (RLS) Policies

### Enable RLS on New Tables

```sql
-- Enable RLS on admin-specific tables
ALTER TABLE admin_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_notifications ENABLE ROW LEVEL SECURITY;
```

### Admin Audit Log Policies

```sql
-- Only admins can view audit logs, and only their own unless super admin
DROP POLICY IF EXISTS "Admins can view own audit logs" ON admin_audit_log;
CREATE POLICY "Admins can view own audit logs" ON admin_audit_log
    FOR SELECT USING (
        auth.uid() = admin_user_id OR 
        EXISTS (
            SELECT 1 FROM user_profiles 
            WHERE id = auth.uid() 
            AND role = 'super_admin'
        )
    );
```

### System Alerts Policies

```sql
-- Admins can view all alerts
DROP POLICY IF EXISTS "Admins can view system alerts" ON system_alerts;
CREATE POLICY "Admins can view system alerts" ON system_alerts
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM user_profiles 
            WHERE id = auth.uid() 
            AND role IN ('admin', 'super_admin')
        )
    );
```

## Helper Functions

### Admin Action Logging Function

```sql
CREATE OR REPLACE FUNCTION log_admin_action(
    p_admin_user_id UUID,
    p_action_type VARCHAR(100),
    p_target_type VARCHAR(50),
    p_target_id UUID,
    p_target_description TEXT,
    p_action_details JSONB,
    p_previous_values JSONB,
    p_new_values JSONB,
    p_ip_address INET,
    p_user_agent TEXT,
    p_session_id VARCHAR(255),
    p_request_id UUID,
    p_outcome_status VARCHAR(50),
    p_error_message TEXT,
    p_execution_time_ms INTEGER
)
RETURNS UUID AS $$
DECLARE
    v_log_id UUID;
BEGIN
    INSERT INTO admin_audit_log (
        admin_user_id, action_type, target_type, target_id, target_description,
        action_details, previous_values, new_values, ip_address, user_agent,
        session_id, request_id, outcome_status, error_message, execution_time_ms
    ) VALUES (
        p_admin_user_id, p_action_type, p_target_type, p_target_id, p_target_description,
        p_action_details, p_previous_values, p_new_values, p_ip_address, p_user_agent,
        p_session_id, p_request_id, p_outcome_status, p_error_message, p_execution_time_ms
    ) RETURNING id INTO v_log_id;
    
    RETURN v_log_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
```

### System Alert Creation Function

```sql
CREATE OR REPLACE FUNCTION create_system_alert(
    p_alert_type VARCHAR(50),
    p_severity VARCHAR(20),
    p_category VARCHAR(50),
    p_title VARCHAR(255),
    p_message TEXT,
    p_metadata JSONB,
    p_affected_services TEXT[],
    p_affected_tenants UUID[]
)
RETURNS UUID AS $$
DECLARE
    v_alert_id UUID;
BEGIN
    INSERT INTO system_alerts (
        alert_type, severity, category, title, message, metadata,
        affected_services, affected_tenants
    ) VALUES (
        p_alert_type, p_severity, p_category, p_title, p_message, p_metadata,
        p_affected_services, p_affected_tenants
    ) RETURNING id INTO v_alert_id;
    
    RETURN v_alert_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
```

## Views for Admin Dashboard

### Admin Dashboard Overview View

```sql
CREATE OR REPLACE VIEW admin_dashboard_overview AS
SELECT 
    (SELECT COUNT(*) FROM tenants WHERE status = 'active') as active_tenants,
    (SELECT COUNT(*) FROM tenants WHERE status = 'suspended') as suspended_tenants,
    (SELECT COUNT(*) FROM user_profiles WHERE status = 'active') as active_users,
    (SELECT COUNT(*) FROM user_profiles WHERE status = 'suspended') as suspended_users,
    (SELECT COUNT(*) FROM calls WHERE created_at >= NOW() - INTERVAL '24 hours') as calls_last_24h,
    (SELECT COUNT(*) FROM calls WHERE created_at >= NOW() - INTERVAL '7 days') as calls_last_7d,
    (SELECT COALESCE(SUM(duration_seconds), 0) FROM calls WHERE created_at >= NOW() - INTERVAL '30 days') as total_minutes_last_30d,
    (SELECT COUNT(*) FROM campaigns WHERE status = 'running') as active_campaigns,
    (SELECT COUNT(*) FROM system_alerts WHERE resolved = FALSE) as unresolved_alerts,
    (SELECT COUNT(*) FROM system_alerts WHERE resolved = FALSE AND severity = 'critical') as critical_alerts;
```

### Tenant Summary View

```sql
CREATE OR REPLACE VIEW tenant_summary AS
SELECT 
    t.id,
    t.business_name,
    t.plan_id,
    p.name as plan_name,
    t.minutes_used,
    t.minutes_allocated,
    t.status,
    t.created_at,
    t.updated_at,
    COALESCE(u.user_count, 0) as user_count,
    COALESCE(c.campaign_count, 0) as campaign_count,
    COALESCE(calls.call_count, 0) as call_count,
    COALESCE(calls.total_minutes, 0) as total_minutes_used,
    COALESCE(recent_activity.last_activity, t.created_at) as last_activity
FROM tenants t
LEFT JOIN plans p ON t.plan_id = p.id
LEFT JOIN (
    SELECT tenant_id, COUNT(*) as user_count 
    FROM user_profiles 
    WHERE status = 'active' 
    GROUP BY tenant_id
) u ON t.id = u.tenant_id
LEFT JOIN (
    SELECT tenant_id, COUNT(*) as campaign_count 
    FROM campaigns 
    GROUP BY tenant_id
) c ON t.id = c.tenant_id
LEFT JOIN (
    SELECT tenant_id, COUNT(*) as call_count, COALESCE(SUM(duration_seconds), 0) as total_minutes 
    FROM calls 
    GROUP BY tenant_id
) calls ON t.id = calls.tenant_id
LEFT JOIN (
    SELECT tenant_id, MAX(created_at) as last_activity 
    FROM calls 
    GROUP BY tenant_id
) recent_activity ON t.id = recent_activity.tenant_id;
```

## Performance Optimization

### Composite Indexes

```sql
-- Create composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_composite 
ON admin_audit_log(admin_user_id, action_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_system_alerts_composite 
ON system_alerts(severity, resolved, acknowledged, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_provider_health_composite 
ON provider_health(provider_type, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_system_metrics_composite 
ON system_metrics(metric_name, collection_timestamp DESC);
```

### Partial Indexes

```sql
-- Create partial indexes for filtered queries
CREATE INDEX IF NOT EXISTS idx_system_alerts_unresolved 
ON system_alerts(created_at DESC) 
WHERE resolved = FALSE;

CREATE INDEX IF NOT EXISTS idx_admin_sessions_active 
ON admin_sessions(user_id, last_activity DESC) 
WHERE revoked = FALSE AND expires_at > NOW();

CREATE INDEX IF NOT EXISTS idx_tenants_active 
ON tenants(business_name) 
WHERE status = 'active';
```

## Maintenance Functions

### Cleanup Old Audit Logs

```sql
CREATE OR REPLACE FUNCTION cleanup_old_audit_logs(p_retention_days INTEGER DEFAULT 90)
RETURNS INTEGER AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    DELETE FROM admin_audit_log 
    WHERE created_at < NOW() - INTERVAL '1 day' * p_retention_days
    RETURNING COUNT(*) INTO v_deleted_count;
    
    RETURN v_deleted_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
```

### Cleanup Old Metrics

```sql
CREATE OR REPLACE FUNCTION cleanup_old_metrics(p_retention_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    DELETE FROM system_metrics 
    WHERE collection_timestamp < NOW() - INTERVAL '1 day' * p_retention_days
    RETURNING COUNT(*) INTO v_deleted_count;
    
    RETURN v_deleted_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
```

## Installation Instructions

### Step 1: Run the Schema Extensions
Execute the SQL commands in this document in the following order:
1. Create new tables
2. Modify existing tables
3. Create indexes
4. Create functions
5. Create views
6. Set up RLS policies

### Step 2: Verify Installation
Run these verification queries:

```sql
-- Check if all tables were created
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('admin_audit_log', 'system_alerts', 'provider_health', 'admin_sessions', 'system_metrics', 'admin_notifications');

-- Check if views were created
SELECT viewname 
FROM pg_views 
WHERE schemaname = 'public' 
AND viewname IN ('admin_dashboard_overview', 'tenant_summary', 'user_activity_summary');

-- Check if functions were created
SELECT proname 
FROM pg_proc 
WHERE proname IN ('log_admin_action', 'create_system_alert', 'cleanup_old_audit_logs', 'cleanup_old_metrics');
```

### Step 3: Test Basic Functionality

```sql
-- Test admin action logging
SELECT log_admin_action(
    '00000000-0000-0000-0000-000000000000', -- Replace with actual admin user ID
    'test_action',
    'system',
    NULL,
    'Test action description',
    '{"test": "data"}'::jsonb,
    '{}'::jsonb,
    '{"new": "data"}'::jsonb,
    '192.168.1.1'::inet,
    'Test User Agent',
    'test-session-id',
    gen_random_uuid(),
    'success',
    NULL,
    100
);

-- Test system alert creation
SELECT create_system_alert(
    'test_alert',
    'low',
    'test',
    'Test Alert',
    'This is a test alert',
    '{"test": "metadata"}'::jsonb,
    ARRAY['test_service'],
    ARRAY[]::UUID[]
);
```

## Security Considerations

### 1. Row Level Security (RLS)
All admin-specific tables have RLS policies that restrict access based on user roles:
- Only admin users can access admin functionality
- Super admins have elevated permissions
- Service role can bypass RLS for system operations

### 2. Audit Logging
All admin actions are automatically logged with:
- User identification
- IP address and user agent
- Action details and outcomes
- Execution time for performance monitoring

### 3. Session Management
Admin sessions are tracked with:
- Secure session tokens
- IP address validation
- Expiration times
- Activity monitoring
- Revocation capabilities

## Performance Considerations

### 1. Indexing Strategy
- Composite indexes for common query patterns
- Partial indexes for filtered queries
- Descending indexes for time-series data
- UUID indexes for relationship queries

### 2. Data Retention
- Audit logs: 90 days default retention
- System metrics: 30 days default retention
- Configurable retention periods
- Automated cleanup functions

### 3. Query Optimization
- Materialized views for complex aggregations
- Partitioning for large tables (future enhancement)
- Connection pooling for high concurrency
- Query result caching (application level)

## Maintenance and Monitoring

### Regular Maintenance Tasks
1. **Daily**: Run cleanup functions for old data
2. **Weekly**: Analyze query performance and optimize indexes
3. **Monthly**: Review audit logs for security incidents
4. **Quarterly**: Archive old data and optimize database

### Monitoring Queries

```sql
-- Check for unresolved critical alerts
SELECT COUNT(*) as critical_alerts 
FROM system_alerts 
WHERE resolved = FALSE AND severity = 'critical';

-- Check provider health status
SELECT provider_type, provider_name, status, latency_ms, error_rate
FROM provider_health 
WHERE status != 'healthy' 
ORDER BY updated_at DESC;

-- Check recent admin activity
SELECT action_type, target_type, outcome_status, created_at
FROM admin_audit_log 
WHERE created_at >= NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC 
LIMIT 10;
```

This schema extension provides a robust foundation for the Talky.ai admin panel with comprehensive audit logging, system monitoring, and administrative capabilities while maintaining security and performance standards.