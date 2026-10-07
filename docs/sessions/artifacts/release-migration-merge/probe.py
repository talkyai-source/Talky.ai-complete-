"""Reviewed local merge acceptance; never migrate the protected database."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import asyncpg

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
PROTECTED = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canonical = load("docs/sessions/artifacts/ag06-canonical-recovery/probe.py", "canonical_safety")
snapshot = canonical.snapshot_loader()


async def protected_snapshot():
    conn = await asyncpg.connect(PROTECTED, timeout=5, command_timeout=60)
    try:
        return await snapshot(conn)
    finally:
        await conn.close()


def hashes():
    paths = [*sorted((BACKEND / "Alembic/versions").glob("*.py")), BACKEND / "Alembic/env.py",
             BACKEND / "alembic.ini", BACKEND / "database/complete_schema.sql",
             BACKEND / "tests/integration/test_release_migration_merge.py", Path(__file__).resolve()]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in paths}


def main():
    output = Path(sys.argv[1]).resolve()
    assert not output.exists(), "Preserve earlier evidence"
    assert os.environ.get("PYTHONPATH") == str(BACKEND)
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
    os.environ["TALKY_RELEASE_MERGE_ADMIN_DSN"] = PROTECTED
    # No accidental pre-fixture fallback is a usable database.
    os.environ["DATABASE_URL"] = "postgresql://unavailable@127.0.0.1:1/unavailable_test"
    os.environ["ENVIRONMENT"] = "test"
    os.chdir(BACKEND)
    report = {"runtime": canonical.runtime_evidence(), "source_before": hashes(), "network": canonical.guard_network()}
    report["protected_before"] = asyncio.run(protected_snapshot())
    import pytest
    try:
        report["pytest_argv"] = ["tests/integration/test_release_migration_merge.py", "-q", "--tb=short", "-o", "addopts="]
        report["pytest_exit_code"] = int(pytest.main(report["pytest_argv"]))
    finally:
        report["cases"] = next((getattr(m, "RUN_EVIDENCE") for name, m in tuple(sys.modules.items())
                                if name.endswith("test_release_migration_merge") and hasattr(m, "RUN_EVIDENCE")), [])
        report["protected_after"] = asyncio.run(protected_snapshot())
        report["protected_unchanged"] = report["protected_before"] == report["protected_after"]
        report["source_after"] = hashes()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    assert report["protected_unchanged"] and report["source_unchanged"]
    assert report["network"]["prohibited_attempts"] == 0
    return report["pytest_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
