"""Bounded affected regression with source hashes and no outbound IO."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
FOLDER = Path(__file__).resolve().parent
MODULES = ["tests/unit/" + name + ".py" for name in (
    "test_campaign_knowledge_staged_http", "test_campaign_knowledge_permissions",
    "test_knowledge_ingest_staging", "test_knowledge_enrichment_routing",
    "test_knowledge_evidence_budget_order", "test_knowledge_uncertainty_contract",
    "test_knowledge_tool", "test_agent_knowledge_repairs")]
SOURCE = ["backend/" + name for name in MODULES] + ["backend/" + name for name in (
    "app/api/v1/endpoints/campaign_knowledge.py", "app/domain/services/campaign_knowledge_access.py",
    "app/services/scripts/knowledge/enricher.py", "app/services/scripts/knowledge/ingest_service.py",
    "app/services/scripts/knowledge/retrieval.py", "app/services/scripts/knowledge/passages.py",
    "app/domain/services/voice_pipeline/kb_budget.py", "app/domain/services/voice_pipeline/knowledge_tool.py",
    "app/domain/services/voice_pipeline/turn_streamer.py", "app/api/v1/dependencies.py", "app/core/db_utils.py")]
SOURCE += ["docs/sessions/artifacts/ag02-diagnostic-evidence/unit_probe.py"]



def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in SOURCE}


def main():
    output = FOLDER / "unit-final-result.json"
    assert not output.exists(), "Preserve earlier evidence"
    sys.path.insert(0, str(ROOT / "backend"))
    os.chdir(ROOT / "backend")
    local = threading.local()
    connect, connect_ex, socketpair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
    transport = asyncio.BaseEventLoop.create_connection
    counts = {"internal_socketpairs": 0, "prohibited_attempts": 0}
    def guard(sock, address, *, original):
        if getattr(local, "socketpair", False) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        counts["prohibited_attempts"] += 1
        raise AssertionError("No network IO permitted for synthetic regression")
    def pair(*args, **kwargs):
        local.socketpair = True
        try:
            result = socketpair(*args, **kwargs)
            counts["internal_socketpairs"] += 1
            return result
        finally:
            local.socketpair = False
    async def no_transport(*_args, **_kwargs):
        counts["prohibited_attempts"] += 1
        raise AssertionError("No async transport permitted for synthetic regression")
    socket.socket.connect = lambda sock, addr: guard(sock, addr, original=connect)
    socket.socket.connect_ex = lambda sock, addr: guard(sock, addr, original=connect_ex)
    socket.socketpair = pair
    asyncio.BaseEventLoop.create_connection = no_transport
    record = {"source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_before": hashes(), "python": sys.executable,
              "pytest_argv": [*MODULES, "-q", "--tb=short", "--disable-warnings"]}
    try:
        import pytest
        result = int(pytest.main(record["pytest_argv"]))
        record["exit_code"] = result
    finally:
        record.update(source_after=hashes(), **counts)
        record["source_unchanged"] = record["source_before"] == record["source_after"]
        output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        socket.socket.connect, socket.socket.connect_ex, socket.socketpair = connect, connect_ex, socketpair
        asyncio.BaseEventLoop.create_connection = transport
    assert record["source_unchanged"] and counts["prohibited_attempts"] == 0
    return result


if __name__ == "__main__":
    raise SystemExit(main())
