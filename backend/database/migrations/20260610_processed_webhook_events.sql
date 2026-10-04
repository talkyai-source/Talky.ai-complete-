-- Historical claim-only schema. This file is retained for migration history.
-- Current installs MUST run Alembic through 0054_billing_webhook_receipts.
-- Existing rows prove an earlier claim, never successful business completion;
-- 0054 preserves them as legacy_unverified for explicit reconciliation.
CREATE TABLE IF NOT EXISTS processed_webhook_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
