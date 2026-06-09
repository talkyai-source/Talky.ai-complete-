-- Phase 1: surface call failures, count all minutes, and post-call lead signals.
-- Additive + idempotent. Applied manually via psql on prod (no auto-runner).

-- 1) Human-readable failure reason on calls (NULL for successful calls).
--    failure_category is a coarse bucket for UI grouping: tts | llm | stt |
--    prewarm | telephony | guard | other.
ALTER TABLE calls ADD COLUMN IF NOT EXISTS failure_reason TEXT;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS failure_category VARCHAR(40);

-- 2) Post-call lead signals on leads (contacts). is_lead drives the green
--    "Lead — please follow up" badge; follow_up_note holds the AI's one-liner;
--    qualified_call_id is a soft reference to the call that flagged them.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_lead BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS follow_up_note TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS qualified_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS qualified_call_id UUID;
-- Fast "show me this campaign's leads" lookup (partial: only flagged rows).
CREATE INDEX IF NOT EXISTS idx_leads_is_lead ON leads (campaign_id) WHERE is_lead;

-- 3) Allow the 'call' category in stream_events. The emitter already emits
--    category='call' for call lifecycle/failure events, but the CHECK
--    constraint rejected it, so every call event was silently dropped (the
--    "emit_event.invalid_category category=call" warnings). DROP+ADD is
--    re-runnable because DROP uses IF EXISTS.
ALTER TABLE stream_events DROP CONSTRAINT IF EXISTS stream_events_category_check;
ALTER TABLE stream_events ADD CONSTRAINT stream_events_category_check
    CHECK (category = ANY (ARRAY[
        'campaign'::text, 'system'::text, 'alert'::text,
        'user_action'::text, 'milestone'::text, 'call'::text
    ]));
