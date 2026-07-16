-- Track 2 (retry classification): add structured failure attributes to
-- dialer_jobs so retry decisions and outage dashboards can facet on
-- category + reason instead of grepping free-text last_error.
--
-- Both columns are nullable. Old rows keep NULL; new rows are populated
-- by app/workers/retry_policy.py via _record_job_failure_classification.
--
-- failure_category: snake_case enum value from FailureCategory
--   (transient_network, auth_gate, carrier_reject, invalid_input,
--   internal). Indexed for dashboard facets.
-- failure_reason: fine-grained snake_case identifier. Examples:
--   caller_id_not_verified, http_503, sip_486_busy. Free-text from the
--   bridge error code or a derived fallback. Not indexed — high cardinality.

ALTER TABLE dialer_jobs
    ADD COLUMN IF NOT EXISTS failure_category TEXT,
    ADD COLUMN IF NOT EXISTS failure_reason   TEXT;

CREATE INDEX IF NOT EXISTS idx_dialer_jobs_failure_category
    ON dialer_jobs (failure_category)
    WHERE failure_category IS NOT NULL;

COMMENT ON COLUMN dialer_jobs.failure_category IS
    'Track 2 retry classifier output: one of transient_network, '
    'auth_gate, carrier_reject, invalid_input, internal. NULL for jobs '
    'that never failed or were written before the classifier shipped.';
COMMENT ON COLUMN dialer_jobs.failure_reason IS
    'Track 2 retry classifier output: fine-grained snake_case reason '
    'string. Typically the bridge error.code (e.g. caller_id_not_verified) '
    'or http_<status> for status-based fallbacks.';
