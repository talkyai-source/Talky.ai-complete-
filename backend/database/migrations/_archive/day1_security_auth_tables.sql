-- =============================================================================
-- Day 1 Security: Core Authentication Tables
-- Migration: day1_security_auth_tables.sql
-- =============================================================================
--
-- OWASP References (official, verified March 2026):
--   Session Management Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
--   Authentication Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
--   Password Storage Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
--
-- What this migration does:
--   1. Creates security_sessions table (server-side session storage)
--   2. Creates login_attempts table (per-account lockout tracking)
--   3. Adds security columns to user_profiles (lock status, active flag)
--   4. Creates all indexes required for fast lockout queries and cleanup
--
-- To apply:
--   psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
--        -f database/migrations/day1_security_auth_tables.sql
--
-- Safe to run multiple times (all DDL uses IF NOT EXISTS / IF EXISTS guards).
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Ensure required extension is present
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- =============================================================================
-- TABLE: security_sessions
-- =============================================================================
--
-- Stores server-side session records so sessions can be revoked immediately,
-- independent of JWT expiry.
--
-- OWASP Session Management rules applied:
--   - Only the SHA-256 hash of the token is stored (raw token given to client once).
--   - Columns support idle-timeout (last_active_at) and absolute-lifetime (expires_at).
--   - revoked flag + revoked_at allow instant server-side invalidation.
--   - Linked to user_profiles via user_id with CASCADE DELETE so removing a user
--     automatically removes all their sessions.
-- =============================================================================

CREATE TABLE IF NOT EXISTS security_sessions (
    -- Primary key
    id                  UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- Owning user — CASCADE DELETE keeps the table clean if a user is removed
    user_id             UUID        NOT NULL,

    -- SHA-256 hex digest of the raw session token.
    -- UNIQUE enforces one row per token hash.
    -- The raw token is NEVER stored here.
    session_token_hash  TEXT        NOT NULL,

    -- Client metadata recorded at session creation for audit and anomaly detection
    ip_address          TEXT,
    user_agent          TEXT,

    -- Lifecycle timestamps (all UTC)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- updated on every valid request
    expires_at          TIMESTAMPTZ NOT NULL,                 -- absolute lifetime ceiling

    -- Revocation state
    revoked             BOOLEAN     NOT NULL DEFAULT FALSE,
    revoked_at          TIMESTAMPTZ,          -- set when revoked = TRUE
    revoke_reason       TEXT,                 -- e.g. 'logout', 'logout_all', 'idle_timeout',
                                              --      'password_change', 'admin_action'

    CONSTRAINT pk_security_sessions
        PRIMARY KEY (id),

    CONSTRAINT uq_security_sessions_token_hash
        UNIQUE (session_token_hash),

    CONSTRAINT fk_security_sessions_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- Logical constraint: revoked_at must be present when revoked = TRUE
    CONSTRAINT chk_revoked_at_consistency
        CHECK (
            (revoked = FALSE AND revoked_at IS NULL)
            OR
            (revoked = TRUE  AND revoked_at IS NOT NULL)
        ),

    -- expires_at must be in the future relative to created_at
    CONSTRAINT chk_expires_after_created
        CHECK (expires_at > created_at)
);

-- --- Indexes for security_sessions -------------------------------------------

-- Primary lookup path: validate a token on every authenticated request.
-- Covers: session_token_hash = $1 AND revoked = FALSE AND expires_at > NOW()
CREATE INDEX IF NOT EXISTS idx_ss_token_lookup
    ON security_sessions (session_token_hash)
    WHERE revoked = FALSE;

-- Used by revoke_all_user_sessions() and get_active_sessions()
CREATE INDEX IF NOT EXISTS idx_ss_user_active
    ON security_sessions (user_id, last_active_at DESC)
    WHERE revoked = FALSE;

-- Used by purge_expired_sessions() background job
CREATE INDEX IF NOT EXISTS idx_ss_cleanup
    ON security_sessions (expires_at)
    WHERE revoked = FALSE;

-- Full user session history (for admin audit view)
CREATE INDEX IF NOT EXISTS idx_ss_user_all
    ON security_sessions (user_id, created_at DESC);


-- =============================================================================
-- TABLE: login_attempts
-- =============================================================================
--
-- Records every login attempt — both successes and failures.
--
-- OWASP Authentication Cheat Sheet rules applied:
--   - Counter is per-account (email) NOT just per-IP.
--     Attacker using rotating IPs would bypass IP-only blocking.
--   - Records IP address as well for threat intelligence / anomaly detection.
--   - user_id is nullable: an attempt for a non-existent email still gets logged
--     (email stored, user_id = NULL).  This must NOT be revealed to the client
--     (always return generic "Invalid username or password").
--   - failure_reason is an internal audit field — never sent to the client.
--   - Table is append-only by convention (no UPDATE / DELETE from app layer).
-- =============================================================================

