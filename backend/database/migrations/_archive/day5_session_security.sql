-- =============================================================================
-- Day 5 Security: Session Security + Device Control
-- Migration: day5_session_security.sql
-- =============================================================================
--
-- OWASP References (official, verified March 2026):
--   Session Management Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
--
-- NIST SP 800-63B (Digital Identity Guidelines):
--   https://pages.nist.gov/800-63-3/sp800-63b.html
--
-- What this migration does:
--   1. Adds device fingerprinting columns to security_sessions
--   2. Adds session binding columns (IP binding, fingerprint binding)
--   3. Adds security flags (suspicious, requires_verification)
--   4. Adds concurrent session limit tracking
--   5. Creates indexes for security queries
--
-- To apply:
--   psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
--        -f database/migrations/day5_session_security.sql
--
-- Safe to run multiple times (all DDL uses IF NOT EXISTS / IF EXISTS guards).
-- =============================================================================

BEGIN;

-- =============================================================================
-- TABLE: security_sessions - Day 5 Enhancements
-- =============================================================================

-- Device fingerprinting columns
ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS device_fingerprint TEXT;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS device_name TEXT;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS device_type TEXT
    CONSTRAINT chk_device_type CHECK (device_type IN ('mobile', 'tablet', 'desktop', 'unknown'));

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS browser TEXT;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS os TEXT;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS country_code TEXT;

-- Session binding columns
ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS bound_ip TEXT;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS ip_binding_enforced BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS fingerprint_binding_enforced BOOLEAN NOT NULL DEFAULT FALSE;

-- Security flags
ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS is_suspicious BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS suspicious_reason TEXT;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS suspicious_detected_at TIMESTAMPTZ;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS requires_verification BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

-- Concurrent session tracking
ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS session_number INTEGER;

-- Session rotation tracking
ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS rotated_from_session_id UUID;

ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS is_rotated BOOLEAN NOT NULL DEFAULT FALSE;

-- =============================================================================
-- INDEXES for Day 5
-- =============================================================================

-- Device fingerprint lookups for anomaly detection
CREATE INDEX IF NOT EXISTS idx_ss_fingerprint
    ON security_sessions (device_fingerprint)
    WHERE device_fingerprint IS NOT NULL;

-- Suspicious session queries
CREATE INDEX IF NOT EXISTS idx_ss_suspicious
    ON security_sessions (user_id, is_suspicious, suspicious_detected_at DESC)
    WHERE is_suspicious = TRUE;

-- Active session lookups with binding
CREATE INDEX IF NOT EXISTS idx_ss_active_binding
    ON security_sessions (session_token_hash, bound_ip, device_fingerprint)
    WHERE revoked = FALSE;

-- Session count per user (for concurrent session limits)
CREATE INDEX IF NOT EXISTS idx_ss_user_session_number
    ON security_sessions (user_id, session_number DESC)
    WHERE revoked = FALSE;

-- Cleanup old suspicious sessions
CREATE INDEX IF NOT EXISTS idx_ss_suspicious_cleanup
    ON security_sessions (suspicious_detected_at)
    WHERE is_suspicious = TRUE;

-- =============================================================================
-- COMMENTS - Document Day 5 additions
-- =============================================================================

COMMENT ON COLUMN security_sessions.device_fingerprint IS
    'SHA-256 hash of device/browser characteristics used for session binding. '
    'Computed from User-Agent, Accept headers, and client hints. '
    'Ref: OWASP Session Management - Device Fingerprinting';

COMMENT ON COLUMN security_sessions.device_name IS
    'Human-readable device name parsed from User-Agent (e.g., "Chrome on Windows")';

COMMENT ON COLUMN security_sessions.device_type IS
    'Device category: mobile|tablet|desktop|unknown - parsed from User-Agent';

COMMENT ON COLUMN security_sessions.browser IS
    'Browser name parsed from User-Agent (e.g., chrome, firefox, safari)';

COMMENT ON COLUMN security_sessions.os IS
    'Operating system parsed from User-Agent (e.g., windows, macos, ios, android)';

COMMENT ON COLUMN security_sessions.bound_ip IS
    'IP address at session creation. Used for IP binding validation. '
    'NULL if ip_binding_enforced = FALSE';

COMMENT ON COLUMN security_sessions.ip_binding_enforced IS
    'Whether IP binding is active for this session. If TRUE, significant IP '
    'changes will mark the session suspicious or revoke it.';

COMMENT ON COLUMN security_sessions.fingerprint_binding_enforced IS
    'Whether device fingerprint binding is active. If TRUE, fingerprint '
    'mismatches will mark the session suspicious.';

COMMENT ON COLUMN security_sessions.is_suspicious IS
    'Flag set when anomalous session activity is detected (IP change, '
    'fingerprint mismatch, etc.). Suspicious sessions may require verification.';

COMMENT ON COLUMN security_sessions.suspicious_reason IS
    'Machine-readable reason for suspicious flag: ip_mismatch, fingerprint_mismatch, '
    'concurrent_limit_exceeded, unusual_location, etc.';

COMMENT ON COLUMN security_sessions.requires_verification IS
    'If TRUE, user must re-authenticate to continue using this session. '
    'Set when suspicious activity is detected and strict security is required.';

COMMENT ON COLUMN security_sessions.session_number IS
    'Per-user session counter. Used to enforce concurrent session limits. '
    'When limit exceeded, oldest session numbers are revoked.';

COMMENT ON COLUMN security_sessions.rotated_from_session_id IS
    'If this session was created via rotation, references the original session. '
    'Used for audit trail of session rotation.';

COMMENT ON COLUMN security_sessions.is_rotated IS
    'TRUE if this session replaced another via session rotation (privilege change).';

-- =============================================================================
-- GRANT notes (apply manually per environment, not in this migration)
-- =============================================================================
--
-- Application role should have:
--   GRANT SELECT, INSERT, UPDATE ON security_sessions TO talkyai_app;
--
-- No additional grants required for Day 5 - existing permissions sufficient.
--
-- =============================================================================

COMMIT;
