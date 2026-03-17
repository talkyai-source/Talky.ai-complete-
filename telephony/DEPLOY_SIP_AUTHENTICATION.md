# Quick Deployment Guide: SIP Authentication

**Time Required:** 20 minutes  
**Difficulty:** Medium  
**Prerequisites:** MySQL, OpenSIPS 3.4, Docker

---

## 🚀 Quick Start (5 Commands)

```bash
# 1. Create database table (2 min)
mysql -u root -p opensips < telephony/database/subscriber_table.sql

# 2. Add test subscriber (1 min)
cd telephony/scripts
./add_sip_subscriber.sh -u alice -d talky.local -p 'SecurePass123!' -e [email protected]

# 3. Backup and replace config (1 min)
cp telephony/opensips/conf/opensips.cfg telephony/opensips/conf/opensips.cfg.backup
cp telephony/opensips/conf/opensips-with-auth.cfg telephony/opensips/conf/opensips.cfg

# 4. Set database URL (1 min)
export DB_URL="mysql://opensips:opensipsrw@localhost/opensips"

# 5. Restart OpenSIPS (1 min)
docker-compose restart opensips
```

---

## ✅ Verification (3 Commands)

```bash
# Check OpenSIPS started
docker logs opensips | grep "auth_db"
# Should see: "auth_db module loaded"

# Check database connection
docker exec opensips opensips-cli -x mi db_check

# Test authentication
# Send INVITE without auth - should get 407
# Send INVITE with auth - should succeed
```

---

## 📋 Detailed Steps

### Step 1: Database Setup

```bash
# Option A: Direct MySQL
mysql -u root -p opensips < telephony/database/subscriber_table.sql

# Option B: Docker MySQL
docker exec -i mysql mysql -u root -p opensips < telephony/database/subscriber_table.sql

# Verify table created
mysql -u root -p -e "DESCRIBE opensips.subscriber;"
```

**Expected Output:**
```
+-------------------+--------------+------+-----+---------------------+
| Field             | Type         | Null | Key | Default             |
+-------------------+--------------+------+-----+---------------------+
| id                | int unsigned | NO   | PRI | NULL                |
| username          | varchar(64)  | NO   | MUL |                     |
| domain            | varchar(64)  | NO   |     |                     |
| ha1               | varchar(64)  | NO   |     |                     |
| ha1_sha256        | varchar(64)  | YES  |     | NULL                |
| ha1_sha512t256    | varchar(64)  | YES  |     | NULL                |
+-------------------+--------------+------+-----+---------------------+
```

### Step 2: Add Subscribers

```bash
cd telephony/scripts
chmod +x add_sip_subscriber.sh

# Add first subscriber
./add_sip_subscriber.sh \
  -u alice \
  -d talky.local \
  -p 'AliceSecurePass123!' \
  -e [email protected]

# Add second subscriber
./add_sip_subscriber.sh \
  -u bob \
  -d talky.local \
  -p 'BobSecurePass456!' \
  -e [email protected]

# Verify subscribers added
mysql -u root -p -e "SELECT username, domain, email_address FROM opensips.subscriber;"
```

**Expected Output:**
```
+----------+-------------+-------------------+
| username | domain      | email_address     |
+----------+-------------+-------------------+
| alice    | talky.local | [email protected] |
| bob      | talky.local | [email protected]   |
+----------+-------------+-------------------+
```

### Step 3: Update OpenSIPS Configuration

```bash
# Backup current config
cp telephony/opensips/conf/opensips.cfg \
   telephony/opensips/conf/opensips.cfg.backup.$(date +%Y%m%d)

# Copy authentication-enabled config
cp telephony/opensips/conf/opensips-with-auth.cfg \
   telephony/opensips/conf/opensips.cfg

# Verify config syntax
docker run --rm -v $(pwd)/telephony/opensips/conf:/etc/opensips \
  opensips/opensips:3.4 opensips -c -f /etc/opensips/opensips.cfg
```

**Expected Output:**
```
config file ok, exiting...
```

### Step 4: Update Environment Variables

```bash
# Add to .env file
cat >> telephony/deploy/docker/.env.telephony <<EOF

# SIP Authentication Database
DB_URL=mysql://opensips:opensipsrw@mysql:3306/opensips
EOF

# Or export directly
export DB_URL="mysql://opensips:opensipsrw@localhost/opensips"
```

### Step 5: Restart Services

```bash
cd telephony/deploy/docker

# Restart OpenSIPS only
docker-compose restart opensips

# Or restart all services
docker-compose down
docker-compose up -d

# Check logs
docker-compose logs -f opensips | grep -i auth
```

**Expected Log Output:**
```
opensips    | INFO:auth:mod_init: initializing
opensips    | INFO:auth_db:mod_init: initializing
opensips    | INFO:auth_db:mod_init: db_url = mysql://opensips:***@mysql:3306/opensips
opensips    | INFO:auth_db:mod_init: calculate_ha1 = 0
opensips    | INFO:auth_db:mod_init: use_domain = 1
```

---

## 🧪 Testing

### Test 1: No Authentication (Should Fail)

