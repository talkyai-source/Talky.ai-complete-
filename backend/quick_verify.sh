#!/bin/bash
# Quick Email Verification System Verification Script
# Fast checks without async code

set -e

echo "================================================================================"
echo "EMAIL VERIFICATION SYSTEM - QUICK VERIFICATION"
echo "================================================================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

passed=0
failed=0

# Function to check and report
check() {
    local test_name=$1
    local result=$2

    if [ $result -eq 0 ]; then
        echo -e "${GREEN}✅ PASS${NC} | $test_name"
        ((passed++))
    else
        echo -e "${RED}❌ FAIL${NC} | $test_name"
        ((failed++))
    fi
}

# ============================================================================
# PHASE 1: CONFIGURATION CHECKS
# ============================================================================
echo "PHASE 1: Configuration Checks"
echo "=============================================================================="

# Check environment variables from .env file
if [ -f .env ]; then
    # Source .env file
    export $(cat .env | grep -v '#' | xargs)
fi

# EMAIL_USER
if [ -n "$EMAIL_USER" ] && [[ "$EMAIL_USER" == *"@"* ]]; then
    check "EMAIL_USER configured" 0
else
    check "EMAIL_USER configured" 1
    echo "  → Set EMAIL_USER=noreply@talkleeai.com in .env"
fi

# EMAIL_PASS
if [ -n "$EMAIL_PASS" ] && [ ${#EMAIL_PASS} -gt 8 ]; then
    check "EMAIL_PASS configured" 0
else
    check "EMAIL_PASS configured" 1
    echo "  → Set EMAIL_PASS=<app_password> in .env (16+ characters)"
fi

# FRONTEND_URL
if [ -n "$FRONTEND_URL" ] && [[ "$FRONTEND_URL" == http* ]]; then
    check "FRONTEND_URL configured" 0
else
    check "FRONTEND_URL configured" 1
    echo "  → Set FRONTEND_URL=https://talkleeai.com in .env"
fi

# API_BASE_URL
if [ -n "$API_BASE_URL" ] && [[ "$API_BASE_URL" == http* ]]; then
    check "API_BASE_URL configured" 0
else
    check "API_BASE_URL configured" 1
    echo "  → Set API_BASE_URL=https://api.talkleeai.com in .env"
fi

# DATABASE_URL
if [ -n "$DATABASE_URL" ] && [[ "$DATABASE_URL" == postgresql* ]]; then
    check "DATABASE_URL configured" 0
else
    check "DATABASE_URL configured" 1
    echo "  → Set DATABASE_URL=postgresql://... in .env"
fi

# JWT_SECRET
if [ -n "$JWT_SECRET" ] && [ ${#JWT_SECRET} -gt 32 ]; then
    check "JWT_SECRET configured" 0
else
    check "JWT_SECRET configured" 1
    echo "  → Set JWT_SECRET=<long_secret> in .env (32+ characters)"
fi

# ============================================================================
# PHASE 2: FILE STRUCTURE CHECKS
# ============================================================================
echo ""
echo "PHASE 2: File Structure"
echo "=============================================================================="

# Check code files exist
[ -f "database/migrations/day1_email_verification.sql" ]
check "Migration file exists" $?

[ -f "app/domain/services/email_service.py" ]
check "Email service file exists" $?

[ -f "app/core/security/verification_tokens.py" ]
check "Token utilities file exists" $?

[ -f "tests/test_email_verification.py" ]
check "Tests file exists" $?

[ -f ".env.example" ]
check ".env.example file exists" $?

# ============================================================================
# PHASE 3: DATABASE CHECKS
# ============================================================================
echo ""
echo "PHASE 3: Database"
echo "=============================================================================="

# Check if psql is available
if command -v psql &> /dev/null; then
    # Test database connection
    if psql "$DATABASE_URL" -c "SELECT 1" &>/dev/null; then
        check "Database connection" 0

        # Check if user_profiles table exists
        if psql "$DATABASE_URL" -c "SELECT 1 FROM user_profiles LIMIT 1" &>/dev/null; then
            check "user_profiles table exists" 0

            # Check columns
            if psql "$DATABASE_URL" -c "SELECT is_verified FROM user_profiles LIMIT 1" &>/dev/null 2>&1; then
                check "is_verified column exists" 0
            else
                check "is_verified column exists" 1
                echo "  → Run migration: psql -f database/migrations/day1_email_verification.sql"
            fi

            if psql "$DATABASE_URL" -c "SELECT verification_token FROM user_profiles LIMIT 1" &>/dev/null 2>&1; then
                check "verification_token column exists" 0
            else
                check "verification_token column exists" 1
            fi
        else
            check "user_profiles table exists" 1
            echo "  → Check database, user_profiles table not found"
        fi
    else
        check "Database connection" 1
        echo "  → Cannot connect to database. Check DATABASE_URL"
        echo "    Value: $DATABASE_URL"
    fi
else
    echo -e "${YELLOW}⚠️  SKIP${NC} | psql not installed, skipping database checks"
fi

# ============================================================================
# PHASE 4: PYTHON CHECKS
# ============================================================================
echo ""
echo "PHASE 4: Python Module Checks"
echo "=============================================================================="

# Check Python version
python_version=$(python3 --version 2>&1 | grep -oP '(?<=Python )\d+\.\d+')
if [ -n "$python_version" ]; then
    check "Python 3 available (v$python_version)" 0
else
    check "Python 3 available" 1
fi

# Check required packages
packages=("aiosmtplib" "asyncpg" "fastapi" "pydantic")
for pkg in "${packages[@]}"; do
    if python3 -c "import $pkg" 2>/dev/null; then
        check "$pkg package installed" 0
    else
        check "$pkg package installed" 1
        echo "  → Install: pip install $pkg"
    fi
done

# ============================================================================
# SUMMARY
# ============================================================================
echo ""
echo "================================================================================"
echo "QUICK VERIFICATION SUMMARY"
echo "================================================================================"

total=$((passed + failed))
percentage=$((passed * 100 / total))

echo "Total Checks:     $total"
echo "Passed:           $passed ✅"
echo "Failed:           $failed ❌"
echo "Pass Rate:        ${percentage}%"

if [ $failed -eq 0 ]; then
    echo ""
    echo -e "${GREEN}🟢 ALL QUICK CHECKS PASSED${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Run full verification: python verify_email_system.py"
    echo "  2. Review: VERIFICATION_GUIDE.md"
    echo "  3. Deploy: Follow EMAIL_SETUP_QUICK_START.md"
    exit 0
else
    echo ""
    echo -e "${RED}🔴 SOME CHECKS FAILED${NC}"
    echo ""
    echo "Fix issues above, then run again:"
    echo "  bash quick_verify.sh"
    exit 1
fi
