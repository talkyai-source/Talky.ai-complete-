#!/usr/bin/env python3
"""
Email Verification System - Automated End-to-End Verification Script

This script verifies the complete email verification system including:
- Database schema and migrations
- SMTP connectivity (Microsoft 365)
- Email service configuration
- All API endpoints
- End-to-end workflow (registration → verification → login)
- Error handling and edge cases

Usage:
    python verify_email_system.py

Output:
    - Console: Real-time verification progress
    - File: verification_report_{timestamp}.html (detailed HTML report)
"""

import asyncio
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
import json

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from verifiers.database_verifier import DatabaseVerifier
from verifiers.smtp_verifier import SmtpVerifier
from verifiers.config_verifier import ConfigVerifier
from verifiers.api_verifier import ApiVerifier
from verifiers.report_generator import ReportGenerator


class EmailVerificationSystemVerifier:
    """Main coordinator for email verification system verification."""

    def __init__(self):
        self.timestamp = datetime.now().isoformat().replace(':', '-')
        self.results: Dict[str, Dict] = {}
        self.failed_tests: List[str] = []
        self.passed_tests: List[str] = []

    async def verify_all(self) -> bool:
        """Run all verification checks."""
        print("=" * 80)
        print("EMAIL VERIFICATION SYSTEM - AUTOMATED VERIFICATION")
        print("=" * 80)
        print(f"Started: {datetime.now().isoformat()}")
        print()

        # Phase 1: Configuration
        await self._verify_phase("Configuration", ConfigVerifier())

        # Phase 2: Database
        await self._verify_phase("Database", DatabaseVerifier())

        # Phase 3: SMTP
        await self._verify_phase("SMTP Connectivity", SmtpVerifier())

        # Phase 4: API Endpoints
        await self._verify_phase("API Endpoints", ApiVerifier())

        # Generate report
        self._generate_report()

        # Print summary
        self._print_summary()

        return len(self.failed_tests) == 0

    async def _verify_phase(self, phase_name: str, verifier) -> None:
        """Run a verification phase."""
        print(f"\n{'=' * 80}")
        print(f"PHASE: {phase_name}")
        print('=' * 80)

        try:
            results = await verifier.verify()
            self.results[phase_name] = results

            for test_name, test_result in results.items():
                status = "✅ PASS" if test_result["passed"] else "❌ FAIL"
                print(f"{status} | {test_name}")

                if test_result["passed"]:
                    self.passed_tests.append(f"{phase_name}: {test_name}")
                else:
                    self.failed_tests.append(f"{phase_name}: {test_name}")
                    print(f"  Error: {test_result.get('error', 'Unknown error')}")

        except Exception as e:
            print(f"❌ PHASE FAILED: {e}")
            self.failed_tests.append(f"{phase_name}: {str(e)}")

    def _generate_report(self) -> None:
        """Generate HTML verification report."""
        generator = ReportGenerator(
            results=self.results,
            passed_tests=self.passed_tests,
            failed_tests=self.failed_tests,
            timestamp=self.timestamp
        )
        generator.generate()

    def _print_summary(self) -> None:
        """Print verification summary."""
        total_tests = len(self.passed_tests) + len(self.failed_tests)
        passed = len(self.passed_tests)
        failed = len(self.failed_tests)
        pass_rate = (passed / total_tests * 100) if total_tests > 0 else 0

        print(f"\n{'=' * 80}")
        print("VERIFICATION SUMMARY")
        print('=' * 80)
        print(f"Total Tests:     {total_tests}")
        print(f"Passed:          {passed} ✅")
        print(f"Failed:          {failed} ❌")
        print(f"Pass Rate:       {pass_rate:.1f}%")
        print(f"Status:          {'🟢 ALL TESTS PASSED' if failed == 0 else '🔴 SOME TESTS FAILED'}")
        print(f"Report:          verification_report_{self.timestamp}.html")
        print('=' * 80)

        if failed > 0:
            print("\nFailed Tests:")
            for test in self.failed_tests:
                print(f"  ❌ {test}")


async def main():
    """Main entry point."""
    verifier = EmailVerificationSystemVerifier()
    success = await verifier.verify_all()

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Verification interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Verification failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
