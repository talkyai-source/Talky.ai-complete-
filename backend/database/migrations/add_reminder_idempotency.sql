-- =============================================================================
-- Add Reminder Idempotency and Tracking Columns
-- =============================================================================
--
-- This migration adds columns to the reminders table for:
--   1. Idempotency key - prevents duplicate sends
--   2. Retry tracking - next_retry_at, max_retries
--   3. Channel tracking - which channel was used (sms/email)
--   4. External message ID - provider's message reference
--
-- Day 27: Timed Communication System
-- Created: January 9, 2026
-- =============================================================================

-- Add idempotency_key column with unique constraint
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255);

-- Create unique index for idempotency lookups (partial - only for non-null keys)
CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_idempotency_key 
ON reminders(idempotency_key) 
WHERE idempotency_key IS NOT NULL;

-- Add max_retries column (default 3 retries)
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 3;

-- Add next_retry_at for scheduled retries
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

-- Add last_error for detailed error tracking
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS last_error TEXT;

-- Add channel column to track which channel was used (sms or email)
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS channel VARCHAR(20);

-- Add external_message_id for provider message tracking
ALTER TABLE reminders 
ADD COLUMN IF NOT EXISTS external_message_id VARCHAR(255);

-- Add index for finding reminders due for retry
CREATE INDEX IF NOT EXISTS idx_reminders_next_retry 
ON reminders(next_retry_at) 
WHERE status = 'pending' AND next_retry_at IS NOT NULL;

-- Add index for finding pending reminders due to be sent
CREATE INDEX IF NOT EXISTS idx_reminders_pending_due
ON reminders(scheduled_at)
WHERE status = 'pending';

-- Update comments
COMMENT ON COLUMN reminders.idempotency_key IS 'Unique key to prevent duplicate sends';
COMMENT ON COLUMN reminders.channel IS 'Delivery channel used: sms or email';
COMMENT ON COLUMN reminders.external_message_id IS 'Message ID from SMS/email provider';
COMMENT ON COLUMN reminders.next_retry_at IS 'When to retry failed send (with backoff)';
COMMENT ON COLUMN reminders.max_retries IS 'Maximum retry attempts (default 3)';
COMMENT ON COLUMN reminders.last_error IS 'Error message from last failed attempt';

-- =============================================================================
-- SUCCESS NOTIFICATION
-- =============================================================================

DO $$
BEGIN
    RAISE NOTICE '============================================================================';
    RAISE NOTICE 'Reminder Idempotency Migration completed successfully!';
    RAISE NOTICE '';
    RAISE NOTICE 'New columns added to reminders table:';
    RAISE NOTICE '  - idempotency_key: Unique key to prevent duplicate sends';
    RAISE NOTICE '  - channel: Delivery channel (sms/email)';
    RAISE NOTICE '  - external_message_id: Provider message ID';
    RAISE NOTICE '  - next_retry_at: Scheduled retry time';
    RAISE NOTICE '  - max_retries: Max retry attempts';
    RAISE NOTICE '  - last_error: Last error message';
    RAISE NOTICE '';
    RAISE NOTICE 'New indexes created:';
    RAISE NOTICE '  - idx_reminders_idempotency_key (unique, partial)';
    RAISE NOTICE '  - idx_reminders_next_retry';
    RAISE NOTICE '  - idx_reminders_pending_due';
    RAISE NOTICE '============================================================================';
END $$;
