-- Stripe webhook idempotency: dedup redelivered events by their Stripe event id.
-- Without this, checkout.session.completed / invoice.paid re-applied minute
-- resets and re-sent confirmation emails on every Stripe redelivery (~up to 3x).
-- billing_service._claim_webhook_event INSERTs ... ON CONFLICT DO NOTHING and
-- skips the handler when the id already exists.
-- Additive + idempotent. Applied manually via psql on prod (no auto-runner).
CREATE TABLE IF NOT EXISTS processed_webhook_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
