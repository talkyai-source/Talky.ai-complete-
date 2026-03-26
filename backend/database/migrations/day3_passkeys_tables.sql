-- =============================================================================
-- Day 3 Security: Passkeys (WebAuthn / FIDO2) Tables
-- Migration: day3_passkeys_tables.sql
-- =============================================================================
--
-- Official References (verified March 2026):
--   W3C WebAuthn Level 3 Candidate Recommendation (January 13, 2026):
--     https://www.w3.org/TR/webauthn-3/
--   py_webauthn 2.7.1 (Duo Labs):
--     https://github.com/duo-labs/py_webauthn
--     https://pypi.org/project/webauthn/
--   FIDO Alliance FIDO2 Specifications:
--     https://fidoalliance.org/specs/fido-v2.1-ps-20210615/
--
-- What this migration does:
--   1. Creates user_passkeys table (registered FIDO2 credentials per user)
--   2. Creates webauthn_challenges table (ephemeral ceremony challenges)
--   3. Adds passkey_count denorm column to user_profiles (fast login check)
--   4. Creates all indexes for fast queries
--
-- Key WebAuthn storage requirements (W3C spec §7.1, §7.2):
--   - credential_id: unique per credential, needed for authentication lookup
--   - credential_public_key: COSE-encoded public key, required for signature
--     verification on every authentication ceremony
--   - sign_count: monotonically increasing counter for clone detection
--     (W3C §6.1.3 — if stored count > 0 and new count <= stored, WARN)
--   - aaguid: identifies the authenticator model (useful for trust decisions)
--   - transports: how the authenticator communicates (for allow_credentials)
--   - backed_up: TRUE when the credential is synced (passkey vs single-device)
--
-- To apply:
--   psql postgresql://talkyai:talkyai_secret@localhost:5432/talkyai \
--        -f database/migrations/day3_passkeys_tables.sql
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
-- TABLE: user_passkeys
-- =============================================================================
--
-- Stores one row per registered FIDO2 credential (passkey) per user.
-- A single user may have multiple passkeys (different devices, backup keys).
--
-- W3C WebAuthn §6.4 — "Credential Records":
--   The server MUST store at minimum:
--     type, id (credential_id), publicKey, signCount, uvInitialized,
--     transports, backupEligible, backupState
--
-- Design decisions:
--   - credential_id stored as TEXT (base64url-encoded bytes).
--     base64url is the canonical WebAuthn wire format; TEXT is easier to
--     query than BYTEA and avoids encoding bugs.
--   - credential_public_key stored as TEXT (base64url-encoded COSE bytes).
--     Decoded back to bytes on every authentication ceremony.
--   - sign_count stored as BIGINT (W3C allows it to reach 2^32 - 1).
--     0 means the authenticator does not support signature counters
--     (common for synced passkeys — skip counter check in that case).
--   - aaguid stored as TEXT — the FIDO metadata UUID string that identifies
--     the authenticator model (e.g. "adce0002-35bc-c60a-648b-0b25f1f05503"
--     for a YubiKey 5 series).
--   - device_type: "singleDevice" (key lives on one device only) or
--     "multiDevice" (key is synced across devices via iCloud / Google etc.).
--   - backed_up: TRUE when the credential is backed up / synced.
--     This is the "backup state" flag in W3C §6.1.3.
--   - transports: PostgreSQL TEXT[] array of transport strings reported by
--     the authenticator during registration.  Used to populate the
--     `transports` field of PublicKeyCredentialDescriptor during
--     authentication options generation, which helps the browser route the
--     request to the correct authenticator.
--     Typical values: "internal", "hybrid", "usb", "nfc", "ble"
--   - display_name: user-assigned label (e.g. "Work MacBook", "iPhone 15").
--     Set by the user at registration time; can be renamed later.
--   - CASCADE DELETE: removing a user removes all their passkeys.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_passkeys (
    -- Primary key (server-generated, not the WebAuthn credential ID)
    id                      UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- Owning user
    user_id                 UUID        NOT NULL,

    -- ---------------------------------------------------------------------------
    -- W3C mandatory credential record fields (§6.4)
    -- ---------------------------------------------------------------------------

    -- The credential ID returned by the authenticator during registration.
    -- base64url-encoded bytes.  UNIQUE — one row per credential globally.
    credential_id           TEXT        NOT NULL,

    -- COSE-encoded public key bytes, base64url-encoded.
    -- Passed as credential_public_key to verify_authentication_response().
    -- Never returned to the client.
    credential_public_key   TEXT        NOT NULL,

    -- Signature counter. Updated after every successful authentication.
    -- 0 = authenticator does not support counters (e.g. synced passkeys).
    -- If counter is > 0 and new_sign_count <= current: log clone warning.
    sign_count              BIGINT      NOT NULL DEFAULT 0,

    -- AAGUID (Authenticator Attestation GUID).
    -- 16-byte UUID string identifying the authenticator model.
    -- May be all-zeros if the authenticator uses "Self" attestation or
    -- AttestationConveyancePreference.NONE was used during registration.
    aaguid                  TEXT,

    -- ---------------------------------------------------------------------------
    -- Backup / sync state
    -- ---------------------------------------------------------------------------

    -- "singleDevice" = private key lives on one device only.
    -- "multiDevice"  = private key is synced (iCloud Keychain, Google PW Mgr).
    device_type             TEXT        CHECK (device_type IN ('singleDevice', 'multiDevice')),

    -- TRUE when the credential is backed up (synced across devices).
    -- Corresponds to the BS (backup state) flag in authData.
    backed_up               BOOLEAN     NOT NULL DEFAULT FALSE,

    -- ---------------------------------------------------------------------------
    -- Transport hints
    -- ---------------------------------------------------------------------------

    -- How the authenticator communicates.  PostgreSQL TEXT[] array.
    -- Examples: {"internal"}, {"usb","nfc"}, {"internal","hybrid"}
    -- Used in allow_credentials during authentication options generation to
    -- help the browser select the correct authenticator.
    transports              TEXT[],

    -- ---------------------------------------------------------------------------
    -- UX metadata
    -- ---------------------------------------------------------------------------

    -- User-assigned human-readable label for this passkey.
    -- Shown in the passkey management UI so the user can identify it.
    display_name            TEXT,

    -- ---------------------------------------------------------------------------
    -- Lifecycle timestamps (UTC)
    -- ---------------------------------------------------------------------------
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at            TIMESTAMPTZ,

    CONSTRAINT pk_user_passkeys
        PRIMARY KEY (id),

    -- credential_id must be globally unique across all users
    CONSTRAINT uq_user_passkeys_credential_id
        UNIQUE (credential_id),

    CONSTRAINT fk_user_passkeys_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- sign_count must be non-negative
    CONSTRAINT chk_user_passkeys_sign_count_non_negative
        CHECK (sign_count >= 0)
);

