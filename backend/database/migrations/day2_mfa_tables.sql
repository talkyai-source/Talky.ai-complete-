-- =============================================================================
-- Day 2 Security: MFA Tables
-- Migration: day2_mfa_tables.sql
-- =============================================================================
--
-- OWASP References (official, verified March 2026):
--   Multifactor Authentication Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html
--   Authentication Cheat Sheet:
--     https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
--
-- RFC References:
--   RFC 6238 — TOTP: Time-Based One-Time Password Algorithm
--   RFC 4226 — HOTP: An HMAC-Based One-Time Password Algorithm
--
-- pyotp official checklist (https://pypi.org/project/pyotp/):
--   - Store secrets in a controlled access database
--   - Deny replay attacks by storing the most recently authenticated timestamp
--   - Throttle brute-force attempts
--
-- What this migration does:
--   1. Creates user_mfa table (encrypted TOTP secrets + replay-prevention tracking)
--   2. Creates recovery_codes table (single-use backup codes, hashed)
--   3. Creates mfa_challenges table (ephemeral two-step login tokens)
--   4. Adds mfa_enabled column to user_profiles (fast lookup)
--   5. Adds mfa_verified column to security_sessions (session-level MFA state)
--   6. Creates all indexes for fast queries
--
-- To apply:
--   psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
--        -f database/migrations/day2_mfa_tables.sql
--
-- Safe to run multiple times (all DDL uses IF NOT EXISTS guards).
-- Wrapped in a single transaction — rolls back entirely on any error.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Ensure required extension
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- =============================================================================
-- TABLE: user_mfa
-- =============================================================================
--
-- Stores the encrypted TOTP secret and MFA state for each user.
--
-- Design decisions:
--   - UNIQUE on user_id: one TOTP config per user at a time.
--   - totp_secret_enc: Fernet(TOTP_ENCRYPTION_KEY) encrypted base32 string.
--     The raw secret is NEVER stored.  If the encryption key is rotated,
--     all secrets must be re-encrypted (key rotation procedure TBD).
--   - enabled: FALSE during setup (secret generated but not yet confirmed
--     by the user entering a valid code).  TRUE after first confirmation.
--   - verified_at: set when the user first confirms their TOTP code.
--     Required before enabled can be set TRUE.
--   - last_used_at: stores the UTC datetime of the MOST RECENT successful
--     TOTP verification.  Used to reject replay attacks within the same
--     30-second window (pyotp checklist requirement).
--   - CASCADE DELETE: removing a user cleans up their MFA record.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_mfa (
    -- Primary key
    id                  UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- Owning user — one MFA record per user
    user_id             UUID        NOT NULL,

    -- Fernet-encrypted TOTP base32 secret.
    -- Decrypt with TOTP_ENCRYPTION_KEY env var.  Never store plaintext.
    totp_secret_enc     TEXT        NOT NULL,

    -- FALSE = secret generated but user has not yet confirmed it with a valid code.
    -- TRUE  = user has confirmed the TOTP setup; MFA is active for this account.
    enabled             BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Timestamp when the user first entered a valid TOTP code after setup.
    -- NULL until the first successful confirmation.
    verified_at         TIMESTAMPTZ,

    -- Lifecycle timestamps (UTC)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Replay-attack prevention (RFC 6238 §5.2 + pyotp checklist).
    -- Updated on every successful TOTP or recovery-code verification.
    -- If the current 30-second time slot matches this timestamp's slot,
    -- the code is rejected as a replay.
    last_used_at        TIMESTAMPTZ,

    CONSTRAINT pk_user_mfa
        PRIMARY KEY (id),

    CONSTRAINT uq_user_mfa_user
        UNIQUE (user_id),

    CONSTRAINT fk_user_mfa_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- enabled can only be TRUE if verified_at is set
    CONSTRAINT chk_user_mfa_verified_before_enabled
        CHECK (
            (enabled = FALSE)
            OR
            (enabled = TRUE AND verified_at IS NOT NULL)
        )
);

-- Index: fast lookup by user_id (primary access pattern)
CREATE INDEX IF NOT EXISTS idx_user_mfa_user_id
    ON user_mfa (user_id);

-- Partial index: find all users who have active MFA (admin dashboards)
CREATE INDEX IF NOT EXISTS idx_user_mfa_enabled
    ON user_mfa (user_id)
    WHERE enabled = TRUE;


-- =============================================================================
-- TABLE: recovery_codes
-- =============================================================================
--
-- Single-use backup codes provided to the user at MFA setup time.
--
-- OWASP: "Provide the user with a number of single-use recovery codes
--         when they first setup MFA."
--
-- Design decisions:
--   - Only SHA-256(raw_code) is stored — never the raw code.
--     Same principle as session tokens (Day 1).
--   - used = TRUE after a code is consumed; the row is kept for audit.
--   - batch_id groups all codes from the same generation so they can be
--     replaced atomically when the user regenerates their codes.
--   - CASCADE DELETE: removing a user removes all their recovery codes.
--   - The table is effectively append-only for audit purposes:
--     rows are inserted at setup/regeneration and soft-deleted (used=TRUE)
--     when consumed.  Hard DELETE is done only when MFA is disabled.
-- =============================================================================

CREATE TABLE IF NOT EXISTS recovery_codes (
    -- Primary key
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- Owning user
    user_id         UUID        NOT NULL,

    -- SHA-256 hex digest of the raw recovery code.
    -- UNIQUE prevents the same code hash appearing twice.
    code_hash       TEXT        NOT NULL,

    -- Groups all codes from the same generation.
    -- When regenerating, delete all rows for user_id and insert a new batch
    -- with a fresh batch_id.
    batch_id        UUID        NOT NULL,

    -- Lifecycle
    used            BOOLEAN     NOT NULL DEFAULT FALSE,
    used_at         TIMESTAMPTZ,          -- set when code is consumed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_recovery_codes
        PRIMARY KEY (id),

    CONSTRAINT uq_recovery_codes_hash
        UNIQUE (code_hash),

    CONSTRAINT fk_recovery_codes_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- used_at must be set when used = TRUE
    CONSTRAINT chk_recovery_codes_used_at
        CHECK (
            (used = FALSE AND used_at IS NULL)
            OR
            (used = TRUE  AND used_at IS NOT NULL)
        )
);

-- Index: primary lookup path — find unused codes for a user
CREATE INDEX IF NOT EXISTS idx_rc_user_unused
    ON recovery_codes (user_id, used)
    WHERE used = FALSE;

-- Index: lookup a code by hash (during verification)
CREATE INDEX IF NOT EXISTS idx_rc_code_hash
    ON recovery_codes (code_hash)
    WHERE used = FALSE;

-- Index: batch operations (regenerate = delete by batch_id or user_id)
CREATE INDEX IF NOT EXISTS idx_rc_user_batch
    ON recovery_codes (user_id, batch_id);

-- Index: audit view — all codes for a user ordered by creation
CREATE INDEX IF NOT EXISTS idx_rc_user_created
    ON recovery_codes (user_id, created_at DESC);


-- =============================================================================
-- TABLE: mfa_challenges
-- =============================================================================
--
-- Ephemeral tokens issued after successful password verification when the
-- user has MFA enabled.  The challenge token must be presented alongside
-- the TOTP code (or recovery code) to complete login.
--
-- Purpose: implement the two-step login flow without issuing a full JWT
--   until BOTH factors have been verified.
--
-- Design decisions:
--   - Short TTL: 5 minutes (enforced in application code via expires_at).
--   - Only SHA-256(raw_token) stored — never the raw challenge token.
--   - Single use: used = TRUE after the challenge is consumed.
--   - ip_address stored at creation; application can optionally check that
--     the MFA verify call comes from the same IP (risk-based optional check).
--   - CASCADE DELETE: removing a user removes all pending challenges.
--   - Rows are cleaned up by a background job (purge_expired_mfa_challenges).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mfa_challenges (
    -- Primary key
    id                  UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- User this challenge belongs to
    user_id             UUID        NOT NULL,

    -- SHA-256 hex digest of the raw challenge token.
    -- Raw token is given to the client once; only the hash is stored.
    challenge_hash      TEXT        NOT NULL,

    -- Client metadata recorded at challenge creation
    ip_address          TEXT,
    user_agent          TEXT,

    -- Lifecycle
    expires_at          TIMESTAMPTZ NOT NULL,   -- typically NOW() + 5 minutes
    used                BOOLEAN     NOT NULL DEFAULT FALSE,
    used_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_mfa_challenges
        PRIMARY KEY (id),

    CONSTRAINT uq_mfa_challenges_hash
        UNIQUE (challenge_hash),

    CONSTRAINT fk_mfa_challenges_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- Logical consistency
    CONSTRAINT chk_mfa_challenge_used_at
        CHECK (
            (used = FALSE AND used_at IS NULL)
            OR
            (used = TRUE  AND used_at IS NOT NULL)
        ),

    CONSTRAINT chk_mfa_challenge_expires_after_created
        CHECK (expires_at > created_at)
);

-- Index: primary lookup — validate a challenge token (hash lookup)
CREATE INDEX IF NOT EXISTS idx_mc_hash_active
    ON mfa_challenges (challenge_hash)
    WHERE used = FALSE;

-- Index: cleanup of expired/used challenges
CREATE INDEX IF NOT EXISTS idx_mc_cleanup
    ON mfa_challenges (expires_at)
    WHERE used = FALSE;

-- Index: per-user view (admin / audit)
CREATE INDEX IF NOT EXISTS idx_mc_user_time
    ON mfa_challenges (user_id, created_at DESC);


-- =============================================================================
-- ALTER TABLE: user_profiles
-- Fast denormalized MFA status flag — avoids a JOIN on every login.
-- =============================================================================

-- TRUE when the user has active MFA (user_mfa.enabled = TRUE).
-- Must be kept in sync by the application when MFA is enabled/disabled.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- Index: quickly find users with/without MFA for reporting
CREATE INDEX IF NOT EXISTS idx_user_profiles_mfa_enabled
    ON user_profiles (mfa_enabled)
    WHERE mfa_enabled = TRUE;


-- =============================================================================
-- ALTER TABLE: security_sessions (from Day 1)
-- Track whether the session was fully authenticated with MFA.
-- =============================================================================

-- TRUE  = password + MFA both verified for this session.
-- FALSE = password verified only (MFA not enabled, or not yet verified).
-- This column lets endpoints enforce "MFA required" policies for sensitive
-- actions without re-checking the user_mfa table on every request.
ALTER TABLE security_sessions
    ADD COLUMN IF NOT EXISTS mfa_verified BOOLEAN NOT NULL DEFAULT FALSE;

-- Index: find sessions that completed MFA (admin audit)
CREATE INDEX IF NOT EXISTS idx_ss_mfa_verified
    ON security_sessions (user_id, mfa_verified)
    WHERE mfa_verified = TRUE;


-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE user_mfa IS
    'TOTP MFA configuration per user. '
    'totp_secret_enc is Fernet-encrypted with TOTP_ENCRYPTION_KEY env var. '
    'enabled=FALSE during pending setup; TRUE after first confirmed verification. '
    'last_used_at is updated on every successful TOTP use to prevent replay attacks. '
    'Ref: RFC 6238, OWASP MFA Cheat Sheet, pyotp checklist.';

COMMENT ON COLUMN user_mfa.totp_secret_enc IS
    'Fernet(TOTP_ENCRYPTION_KEY)-encrypted base32 TOTP secret. '
    'NEVER store the plaintext secret. Decrypt only in memory during verification.';

COMMENT ON COLUMN user_mfa.last_used_at IS
    'UTC datetime of the last successful TOTP verification. '
    'If the current 30-second slot matches this timestamp slot, reject as replay. '
    'pyotp checklist: deny replay attacks by tracking last authenticated timestamp.';

COMMENT ON COLUMN user_mfa.enabled IS
    'FALSE = setup initiated but not yet confirmed by the user. '
    'TRUE = user has entered a valid TOTP code; MFA is actively enforced.';

COMMENT ON TABLE recovery_codes IS
    'Single-use MFA backup codes. Only SHA-256(raw_code) is stored. '
    'Raw codes are shown to the user once at MFA setup and never retrievable. '
    'used=TRUE after consumption; rows kept for audit. '
    'OWASP: provide single-use recovery codes when MFA is set up.';

COMMENT ON COLUMN recovery_codes.code_hash IS
    'SHA-256 hex digest of the raw recovery code. UNIQUE. '
    'The raw code is NEVER stored. Same principle as session token hashing.';

COMMENT ON COLUMN recovery_codes.batch_id IS
    'Groups all codes from a single generation. '
    'DELETE all rows by user_id before inserting a fresh batch. '
    'Allows atomic regeneration of the entire code set.';

COMMENT ON TABLE mfa_challenges IS
    'Short-lived (5-minute) tokens issued after password verification '
    'when the user has MFA enabled. The client must present this token '
    'with a valid TOTP/recovery code to receive a full session. '
    'Only SHA-256(raw_token) is stored. Single-use.';

COMMENT ON COLUMN mfa_challenges.challenge_hash IS
    'SHA-256 hex digest of the raw challenge token issued to the client. '
    'Raw token is transmitted once and never stored server-side.';

COMMENT ON COLUMN user_profiles.mfa_enabled IS
    'Denormalized flag kept in sync with user_mfa.enabled. '
    'Allows fast MFA check at login without a JOIN on user_mfa.';

COMMENT ON COLUMN security_sessions.mfa_verified IS
    'TRUE when this session completed both password and TOTP/recovery verification. '
    'FALSE for sessions where MFA was not required or not yet completed. '
    'Used by sensitive endpoints to enforce step-up authentication.';


-- =============================================================================
-- GRANT notes (apply manually per environment)
-- =============================================================================
--
-- Application role should have:
--   GRANT SELECT, INSERT, UPDATE ON user_mfa          TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE ON recovery_codes    TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE ON mfa_challenges    TO talkyai_app;
--   GRANT DELETE                 ON recovery_codes    TO talkyai_app;  -- disable/regenerate
--   GRANT DELETE                 ON mfa_challenges    TO talkyai_app;  -- purge job
--
-- Application role should NOT have:
--   DELETE on user_mfa        (soft-disable only via enabled=FALSE)
--   UPDATE on recovery_codes  (append-only; mark used only)
--
-- =============================================================================

COMMIT;
