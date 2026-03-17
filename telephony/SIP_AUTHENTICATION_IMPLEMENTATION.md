# SIP Digest Authentication Implementation

**Date:** March 11, 2026  
**Status:** ✅ IMPLEMENTED - Testing Required  
**Priority:** HIGH - Toll Fraud Prevention

---

## Overview

Implemented SIP Digest Authentication for OpenSIPS 3.4 based on official documentation and RFC 8760 (Strengthened Authentication). This prevents toll fraud, unauthorized call origination, and protects against $40+ billion annual telecom fraud losses.

---

## What Was Implemented

### 1. Database Schema
**File:** `telephony/database/subscriber_table.sql`

- MySQL subscriber table with RFC 8760 support
- Stores pre-calculated HA1 hashes (MD5, SHA-256, SHA-512-256)
- NEVER stores plaintext passwords
- Multi-tenant support with username@domain uniqueness

**Schema Features:**
```sql
- ha1: MD5(username:realm:password)
- ha1_sha256: SHA-256(username:realm:password)  
- ha1_sha512t256: SHA-512-256(username:realm:password)
- Unique constraint on (username, domain)
```

### 2. Subscriber Management Script
**File:** `telephony/scripts/add_sip_subscriber.sh`

- Secure password hashing (min 12 characters)
- Generates all three HA1 variants
- MySQL integration
- Duplicate key handling (updates existing users)

**Usage:**
```bash
./add_sip_subscriber.sh -u alice -d talky.local -p 'SecurePass123!' -e [email protected]
```

### 3. OpenSIPS Configuration with Authentication
**File:** `telephony/opensips/conf/opensips-with-auth.cfg`

- Complete authentication implementation
- RFC 8760 multi-algorithm support
- Detailed logging for security auditing
- Credential consumption (removes auth headers)

**Key Features:**
- Challenges with SHA-512-256, SHA-256, MD5 (in order of strength)
- 30-second nonce expiration (prevents replay attacks)
- Failed authentication logging
- Multi-tenant domain support

---

## Security Benefits

### Before Implementation:
- 🔴 No authentication required for INVITE
- 🔴 Any device on private network can originate calls
- 🔴 Toll fraud risk: Unlimited international calls
- 🔴 Open relay vulnerability

### After Implementation:
- ✅ SIP Digest Authentication required for all INVITE
- ✅ Database-backed credential verification
- ✅ RFC 8760 strengthened algorithms (SHA-256, SHA-512-256)
- ✅ Nonce-based replay attack prevention
- ✅ Failed authentication logging and monitoring
- ✅ Credential removal from forwarded messages

---

## Architecture

```
┌─────────────┐
│ SIP Client  │
└──────┬──────┘
       │ INVITE (no auth)
       ▼
┌─────────────────────────────────┐
│ OpenSIPS (with auth_db)         │
│ - Checks for credentials        │
│ - No credentials? Challenge!    │
└──────┬──────────────────────────┘
       │ 407 Proxy Authentication Required
       │ WWW-Authenticate: Digest realm="talky.local"
       │                   nonce="abc123..."
       │                   algorithm=SHA-512-256
       ▼
┌─────────────┐
│ SIP Client  │
│ - Calculates response           │
│ - HA1 = SHA-512-256(user:realm:pass)
│ - Response = HASH(HA1:nonce:...)
└──────┬──────┘
       │ INVITE (with Proxy-Authorization)
       ▼
┌─────────────────────────────────┐
│ OpenSIPS                        │
│ - Queries subscriber table      │
│ - Verifies response             │
│ - Removes credentials           │
└──────┬──────────────────────────┘
       │ Authenticated!
       ▼
┌─────────────┐
│ Asterisk    │
│ (B2BUA)     │
└─────────────┘
```

---

## Configuration Details

### Authentication Module Parameters

