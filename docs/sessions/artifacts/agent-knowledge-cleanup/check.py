"""Bounded affected regression with source hashes and no outbound IO."""
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
FOLDER = Path(__file__).resolve().parent
MODULES = ['tests/unit/test_assistant_knowledge_authorization.py', 'tests/unit/test_assistant_agent_service.py', 'tests/unit/test_knowledge_model_sections.py', 'tests/unit/test_knowledge_tool.py', 'tests/unit/test_knowledge_relevance.py', 'tests/unit/test_agent_knowledge_repairs.py', 'tests/unit/voice_pipeline/test_turn_streamer_kb_prefetch.py', 'tests/security/test_kb_tool_injection.py']
SOURCE = ['backend/tests/unit/test_assistant_knowledge_authorization.py', 'backend/tests/unit/test_assistant_agent_service.py', 'backend/tests/unit/test_knowledge_model_sections.py', 'backend/tests/unit/test_knowledge_tool.py', 'backend/tests/unit/test_knowledge_relevance.py', 'backend/tests/unit/test_agent_knowledge_repairs.py', 'backend/tests/unit/voice_pipeline/test_turn_streamer_kb_prefetch.py', 'backend/tests/security/test_kb_tool_injection.py', 'backend/app/infrastructure/assistant/tools/campaign_admin.py', 'backend/app/infrastructure/assistant/tools/llm_schemas.py', 'backend/app/infrastructure/assistant/tools/__init__.py', 'backend/app/infrastructure/assistant/agent.py', 'backend/app/services/scripts/knowledge/sections.py', 'backend/app/domain/services/voice_pipeline/knowledge_tool.py']



def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in SOURCE}


def main():
    phase = sys.argv[1]
    assert phase in {"baseline", "final", "corrected"}
    output = FOLDER / f"unit-{phase}-result.json"
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
              "python_version": sys.version,
              "environment": {key: os.environ.get(key) for key in (
                  "PYTHONPATH", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE")},
              "installed_versions": {name: importlib.metadata.version(name) for name in (
                  "pytest", "pytest-asyncio", "pydantic", "fastapi", "groq")},
              "pytest_argv": [*(MODULES if phase != "baseline" else MODULES[:1]), "-q", "--tb=short", "--disable-warnings"]}
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