-- ---------------------------------------------------------------------------
-- Indexes for user_passkeys
-- ---------------------------------------------------------------------------

-- Primary authentication lookup: find passkey by credential_id.
-- Called on every authentication ceremony completion.
CREATE INDEX IF NOT EXISTS idx_up_credential_id
    ON user_passkeys (credential_id);

-- List all passkeys for a user (management UI, allow_credentials list).
CREATE INDEX IF NOT EXISTS idx_up_user_id
    ON user_passkeys (user_id, created_at DESC);

-- Find all backed-up / synced passkeys for reporting.
CREATE INDEX IF NOT EXISTS idx_up_backed_up
    ON user_passkeys (user_id, backed_up)
    WHERE backed_up = TRUE;


-- =============================================================================
-- TABLE: webauthn_challenges
-- =============================================================================
--
-- Stores ephemeral challenges for WebAuthn registration and authentication
-- ceremonies.
--
-- W3C WebAuthn §7.1 (Registration) step 6:
--   "Let challenge be a cryptographically random value of at least 16 bytes."
--   The server must store the challenge and verify it against the value
--   returned in clientDataJSON.challenge during ceremony completion.
--
-- W3C WebAuthn §7.2 (Authentication) step 7:
--   Same requirement.
--
-- Design decisions (same principles as mfa_challenges from Day 2):
--   - challenge stored as TEXT (base64url).
--     py_webauthn generates challenges as bytes; we encode them for storage
--     and decode with base64url_to_bytes() before passing to verify functions.
--   - ceremony_id (UUID) is returned to the client and sent back on completion.
--     It is the lookup key — we never send the raw challenge to the client
--     as a lookup key (that would allow challenge fixation).
--   - Short TTL: 5 minutes (WEBAUTHN_CHALLENGE_TTL_MINUTES).
--   - Single-use: used = TRUE after the challenge is consumed.
--   - user_id is nullable: during a discoverable-credential authentication
--     begin, we do not yet know which user will authenticate.  The user is
--     identified from the userHandle in the authentication response.
--   - ip_address stored for anomaly detection (challenge created from
--     a different IP than completion = possible MITM indicator).
--   - CASCADE DELETE: removing a user removes their pending challenges.
-- =============================================================================

