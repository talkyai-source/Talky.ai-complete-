"""Existing restricted UUID lead fixture; exact designated local PG only."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
for key in list(os.environ):
    if any(part in key.upper() for part in ("API_KEY", "SECRET", "TOKEN", "DATABASE_URL", "DSN", "PASSWORD")):
        del os.environ[key]
os.environ.update(ENVIRONMENT="test", DATABASE_URL="postgresql://test:test@127.0.0.1:1/unavailable_test",
    TEST_DATABASE_URL=DSN, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(BACKEND),
    JWT_SECRET="synthetic-identity-offline-test-secret-0123456789")
sys.path.insert(0, str(BACKEND))
spec = importlib.util.spec_from_file_location("identity_pg_guard", ROOT / "docs/sessions/artifacts/ag06-canonical-recovery/probe.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
network = guard.guard_network()
snapshot = guard.snapshot_loader()
import asyncpg

async def inspect():
    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval("SELECT current_database()") == "cp04_acceptance_test"
        state = await snapshot(conn)
        assert state["head"] == "0061_dnc_runtime_contract"
        return {"public": state,
            "roles": [r["rolname"] for r in await conn.fetch("SELECT rolname FROM pg_roles ORDER BY rolname")],
            "schemas": [r["nspname"] for r in await conn.fetch("SELECT nspname FROM pg_namespace ORDER BY nspname")]}
    finally:
        await conn.close()

def hashes():
    paths = subprocess.check_output(["git", "diff", "--name-only", "6c9eed991a7c69afd70536379427933f099052d9"], cwd=ROOT, text=True).splitlines()
    paths += ["backend/tests/unit/test_model_identity_recording.py", "backend/tests/integration/test_model_identity_lead_evidence.py",
              "docs/sessions/artifacts/model-identity-capture/pg_check.py", "docs/sessions/artifacts/agent-limitations/pg_check.py", "docs/sessions/artifacts/ag06-canonical-recovery/probe.py"]
    return {p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in paths if (ROOT / p).is_file()}

output = ROOT / sys.argv[1]
assert not output.exists(), "Preserve earlier results"
report = {"source_before": hashes(), "before": asyncio.run(inspect()), "network": network,
          "runtime": guard.runtime_evidence()}
os.chdir(BACKEND)
argv = ["tests/integration/test_ag05_lead_evidence.py", "tests/integration/test_model_identity_lead_evidence.py",
        "tests/integration/test_capture_restart_recovery.py", "-q", "--tb=short", "-o", "addopts="]
try:
    import pytest
    report["pytest_argv"] = argv
    report["pytest_exit_code"] = int(pytest.main(argv))
finally:
    report["after"] = asyncio.run(inspect())
    report["source_after"] = hashes()
    report["source_unchanged"] = report["source_before"] == report["source_after"]
    report["protected_unchanged"] = report["before"] == report["after"]
    output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
assert report["source_unchanged"] and report["protected_unchanged"]
assert network["prohibited_attempts"] == 0
raise SystemExit(report["pytest_exit_code"])
