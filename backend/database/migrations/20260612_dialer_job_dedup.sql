-- 20260612_dialer_job_dedup.sql
--
-- Enforce: AT MOST ONE active dialer job per lead.
--
-- Root cause of the double-dial bug: dialer_jobs had no uniqueness on lead_id,
-- so the campaign loop / retry path could mint multiple jobs for the same lead
-- and the worker would dial the number again while a call was still live.
--
-- "active" status set MUST stay in lockstep with
-- app/domain/services/dialer/job_states.py ACTIVE_STATUSES.
--
-- Idempotent: safe to re-run.

BEGIN;

-- 1) Resolve existing duplicates so the unique index can be built. Keep the
--    most-recently-updated active job per lead; cancel the older siblings.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY lead_id
               ORDER BY updated_at DESC, created_at DESC
           ) AS rn
    FROM dialer_jobs
    WHERE status IN ('pending', 'queued', 'retry_scheduled', 'processing', 'calling')
)
UPDATE dialer_jobs d
   SET status         = 'cancelled',
       failure_reason = 'dedup_superseded',
       last_error     = 'dedup_superseded',
       updated_at     = now()
FROM ranked r
WHERE d.id = r.id
  AND r.rn > 1;

-- 2) Enforce one active job per lead from here on.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dialer_jobs_one_active_per_lead
    ON dialer_jobs (lead_id)
    WHERE status IN ('pending', 'queued', 'retry_scheduled', 'processing', 'calling');

COMMIT;
