"""Wiring guards, not workflow executions or GitHub protection evidence."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _text(path):
    target = ROOT / path
    assert target.exists(), f"F16-02 missing prepared artifact: {path}"
    return target.read_text(encoding="utf-8")


def test_nightly_scans_are_scheduled_bounded_and_read_only():
    text = _text(".github/workflows/qa-nightly.yml")
    for required in ("schedule:", "cron:", "workflow_dispatch:", "contents: read",
                     "timeout-minutes:", "gitleaks", "pip-audit", "npm audit", "scan-type: fs"):
        assert required in text
    assert "Talk-Leee" in text and "Admin/frontend" in text
    assert "pull_request_target" not in text and "continue-on-error: true" not in text
    assert "deploy_to_server" not in text and "secrets.TALKY" not in text


def test_browser_job_can_fail_and_uses_only_local_smoke_and_visual_contracts():
    text = _text(".github/workflows/qa-browser.yml")
    for required in ("pull_request:", "timeout-minutes:",
                     "npm ci", "playwright install", "--project=chromium", "demo.smoke.spec.ts",
                     "dashboard-first-row.visual.spec.ts", "upload-artifact", "if: always()"):
        assert required in text
    # The browser checks are a real check since 17 Nov 2026: a failure must fail the run.
    assert "continue-on-error: true" not in text
    assert "PW_PROD_AUDIT: \"0\"" in text
    assert "127.0.0.1" in text and "api.talkleeai.com" not in text
    assert "pull_request_target" not in text


def test_browser_reference_images_are_written_only_by_a_manual_run():
    """A pull request must always compare. Writing new reference images is allowed
    only in the step a person starts by hand with the input ticked, and that step
    uploads the images instead of committing them."""
    lines = _text(".github/workflows/qa-browser.yml").splitlines()
    text = "\n".join(lines)
    assert "workflow_dispatch:" in text and "update_linux_snapshots:" in text
    writers = [index for index, line in enumerate(lines) if "--update-snapshots" in line]
    assert len(writers) == 1
    guard = "if: ${{ inputs.update_linux_snapshots }}"
    # The guard sits on the same step, directly above the command.
    assert any(guard in line for line in lines[max(0, writers[0] - 3):writers[0]])
    # Every comparing run is switched off for that manual run, so nothing is compared and passed by accident.
    comparing = [index for index, line in enumerate(lines)
                 if "playwright test" in line and "--update-snapshots" not in line]
    assert comparing
    for index in comparing:
        assert any("if: ${{ !inputs.update_linux_snapshots }}" in line for line in lines[max(0, index - 6):index])
    assert "git commit" not in text and "git push" not in text


def test_required_ci_executes_read_only_quarantine_and_backend_diff_gates():
    text = _text(".github/workflows/ci.yml")
    assert "name: Quarantine policy" in text
    assert "check_release_quarantine.py" in text
    assert "name: Changed-line coverage" in text
    assert "check_diff_coverage.py" in text and "--threshold 80" in text
    assert "download-artifact" in text and "name: backend-coverage" in text
    assert "github.event.pull_request.base.sha" in text
    # Existing canonical coverage and parity gates must remain intact.
    assert "--cov-fail-under=20" in text
    assert "verify_schema_parity.py" in text and "16.14" in text


def test_owner_ruleset_targets_main_and_requires_real_nonoptional_contexts():
    ruleset = json.loads(_text(".github/main-ruleset.json"))
    assert ruleset["target"] == "branch" and ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/main"]
    assert not ruleset.get("bypass_actors")
    rules = {rule["type"]: rule for rule in ruleset["rules"]}
    assert {"deletion", "non_fast_forward", "pull_request", "required_status_checks"} <= rules.keys()
    assert rules["pull_request"]["parameters"]["required_approving_review_count"] >= 1
    required = rules["required_status_checks"]["parameters"]
    assert required["strict_required_status_checks_policy"] is True
    contexts = {value["context"] for value in required["required_status_checks"]}
    assert {"Backend", "Frontend", "Admin Frontend", "Quarantine policy", "Changed-line coverage"} <= contexts
    assert not any("Browser" in value for value in contexts)


def test_pr_template_requests_red_and_green_without_claiming_automated_enforcement():
    text = _text(".github/PULL_REQUEST_TEMPLATE.md").lower()
    assert "failing test added" in text and "before" in text and "after" in text


def test_owner_runbook_resolves_repo_and_does_not_claim_protection_was_applied():
    text = _text("backend/docs/ci-quality-gates.md")
    for required in ("gh repo view --json nameWithOwner", "gh api", "--method POST",
                     "--input .github/main-ruleset.json", "14 days", "backend/app",
                     "Schemathesis", "Claude", "not applied"):
        assert required in text
