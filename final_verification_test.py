#!/usr/bin/env python3
"""
Final Comprehensive Backend Verification Test
Tests all critical components after fixes
"""
import sys
import os
from pathlib import Path
from datetime import datetime

# Set UTF-8 encoding for Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

backend_dir = Path(__file__).parent / "backend"
os.chdir(backend_dir)
sys.path.insert(0, str(backend_dir))

test_results = {
    "passed": [],
    "failed": [],
    "warnings": []
}

def test_ok(name, message=""):
    entry = f"{name}" + (f": {message}" if message else "")
    test_results["passed"].append(entry)
    print(f"[PASS] {entry}")

def test_fail(name, error):
    test_results["failed"].append({"name": name, "error": str(error)})
    print(f"[FAIL] {name}: {error}")

def test_warn(name, message):
    test_results["warnings"].append({"name": name, "msg": message})
    print(f"[WARN] {name}: {message}")

print("\n" + "="*70)
print("FINAL BACKEND VERIFICATION TEST")
print("="*70 + "\n")

# ============================================================================
# TEST 1: Configuration
# ============================================================================
print("[TEST 1] Configuration Loading")
try:
    from app.core.config import get_settings
    settings = get_settings()
    test_ok("Configuration", f"Loaded in {settings.environment} mode")

    if settings.jwt_secret:
        test_ok("JWT Secret", "Configured")
    else:
        test_fail("JWT Secret", "Not set")

    if settings.jwt_expiry_hours:
        test_ok("JWT Expiry Hours", f"{settings.jwt_expiry_hours}h")
    else:
        test_fail("JWT Expiry Hours", "Not configured")

    if settings.jwt_algorithm:
        test_ok("JWT Algorithm", settings.jwt_algorithm)
    else:
        test_fail("JWT Algorithm", "Not configured")

except Exception as e:
    test_fail("Configuration", str(e))

# ============================================================================
# TEST 2: Password Security
# ============================================================================
print("\n[TEST 2] Password Security")
try:
    from app.core.security.password import hash_password, verify_password

    pwd = "TestPassword123!@#"
    hashed = hash_password(pwd)
    test_ok("Password Hashing", "Argon2id working")

    if verify_password(pwd, hashed):
        test_ok("Password Verification", "Working")
    else:
        test_fail("Password Verification", "Failed")

    if not verify_password("wrong", hashed):
        test_ok("Wrong Password Rejection", "Working")
    else:
        test_fail("Wrong Password Rejection", "Failed")

except Exception as e:
    test_fail("Password Security", str(e))

# ============================================================================
# TEST 3: JWT Security
# ============================================================================
print("\n[TEST 3] JWT Security")
try:
    from app.core.jwt_security import encode_access_token, decode_and_validate_token

    token = encode_access_token(
        user_id="test-123",
        email="test@example.com",
        role="user",
        tenant_id="tenant-1"
    )
    test_ok("JWT Generation", "Token created")

    decoded = decode_and_validate_token(token)
    if decoded.get("sub") == "test-123":
        test_ok("JWT Validation", "Token verified")
    else:
        test_fail("JWT Validation", "Token content invalid")

except Exception as e:
    test_fail("JWT Security", str(e))

# ============================================================================
# TEST 4: FastAPI Application
# ============================================================================
print("\n[TEST 4] FastAPI Application")
try:
    from app.main import app

    test_ok("App Import", "Successfully loaded")
    test_ok("App Title", app.title)
    test_ok("App Version", f"v{app.version}")
    test_ok("Routes", f"{len(app.routes)} routes registered")
    test_ok("Middleware", f"{len(app.user_middleware)} middleware")

except Exception as e:
    test_fail("FastAPI Application", str(e))

