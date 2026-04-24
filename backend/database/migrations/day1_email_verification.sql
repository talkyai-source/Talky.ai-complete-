-- =============================================================================
-- Day 1: Email Verification System
-- =============================================================================
-- Adds email verification fields to user_profiles table.
-- Users cannot log in until they verify their email address.

-- Add email verification columns
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS verification_token TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS verification_token_expires_at TIMESTAMPTZ;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

-- Create index for token lookup (used for verification endpoint)
CREATE INDEX IF NOT EXISTS idx_user_profiles_verification_token ON user_profiles(verification_token) WHERE verification_token IS NOT NULL;

-- Create index for verification status (used in login checks)
CREATE INDEX IF NOT EXISTS idx_user_profiles_is_verified ON user_profiles(is_verified) WHERE is_verified = FALSE;

-- Constraint: if verified, token must be null and verified_at must be set
ALTER TABLE user_profiles ADD CONSTRAINT chk_email_verification_consistency
    CHECK ((is_verified = FALSE) OR (is_verified = TRUE AND verification_token IS NULL AND email_verified_at IS NOT NULL));