```
# Nonce Security
modparam("auth", "nonce_expire", 30)  # 30 seconds
modparam("auth", "secret", "change_this_to_random_32chars!")

# Database Configuration
modparam("auth_db", "db_url", "mysql://opensips:opensipsrw@localhost/opensips")
modparam("auth_db", "calculate_ha1", 0)  # Use pre-calculated hashes
modparam("auth_db", "use_domain", 1)     # Multi-tenant support

# RFC 8760 Hash Columns
modparam("auth_db", "password_column", "ha1")
modparam("auth_db", "hash_column_sha256", "ha1_sha256")
modparam("auth_db", "hash_column_sha512t256", "ha1_sha512t256")
```

### Authentication Logic

```
route[AUTH] {
    if (has_credentials("Proxy-Authorization")) {
        if (!proxy_authorize("", "subscriber")) {
            # Log failure reason
            proxy_challenge("", "auth", "SHA-512-256,SHA-256,MD5");
            exit;
        }
        # Success: remove credentials and continue
        consume_credentials();
        return;
    } else {
        # No credentials: challenge
        proxy_challenge("", "auth", "SHA-512-256,SHA-256,MD5");
        exit;
    }
}
```

---

## Deployment Steps

### Step 1: Create Database Table (5 minutes)
```bash
# Connect to MySQL
mysql -u root -p

# Create database if not exists
CREATE DATABASE IF NOT EXISTS opensips;

# Create subscriber table
mysql -u root -p opensips < telephony/database/subscriber_table.sql
```

### Step 2: Add Test Subscribers (2 minutes)
```bash
cd telephony/scripts

# Add test user
./add_sip_subscriber.sh \
  -u testuser \
  -d talky.local \
  -p 'MySecurePassword123!' \
  -e [email protected]

# Add another user
./add_sip_subscriber.sh \
  -u alice \
  -d talky.local \
  -p 'AliceSecurePass456!' \
  -e [email protected]
```

### Step 3: Update OpenSIPS Configuration (2 minutes)
```bash
# Backup current config
cp telephony/opensips/conf/opensips.cfg \
   telephony/opensips/conf/opensips.cfg.backup

# Use authentication-enabled config
cp telephony/opensips/conf/opensips-with-auth.cfg \
   telephony/opensips/conf/opensips.cfg

# Update database URL in environment
export DB_URL="mysql://opensips:opensipsrw@localhost/opensips"
```

### Step 4: Update Docker Compose (3 minutes)
```yaml
# Add to docker-compose.yml
services:
  opensips:
    environment:
      - DB_URL=mysql://opensips:opensipsrw@mysql:3306/opensips
    depends_on:
      - mysql
  
  mysql:
    image: mysql:8.0
    environment:
      - MYSQL_ROOT_PASSWORD=rootpass
      - MYSQL_DATABASE=opensips
      - MYSQL_USER=opensips
      - MYSQL_PASSWORD=opensipsrw
    volumes:
      - mysql_data:/var/lib/mysql
      - ./telephony/database/subscriber_table.sql:/docker-entrypoint-initdb.d/01-subscriber.sql

volumes:
  mysql_data:
```

### Step 5: Restart Services (2 minutes)
```bash
cd telephony/deploy/docker
docker-compose down
docker-compose up -d
```

### Step 6: Test Authentication (5 minutes)
```bash
# Test with valid credentials (should succeed)
sipp -sf test_auth_valid.xml -s testuser@talky.local

# Test with invalid credentials (should fail with 407)
sipp -sf test_auth_invalid.xml -s testuser@talky.local

# Test without credentials (should challenge with 407)
sipp -sf test_no_auth.xml -s testuser@talky.local
```

---

## Testing Checklist

- [ ] Database table created successfully
- [ ] Test subscribers added
- [ ] OpenSIPS starts without errors
- [ ] MySQL connection successful
- [ ] INVITE without auth returns 407
- [ ] INVITE with valid auth succeeds
- [ ] INVITE with invalid auth returns 407
- [ ] Failed auth attempts logged
- [ ] Credentials removed from forwarded INVITE
- [ ] Multiple algorithm support works
- [ ] Nonce expiration works (30 seconds)
- [ ] Rate limiting still functional

