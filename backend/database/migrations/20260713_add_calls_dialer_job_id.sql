-- 2026-07-13: link a calls row back to the dialer_job that placed it.
--
-- Root-cause fix for "answered leads/jobs never finalized": call teardown
-- (call_service._handle_call_status_pooled) and the stuck-job reaper
-- (stuck_job_reaper.reap_stuck_jobs) both need to resolve the originating
-- dialer_job from the calls row. The column was referenced by code but never
-- existed in the schema, so dialer_worker._create_call_record now writes it and
-- these consumers read it. Nullable (browser / test / inbound calls have no
-- dialer_job); ON DELETE SET NULL so purging a job never deletes call history.
ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS dialer_job_id UUID
        REFERENCES dialer_jobs(id) ON DELETE SET NULL;

-- Partial index: the reaper's anti-join and teardown lookup only ever query by
-- a non-null dialer_job_id, so keep the index small.
CREATE INDEX IF NOT EXISTS idx_calls_dialer_job_id
    ON calls(dialer_job_id)
    WHERE dialer_job_id IS NOT NULL;
