-- OpenSIPS Subscriber Table for SIP Digest Authentication
-- Based on OpenSIPS 3.4 official schema with RFC 8760 support
-- Created: March 11, 2026

CREATE TABLE IF NOT EXISTS subscriber (
    id INT(10) UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) NOT NULL DEFAULT '',
    domain VARCHAR(64) NOT NULL DEFAULT '',
    password VARCHAR(64) NOT NULL DEFAULT '',
    
    -- RFC 8760: Strengthened Authentication Support
    -- Pre-calculated HA1 hashes for different algorithms
    ha1 VARCHAR(64) NOT NULL DEFAULT '',              -- MD5(username:realm:password)
    ha1_sha256 VARCHAR(64) DEFAULT NULL,              -- SHA-256(username:realm:password)
    ha1_sha512t256 VARCHAR(64) DEFAULT NULL,          -- SHA-512-256(username:realm:password)
    
    -- Additional subscriber information
    email_address VARCHAR(128) NOT NULL DEFAULT '',
    ha1b VARCHAR(64) NOT NULL DEFAULT '',             -- HA1 for authentication with domain
    rpid VARCHAR(128) DEFAULT NULL,                   -- Remote-Party-ID
    
    -- Account status and metadata
    datetime_created DATETIME NOT NULL DEFAULT '1900-01-01 00:00:01',
    datetime_modified DATETIME NOT NULL DEFAULT '1900-01-01 00:00:01',
    
    -- Unique constraint on username@domain
    UNIQUE KEY account_idx (username, domain),
    KEY username_idx (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Example: Insert test subscriber with pre-calculated HA1
-- Username: testuser
-- Domain: talky.local
-- Password: SecurePass123!
-- 
-- HA1 (MD5): MD5(testuser:talky.local:SecurePass123!)
-- HA1_SHA256: SHA-256(testuser:talky.local:SecurePass123!)
-- HA1_SHA512T256: SHA-512-256(testuser:talky.local:SecurePass123!)
--
-- INSERT INTO subscriber (username, domain, password, ha1, ha1_sha256, ha1_sha512t256, email_address, datetime_created, datetime_modified)
-- VALUES (
--     'testuser',
--     'talky.local',
--     '',  -- Leave empty when using pre-calculated hashes
--     MD5(CONCAT('testuser', ':', 'talky.local', ':', 'SecurePass123!')),
--     SHA2(CONCAT('testuser', ':', 'talky.local', ':', 'SecurePass123!'), 256),
--     SHA2(CONCAT('testuser', ':', 'talky.local', ':', 'SecurePass123!'), 512),
--     '[email protected]',
--     NOW(),
--     NOW()
-- );

-- Security Notes:
-- 1. NEVER store plaintext passwords in production
-- 2. Always use pre-calculated HA1 hashes (calculate_ha1 = 0)
-- 3. Use SHA-256 or SHA-512-256 for stronger security (RFC 8760)
-- 4. Rotate credentials regularly (90-day cycle recommended)
-- 5. Monitor failed authentication attempts for brute force attacks
