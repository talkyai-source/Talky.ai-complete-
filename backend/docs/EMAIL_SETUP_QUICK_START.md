# Email Verification System - Quick Start Setup Guide

> **Timeline:** ~15 minutes to configure, 5-10 minutes for testing
> **Audience:** DevOps engineers, backend developers

---

## Prerequisites

- ✅ Access to [Microsoft 365 Admin Center](https://admin.microsoft.com)
- ✅ Access to [GoDaddy DNS settings](https://dcc.godaddy.com)
- ✅ Backend FastAPI server running
- ✅ PostgreSQL database with migrations applied

---

## Step 1: Prepare Environment Variables (5 minutes)

### 1.1 Copy Template
```bash
cd /path/to/backend
cp .env.example .env
```

### 1.2 Get Microsoft 365 App Password

**Option A: If you have MFA enabled (RECOMMENDED)**

1. Go to [Microsoft Account Security](https://account.microsoft.com/security)
2. Click **App passwords** in the left sidebar
3. If prompted, sign in with your Microsoft 365 account
4. Select:
   - App: **Mail**
   - Device: **Other (custom)** → Type "Talky.ai Email Service"
5. Click **Create**
6. Copy the 16-character password displayed
7. Paste into `.env` file

**Option B: If you DON'T have MFA enabled (ALTERNATIVE)**

Use your Microsoft 365 account password directly (less secure, but works)

### 1.3 Fill in .env File

```bash
# Edit .env with your values
nano .env

# Required:
EMAIL_USER=noreply@talkleeai.com
EMAIL_PASS=<paste_app_password_here>
FRONTEND_URL=https://talkleeai.com
API_BASE_URL=https://api.talkleeai.com
```

**Example .env:**
```env
EMAIL_USER=noreply@talkleeai.com
EMAIL_PASS=abcdxyzw1234abcd
FRONTEND_URL=https://talkleeai.com
API_BASE_URL=https://api.talkleeai.com
DATABASE_URL=postgresql://talkyai:password@localhost:5432/talkyai
JWT_SECRET=your_long_secret_here
ENVIRONMENT=production
DEBUG=false
```

---

## Step 2: Enable SMTP AUTH in Microsoft 365 (3 minutes)

### 2.1 Access Admin Center
1. Go to [Microsoft 365 Admin Center](https://admin.microsoft.com)
2. Sign in with your admin account

### 2.2 Enable SMTP AUTH
1. Left sidebar: **Settings** → **Org Settings**
2. Click **Org Settings** tab
3. Scroll down and find **Mail Settings**
4. Click **Edit** next to "Mail settings"
5. Toggle **SMTP AUTH** to **ON** / **Enabled**
6. Click **Save**

⏱️ **Wait:** Changes take 5-10 minutes to propagate

---

## Step 3: Apply Database Migration (2 minutes)

```bash
# Apply migration to your PostgreSQL database
psql -U talkyai -h localhost -d talkyai -f backend/database/migrations/day1_email_verification.sql

# Verify success
psql -U talkyai -h localhost -d talkyai -c "SELECT column_name FROM information_schema.columns WHERE table_name='user_profiles' AND column_name LIKE 'is_verified%';"
```

Expected output:
```
      column_name
--------------------
 is_verified
 verification_token
 verification_token_expires_at
 email_verified_at
```

---

## Step 4: Verify Dependencies (2 minutes)

```bash
# Ensure aiosmtplib is installed
pip install aiosmtplib

# Or if using requirements.txt
pip install -r requirements.txt
```

---

## Step 5: Test SMTP Connection (5 minutes)

### Quick Test Script
```python
# Save as test_smtp.py
import asyncio
import aiosmtplib

async def test_smtp():
    try:
        async with aiosmtplib.SMTP(
            hostname='smtp.office365.com',
            port=587,
            use_tls=True
        ) as smtp:
            await smtp.login('noreply@talkleeai.com', 'YOUR_APP_PASSWORD')
            print('✅ SMTP connection successful!')
            return True
    except Exception as e:
        print(f'❌ SMTP connection failed: {e}')
        return False

asyncio.run(test_smtp())
```

Run test:
```bash
python test_smtp.py
```

Expected output:
```
✅ SMTP connection successful!
```

---

## Step 6: Restart Backend Service (1 minute)

```bash
# If using Docker
docker-compose restart backend

# If using systemd
sudo systemctl restart talkyai-backend

# If running locally
# Stop the running server and restart:
# python -m uvicorn app.main:app --reload
```

---

## Step 7: Test Email Verification Flow (3 minutes)

### 7.1 Register a User
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "TestPass123!",
    "business_name": "Test Company"
  }'
```

Expected response:
```json
{
  "access_token": "eyJ0eXAi...",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "testuser@example.com",
  "message": "Registration successful. Please verify your email to enable full access."
}
```

### 7.2 Get Verification Token from Database
```bash
psql -U talkyai -h localhost -d talkyai -c \
  "SELECT email, verification_token FROM user_profiles WHERE email='testuser@example.com';"
```

Copy the `verification_token` value

### 7.3 Verify Email
```bash
curl http://localhost:8000/api/v1/auth/verify-email?token=<paste_token_here>
```

Expected response:
```json
{
  "message": "Email verified successfully! You can now log in.",
  "email": "testuser@example.com"
}
```

### 7.4 Test Login
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "TestPass123!"
  }'
```

Expected response:
```json
{
  "access_token": "eyJ0eXAi...",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "testuser@example.com",
  "message": "Login successful."
}
```

---

## Step 8: Verify Email Actually Sends (Optional - Production Check)

### 8.1 Register with Real Email
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "your-real-email@gmail.com",
    "password": "TestPass123!",
    "business_name": "Test Company"
  }'
```

### 8.2 Check Email Inbox
- Check inbox (including spam/junk folder)
- Should receive email from `noreply@talkleeai.com`
- Subject: "Please verify your email address"
- Contains clickable verification button

---

## Troubleshooting Quick Reference

| Issue | Solution |
|-------|----------|
| **SMTP Auth Failed** | ✅ Use App Password, not account password |
| **Email Not Sending** | ✅ Check SMTP AUTH enabled in M365 Admin Center |
| **Connection Timeout** | ✅ Verify port 587 not blocked by firewall |
| **Token Invalid** | ✅ Token expires after 24 hours, regenerate registration |
| **Login Still Blocked** | ✅ Verify `is_verified=true` in database: `SELECT is_verified FROM user_profiles WHERE email='...';` |

---

## DNS Configuration (For Production Email Delivery)

### Add SPF Record (GoDaddy DNS)
```
v=spf1 include:outlook.com ~all
```

### Enable DKIM (Microsoft 365)
1. Microsoft 365 Admin Center → **Settings** → **Domains**
2. Select `talkleeai.com`
3. Check DKIM is enabled
4. Copy DKIM DNS records to GoDaddy

### Add DMARC Record (GoDaddy DNS)
```
v=DMARC1; p=reject; rua=mailto:postmaster@talkleeai.com
```

⚠️ **Note:** Without SPF/DKIM/DMARC, emails may be marked as spam in production.

---

## Files Modified

| File | Changes |
|------|---------|
| `backend/database/migrations/day1_email_verification.sql` | New - Database schema |
| `backend/app/domain/services/email_service.py` | New - SMTP email service |
| `backend/app/core/security/verification_tokens.py` | New - Token utilities |
| `backend/app/core/config.py` | Updated - Added email_user, email_pass |
| `backend/app/api/v1/endpoints/auth.py` | Updated - Register, verify, login endpoints |
| `backend/tests/test_email_verification.py` | New - Integration tests |
| `.env.example` | New - Configuration template |

---

## Success Checklist

- [ ] Email credentials configured in .env
- [ ] SMTP AUTH enabled in Microsoft 365
- [ ] Database migration applied
- [ ] SMTP connection test passed
- [ ] Backend restarted
- [ ] Test registration works
- [ ] Email verification endpoint works
- [ ] Login works after verification
- [ ] Login blocked before verification (correct!)

---

## Need Help?

**Problem:** "SMTP AUTH is disabled"
→ [Go to Step 2](#step-2-enable-smtp-auth-in-microsoft-365-3-minutes)

**Problem:** "Authentication unsuccessful"
→ Use App Password instead of account password

**Problem:** Email never arrives
→ Check spam folder, verify SPF/DKIM records, check logs

**Problem:** Verification token invalid
→ Token expires in 24 hours, register a new user

---

## Next Steps

After verification setup is complete:

1. **Email Templates:** Customize HTML email templates in `email_service.py`
2. **Resend Email:** Implement `POST /api/v1/auth/resend-verification-email`
3. **Email Cleanup:** Add background job to delete unverified users after 7 days
4. **Analytics:** Track verification rates in dashboard
5. **SMS Alternative:** Add SMS verification as backup

---

**Questions?** Check the full documentation in `day 1 plan.md`
