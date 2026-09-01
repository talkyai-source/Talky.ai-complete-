"""Static guards for the repository's canonical CI release gate."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_ci_runs_every_canonical_repository_suite() -> None:
    workflow = _workflow()

    assert 'NODE_VERSION: "22"' in workflow
    assert "ruff check app/ --select F --extend-ignore F401,F841" in workflow
    assert "pytest tests/unit tests/security" in workflow
    assert "run: npm run typecheck" in workflow
    assert "run: npm test" in workflow
    assert "admin-frontend:" in workflow
    assert "telephony-static:" in workflow
    assert "needs: [backend, frontend, admin-frontend, telephony-static]" in workflow


def test_ci_bootstraps_before_migrating_and_never_crosses_forward_only_boundary() -> None:
    workflow = _workflow()
    primary = workflow[
        workflow.index("Bootstrap and migrate primary test database") :
        workflow.index("Preserved baseline bootstrap contract")
    ]

    assert primary.index("database/complete_schema.sql") < primary.index(
        "alembic stamp 0008_tenant_voice_tuning"
    ) < primary.index("alembic upgrade head")
    assert "alembic downgrade base" not in workflow
