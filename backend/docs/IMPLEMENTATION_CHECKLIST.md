# Email Verification System - Implementation Checklist

**Project:** Talky.ai Email Verification (Day 1)  
**Timeline:** 15-30 minutes for complete setup  
**Owner:** [Your Name]  
**Date Started:** ___________  
**Date Completed:** ___________

---

## Phase 1: Planning & Preparation (5 minutes)

- [ ] Read `day 1 plan.md` (Section 1-3, SMTP & Email Flow)
- [ ] Read `EMAIL_SETUP_QUICK_START.md` 
- [ ] Review requirement checklist with team
- [ ] Identify Microsoft 365 admin account
- [ ] Identify GoDaddy domain administrator
- [ ] Allocate ~30 minutes for setup

---

## Phase 2: Microsoft 365 Configuration (5 minutes)

### Enable SMTP AUTH
- [ ] Log into [Microsoft 365 Admin Center](https://admin.microsoft.com)
- [ ] Navigate to Settings → Org Settings
- [ ] Find Mail Settings section
- [ ] Click Edit next to "Mail settings"
- [ ] Toggle SMTP AUTH to ON
- [ ] Click Save
- [ ] ⏱️ **Wait:** 5-10 minutes for changes to propagate

### Create App Password (Recommended)
- [ ] Go to [Microsoft Account Security](https://account.microsoft.com/security)
- [ ] Verify Multi-Factor Authentication is enabled
- [ ] Click "App passwords" in sidebar
- [ ] Select: App = Mail, Device = Other (Talky.ai Email Service)
- [ ] Click Create
- [ ] Copy 16-character password
- [ ] Save password securely (will need in Step 5)

---

## Phase 3: Database Preparation (2 minutes)

### Verify PostgreSQL Connection
- [ ] Confirm PostgreSQL is running
- [ ] Test connection: `psql -U talkyai -h localhost -d talkyai -c "SELECT 1;"`
- [ ] Result should be: `1`

### Verify Backup
- [ ] Create database backup before migration
  ```bash
  pg_dump -U talkyai -h localhost talkyai > talkyai_backup.sql
  ```
- [ ] Confirm backup file created and has reasonable size (>1MB)

---

## Phase 4: Environment Configuration (3 minutes)

### Prepare .env File
- [ ] Copy template: `cp backend/.env.example backend/.env`
- [ ] Open `.env` file in editor
- [ ] Check variables needed:
  - [ ] EMAIL_USER = `noreply@talkleeai.com`
  - [ ] EMAIL_PASS = (App Password from Phase 2)
  - [ ] FRONTEND_URL = `https://talkleeai.com`
  - [ ] API_BASE_URL = `https://api.talkleeai.com`
  - [ ] DATABASE_URL = (Your PostgreSQL connection string)
  - [ ] JWT_SECRET = (Generate new secret)

### Generate JWT Secret (if needed)
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
- [ ] Copy output to `.env` as JWT_SECRET

### Verify .env Values
- [ ] EMAIL_USER not empty
- [ ] EMAIL_PASS not empty (16+ characters if App Password)
- [ ] FRONTEND_URL set correctly
- [ ] API_BASE_URL set correctly
- [ ] DATABASE_URL valid PostgreSQL connection
- [ ] ⚠️ **IMPORTANT:** Do NOT commit .env to git

---

## Phase 5: Code Deployment (2 minutes)

### Verify Files Exist
- [ ] `backend/database/migrations/day1_email_verification.sql`
- [ ] `backend/app/domain/services/email_service.py`
- [ ] `backend/app/core/security/verification_tokens.py`
- [ ] `backend/app/core/config.py` (should have email_user, email_pass)
- [ ] `backend/app/api/v1/endpoints/auth.py` (should have verify-email endpoint)
- [ ] `backend/tests/test_email_verification.py`

### Install Dependencies
- [ ] Run: `pip install aiosmtplib`
- [ ] Verify: `python -c "import aiosmtplib; print('OK')"`

---

## Phase 6: Database Migration (2 minutes)

### Apply Migration
```bash
psql -U talkyai -h localhost -d talkyai -f backend/database/migrations/day1_email_verification.sql
```
- [ ] Migration runs without errors
- [ ] No SQL errors in output

### Verify Schema
```sql
SELECT column_name FROM information_schema.columns 
WHERE table_name='user_profiles' 
ORDER BY ordinal_position;
```
- [ ] Should see: `is_verified`
- [ ] Should see: `verification_token`
- [ ] Should see: `verification_token_expires_at`
- [ ] Should see: `email_verified_at`

---

## Phase 7: SMTP Connection Test (2 minutes)

### Create Test Script
**File: `test_smtp.py`**
```python
import asyncio
import aiosmtplib
from dotenv import load_dotenv
import os

load_dotenv()

async def test_smtp():
    try:
        async with aiosmtplib.SMTP(
            hostname='smtp.office365.com',
            port=587,
            use_tls=True
        ) as smtp:
            email = os.getenv('EMAIL_USER')
            password = os.getenv('EMAIL_PASS')
            await smtp.login(email, password)
            print('✅ SMTP connection successful!')
    except Exception as e:
        print(f'❌ SMTP connection failed: {e}')
        print('Check:')
        print('1. EMAIL_USER and EMAIL_PASS in .env')
        print('2. SMTP AUTH enabled in Microsoft 365')
        print('3. Port 587 not blocked by firewall')

asyncio.run(test_smtp())
```

### Run Test
```bash
python test_smtp.py
```
- [ ] Output shows: `✅ SMTP connection successful!`
- [ ] If fails: Check items in error message

---

## Phase 8: Backend Restart (2 minutes)

### Restart Service
**Option A: Docker**
```bash
docker-compose restart backend
```

**Option B: Systemd**
```bash
sudo systemctl restart talkyai-backend
```

**Option C: Manual (for development)**
```bash
# Stop current process (Ctrl+C)
# Then restart:
python -m uvicorn app.main:app --reload
```

- [ ] Backend starts without errors
- [ ] No error messages in logs
- [ ] Server listens on port 8000 (or configured port)

### Check Logs
```bash
docker logs backend
# OR
tail -f /var/log/talkyai/backend.log
```
- [ ] No "email" or "SMTP" related errors
- [ ] No "config" errors about missing EMAIL_USER/EMAIL_PASS

---

## Phase 9: Functional Testing (5 minutes)

### Test 1: Register User
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "TestPass123!",
    "business_name": "Test Company"
  }'
```
- [ ] Response code: 200
- [ ] Response contains `access_token`
- [ ] Response contains message about verification

### Test 2: Get Verification Token
```bash
psql -U talkyai -h localhost -d talkyai -c \
  "SELECT email, verification_token FROM user_profiles WHERE email='testuser@example.com';"
```
- [ ] Returns one row
- [ ] `verification_token` is not NULL and not empty
- [ ] Copy the token value

### Test 3: Verify Email
```bash
curl http://localhost:8000/api/v1/auth/verify-email?token=<PASTE_TOKEN_HERE>
```
- [ ] Response code: 200
- [ ] Response contains: "Email verified successfully"
- [ ] Response contains the email address

### Test 4: Check User Verified in DB
```bash
psql -U talkyai -h localhost -d talkyai -c \
  "SELECT email, is_verified, verification_token FROM user_profiles WHERE email='testuser@example.com';"
```
- [ ] `is_verified` = true
- [ ] `verification_token` = NULL
- [ ] `email_verified_at` = (not NULL)

### Test 5: Login (Should Succeed)
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "TestPass123!"
  }'
```
- [ ] Response code: 200
- [ ] Response contains `access_token`
- [ ] Response contains message "Login successful"

### Test 6: Login Before Verification (Should Fail)
```bash
# Register new user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "unverified@example.com",
    "password": "TestPass123!",
    "business_name": "Test Company"
  }'

# Try to login immediately (without verification)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "unverified@example.com",
    "password": "TestPass123!"
  }'
```
- [ ] Response code: 403
- [ ] Response contains: "Please verify your email"

---

## Phase 10: Production Email Test (Optional, 5 minutes)

### Send Test to Real Email
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "your.email@gmail.com",
    "password": "TestPass123!",
    "business_name": "Real Test"
  }'
