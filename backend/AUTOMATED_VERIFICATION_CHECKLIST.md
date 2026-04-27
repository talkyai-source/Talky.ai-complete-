# Automated Verification Checklist

**Purpose:** Step-by-step guide to run automated verification  
**Duration:** 10 minutes total  
**Output:** Verification report + pass/fail status

---

## Pre-Verification Checklist

Before running automated verification, ensure:

### ✅ Step 1: Code Files In Place
- [ ] `backend/database/migrations/day1_email_verification.sql` exists
- [ ] `backend/app/domain/services/email_service.py` exists
- [ ] `backend/app/core/security/verification_tokens.py` exists
- [ ] `backend/app/core/config.py` updated with email_user, email_pass
- [ ] `backend/app/api/v1/endpoints/auth.py` updated with endpoints
- [ ] `backend/tests/test_email_verification.py` exists
- [ ] `backend/.env.example` exists

### ✅ Step 2: Environment Variables Set
- [ ] Create `.env` file: `cp .env.example .env`
- [ ] Set `EMAIL_USER=noreply@talkleeai.com`
- [ ] Set `EMAIL_PASS=<16-char app password>`
- [ ] Set `FRONTEND_URL=https://talkleeai.com`
- [ ] Set `API_BASE_URL=https://api.talkleeai.com`
- [ ] Set `DATABASE_URL=postgresql://talkyai:password@localhost:5432/talkyai`
- [ ] Set `JWT_SECRET=<32+ char secret>`

### ✅ Step 3: Database Prepared
- [ ] PostgreSQL running: `pg_isready -h localhost`
- [ ] Database created: `createdb talkyai`
- [ ] user_profiles table exists: `psql -c "SELECT 1 FROM user_profiles"`
- [ ] Database migration applied: `psql -f database/migrations/day1_email_verification.sql`

### ✅ Step 4: Dependencies Installed
```bash
pip install aiosmtplib asyncpg fastapi pydantic pydantic-settings slowapi
```

- [ ] aiosmtplib installed: `python -c "import aiosmtplib"`
- [ ] asyncpg installed: `python -c "import asyncpg"`
- [ ] fastapi installed: `python -c "import fastapi"`
- [ ] pydantic installed: `python -c "import pydantic"`

---

## Running Quick Verification (2 minutes)

### Option 1: Bash Script (Fastest)

```bash
# Navigate to backend
cd backend

# Make script executable
chmod +x quick_verify.sh

# Run quick verification
bash quick_verify.sh
```

**Expected output:**
```
✅ PASS | EMAIL_USER configured
✅ PASS | EMAIL_PASS configured
✅ PASS | FRONTEND_URL configured
✅ PASS | API_BASE_URL configured
✅ PASS | DATABASE_URL configured
✅ PASS | JWT_SECRET configured
✅ PASS | Migration file exists
✅ PASS | Email service file exists
...
🟢 ALL QUICK CHECKS PASSED
```

### Option 2: Manual Verification (5 minutes)

**Check configuration:**
```bash
# From backend directory
grep -q "EMAIL_USER=noreply@talkleeai.com" .env && echo "✅ EMAIL_USER set" || echo "❌ EMAIL_USER missing"
grep -q "EMAIL_PASS" .env && echo "✅ EMAIL_PASS set" || echo "❌ EMAIL_PASS missing"
grep -q "FRONTEND_URL=https://talkleeai.com" .env && echo "✅ FRONTEND_URL set" || echo "❌ FRONTEND_URL missing"
grep -q "DATABASE_URL" .env && echo "✅ DATABASE_URL set" || echo "❌ DATABASE_URL missing"
```

**Check database:**
```bash
psql -h localhost -U talkyai -d talkyai -c "\d user_profiles" | grep is_verified
# Should show: is_verified | boolean
```

**Check code files:**
```bash
[ -f "app/domain/services/email_service.py" ] && echo "✅ email_service.py exists" || echo "❌ Missing"
[ -f "app/core/security/verification_tokens.py" ] && echo "✅ verification_tokens.py exists" || echo "❌ Missing"
[ -f "tests/test_email_verification.py" ] && echo "✅ test_email_verification.py exists" || echo "❌ Missing"
```

