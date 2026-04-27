#!/usr/bin/env python3
"""
Backend Audit Runner - Handles encoding issues on Windows
"""
import sys
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime

# Set UTF-8 encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Change to backend directory
backend_dir = Path(__file__).parent / "backend"
os.chdir(backend_dir)
sys.path.insert(0, str(backend_dir))

results = {
    "timestamp": datetime.now().isoformat(),
    "working": [],
    "failing": [],
    "warnings": [],
}

def log_pass(name, msg=""):
    """Log passing test"""
    entry = f"{name}" + (f": {msg}" if msg else "")
    results["working"].append(entry)
    print(f"[PASS] {entry}")

def log_fail(name, error, cause="", fix=""):
    """Log failing test"""
    entry = {
        "component": name,
        "error": str(error),
        "cause": cause,
        "fix": fix
    }
    results["failing"].append(entry)
    print(f"[FAIL] {name}: {error}")
    if cause:
        print(f"       Root Cause: {cause}")
    if fix:
        print(f"       Fix: {fix}")

def log_warn(name, msg):
    """Log warning"""
    entry = f"{name}: {msg}"
    results["warnings"].append(entry)
    print(f"[WARN] {entry}")


async def main():
    print("\n" + "="*70)
    print("[AUDIT] TALKY.AI BACKEND SYSTEM AUDIT")
    print("="*70 + "\n")

    # 1. Configuration
    print("[TEST] Environment & Configuration")
    try:
        from app.core.config import get_settings
        settings = get_settings()
        log_pass("Configuration", f"Loaded in {settings.environment} mode")
        log_pass("Debug Mode", f"Debug={settings.debug}")
        log_pass("API Prefix", settings.api_prefix)
    except Exception as e:
        log_fail("Configuration", str(e), "Config module failed to load", "Check .env file")

    # 2. Database
    print("\n[TEST] Database (PostgreSQL)")
    try:
        from app.core.db import init_db_pool, close_db_pool
        pool = await init_db_pool()
        log_pass("Database Connection Pool", "Initialized asyncpg pool")

        try:
            async with pool.acquire() as conn:
                version = await conn.fetchval("SELECT version()")
                log_pass("Database Version Check", version[:60])

                count = await conn.fetchval("SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public'")
                log_pass("Database Tables", f"Found {count} tables")
        except Exception as e:
            log_fail("Database Query", str(e), "Cannot execute queries", "Check database is running")
        finally:
            await close_db_pool()
    except Exception as e:
        log_fail("Database Connection", str(e), "asyncpg pool failed", "Check DATABASE_URL")

    # 3. Redis
    print("\n[TEST] Cache (Redis)")
    try:
        import redis.asyncio as redis
        from app.core.container import _build_redis_url

        redis_url = _build_redis_url()
        r = await redis.from_url(redis_url)

        pong = await r.ping()
        log_pass("Redis Connection", f"PING response: {pong}")

        # Test SET/GET
        await r.set("test_key", "test_value", ex=10)
        value = await r.get("test_key")
        if value:
            log_pass("Redis Operations", "SET/GET working")
        else:
            log_fail("Redis Operations", "Failed to retrieve value", "Redis operations broken", "Check Redis is running")

        await r.close()
    except ImportError:
        log_warn("Redis", "redis.asyncio not installed")
    except Exception as e:
        log_fail("Redis Connection", str(e), "Redis connection failed", "Check REDIS_URL")

    # 4. Authentication
    print("\n[TEST] Authentication System")
    try:
        from app.core.security.password import hash_password, verify_password

        test_pwd = "TestPassword123!@#"
        hashed = hash_password(test_pwd)
        log_pass("Password Hashing", "Argon2id hashing working")

        is_valid = verify_password(test_pwd, hashed)
        if is_valid:
            log_pass("Password Verification", "Verification working")
        else:
            log_fail("Password Verification", "Failed to verify password", "Hashing/verification broken", "Check password hashing")

        # JWT
        from app.core.jwt_security import encode_access_token, decode_access_token
        token = encode_access_token(subject="test_user", expires_hours=24)
        log_pass("JWT Generation", "Tokens generated")

        decoded = decode_access_token(token)
        if decoded and decoded.get("sub") == "test_user":
            log_pass("JWT Verification", "Token validation working")
        else:
            log_fail("JWT Verification", "Failed to decode token", "JWT broken", "Check JWT_SECRET")
    except Exception as e:
        log_fail("Authentication", str(e), "Auth components failed", "Check security module")

    # 5. FastAPI App
    print("\n[TEST] FastAPI Application")
    try:
        from app.main import app
        log_pass("FastAPI Import", f"Title: {app.title}")
        log_pass("App Version", f"v{app.version}")

        # Check routes
        route_count = len(app.routes)
        log_pass("API Routes", f"Found {route_count} routes")

        # Check for critical endpoints
        routes_paths = [r.path for r in app.routes if hasattr(r, 'path')]
        if "/" in routes_paths:
            log_pass("Root Endpoint", "GET / registered")
        if "/health" in routes_paths:
            log_pass("Health Endpoint", "GET /health registered")
        else:
            log_warn("Health Endpoint", "GET /health not found")
    except Exception as e:
        log_fail("FastAPI App", str(e), "Failed to load app.main", "Check main.py")

    # 6. Services
    print("\n[TEST] Domain Services")
    services = [
        ("Email Service", "app.domain.services.email_service"),
        ("Audit Logger", "app.domain.services.audit_logger"),
        ("Session Manager", "app.domain.services.session_manager"),
        ("Queue Service", "app.domain.services.queue_service"),
    ]

    for service_name, module_path in services:
        try:
            __import__(module_path)
            log_pass(f"Service: {service_name}", "Imported successfully")
        except ImportError as e:
            log_fail(f"Service: {service_name}", str(e), "Module not found", f"Check {module_path}")
        except Exception as e:
            log_fail(f"Service: {service_name}", str(e), "Import failed", "Check dependencies")

    # 7. Workers
    print("\n[TEST] Background Workers")
    workers = [
        ("Dialer Worker", "app.workers.dialer_worker"),
        ("Voice Worker", "app.workers.voice_worker"),
        ("Reminder Worker", "app.workers.reminder_worker"),
    ]

    for worker_name, module_path in workers:
        try:
            __import__(module_path)
            log_pass(f"Worker: {worker_name}", "Module loaded")
        except ImportError as e:
            log_fail(f"Worker: {worker_name}", str(e), "Module not found", f"Check {module_path}")
        except Exception as e:
            log_fail(f"Worker: {worker_name}", str(e), "Load failed", "Check dependencies")

    # 8. API Endpoints
    print("\n[TEST] Critical API Routes")
    try:
        from app.api.v1.routes import api_router

        routes = [r for r in api_router.routes if hasattr(r, 'path')]
        log_pass("Route Registration", f"Registered {len(routes)} routes")

        # Check for endpoint existence
        route_paths = [r.path for r in routes]
        critical_endpoints = [
            "/auth/register",
            "/auth/login",
            "/campaigns",
            "/calls",
            "/billing",
            "/health"
        ]

        for endpoint in critical_endpoints:
            if any(endpoint in path for path in route_paths):
                log_pass(f"Endpoint: {endpoint}", "Route exists")
            else:
                log_warn(f"Endpoint: {endpoint}", "Route not found")
    except Exception as e:
        log_fail("API Routes", str(e), "Failed to load routes", "Check routes.py")

    # Summary
    print("\n" + "="*70)
    print("[SUMMARY] BACKEND AUDIT RESULTS")
    print("="*70)
    print(f"Passing Tests: {len(results['working'])}")
    print(f"Failing Tests: {len(results['failing'])}")
    print(f"Warnings: {len(results['warnings'])}")

    if results['failing']:
        print("\n[FAILING COMPONENTS]")
        for item in results['failing']:
            if isinstance(item, dict):
                print(f"  - {item['component']}: {item['error']}")
            else:
                print(f"  - {item}")

    if results['warnings']:
        print("\n[WARNINGS]")
        for item in results['warnings']:
            print(f"  - {item}")

    # Save results
    results_file = Path(__file__).parent / "Backend Checklist.md"

    # Generate markdown report
    markdown = f"""# Backend Audit Report

**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary
- **Passing Tests:** {len(results['working'])}
- **Failing Tests:** {len(results['failing'])}
- **Warnings:** {len(results['warnings'])}

## Working Components

"""

    for item in results['working']:
        markdown += f"- [OK] {item}\n"

    markdown += "\n## Failing Components\n\n"
    for item in results['failing']:
        if isinstance(item, dict):
            markdown += f"- [FAIL] {item['component']}: {item['error']}\n"
            if item.get('cause'):
                markdown += f"  - Root Cause: {item['cause']}\n"
            if item.get('fix'):
                markdown += f"  - Fix: {item['fix']}\n"
        else:
            markdown += f"- [FAIL] {item}\n"

    markdown += "\n## Warnings\n\n"
    for item in results['warnings']:
        markdown += f"- [WARN] {item}\n"

    with open(results_file, 'w', encoding='utf-8') as f:
        f.write(markdown)

    print(f"\nReport saved to: {results_file}")

    return len(results['failing']) == 0


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Audit failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