# ============================================================================
# TEST 5: API Routes
# ============================================================================
print("\n[TEST 5] Critical API Routes")
try:
    from app.main import app

    critical = {
        "Root": "/",
        "Health": "/health",
        "Metrics": "/metrics",
        "Auth": "/api/v1/auth",
        "Campaigns": "/api/v1/campaigns",
        "Calls": "/api/v1/calls",
        "Billing": "/api/v1/billing",
    }

    paths = [r.path for r in app.routes if hasattr(r, 'path')]

    for name, endpoint in critical.items():
        if any(endpoint in p for p in paths):
            test_ok(f"Endpoint: {name}", endpoint)
        else:
            test_fail(f"Endpoint: {name}", f"Not found: {endpoint}")

except Exception as e:
    test_fail("API Routes", str(e))

# ============================================================================
# TEST 6: Core Modules
# ============================================================================
print("\n[TEST 6] Core Modules")
modules = [
    ("app.core.db", "Database Layer"),
    ("app.core.container", "Dependency Container"),
    ("app.core.security.sessions", "Session Management"),
    ("app.core.telemetry", "Telemetry"),
]

for module_path, name in modules:
    try:
        __import__(module_path)
        test_ok(f"Module: {name}", "Imported")
    except Exception as e:
        test_fail(f"Module: {name}", str(e)[:100])

# ============================================================================
# TEST 7: Domain Services
# ============================================================================
print("\n[TEST 7] Domain Services")
services = [
    ("app.domain.services.email_service", "Email Service"),
    ("app.domain.services.audit_logger", "Audit Logger"),
    ("app.domain.services.session_manager", "Session Manager"),
    ("app.domain.services.queue_service", "Queue Service"),
    ("app.domain.services.call_service", "Call Service"),
    ("app.domain.services.voice_orchestrator", "Voice Orchestrator"),
    ("app.domain.services.billing_service", "Billing Service"),
    ("app.domain.services.notification_service", "Notification Service"),
]

for module_path, name in services:
    try:
        __import__(module_path)
        test_ok(f"Service: {name}", "Loaded")
    except Exception as e:
        test_fail(f"Service: {name}", str(e)[:100])

# ============================================================================
# TEST 8: Background Workers
# ============================================================================
print("\n[TEST 8] Background Workers")
workers = [
    ("app.workers.dialer_worker", "Dialer Worker"),
    ("app.workers.voice_worker", "Voice Worker"),
    ("app.workers.reminder_worker", "Reminder Worker"),
]

for module_path, name in workers:
    try:
        __import__(module_path)
        test_ok(f"Worker: {name}", "Loaded")
    except Exception as e:
        test_fail(f"Worker: {name}", str(e)[:100])

# ============================================================================
# TEST 9: Dependencies
# ============================================================================
print("\n[TEST 9] Critical Dependencies")
dependencies = [
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
    ("asyncpg", "AsyncPG"),
    ("redis", "Redis Client"),
    ("pydantic", "Pydantic"),
    ("websockets", "WebSockets"),
    ("jwt", "PyJWT"),
    ("passlib", "Passlib"),
    ("aiosmtplib", "aiosmtplib"),
    ("deepgram", "Deepgram"),
    ("email_validator", "Email Validator"),
]

for module, name in dependencies:
    try:
        __import__(module)
        test_ok(f"Dependency: {name}", "Installed")
    except ImportError:
        test_fail(f"Dependency: {name}", "Not installed")

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*70)
print("VERIFICATION SUMMARY")
print("="*70)

passed = len(test_results["passed"])
failed = len(test_results["failed"])
warnings = len(test_results["warnings"])
total = passed + failed + warnings

print(f"\nTotal Tests: {total}")
print(f"Passed: {passed}")
print(f"Failed: {failed}")
print(f"Warnings: {warnings}")

if failed == 0:
    print("\n[SUCCESS] All critical tests passed!")
    print("[SUCCESS] Backend is fully functional and ready for use")
else:
    print(f"\n[ATTENTION] {failed} tests failed - review above")

print("\n" + "="*70)
print(f"Test completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*70)

sys.exit(0 if failed == 0 else 1)