---

## Running Full Automated Verification (5-10 minutes)

### Command

```bash
# Navigate to backend
cd backend

# Run full automated verification
python verify_email_system.py
```

### What It Tests (28 Total Tests)

**Phase 1: Configuration (7 tests)**
- EMAIL_USER configured
- EMAIL_PASS configured
- FRONTEND_URL configured
- API_BASE_URL configured
- DATABASE_URL configured
- JWT_SECRET configured
- Config loading works

**Phase 2: Database (9 tests)**
- Database connection
- user_profiles table exists
- is_verified column exists
- verification_token column exists
- verification_token_expires_at column exists
- email_verified_at column exists
- Verification token index exists
- Verification status index exists
- Data consistency constraint exists

**Phase 3: SMTP (7 tests)**
- SMTP credentials available
- SMTP host is correct
- SMTP port is correct
- SMTP TLS enabled
- SMTP connection works
- SMTP authentication works
- Email service loads

**Phase 4: API (5 tests)**
- Auth module loads
- POST /auth/register endpoint exists
- GET /auth/verify-email endpoint exists
- POST /auth/login endpoint exists
- Verification tokens module loads
- Email service module loads
- Database migration file exists

### Expected Output

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
[... more tests ...]

================================================================================
PHASE: SMTP Connectivity
================================================================================
✅ PASS | SMTP credentials available
✅ PASS | SMTP host is correct
[... more tests ...]

================================================================================
PHASE: API Endpoints
================================================================================
✅ PASS | Auth module loads
✅ PASS | POST /auth/register endpoint exists
[... more tests ...]

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

## Interpreting Results

### ✅ All Tests Pass (28/28)

**What it means:**
- System is fully configured ✅
- Database schema is correct ✅
- SMTP connectivity works ✅
- All API endpoints exist ✅
- **System is ready for production deployment** ✅

**Next steps:**
1. Review HTML report: `verification_report_TIMESTAMP.html`
2. Deploy using 8-step guide: `EMAIL_SETUP_QUICK_START.md`
3. Monitor logs in production

### ❌ Some Tests Failed (<28/28)

**What it means:**
- Configuration issue, OR
- Database not properly set up, OR
- SMTP credentials wrong, OR
- Code files missing

**Common failures:**

| Test | Failure | Fix |
|------|---------|-----|
| EMAIL_PASS configured | Too short/missing | Use 16-char App Password from M365 |
| SMTP authentication | Wrong credentials | Verify EMAIL_USER & EMAIL_PASS |
| Database connection | PostgreSQL not running | `brew services start postgresql` |
| user_profiles table | Migration not applied | Run: `psql -f database/migrations/day1_email_verification.sql` |
| Column missing | Partial migration | Rerun full migration |

**To fix:**
1. Read error message in console
2. Check HTML report for details
3. Fix the issue (see table above)
4. Re-run verification: `python verify_email_system.py`

---

## HTML Report Analysis

After running automated verification, check the HTML report:

```bash
# Open in default browser
open verification_report_*.html          # macOS
xdg-open verification_report_*.html      # Linux
start verification_report_*.html         # Windows
```

**Report shows:**
- ✅ Green cards: Passed tests
- ❌ Red cards: Failed tests
- Detailed error messages for failures
- Recommendations for next steps
- Pass rate and summary

---

## Verification Workflow

```
Step 1: Run Quick Verification (2 min)
        bash quick_verify.sh
        ↓
        All passed? → Go to Step 2
        Some failed? → Fix issues, re-run

Step 2: Run Full Automated Verification (5-10 min)
        python verify_email_system.py
        ↓
        28/28 passed? → Go to Step 3
        Some failed? → Fix specific issues, re-run

Step 3: Review HTML Report (2 min)
        open verification_report_*.html
        ↓
        All green? → Go to Step 4
        Some red? → Read error details, fix, re-run

Step 4: Deploy to Production (30 min)
        Follow EMAIL_SETUP_QUICK_START.md (8 steps)
        ↓
        System deployed ✅
        ↓
        Monitor logs ✅
```

