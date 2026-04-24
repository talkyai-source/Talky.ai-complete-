#!/usr/bin/env python3
"""
Comprehensive Backend System Audit for Talky.ai
Tests all critical components: API, Database, Auth, Integrations, Middleware, etc.
"""

import sys
import os
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

@dataclass
class AuditResult:
    """Audit result for a component"""
    component: str
    status: str  # [OK], [FAIL], [WARN]
    message: str
    details: Dict[str, Any] = None
    root_cause: str = None
    fix: str = None

class BackendAudit:
    """Comprehensive backend audit system"""

    def __init__(self):
        self.results: List[AuditResult] = []
        self.errors: Dict[str, str] = {}
        self.warnings: Dict[str, str] = {}
        self.success_count = 0

    def add_result(self, result: AuditResult):
        """Add an audit result"""
        self.results.append(result)
        if result.status == "[OK]":
            self.success_count += 1
        elif result.status == "[FAIL]":
            self.errors[result.component] = result.message
        elif result.status == "[WARN]":
            self.warnings[result.component] = result.message

    def report(self):
        """Generate final audit report"""
        print("\n" + "="*80)
        print("[AUDIT] TALKY.AI BACKEND COMPREHENSIVE AUDIT REPORT")
        print("="*80)
        print(f"Audit Timestamp: {datetime.now().isoformat()}")
        print(f"Total Tests: {len(self.results)}")
        print(f"Success: {self.success_count} | Warnings: {len(self.warnings)} | Failures: {len(self.errors)}")
        print("\n")

        # Group results by category
        by_status = {"[OK]": [], "[WARN]": [], "[FAIL]": []}
        for result in self.results:
            by_status[result.status].append(result)

        # [OK] PASSING
        if by_status["[OK]"]:
            print("[OK] RUNNING COMPONENTS (Working Correctly)")
            print("-" * 80)
            for r in by_status["[OK]"]:
                print(f"  * {r.component}: {r.message}")
                if r.details:
                    for k, v in r.details.items():
                        print(f"      └─ {k}: {v}")
            print()

        # [WARN] WARNINGS
        if by_status["[WARN]"]:
            print("[WARN] WARNINGS / POTENTIAL ISSUES")
            print("-" * 80)
            for r in by_status["[WARN]"]:
                print(f"  ! {r.component}: {r.message}")
                if r.details:
                    for k, v in r.details.items():
                        print(f"      └─ {k}: {v}")
                if r.root_cause:
                    print(f"      └─ Root Cause: {r.root_cause}")
                if r.fix:
                    print(f"      └─ Fix: {r.fix}")
            print()

        # [FAIL] FAILURES
        if by_status["[FAIL]"]:
            print("[FAIL] NOT RUNNING / FAILING COMPONENTS")
            print("-" * 80)
            for r in by_status["[FAIL]"]:
                print(f"  X {r.component}: {r.message}")
                if r.details:
                    for k, v in r.details.items():
                        print(f"      └─ {k}: {v}")
                if r.root_cause:
                    print(f"      └─ Root Cause: {r.root_cause}")
                if r.fix:
                    print(f"      └─ Fix: {r.fix}")
            print()

    def save_json_report(self, path: str):
        """Save detailed JSON report"""
        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": len(self.results),
                "passed": self.success_count,
                "warnings": len(self.warnings),
                "failed": len(self.errors)
            },
            "results": [asdict(r) for r in self.results]
        }
        with open(path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n[REPORT] Detailed JSON report saved to: {path}")


async def run_audit():
    """Run the complete backend audit"""
    audit = BackendAudit()

    print("[START] Starting Talky.ai Backend Audit...\n")

    # ========================================================================
    # 1. ENVIRONMENT & CONFIGURATION
    # ========================================================================
    print("[1/10] Checking Environment & Configuration...")

    # Check .env file
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        audit.add_result(AuditResult(
            component="Environment File (.env)",
            status="[WARN]",
            message=".env file not found",
            root_cause="Missing .env configuration file",
            fix="Copy .env.example to .env and fill in required values"
        ))
    else:
        audit.add_result(AuditResult(
            component="Environment File (.env)",
            status="[OK]",
            message=".env file exists"
        ))

    # Check .env.example
    env_example = Path(__file__).parent / ".env.example"
    if env_example.exists():
        audit.add_result(AuditResult(
            component="Environment Template",
            status="[OK]",
            message=".env.example template exists"
        ))
    else:
        audit.add_result(AuditResult(
            component="Environment Template",
            status="[FAIL]",
            message=".env.example not found"
        ))

    # ========================================================================
    # 2. PYTHON DEPENDENCIES
    # ========================================================================
    print("[2/10] Checking Python Dependencies...")

    deps_to_check = [
        ("fastapi", "FastAPI"),
        ("sqlalchemy", "SQLAlchemy"),
        ("asyncpg", "AsyncPG"),
        ("redis", "Redis"),
        ("pydantic", "Pydantic"),
        ("websockets", "WebSockets"),
    ]

    missing_deps = []
    for module, name in deps_to_check:
        try:
            __import__(module)
            audit.add_result(AuditResult(
                component=f"Dependency: {name}",
                status="[OK]",
                message=f"{name} installed and importable"
            ))
        except ImportError:
            missing_deps.append(name)
            audit.add_result(AuditResult(
                component=f"Dependency: {name}",
                status="[FAIL]",
                message=f"{name} not installed",
                root_cause="Module not found in Python path",
                fix=f"Run: pip install -r requirements.txt"
            ))

    # ========================================================================
    # 3. APPLICATION IMPORT
    # ========================================================================
    print("[3/10] Testing Application Import...")

    try:
        from app.main import app
        audit.add_result(AuditResult(
            component="Application Entry Point",
            status="[OK]",
            message="app.main imports successfully",
            details={"app_title": app.title, "app_version": app.version}
        ))
    except Exception as e:
        audit.add_result(AuditResult(
            component="Application Entry Point",
            status="[FAIL]",
            message=f"Failed to import app.main: {str(e)[:100]}",
            root_cause=str(e)[:200],
            fix="Check Python path, dependencies, and syntax errors"
        ))
        return audit

    # ========================================================================
    # 4. CORE CONFIGURATION
    # ========================================================================
    print("[4/10] Checking Core Configuration...")

    try:
        from app.core.config import get_settings
        settings = get_settings()

        # Check required settings
        required_settings = {
            "environment": settings.environment,
            "debug": settings.debug,
            "api_prefix": settings.api_prefix,
            "cors_origins": settings.cors_origins,
        }

        audit.add_result(AuditResult(
            component="Settings Configuration",
            status="✅",
            message="Settings loaded successfully",
            details=required_settings
        ))

        # Check optional but important settings
        optional_checks = {
            "jwt_secret": settings.jwt_secret is not None,
            "master_key": settings.master_key is not None,
            "deepgram_key": settings.deepgram_api_key is not None,
            "groq_key": settings.groq_api_key is not None,
        }

        for setting, is_set in optional_checks.items():
            if not is_set:
                audit.add_result(AuditResult(
                    component=f"Config: {setting}",
                    status="⚠️",
                    message=f"{setting} not configured",
                    root_cause="Environment variable not set",
                    fix="Add to .env file"
                ))

    except Exception as e:
        audit.add_result(AuditResult(
            component="Settings Configuration",
            status="❌",
            message=f"Failed to load settings: {str(e)[:100]}",
            root_cause=str(e),
            fix="Check .env file and config.py"
        ))

    # ========================================================================
    # 5. ROUTES & ENDPOINTS
    # ========================================================================
    print("📋 [5/10] Checking API Routes & Endpoints...")

    try:
        from app.api.v1 import routes

        # Count routers
        router_count = len([r for r in dir(routes) if 'router' in r.lower()])

        # Get routes from app
        endpoint_count = len(app.routes)

        audit.add_result(AuditResult(
            component="API Routes",
            status="✅",
            message="API routes loaded successfully",
            details={
                "total_endpoints": endpoint_count,
                "routers_imported": router_count
            }
        ))

        # Check specific critical routes
        critical_routes = ["/health", "/metrics", "/"]
        missing_routes = []
        for route in app.routes:
            for critical in critical_routes:
                if critical in str(route.path):
                    critical_routes.remove(critical)
                    break

        if critical_routes:
            audit.add_result(AuditResult(
                component="Critical Routes",
                status="⚠️",
                message=f"Missing critical routes: {critical_routes}",
                root_cause="Routes not registered in main.py",
                fix="Check api router registration"
            ))
        else:
            audit.add_result(AuditResult(
                component="Critical Routes",
                status="✅",
                message="All critical routes registered"
            ))

    except Exception as e:
        audit.add_result(AuditResult(
            component="API Routes",
            status="❌",
            message=f"Failed to load routes: {str(e)[:100]}",
            root_cause=str(e),
            fix="Check routes.py and endpoint files"
        ))

    # ========================================================================
    # 6. DATABASE & ORM
    # ========================================================================
    print("📋 [6/10] Checking Database & ORM Setup...")

    try:
        from app.core.db import init_db_pool
        audit.add_result(AuditResult(
            component="Database ORM",
            status="✅",
            message="SQLAlchemy and asyncpg configured"
        ))
    except Exception as e:
        audit.add_result(AuditResult(
            component="Database ORM",
            status="❌",
            message=f"Database setup failed: {str(e)[:100]}",
            root_cause=str(e),
            fix="Check DATABASE_URL in .env and database/models"
        ))

    # Check migrations
    migrations_dir = Path(__file__).parent / "database" / "migrations"
    if migrations_dir.exists():
        migration_files = list(migrations_dir.glob("*.sql"))
        audit.add_result(AuditResult(
            component="Database Migrations",
            status="✅",
            message=f"Found {len(migration_files)} migration files",
            details={"migration_count": len(migration_files)}
        ))
    else:
        audit.add_result(AuditResult(
            component="Database Migrations",
            status="⚠️",
            message="No migrations directory found",
            root_cause="Migration files not set up",
            fix="Create database/migrations directory with SQL files"
        ))

    # ========================================================================
    # 7. AUTHENTICATION & SECURITY
    # ========================================================================
    print("📋 [7/10] Checking Authentication & Security...")

    try:
        from app.core.jwt_security import create_access_token
        audit.add_result(AuditResult(
            component="JWT Security",
            status="✅",
            message="JWT module configured"
        ))
    except Exception as e:
        audit.add_result(AuditResult(
            component="JWT Security",
            status="⚠️",
            message=f"JWT configuration issue: {str(e)[:100]}"
        ))

    try:
        from app.core.security.password import hash_password, verify_password
        # Test password hashing
        test_pwd = "test_password_123"
        hashed = hash_password(test_pwd)
        is_valid = verify_password(test_pwd, hashed)

        if is_valid:
            audit.add_result(AuditResult(
                component="Password Hashing",
                status="✅",
                message="Password hashing working correctly"
            ))
        else:
            audit.add_result(AuditResult(
                component="Password Hashing",
                status="❌",
                message="Password verification failed"
            ))
    except Exception as e:
        audit.add_result(AuditResult(
            component="Password Hashing",
            status="❌",
            message=f"Password hashing error: {str(e)[:100]}"
        ))

    # Check middleware
    middleware_checks = [
        ("CORSMiddleware", "CORS"),
        ("TenantMiddleware", "Tenant Isolation"),
        ("SessionSecurityMiddleware", "Session Security"),
        ("APISecurityMiddleware", "API Security"),
    ]

    for middleware_name, label in middleware_checks:
        try:
            if middleware_name in str(app.middleware):
                audit.add_result(AuditResult(
                    component=f"Middleware: {label}",
                    status="✅",
                    message=f"{label} middleware registered"
                ))
            else:
                audit.add_result(AuditResult(
                    component=f"Middleware: {label}",
                    status="⚠️",
                    message=f"{label} middleware not found in middleware stack"
                ))
        except Exception as e:
            audit.add_result(AuditResult(
                component=f"Middleware: {label}",
                status="⚠️",
                message=f"Could not verify {label} middleware"
            ))

    # ========================================================================
    # 8. THIRD-PARTY INTEGRATIONS
    # ========================================================================
    print("📋 [8/10] Checking Third-Party Integrations...")

    integrations = [
        ("deepgram_sdk", "Deepgram STT"),
        ("groq", "Groq LLM"),
        ("redis", "Redis Cache"),
        ("vonage", "Vonage Telephony"),
    ]

    for module, name in integrations:
        try:
            __import__(module)
            audit.add_result(AuditResult(
                component=f"Integration: {name}",
                status="✅",
                message=f"{name} SDK available"
            ))
        except ImportError:
            audit.add_result(AuditResult(
                component=f"Integration: {name}",
                status="⚠️",
                message=f"{name} SDK not installed",
                root_cause="Module import failed",
                fix=f"pip install -r requirements.txt to ensure all dependencies"
            ))

    # ========================================================================
    # 9. DOMAIN MODELS & SERVICES
    # ========================================================================
    print("📋 [9/10] Checking Domain Models & Services...")

    domain_components = [
        ("app.domain.models.campaign", "Campaign Model"),
        ("app.domain.models.call", "Call Model"),
        ("app.domain.models.conversation", "Conversation Model"),
        ("app.domain.services.voice_orchestrator", "Voice Orchestrator"),
    ]

    for module_path, name in domain_components:
        try:
            parts = module_path.split('.')
            module = __import__(module_path, fromlist=[parts[-1]])
            audit.add_result(AuditResult(
                component=f"Domain: {name}",
                status="✅",
                message=f"{name} imported successfully"
            ))
        except Exception as e:
            audit.add_result(AuditResult(
                component=f"Domain: {name}",
                status="❌",
                message=f"Failed to import {name}",
                root_cause=str(e)[:100],
                fix="Check domain models and services"
            ))

    # ========================================================================
    # 10. WORKER & BACKGROUND JOBS
    # ========================================================================
    print("📋 [10/10] Checking Workers & Background Jobs...")

    workers_dir = Path(__file__).parent / "app" / "workers"
    if workers_dir.exists():
        worker_files = list(workers_dir.glob("*.py"))
        audit.add_result(AuditResult(
            component="Background Workers",
            status="✅",
            message=f"Found {len(worker_files)} worker files",
            details={"worker_files": len(worker_files)}
        ))
    else:
        audit.add_result(AuditResult(
            component="Background Workers",
            status="⚠️",
            message="Workers directory not found"
        ))

    return audit


async def main():
    """Main audit function"""
    try:
        audit = await run_audit()
        audit.report()

        # Save JSON report
        json_report_path = Path(__file__).parent / "backend_audit_report.json"
        audit.save_json_report(str(json_report_path))

        # Summary footer
        print("\n" + "="*80)
        print(f"📊 AUDIT COMPLETE")
        print(f"   ✅ Passed: {audit.success_count}/{len(audit.results)}")
        print(f"   ⚠️  Warnings: {len(audit.warnings)}")
        print(f"   ❌ Failed: {len(audit.errors)}")
        print("="*80)

        # Exit with appropriate code
        sys.exit(0 if len(audit.errors) == 0 else 1)

    except Exception as e:
        print(f"\n❌ AUDIT FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