CREATE TABLE IF NOT EXISTS login_attempts (
    -- Primary key
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- The email address that was submitted (always stored lower-cased)
    email           TEXT        NOT NULL,

    -- Resolved user, if the email matches a known account.
    -- NULL when the email is not found — do NOT expose this to callers.
    user_id         UUID,

    -- Network / client metadata
    ip_address      TEXT        NOT NULL,
    user_agent      TEXT,

    -- Outcome
    success         BOOLEAN     NOT NULL,

    -- Short machine-readable reason code for failures.
    -- Examples: 'wrong_password', 'account_locked', 'account_inactive',
    --           'user_not_found' (internal only — NEVER sent to client).
    failure_reason  TEXT,

    -- When the attempt was made (UTC)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_login_attempts
        PRIMARY KEY (id),

    CONSTRAINT fk_login_attempts_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE SET NULL   -- keep history even if user is deleted
        ON UPDATE CASCADE
);

-- --- Indexes for login_attempts -----------------------------------------------

-- Primary lockout query: count recent failures per email within observation window.
-- Covers: WHERE email = $1 AND success = FALSE AND created_at >= $2
CREATE INDEX IF NOT EXISTS idx_la_email_failures
    ON login_attempts (email, created_at DESC)
    WHERE success = FALSE;

-- Secondary: most recent failure timestamp per email (used by check_account_locked)
CREATE INDEX IF NOT EXISTS idx_la_email_time
    ON login_attempts (email, created_at DESC);

-- Per-user history (for admin audit view)
CREATE INDEX IF NOT EXISTS idx_la_user_time
    ON login_attempts (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;

-- Per-IP analysis (threat intelligence / credential stuffing detection)
CREATE INDEX IF NOT EXISTS idx_la_ip_time
    ON login_attempts (ip_address, created_at DESC);


-- =============================================================================
-- ALTER TABLE: user_profiles
-- Add security-related columns required by Day 1 authentication hardening.
-- All use ADD COLUMN IF NOT EXISTS so this migration is idempotent.
-- =============================================================================

-- Hard lock until a specific time (set by the lockout logic).
-- NULL means not locked.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS account_locked_until TIMESTAMPTZ;

-- Soft-delete / admin suspension flag.
-- FALSE = account is suspended and cannot log in.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- Timestamp of the last password change.
-- Used to invalidate all sessions created before this time.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

-- Track failed attempts count directly on the user row as a fast denorm.
-- The canonical truth is still in login_attempts; this is a quick cache.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0;

-- Timestamp of the last successful login (for audit and anomaly detection).
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- Index on is_active to support fast "is this account suspended?" checks.
CREATE INDEX IF NOT EXISTS idx_user_profiles_is_active
    ON user_profiles (is_active)
    WHERE is_active = FALSE;


-- =============================================================================
-- COMMENTS — document intent for future engineers
-- =============================================================================

COMMENT ON TABLE security_sessions IS
    'Server-side session storage. Only the SHA-256 hash of the session token is '
    'stored here. Raw tokens are handed to clients once via httpOnly cookies and '
    'are never persisted. Revoked=TRUE immediately invalidates a session regardless '
    'of whether the client still holds the token. '
    'Ref: OWASP Session Management Cheat Sheet';

COMMENT ON COLUMN security_sessions.session_token_hash IS
    'SHA-256(raw_token) in hex. UNIQUE. The raw token is NEVER stored.';

COMMENT ON COLUMN security_sessions.last_active_at IS
    'Updated on every valid authenticated request. Used to enforce idle timeout '
    '(default 30 minutes). Distinct from expires_at which is the absolute ceiling.';

COMMENT ON COLUMN security_sessions.revoke_reason IS
    'Human-readable reason code: logout | logout_all | idle_timeout | '
    'password_change | admin_action | account_suspended';

COMMENT ON TABLE login_attempts IS
    'Append-only log of every login attempt. Used for per-account progressive '
    'lockout (OWASP: counter must be per-account, not just per-IP). '
    'failure_reason is an INTERNAL audit field — never expose it to clients. '
    'Ref: OWASP Authentication Cheat Sheet';

COMMENT ON COLUMN login_attempts.failure_reason IS
    'Internal audit only. NEVER send this value to the client. '
    'Always return generic "Invalid username or password" in API responses.';

COMMENT ON COLUMN login_attempts.user_id IS
    'NULL when the submitted email does not match any account. '
    'This distinction must NEVER be disclosed to the client.';

COMMENT ON COLUMN user_profiles.is_active IS
    'FALSE = account suspended. Suspended accounts cannot log in and cannot '
    'start new call sessions. Set via admin suspension API only.';

COMMENT ON COLUMN user_profiles.password_changed_at IS
    'Set whenever the password is changed. All sessions created BEFORE this '
    'timestamp should be treated as invalid (rotate sessions on password change).';


-- =============================================================================
-- GRANT notes (apply manually per environment, not in this migration)
-- =============================================================================
--
-- Application role should have:
--   GRANT SELECT, INSERT, UPDATE ON security_sessions TO talkyai_app;
--   GRANT SELECT, INSERT        ON login_attempts     TO talkyai_app;
--   GRANT DELETE                ON security_sessions  TO talkyai_app;  -- for purge job
--
-- Application role should NOT have:
--   DELETE on login_attempts  (append-only — protect audit integrity)
--   UPDATE on login_attempts  (immutable records)
--
-- =============================================================================

COMMIT;
