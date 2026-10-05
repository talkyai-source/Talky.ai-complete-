"""Opt-in bounded knowledge SQL controls; exact local endpoint only.

Existing knowledge fixtures create/drop their private UUID schemas and roles.
Public data/schema/head are read-snapshotted before/after; no provider calls.
"""
import argparse
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
DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
MODULE = "tests/integration/test_knowledge_retrieval_boundaries.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("docs/sessions/artifacts/ag06-canonical-recovery/probe.py", "canonical_guard")
snapshot = guard.snapshot_loader()


def hashes():
    paths = ["backend/app/services/scripts/knowledge/retrieval.py",
             "backend/app/services/scripts/knowledge/ingest_service.py",
             "backend/app/services/scripts/knowledge/enricher.py",
             "backend/app/services/scripts/knowledge/passages.py",
             "backend/app/domain/services/voice_pipeline/kb_budget.py",
             "backend/Alembic/versions/0010_campaign_knowledge.py",
             "backend/tests/fixtures/knowledge/ag02_gold.json",
             "backend/scripts/evaluate_ag02_knowledge.py",
             "docs/sessions/artifacts/ag06-canonical-recovery/probe.py",
             "docs/sessions/artifacts/ag06-saved-ack-recovery/pg_probe.py",
             "backend/" + MODULE, str(Path(__file__).relative_to(ROOT)).replace("\\", "/")]
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}


async def inspect_database():
    conn = await asyncpg.connect(DSN, timeout=5, command_timeout=30)
    try:
        assert await conn.fetchval("SELECT current_database()") == "cp04_acceptance_test"
        assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_trgm')"), "Existing pg_trgm required; no installation authorized"
        public = await snapshot(conn)
        assert public["head"] == "0061_dnc_runtime_contract"
        return {"public": public,
                "schemas": [r["nspname"] for r in await conn.fetch("SELECT nspname FROM pg_namespace ORDER BY nspname")],
                "roles": [r["rolname"] for r in await conn.fetch("SELECT rolname FROM pg_roles ORDER BY rolname")],
                "postgres_version": await conn.fetchval("SHOW server_version")}
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--focused", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    assert not output.exists(), "Preserve previous evidence"
    network = guard.guard_network()
    os.environ.update(ENVIRONMENT="test", DATABASE_URL=DSN, TEST_DATABASE_URL=DSN,
                      PYTHONPATH=str(BACKEND), PYTHONDONTWRITEBYTECODE="1",
                      AG02_POSTGRES_GOLD_OUTPUT=str(output.with_name(output.stem + "-gold.json")))
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    report = {"source_before": hashes(), "network": network, "runtime": guard.runtime_evidence(),
              "database": "127.0.0.1:55434/cp04_acceptance_test"}
    report["before"] = asyncio.run(inspect_database())
    try:
        import pytest
        argv = [MODULE, "-q", "-s", "--tb=short", "-o", "addopts="]
        if args.focused:
            argv.extend(["-k", "generated_routing_aliases or authored_source_coverage or other_nodes_generated_alias"])
        report["pytest_argv"] = argv
        report["pytest_exit_code"] = int(pytest.main(argv))
    finally:
        report["after"] = asyncio.run(inspect_database())
        report["source_after"] = hashes()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["protected_unchanged"] = report["before"] == report["after"]
        output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    assert report["source_unchanged"] and report["protected_unchanged"]
    assert network["prohibited_attempts"] == 0
    return report["pytest_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
