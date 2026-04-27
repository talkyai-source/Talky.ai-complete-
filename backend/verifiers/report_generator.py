"""HTML report generation for verification results."""

from typing import Dict, List
from datetime import datetime
import json


class ReportGenerator:
    """Generate HTML verification report."""

    def __init__(
        self,
        results: Dict,
        passed_tests: List[str],
        failed_tests: List[str],
        timestamp: str
    ):
        self.results = results
        self.passed_tests = passed_tests
        self.failed_tests = failed_tests
        self.timestamp = timestamp

    def generate(self) -> str:
        """Generate HTML report."""
        report_filename = f"verification_report_{self.timestamp}.html"

        total = len(self.passed_tests) + len(self.failed_tests)
        passed = len(self.passed_tests)
        failed = len(self.failed_tests)
        pass_rate = (passed / total * 100) if total > 0 else 0

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Email Verification System - Verification Report</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 32px;
        }}
        .header p {{
            margin: 5px 0 0 0;
            opacity: 0.9;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .summary-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .summary-card .value {{
            font-size: 32px;
            font-weight: bold;
            margin: 10px 0;
        }}
        .summary-card.passed .value {{ color: #27ae60; }}
        .summary-card.failed .value {{ color: #e74c3c; }}
        .summary-card.total .value {{ color: #667eea; }}
        .summary-card.rate .value {{ color: #f39c12; }}
        .phase {{
            background: white;
            margin-bottom: 20px;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .phase-header {{
            background: #667eea;
            color: white;
            padding: 15px 20px;
            font-weight: bold;
            font-size: 18px;
        }}
        .phase-content {{
            padding: 20px;
        }}
        .test {{
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 4px;
            border-left: 4px solid;
        }}
        .test.passed {{
            background-color: #f0f8f0;
            border-color: #27ae60;
        }}
        .test.failed {{
            background-color: #fff5f5;
            border-color: #e74c3c;
        }}
        .test-name {{
            font-weight: 600;
            margin-bottom: 5px;
        }}
        .test-status {{
            font-size: 14px;
            font-weight: bold;
        }}
        .test.passed .test-status {{ color: #27ae60; }}
        .test.failed .test-status {{ color: #e74c3c; }}
        .test-error {{
            font-size: 12px;
            color: #e74c3c;
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid rgba(231, 76, 60, 0.2);
            font-family: 'Courier New', monospace;
        }}
        .footer {{
            text-align: center;
            padding: 20px;
            color: #666;
            font-size: 12px;
        }}
        .status {{
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 18px;
            font-weight: bold;
            text-align: center;
        }}
        .status.success {{
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }}
        .status.failure {{
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }}
        .recommendations {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #f39c12;
            margin-bottom: 20px;
        }}
        .recommendations h3 {{
            margin-top: 0;
            color: #f39c12;
        }}
        .recommendations ul {{
            margin: 10px 0;
            padding-left: 20px;
        }}
        .recommendations li {{
            margin-bottom: 8px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📧 Email Verification System</h1>
        <p>Automated End-to-End Verification Report</p>
        <p style="margin-top: 15px; font-size: 12px;">Generated: {datetime.now().isoformat()}</p>
    </div>

    <div class="status {'success' if failed == 0 else 'failure'}">
        {'✅ ALL TESTS PASSED - System Ready for Production' if failed == 0 else '❌ SOME TESTS FAILED - Review Issues Below'}
    </div>

    <div class="summary">
        <div class="summary-card total">
            <div class="label">Total Tests</div>
            <div class="value">{total}</div>
        </div>
        <div class="summary-card passed">
            <div class="label">Passed</div>
            <div class="value">{passed}</div>
        </div>
        <div class="summary-card failed">
            <div class="label">Failed</div>
            <div class="value">{failed}</div>
        </div>
        <div class="summary-card rate">
            <div class="label">Pass Rate</div>
            <div class="value">{pass_rate:.1f}%</div>
        </div>
    </div>

    {self._generate_recommendations(failed)}

    {self._generate_phases()}

    <div class="footer">
        <p>Verification Report: {report_filename}</p>
        <p>Email Verification System - Day 1 Implementation</p>
        <p>© 2026 Talky.ai - All Rights Reserved</p>
    </div>
</body>
</html>
"""

        # Write to file
        with open(report_filename, 'w') as f:
            f.write(html_content)

        print(f"\n✅ Report generated: {report_filename}")
        return report_filename

    def _generate_recommendations(self, failed: int) -> str:
        """Generate recommendations section."""
        if failed == 0:
            return """
    <div class="recommendations">
        <h3>✅ Next Steps</h3>
        <ul>
            <li>System is ready for production deployment</li>
            <li>Follow 8-step deployment guide in EMAIL_SETUP_QUICK_START.md</li>
            <li>Monitor email delivery logs after deployment</li>
            <li>Test verification flow with real users</li>
        </ul>
    </div>
"""
        else:
            return """
    <div class="recommendations">
        <h3>⚠️ Issues Found - Action Required</h3>
        <ul>
            <li>Review failed tests below</li>
            <li>Check environment variables: EMAIL_USER, EMAIL_PASS, DATABASE_URL</li>
            <li>Verify database migration has been applied</li>
            <li>Verify Microsoft 365 SMTP AUTH is enabled</li>
            <li>Verify network/firewall allows port 587 outbound</li>
            <li>See troubleshooting guide in day 1 plan.md</li>
        </ul>
    </div>
"""

    def _generate_phases(self) -> str:
        """Generate phase details."""
        phases_html = ""

        for phase_name, phase_results in self.results.items():
            phases_html += f"""
    <div class="phase">
        <div class="phase-header">{phase_name}</div>
        <div class="phase-content">
"""
            for test_name, test_result in phase_results.items():
                status = "passed" if test_result["passed"] else "failed"
                status_text = "✅ PASS" if test_result["passed"] else "❌ FAIL"

                phases_html += f"""
            <div class="test {status}">
                <div class="test-name">{test_name}</div>
                <div class="test-status">{status_text}</div>
"""
                if not test_result["passed"] and "error" in test_result:
                    phases_html += f"""
                <div class="test-error">Error: {test_result['error']}</div>
"""
                phases_html += """
            </div>
"""

            phases_html += """
        </div>
    </div>
"""

        return phases_html