CREATE TABLE IF NOT EXISTS webauthn_challenges (
    -- Server-generated ceremony ID returned to the client as a lookup key.
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),

    -- base64url-encoded challenge bytes generated by py_webauthn.
    -- Decoded with base64url_to_bytes() and passed as expected_challenge.
    challenge       TEXT        NOT NULL,

    -- Which WebAuthn ceremony this challenge belongs to.
    ceremony        TEXT        NOT NULL CHECK (ceremony IN ('registration', 'authentication')),

    -- The user initiating the ceremony.
    -- NULL for discoverable-credential authentication begin (user unknown).
    user_id         UUID,

    -- Client network metadata for anomaly detection.
    ip_address      TEXT,
    user_agent      TEXT,

    -- Short TTL — ceremony must be completed within 5 minutes.
    expires_at      TIMESTAMPTZ NOT NULL,

    -- Single-use flag.
    used            BOOLEAN     NOT NULL DEFAULT FALSE,
    used_at         TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_webauthn_challenges
        PRIMARY KEY (id),

    CONSTRAINT fk_wc_user
        FOREIGN KEY (user_id)
        REFERENCES user_profiles (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- Logical consistency
    CONSTRAINT chk_wc_used_at
        CHECK (
            (used = FALSE AND used_at IS NULL)
            OR
            (used = TRUE  AND used_at IS NOT NULL)
        ),

    CONSTRAINT chk_wc_expires_after_created
        CHECK (expires_at > created_at)
);

-- ---------------------------------------------------------------------------
-- Indexes for webauthn_challenges
-- ---------------------------------------------------------------------------

-- Primary lookup: validate a ceremony by ID on completion.
CREATE INDEX IF NOT EXISTS idx_wc_id_active
    ON webauthn_challenges (id)
    WHERE used = FALSE;

-- Cleanup job: find and delete expired, unused challenges.
CREATE INDEX IF NOT EXISTS idx_wc_cleanup
    ON webauthn_challenges (expires_at)
    WHERE used = FALSE;