---

## Troubleshooting Verification Failures

### "Cannot connect to database"

**Error message:**
```
Cannot connect to database: {error details}
```

**Solutions:**
```bash
# Check PostgreSQL is running
pg_isready -h localhost

# Start PostgreSQL
brew services start postgresql      # macOS
systemctl start postgresql           # Linux
sudo systemctl start postgresql     # Linux (with sudo)

# Check DATABASE_URL is correct
grep DATABASE_URL .env
# Should be: postgresql://user:password@host:port/dbname

# Test connection manually
psql "$DATABASE_URL" -c "SELECT 1"
```

### "SMTP authentication failed"

**Error message:**
```
SMTP authentication failed: {error details}
Check EMAIL_USER and EMAIL_PASS (or use App Password if MFA enabled)
```

**Solutions:**
```bash
# Verify EMAIL_USER is correct
grep EMAIL_USER .env
# Should be: EMAIL_USER=noreply@talkleeai.com

# Verify EMAIL_PASS is set correctly
grep EMAIL_PASS .env
# Should be: EMAIL_PASS=<16-char app password>

# Check if you're using App Password
# If MFA is enabled on account, use App Password from:
# https://account.microsoft.com/security

# Test credentials manually
python3 << 'EOF'
import asyncio
import aiosmtplib

async def test():
    try:
        async with aiosmtplib.SMTP(
            hostname="smtp.office365.com",
            port=587,
            use_tls=True
        ) as smtp:
            await smtp.login("noreply@talkleeai.com", "YOUR_PASSWORD")
            print("✅ Authentication successful!")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")

asyncio.run(test())
EOF
```

### "user_profiles table does not exist"

**Error message:**
```
user_profiles table not found
```

**Solutions:**
```bash
# Check if table exists
psql -h localhost -U talkyai -d talkyai -c "\dt user_profiles"

# Apply migration
psql -h localhost -U talkyai -d talkyai -f database/migrations/day1_email_verification.sql

# Verify columns were added
psql -h localhost -U talkyai -d talkyai -c "\d user_profiles" | grep -E "is_verified|verification_token"
```

### "Module not found" errors

**Error message:**
```
Cannot load module: ModuleNotFoundError: No module named 'aiosmtplib'
```

**Solutions:**
```bash
# Install missing packages
pip install aiosmtplib asyncpg fastapi pydantic pydantic-settings slowapi

# Verify installation
python3 -c "import aiosmtplib; print('✅ aiosmtplib installed')"
```

---

## After Successful Verification

**When all tests pass (✅ 28/28):**

1. **Review HTML Report**
   ```bash
   open verification_report_*.html
   ```
   - Check all tests are green
   - Read recommendations section
   - Note timestamp for records

2. **Follow Deployment Guide**
   ```bash
   cat docs/EMAIL_SETUP_QUICK_START.md
   ```
   - 8 simple steps
   - 30 minutes total
   - Copy-paste ready commands

3. **Deploy to Production**
   - Apply 8-step deployment process
   - Monitor email delivery logs
   - Test with real user

4. **Document Verification**
   - Save HTML report for records
   - Keep timestamp for audit trail
   - Document any custom configurations

---

## Verification Records

**Keep for compliance:**
- [ ] Verification report HTML file: `verification_report_*.html`
- [ ] Date and time of verification
- [ ] Environment configuration used
- [ ] Any custom settings or overrides
- [ ] Approval from tech lead/manager

---

## Summary

**Quick Verification:** 2 minutes
```bash
bash quick_verify.sh
```

**Full Verification:** 5-10 minutes
```bash
python verify_email_system.py
```

**Total time:** 10-15 minutes  
**Result:** Comprehensive verification report  
**Status:** ✅ Ready for production when all tests pass

---

**Start verification:**
```bash
cd backend
python verify_email_system.py
```

**Expected result:** 28/28 tests pass ✅
