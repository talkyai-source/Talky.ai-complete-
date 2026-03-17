-- Phase 2 / WS-J
-- Immutable tenant policy mutation audit log + operational retention helper.
-- Safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

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


ALTER TABLE tenant_policy_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_policy_audit_log FORCE ROW LEVEL SECURITY;

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