-- Per-user audit view.
CREATE INDEX IF NOT EXISTS idx_wc_user_time
    ON webauthn_challenges (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;


-- =============================================================================
-- ALTER TABLE: user_profiles
-- Denormalized passkey count for fast O(1) login check.
-- =============================================================================

-- Number of registered passkeys for this user.
-- Incremented on successful registration, decremented on deletion.
-- Allows login to skip a JOIN on user_passkeys entirely.
-- Authoritative count is always COUNT(*) FROM user_passkeys.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS passkey_count INTEGER NOT NULL DEFAULT 0;

-- Ensure count never goes negative.
ALTER TABLE user_profiles
    ADD CONSTRAINT IF NOT EXISTS chk_user_profiles_passkey_count_non_negative
        CHECK (passkey_count >= 0);

-- Index: quickly find users who have passkeys registered.
CREATE INDEX IF NOT EXISTS idx_user_profiles_has_passkey
    ON user_profiles (passkey_count)
    WHERE passkey_count > 0;


-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE user_passkeys IS
    'Registered FIDO2 / WebAuthn credentials (passkeys) per user. '
    'One row per authenticator credential. A user may have multiple. '
    'credential_id is the globally unique authenticator-generated identifier. '
    'credential_public_key (COSE) is required for signature verification. '
    'sign_count tracks the authenticator counter for clone detection. '
    'Ref: W3C WebAuthn Level 3 §6.4, py_webauthn 2.7.1';

COMMENT ON COLUMN user_passkeys.credential_id IS
    'base64url-encoded credential ID bytes from the authenticator. '
    'Globally UNIQUE. Used as the lookup key during authentication.';

COMMENT ON COLUMN user_passkeys.credential_public_key IS
    'base64url-encoded COSE public key bytes. '
    'Passed as credential_public_key to verify_authentication_response(). '
    'NEVER returned to the client.';

COMMENT ON COLUMN user_passkeys.sign_count IS
    'Authenticator signature counter. Updated after every successful auth. '
    '0 = authenticator does not support counters (skip clone check). '
    'If new_sign_count > 0 and new_sign_count <= stored: log clone warning.';

COMMENT ON COLUMN user_passkeys.aaguid IS
    'Authenticator Attestation GUID — identifies the authenticator model. '
    'All-zeros when attestation was not requested or self-attested.';

COMMENT ON COLUMN user_passkeys.device_type IS
    '"singleDevice" = private key on one device only. '
    '"multiDevice"  = synced via iCloud Keychain, Google Password Manager etc.';

COMMENT ON COLUMN user_passkeys.backed_up IS
    'TRUE when the credential is backed up / synced across devices. '
    'Corresponds to the BS (Backup State) flag in authenticatorData.';

COMMENT ON COLUMN user_passkeys.transports IS
    'Transport hints reported during registration. '
    'Passed to PublicKeyCredentialDescriptor.transports in auth options '
    'to help the browser route the request to the correct authenticator. '
    'Values: "internal", "hybrid", "usb", "nfc", "ble", "smart-card"';

COMMENT ON TABLE webauthn_challenges IS
    'Ephemeral challenges for WebAuthn registration and authentication. '
    'challenge is base64url-encoded bytes generated by py_webauthn. '
    'TTL: 5 minutes. Single-use. '
    'user_id is NULL during discoverable-credential authentication begin '
    '(user is identified from the userHandle in the authentication response). '
    'Ref: W3C WebAuthn §7.1 step 6, §7.2 step 7';

COMMENT ON COLUMN webauthn_challenges.challenge IS
    'base64url-encoded challenge bytes from py_webauthn. '
    'Decoded with base64url_to_bytes() and passed as expected_challenge '
    'to verify_registration_response() / verify_authentication_response().';

COMMENT ON COLUMN webauthn_challenges.ceremony IS
    '"registration" = passkey creation ceremony. '
    '"authentication" = passkey login ceremony.';

COMMENT ON COLUMN user_profiles.passkey_count IS
    'Denormalized count of registered passkeys for this user. '
    'Authoritative truth: SELECT COUNT(*) FROM user_passkeys WHERE user_id=$1. '
    'Used at login to skip a JOIN when the user has no passkeys.';


-- =============================================================================
-- GRANT notes (apply manually per environment)
-- =============================================================================
--
-- Application role should have:
--   GRANT SELECT, INSERT, UPDATE, DELETE ON user_passkeys       TO talkyai_app;
--   GRANT SELECT, INSERT, UPDATE         ON webauthn_challenges  TO talkyai_app;
--   GRANT DELETE                         ON webauthn_challenges  TO talkyai_app; -- purge job
--
-- Application role should NOT have:
--   DELETE on user_passkeys without going through the API (audit trail)
--
-- =============================================================================

COMMIT;
