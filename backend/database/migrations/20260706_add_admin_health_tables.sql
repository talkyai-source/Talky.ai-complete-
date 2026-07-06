-- Admin-console health/webhook tables: incidents, webhook_endpoints,
-- webhook_deliveries, worker_status.
--
-- These four tables are queried by the admin console but never existed in the
-- schema. Every reading endpoint currently wraps its query in try/except and
-- falls back to empty/synthetic data (incidents.py:84, workers.py:61,
-- webhooks_admin.py list_* return []), so the missing tables are masked, not
-- fatal. Creating them makes the real (persisted) path live while keeping the
-- fallbacks as a safety net.
--
-- Columns/types match EXACTLY what the endpoints read/write:
--   incidents          -> admin/health/incidents.py
--   webhook_endpoints  -> webhooks_admin.py (INSERT payload :73-80)
--   webhook_deliveries -> webhooks_admin.py:145-158 (read-only here)
--   worker_status      -> admin/health/workers.py:34 (read-only here)
--
-- Additive + idempotent (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS / DROP POLICY IF EXISTS). Applied manually via psql on prod — there
-- is NO auto-runner (mirrors the rest of this directory).

-- ────────────────────────────────────────────────────────────────────────
-- 1) incidents — list / get / acknowledge / resolve.
--    id is TEXT (not uuid): get_incident filters by a raw path string and the
--    codebase uses synthetic string ids like "synthetic-calls-failed"
--    (those never persist, but keeping the column TEXT avoids uuid-cast errors
--    if one ever does). triggered_at is the list order key; the reader falls
--    back to created_at when it is NULL.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    status          TEXT NOT NULL DEFAULT 'open',
    description     TEXT,
    triggered_at    TIMESTAMPTZ DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT
);

-- List filters/order: WHERE status = / severity = , ORDER BY triggered_at DESC.
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents (severity);
CREATE INDEX IF NOT EXISTS idx_incidents_triggered_at
    ON incidents (triggered_at DESC);

COMMENT ON TABLE incidents IS
    'Operator-console incidents (open/acknowledged/resolved). Platform-wide '
    'admin health view; read/mutated only by admin endpoints.';

-- ────────────────────────────────────────────────────────────────────────
-- 2) webhook_endpoints — tenant-scoped webhook registrations.
--    INSERT writes id, tenant_id, url, events, active, created_at. events is a
--    JSON list (JSONB). The reader tolerates an endpoint_url alias but url is
--    what is written.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID,
    url        TEXT NOT NULL,
    events     JSONB NOT NULL DEFAULT '[]'::jsonb,
    active     BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The endpoints filter/scope by tenant_id (defense in depth alongside RLS).
CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_tenant
    ON webhook_endpoints (tenant_id);

-- RLS: tenant-scoped exactly like contact_lists/leads. The app also filters by
-- tenant_id; this policy is the backstop. bypass_rls lets the admin/worker
-- adapter path see rows the same way it does elsewhere. Idempotent.
ALTER TABLE webhook_endpoints ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS webhook_endpoints_tenant_isolation ON webhook_endpoints;
CREATE POLICY webhook_endpoints_tenant_isolation ON webhook_endpoints
    FOR ALL
    USING (
        current_setting('app.bypass_rls', TRUE) = 'true'
        OR tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
    )
    WITH CHECK (
        current_setting('app.bypass_rls', TRUE) = 'true'
        OR tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
    );

COMMENT ON TABLE webhook_endpoints IS
    'Per-tenant outbound webhook registrations (url + subscribed events). '
    'tenant_id scopes rows via RLS + explicit app-level filtering.';

-- ────────────────────────────────────────────────────────────────────────
-- 3) webhook_deliveries — delivery history (read-only from the admin endpoint).
--    Filtered by webhook_id; reads id, webhook_id, event, status, created_at.
--    tenant_id added for RLS parity so per-tenant history stays isolated when
--    a writer path lands later.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id UUID,
    tenant_id  UUID,
    event      TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reader filters by webhook_id.
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook
    ON webhook_deliveries (webhook_id);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_tenant
    ON webhook_deliveries (tenant_id);

ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS webhook_deliveries_tenant_isolation ON webhook_deliveries;
CREATE POLICY webhook_deliveries_tenant_isolation ON webhook_deliveries
    FOR ALL
    USING (
        current_setting('app.bypass_rls', TRUE) = 'true'
        OR tenant_id IS NULL
        OR tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
    )
    WITH CHECK (
        current_setting('app.bypass_rls', TRUE) = 'true'
        OR tenant_id IS NULL
        OR tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid
    );

COMMENT ON TABLE webhook_deliveries IS
    'Outbound webhook delivery attempts (event + status) per webhook_endpoint.';

-- ────────────────────────────────────────────────────────────────────────
-- 4) worker_status — background-worker heartbeats (read-only from the admin
--    endpoint). Platform-wide operator view (not tenant-scoped): workers are
--    shared infrastructure. started_at drives uptime; last_heartbeat is read
--    as a string.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS worker_status (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL DEFAULT 'worker',
    status          TEXT NOT NULL DEFAULT 'idle',
    current_task    TEXT,
    processed_count INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    last_heartbeat  TIMESTAMPTZ,
    started_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_worker_status_heartbeat
    ON worker_status (last_heartbeat DESC);

COMMENT ON TABLE worker_status IS
    'Background-worker heartbeat/liveness rows for the admin health console. '
    'Shared infra — platform-wide, not tenant-scoped.';
