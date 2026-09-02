"""Static guards for the repository's canonical CI release gate."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DOCKERFILE = ROOT / "backend" / "Dockerfile"
PYPROJECT = ROOT / "backend" / "pyproject.toml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_ci_runs_every_canonical_repository_suite() -> None:
    workflow = _workflow()

    # Production and the dedicated voice workflow run Python 3.12, while the
    # package contract requires >=3.11. Pinning this gate to 3.10 turns valid
    # stdlib usage (for example datetime.UTC) into collection-time failures.
    assert 'PYTHON_VERSION: "3.12"' in workflow
    assert 'NODE_VERSION: "22"' in workflow
    assert "ruff check app/ --select F --extend-ignore F401,F841" in workflow
    assert "pytest tests/unit tests/security" in workflow
    assert "run: npm run typecheck" in workflow
    assert "run: npm test" in workflow
    assert "admin-frontend:" in workflow
    assert "telephony-static:" in workflow
    assert "needs: [backend, frontend, admin-frontend, telephony-static]" in workflow


def test_backend_docker_python_matches_ci_and_package_contract() -> None:
    workflow = _workflow()
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    ci_match = re.search(
        r'^\s*PYTHON_VERSION:\s*["\'](?P<version>\d+\.\d+)["\']\s*$', workflow, re.M
    )
    docker_match = re.search(
        r"^FROM\s+python:(?P<version>\d+\.\d+)-slim(?:\s|$)", dockerfile, re.M
    )
    minimum_match = re.fullmatch(r">=(?P<version>\d+\.\d+)", project["requires-python"])

    assert ci_match is not None, "CI must pin a Python major.minor"
    assert docker_match is not None, "backend Dockerfile must pin python:<major.minor>-slim"
    assert minimum_match is not None, "requires-python must declare a major.minor lower bound"
    docker_python = docker_match.group("version")
    assert docker_python == ci_match.group("version")
    assert tuple(map(int, docker_python.split("."))) >= tuple(
        map(int, minimum_match.group("version").split("."))
    )


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