```
- [ ] Replace `your.email@gmail.com` with your actual email
- [ ] Check inbox (and spam folder)
- [ ] Should receive email from `noreply@talkleeai.com`
- [ ] Email subject: "Please verify your email address"
- [ ] Contains verification link button
- [ ] Click button to verify

---

## Phase 11: Integration Tests (2 minutes)

### Run Test Suite
```bash
pytest backend/tests/test_email_verification.py -v
```
- [ ] All tests pass
- [ ] No errors or failures
- [ ] Coverage includes token generation, endpoints, edge cases

### Specific Tests to Verify
```bash
# Run specific test class
pytest backend/tests/test_email_verification.py::TestEmailVerificationTokens -v

# Run specific test method
pytest backend/tests/test_email_verification.py::TestEmailVerificationEndpoints::test_login_blocks_unverified_user -v
```

---

## Phase 12: Monitoring & Logging (2 minutes)

### Enable Email Service Logging
Verify in logs that emails are being sent:
```bash
# Look for success messages
grep "Email sent successfully" /var/log/talkyai/backend.log

# Look for errors
grep -i "email\|smtp" /var/log/talkyai/backend.log | grep -i error
```
- [ ] Should see "Email sent successfully" messages
- [ ] No SMTP errors
- [ ] No connection timeouts

### Check Audit Logs
```sql
SELECT event_type, action, created_at FROM audit_logs 
WHERE event_type IN ('USER_CREATED', 'USER_UPDATED') 
ORDER BY created_at DESC LIMIT 10;
```
- [ ] Should see USER_CREATED events when registering
- [ ] Should see email_verified action when verifying

---

## Phase 13: Documentation Update (2 minutes)

### Update Team Documentation
- [ ] Add environment variable setup guide to wiki
- [ ] Add Microsoft 365 admin steps to runbook
- [ ] Add troubleshooting section to team docs
- [ ] Create deployment guide for new environments

### Update CHANGELOG
- [ ] Document email verification feature
- [ ] Note Microsoft 365 SMTP configuration
- [ ] List new endpoints: POST /auth/register, GET /auth/verify-email
- [ ] Note database schema changes

---

## Phase 14: Monitoring Setup (3 minutes)

### Configure Alerts (if applicable)
- [ ] Email failure alert: Log parsing for "Failed to send email"
- [ ] SMTP auth failure alert: Monitor for "SMTPAuthenticationError"
- [ ] Email queue monitoring: Track pending verifications
- [ ] Unverified user tracking: Monitor for stuck unverified accounts

### Add Dashboard Metrics (optional)
- [ ] Registrations per hour
- [ ] Email verification rate
- [ ] Email delivery success rate
- [ ] Average time to verify
- [ ] Login blocked (unverified) rate

---

## Phase 15: Security Review (2 minutes)

- [ ] No hardcoded secrets in code
- [ ] .env file in .gitignore
- [ ] No plaintext passwords in logs
- [ ] Tokens are hashed before storage
- [ ] STARTTLS enabled for SMTP
- [ ] Rate limiting configured
- [ ] Audit logging enabled
- [ ] No email enumeration possible
- [ ] Proper HTTP status codes used
- [ ] Token expiration (24 hours) enforced

---

## Phase 16: Handoff & Documentation (5 minutes)

### Prepare Handoff Materials
- [ ] Print/export this checklist
- [ ] Prepare `EMAIL_SETUP_QUICK_START.md` for operations team
- [ ] Create runbook entry for "Email Verification System"
- [ ] Document backup procedure for email failures

### Team Training
- [ ] Share plan document with team
- [ ] Walkthrough of verification flow
- [ ] Explain token security approach
- [ ] Troubleshooting scenarios

---

## Sign-Off

### Completed By
**Name:** _______________________  
**Title:** _______________________  
**Date:** ________________________  

### Verified By
**Name:** _______________________  
**Title:** _______________________  
**Date:** ________________________  

### Approved For Production
**Name:** _______________________  
**Title:** _______________________  
**Date:** ________________________  

---

## Post-Implementation Follow-up (Next 7 days)

- [ ] Monitor email delivery logs
- [ ] Track verification rate (should be >80%)
- [ ] Monitor SMTP connection health
- [ ] Check for any authentication failures
- [ ] Gather user feedback on email clarity
- [ ] Plan Phase 2: Resend verification email feature

---

## Common Issues & Quick Fixes

| Issue | Quick Check |
|-------|-------------|
| **Emails not sending** | `psql -c "SELECT * FROM security_logs WHERE error LIKE '%SMTP%';"` |
| **Login blocked for verified** | `psql -c "SELECT is_verified FROM user_profiles WHERE email='...';"` |
| **SMTP auth fails** | Check SMTP AUTH enabled in M365, verify App Password |
| **Token always invalid** | Check token hash matches: `SELECT verification_token FROM user_profiles` |
| **Connection timeout** | Check `telnet smtp.office365.com 587` works |

---

## Success Criteria

✅ All tests pass  
✅ Emails send successfully  
✅ Unverified users cannot log in  
✅ Verified users can log in  
✅ Token expires after 24 hours  
✅ No hardcoded secrets  
✅ Audit logs record events  
✅ Monitoring and alerts configured  

---

**Congratulations!** Your email verification system is now live and production-ready. 🎉

For questions, refer to:
- Full plan: `day 1 plan.md`
- Quick setup: `EMAIL_SETUP_QUICK_START.md`
- Troubleshooting: Section 9 of plan document