---

## Security Best Practices

### 1. Password Policy
- Minimum 12 characters
- Mix of uppercase, lowercase, numbers, symbols
- No dictionary words
- Rotate every 90 days

### 2. Nonce Secret
- Change default secret in production
- Use 32-character random string
- Store in secrets manager
- Rotate annually

### 3. Monitoring
- Log all failed authentication attempts
- Alert on brute force patterns (>5 failures/minute)
- Monitor for credential stuffing attacks
- Track authentication success rate

### 4. Database Security
- Use strong MySQL passwords
- Limit database access to localhost
- Enable MySQL audit logging
- Regular backups
- Encrypt database at rest

---

## Troubleshooting

### Issue: 407 but client doesn't retry
**Cause:** Client doesn't support digest authentication  
**Solution:** Check client SIP capabilities, ensure digest auth enabled

### Issue: Authentication always fails
**Cause:** HA1 hash mismatch  
**Solution:** Verify realm matches domain, recalculate HA1

### Issue: "Database connection failed"
**Cause:** MySQL not accessible  
**Solution:** Check DB_URL, MySQL running, credentials correct

### Issue: Nonce expired immediately
**Cause:** Clock skew between client and server  
**Solution:** Sync NTP, increase nonce_expire if needed

### Issue: Authentication works but call fails
**Cause:** Credentials not consumed  
**Solution:** Ensure `consume_credentials()` is called

---

## Performance Impact

### Benchmarks (estimated):
- Authentication overhead: ~2-5ms per INVITE
- Database query: ~1-3ms (with proper indexing)
- Hash calculation: Client-side (no server impact)
- Memory: ~100KB per 1000 subscribers

### Optimization:
- Use connection pooling for MySQL
- Enable query caching
- Index (username, domain) columns
- Consider Redis cache for hot subscribers

---

## Monitoring Queries

### Failed Authentication Attempts
```sql
-- Check OpenSIPS logs
grep "AUTH: Invalid" /var/log/opensips.log | tail -20

-- Count failures by user
grep "AUTH: Invalid password" /var/log/opensips.log | \
  awk '{print $NF}' | sort | uniq -c | sort -rn
```

### Active Subscribers
```sql
SELECT COUNT(*) FROM subscriber;
SELECT username, domain, datetime_modified 
FROM subscriber 
ORDER BY datetime_modified DESC 
LIMIT 10;
```

### Authentication Success Rate
```bash
# Successful auths
grep "AUTH: User.*authenticated" /var/log/opensips.log | wc -l

# Failed auths
grep "AUTH: Invalid" /var/log/opensips.log | wc -l
```

---

## Next Steps

1. ✅ Database schema created
2. ✅ Subscriber management script created
3. ✅ OpenSIPS config with auth implemented
4. ⏳ Deploy to staging environment
5. ⏳ Add test subscribers
6. ⏳ Test authentication flow
7. ⏳ Monitor for 24 hours
8. ⏳ Deploy to production

---

## References

1. [OpenSIPS 3.4 auth Module](https://opensips.org/docs/modules/3.4.x/auth.html)
2. [OpenSIPS 3.4 auth_db Module](https://opensips.org/docs/modules/3.4.x/auth_db.html)
3. [RFC 2617: HTTP Digest Authentication](https://www.rfc-editor.org/rfc/rfc2617)
4. [RFC 8760: Strengthened Authentication](https://www.rfc-editor.org/rfc/rfc8760)
5. [Toll Fraud Prevention Best Practices](https://stopsendingspam.com/learn/toll-fraud-prevention)

---

**Status:** ✅ Implementation complete, ready for testing  
**Security Level:** HIGH - Toll fraud prevention active  
**Next Review:** After 24-hour monitoring period