```bash
# Using sipp
sipp -sf test_no_auth.xml -s alice@talky.local localhost:15060

# Expected: 407 Proxy Authentication Required
```

### Test 2: Valid Authentication (Should Succeed)

```bash
# Using sipp with auth
sipp -sf test_with_auth.xml \
  -s alice@talky.local \
  -au alice \
  -ap 'AliceSecurePass123!' \
  localhost:15060

# Expected: 200 OK
```

### Test 3: Invalid Password (Should Fail)

```bash
# Using sipp with wrong password
sipp -sf test_with_auth.xml \
  -s alice@talky.local \
  -au alice \
  -ap 'WrongPassword' \
  localhost:15060

# Expected: 407 Proxy Authentication Required (again)
```

### Test 4: Check Logs

```bash
# View authentication logs
docker logs opensips 2>&1 | grep "AUTH:"

# Expected output:
# AUTH: Challenging user from 192.168.1.100
# AUTH: User alice authenticated from 192.168.1.100
# AUTH: Invalid password for alice from 192.168.1.100
```

---

## 🔧 Troubleshooting

### Issue: "Database connection failed"

```bash
# Check MySQL is running
docker ps | grep mysql

# Check database exists
mysql -u root -p -e "SHOW DATABASES LIKE 'opensips';"

# Check user permissions
mysql -u root -p -e "SHOW GRANTS FOR 'opensips'@'%';"

# Test connection manually
mysql -h localhost -u opensips -p opensips
```

### Issue: "Subscriber not found"

```bash
# Check subscriber exists
mysql -u root -p -e "SELECT * FROM opensips.subscriber WHERE username='alice';"

# Check domain matches
# Domain in database must match From domain in SIP request

# Re-add subscriber if needed
./add_sip_subscriber.sh -u alice -d talky.local -p 'NewPassword123!'
```

### Issue: "Authentication always fails"

```bash
# Check HA1 calculation
# Realm must match domain
echo -n "alice:talky.local:AliceSecurePass123!" | md5sum

# Compare with database
mysql -u root -p -e "SELECT username, domain, ha1 FROM opensips.subscriber WHERE username='alice';"

# If mismatch, recalculate
./add_sip_subscriber.sh -u alice -d talky.local -p 'AliceSecurePass123!'
```

### Issue: "Config syntax error"

```bash
# Check config syntax
docker run --rm -v $(pwd)/telephony/opensips/conf:/etc/opensips \
  opensips/opensips:3.4 opensips -c -f /etc/opensips/opensips.cfg

# Check for missing modules
grep "loadmodule" telephony/opensips/conf/opensips.cfg | grep auth

# Should see:
# loadmodule "auth.so"
# loadmodule "auth_db.so"
```

---

## 📊 Monitoring

### Authentication Metrics

```bash
# Count successful authentications (last hour)
docker logs opensips 2>&1 | \
  grep "AUTH: User.*authenticated" | \
  grep "$(date +%Y-%m-%d)" | \
  wc -l

# Count failed authentications (last hour)
docker logs opensips 2>&1 | \
  grep "AUTH: Invalid" | \
  grep "$(date +%Y-%m-%d)" | \
  wc -l

# Top failed authentication users
docker logs opensips 2>&1 | \
  grep "AUTH: Invalid password for" | \
  awk '{print $(NF-2)}' | \
  sort | uniq -c | sort -rn | head -10
```

### Database Queries

```sql
-- Total subscribers
SELECT COUNT(*) FROM subscriber;

-- Recently modified subscribers
SELECT username, domain, datetime_modified 
FROM subscriber 
ORDER BY datetime_modified DESC 
LIMIT 10;

-- Subscribers by domain
SELECT domain, COUNT(*) as count 
FROM subscriber 
GROUP BY domain;
```

---

## 🔒 Security Checklist

- [ ] Database table created with proper indexes
- [ ] Test subscribers added with strong passwords (12+ chars)
- [ ] OpenSIPS config updated with authentication
- [ ] Database URL configured (not hardcoded)
- [ ] MySQL user has minimal required permissions
- [ ] Nonce secret changed from default
- [ ] Failed authentication logging enabled
- [ ] Rate limiting still functional
- [ ] TLS certificate validation enabled
- [ ] SRTP encryption enforced

---

## 📚 Next Steps

1. Add production subscribers
2. Configure password rotation policy
3. Set up authentication monitoring alerts
4. Integrate with SIEM for security events
5. Document user onboarding process
6. Create password reset procedure
7. Implement account lockout after N failures
8. Add two-factor authentication (future)

---

## 🆘 Rollback Procedure

If authentication causes issues:

```bash
# 1. Restore backup config
cp telephony/opensips/conf/opensips.cfg.backup \
   telephony/opensips/conf/opensips.cfg

# 2. Restart OpenSIPS
docker-compose restart opensips

# 3. Verify service restored
docker logs opensips | tail -20

# 4. Test calls work without auth
# (temporarily, for emergency only)
```

---

**Deployment Time:** ~20 minutes  
**Rollback Time:** ~2 minutes  
**Risk Level:** Medium (test in staging first)  
**Impact:** HIGH - Prevents toll fraud
