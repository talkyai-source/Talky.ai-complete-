"""Affected source checks with sockets and async transports denied."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
FOLDER = Path(__file__).resolve().parent
MODULES = ["test_knowledge_authored_coverage", "test_knowledge_enrichment_routing",
           "test_kb_evidence_contract", "test_knowledge_relevance", "test_agent_knowledge_repairs",
           "test_knowledge_render", "test_knowledge_tool", "test_kb_source_first_all_paths",
           "test_realtime_knowledge_evidence", "test_ag02_gold_matrix"]
paths = ["backend/tests/unit/" + name + ".py" for name in MODULES] + [
    "backend/app/services/scripts/knowledge/retrieval.py",
    "backend/app/services/scripts/knowledge/ingest_service.py",
    "backend/app/services/scripts/knowledge/enricher.py",
    "backend/app/services/scripts/knowledge/passages.py",
    "backend/app/domain/services/voice_pipeline/kb_budget.py",
    "backend/tests/fixtures/knowledge/ag02_gold.json", "backend/scripts/evaluate_ag02_knowledge.py",
    str(Path(__file__).relative_to(ROOT)).replace("\\", "/")]


def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}


local = threading.local()
original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
counts = {"internal_socketpairs": 0, "prohibited_attempts": 0}


def connect(sock, address, original):
    if getattr(local, "socketpair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original(sock, address)
    counts["prohibited_attempts"] += 1
    raise AssertionError("No provider/database socket permitted")


def pair(*args, **kwargs):
    local.socketpair = True
    try:
        result = original_pair(*args, **kwargs)
        counts["internal_socketpairs"] += 1
        return result
    finally:
        local.socketpair = False


async def transport(*args, **kwargs):
    counts["prohibited_attempts"] += 1
    raise AssertionError("No provider/database transport permitted")


socket.socket.connect = lambda sock, addr: connect(sock, addr, original_connect)
socket.socket.connect_ex = lambda sock, addr: connect(sock, addr, original_ex)
socket.socketpair = pair
asyncio.BaseEventLoop.create_connection = transport
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
assert os.environ.get("PYTHONPATH") == str(BACKEND)
assert os.environ.get("DATABASE_URL") == "postgresql://test:test@127.0.0.1:1/unavailable_test"
before = hashes()
import pytest  # noqa: E402
argv = [*("tests/unit/" + name + ".py" for name in MODULES), "-q", "--tb=short", "-o", "addopts="]
result = int(pytest.main(argv))
after = hashes()
report = {"executable": sys.executable, "version": sys.version, "pytest_argv": argv,
          "source_before": before, "source_after": after, "source_unchanged": before == after,
          "network": counts, "pytest_exit_code": result}
output = FOLDER / os.environ.get("AG02_UNIT_OUTPUT", "unit-final.json")
assert not output.exists(), "Preserve previous evidence"
output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
assert before == after and counts["prohibited_attempts"] == 0
raise SystemExit(result)
