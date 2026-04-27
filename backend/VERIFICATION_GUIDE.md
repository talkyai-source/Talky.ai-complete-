# Email Verification System - Automated Verification Guide

**Purpose:** Run automated end-to-end verification of the email verification system  
**Duration:** 5-10 minutes  
**Output:** HTML verification report + console output

---

## Prerequisites

Before running verification, ensure:

✅ **All code files in place:**
```
backend/database/migrations/day1_email_verification.sql
backend/app/domain/services/email_service.py
backend/app/core/security/verification_tokens.py
backend/app/core/config.py (updated)
backend/app/api/v1/endpoints/auth.py (updated)
backend/tests/test_email_verification.py
```

✅ **Database migration applied:**
```bash
psql -U talkyai -h localhost -d talkyai -f backend/database/migrations/day1_email_verification.sql
```

✅ **Environment variables set in `.env`:**
```bash
EMAIL_USER=noreply@talkleeai.com
EMAIL_PASS=<your_app_password>
FRONTEND_URL=https://talkleeai.com
API_BASE_URL=https://api.talkleeai.com
DATABASE_URL=postgresql://talkyai:password@localhost:5432/talkyai
JWT_SECRET=<your_jwt_secret>
```

✅ **Dependencies installed:**
```bash
pip install aiosmtplib asyncpg fastapi pydantic
```

---

## Running Verification

### Option 1: Using Python Script (Recommended)

**1. Navigate to backend directory:**
```bash
cd backend
```

**2. Run verification script:**
```bash
python verify_email_system.py
```

**3. Expected output:**
```
================================================================================
EMAIL VERIFICATION SYSTEM - AUTOMATED VERIFICATION
================================================================================
Started: 2026-04-07T10:30:45.123456

================================================================================
PHASE: Configuration
================================================================================
✅ PASS | EMAIL_USER configured
✅ PASS | EMAIL_PASS configured
✅ PASS | FRONTEND_URL configured
✅ PASS | API_BASE_URL configured
✅ PASS | DATABASE_URL configured
✅ PASS | JWT_SECRET configured
✅ PASS | Config loading works

================================================================================
PHASE: Database
================================================================================
✅ PASS | Database connection
✅ PASS | user_profiles table exists
✅ PASS | is_verified column exists
✅ PASS | verification_token column exists
✅ PASS | verification_token_expires_at column exists
✅ PASS | email_verified_at column exists
✅ PASS | Verification token index exists
✅ PASS | Verification status index exists
✅ PASS | Data consistency constraint exists

================================================================================
PHASE: SMTP Connectivity
================================================================================
✅ PASS | SMTP credentials available
✅ PASS | SMTP host is correct
✅ PASS | SMTP port is correct
✅ PASS | SMTP TLS is enabled
✅ PASS | SMTP connection
✅ PASS | SMTP authentication
✅ PASS | Email service loads

================================================================================
PHASE: API Endpoints
================================================================================
✅ PASS | Auth module loads
✅ PASS | POST /auth/register endpoint exists
✅ PASS | GET /auth/verify-email endpoint exists
✅ PASS | POST /auth/login endpoint exists
✅ PASS | Verification tokens module loads
✅ PASS | Email service module loads
✅ PASS | Database migration file exists

================================================================================
VERIFICATION SUMMARY
================================================================================
Total Tests:     28
Passed:          28 ✅
Failed:          0 ❌
Pass Rate:       100.0%
Status:          🟢 ALL TESTS PASSED
Report:          verification_report_2026-04-07T10-30-45.html
================================================================================
```

---

## Understanding Results

### ✅ All Tests Passed (28/28)

**Means:** System is fully functional and ready for production.

**Next steps:**
1. Review HTML report: `verification_report_TIMESTAMP.html`
2. Follow 8-step deployment guide in `EMAIL_SETUP_QUICK_START.md`
3. Deploy to production

### ❌ Some Tests Failed

**Common failures and solutions:**

| Failed Test | Likely Cause | Solution |
|------------|--------------|----------|
| EMAIL_USER configured | Missing EMAIL_USER env var | Add EMAIL_USER=noreply@talkleeai.com to .env |
| EMAIL_PASS configured | Missing/short EMAIL_PASS | Use 16-char App Password from Microsoft 365 |
| SMTP connection | Port 587 blocked | Check firewall allows outbound 587 |
| SMTP authentication | Wrong credentials | Verify EMAIL_USER and EMAIL_PASS, enable SMTP AUTH |
| Database connection | PostgreSQL not running | Start: `postgres -D /usr/local/var/postgres` |
| user_profiles table exists | Migration not applied | Run migration: `psql -f day1_email_verification.sql` |
| Column missing | Migration incomplete | Rerun migration or check for errors |

---

## HTML Report

A detailed HTML report is generated: `verification_report_TIMESTAMP.html`

**Report includes:**
- Executive summary (passed/failed counts, pass rate)
- Detailed results for each phase
- Status indicator (green for pass, red for fail)
- Error details for failed tests
- Recommendations for next steps

**Open report:**
```bash
# macOS
open verification_report_*.html

# Linux
xdg-open verification_report_*.html

# Windows
start verification_report_*.html
```

---

## Verification Phases

### Phase 1: Configuration (7 Tests)
Verifies:
- EMAIL_USER environment variable set
- EMAIL_PASS environment variable set
- FRONTEND_URL configured
- API_BASE_URL configured
- DATABASE_URL configured
- JWT_SECRET configured
- FastAPI config loads correctly

**Status:** ✅ All config variables available

### Phase 2: Database (9 Tests)
Verifies:
- PostgreSQL connection works
- user_profiles table exists
- is_verified column exists
- verification_token column exists
- verification_token_expires_at column exists
- email_verified_at column exists
- Verification token index exists
- Verification status index exists
- Data consistency constraint exists

**Status:** ✅ Database schema complete

### Phase 3: SMTP Connectivity (7 Tests)
Verifies:
- SMTP credentials available
- SMTP host configured correctly (smtp.office365.com)
- SMTP port correct (587)
- STARTTLS enabled
- SMTP connection succeeds
- SMTP authentication works
- EmailService loads correctly

**Status:** ✅ SMTP ready to send emails

### Phase 4: API Endpoints (5 Tests)
Verifies:
- Auth module loads
- POST /auth/register endpoint exists and is callable
- GET /auth/verify-email endpoint exists and is callable
- POST /auth/login endpoint exists and is callable
- Token utilities module loads
- Email service module loads
- Database migration file exists

**Status:** ✅ All endpoints implemented

---

## Troubleshooting

### "Failed to connect to database"
```bash
# Check PostgreSQL is running
pg_isready -h localhost

# Start PostgreSQL if needed
brew services start postgresql  # macOS
systemctl start postgresql       # Linux
```

### "SMTP authentication failed"
```bash
# Check SMTP AUTH is enabled in Microsoft 365 Admin Center
# Check EMAIL_USER is correct: noreply@talkleeai.com
# Check EMAIL_PASS is correct (16-char App Password)
# Try password in Gmail/Outlook - if works there, issue is elsewhere
```

### "DATABASE_URL not set"
```bash
# Create/update .env file
cp .env.example .env

# Edit and set correct connection string
nano .env
# DATABASE_URL=postgresql://talkyai:password@localhost:5432/talkyai
```

### "user_profiles table does not exist"
```bash
# Apply migration manually
psql -U talkyai -h localhost -d talkyai << 'EOF'
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS verification_token TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS verification_token_expires_at TIMESTAMPTZ;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
EOF
```

### "Module not found" errors
```bash
# Install dependencies
pip install aiosmtplib asyncpg fastapi pydantic pydantic-settings slowapi

# Ensure backend directory is in Python path
export PYTHONPATH="${PYTHONPATH}:/path/to/backend"
```

---

## Verification Checklist

Use this checklist while running verification:

- [ ] Verification script starts without errors
- [ ] Configuration phase: All 7 tests pass
- [ ] Database phase: All 9 tests pass
- [ ] SMTP phase: All 7 tests pass
- [ ] API phase: All 5 tests pass
- [ ] Total: 28/28 tests pass
- [ ] Pass rate: 100%
- [ ] HTML report generated successfully
- [ ] No errors in console output

---

## Next Steps After Verification

### If All Tests Pass (✅ 28/28)

1. **Review Report**
   ```bash
   # Open HTML report in browser
   open verification_report_*.html
   ```

2. **Follow Deployment Guide**
   ```bash
   # Read quick start guide
   cat docs/EMAIL_SETUP_QUICK_START.md
   ```

3. **Deploy to Production**
   - Follow 8-step deployment process
   - Monitor email delivery logs
   - Test verification flow with real user

### If Some Tests Fail (❌ <28/28)

1. **Check Error Messages**
   - Review console output
   - Check HTML report for details

2. **Fix Issues**
   - See troubleshooting section above
   - Refer to specific failed test

3. **Re-run Verification**
   ```bash
   python verify_email_system.py
   ```

4. **Check Documentation**
   - See `day 1 plan.md` Section 9 (Troubleshooting)
   - See `EMAIL_SETUP_QUICK_START.md` (Setup guide)

---

## Automated Verification in CI/CD

To integrate verification into your CI/CD pipeline:

### GitHub Actions
```yaml
name: Email Verification System

on: [push, pull_request]

jobs:
  verify:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:14
        env:
          POSTGRES_USER: talkyai
          POSTGRES_PASSWORD: password
          POSTGRES_DB: talkyai
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: 3.10

      - name: Install dependencies
        run: pip install aiosmtplib asyncpg fastapi pydantic pydantic-settings

      - name: Apply database migration
        run: |
          psql -h localhost -U talkyai -d talkyai -f backend/database/migrations/day1_email_verification.sql
        env:
          PGPASSWORD: password

      - name: Run verification
        run: |
          cd backend
          python verify_email_system.py
        env:
          EMAIL_USER: test@example.com
          EMAIL_PASS: testpass123456
          FRONTEND_URL: http://localhost:3000
          API_BASE_URL: http://localhost:8000
          DATABASE_URL: postgresql://talkyai:password@localhost:5432/talkyai
          JWT_SECRET: testsecret1234567890123456789012

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v2
        with:
          name: verification-report
          path: backend/verification_report_*.html
```

---

## Support

**Questions about verification?**
- Check: `day 1 plan.md` (Section 9 - Troubleshooting)
- Check: `EMAIL_SETUP_QUICK_START.md` (Troubleshooting section)
- Review: HTML report for specific error details

**Need help running the script?**
- Ensure all prerequisites are met
- Check environment variables are set
- Verify database is running and accessible
- Check network allows port 587 outbound

---

## Summary

**Automated Verification Process:**

```
1. Run: python verify_email_system.py
   ↓
2. Script tests 28 components across 4 phases
   ↓
3. Generates HTML report + console output
   ↓
4. Shows: ✅ All pass → Ready to deploy
           ❌ Some fail → Fix issues and retry
   ↓
5. Report available: verification_report_TIMESTAMP.html
```

**Time to verify:** 5-10 minutes  
**No manual testing needed:** Fully automated  
**Production ready:** When all tests pass

---

**Start verification:**
```bash
cd backend
python verify_email_system.py
```

**Expected result:** ✅ 28/28 tests pass
