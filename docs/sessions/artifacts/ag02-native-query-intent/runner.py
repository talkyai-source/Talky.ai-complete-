"""Offline composed-prompt and synthetic wire checks; no model-quality claim."""
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
OUT = Path(__file__).resolve().parent
stage = sys.argv[1]
assert stage in {"baseline", "final"}
result_path = OUT / (stage + ".json")
assert not result_path.exists(), "Preserve prior evidence"
assert not (ROOT / "backend/.env").exists(), "No dotenv fixture is authorized"
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
modules = ["test_realtime_quality_pass.py", "test_realtime_profile_contract.py",
           "test_realtime_independent.py", "test_explicit_secret_boundary.py",
           "test_true_inbound_prompt_separation.py", "test_prompt_versions.py"]
paths = ["backend/app/realtime/" + name for name in
         ["prompts.py", "openai.py", "prompt_config.py", "runtime.py", "personas.py", "xai.py"]]
paths += ["backend/app/domain/services/telephony_session_config.py",
          "backend/app/domain/services/voice_orchestrator.py",
          "backend/app/services/scripts/prompts/versions.py"]
paths += ["backend/tests/unit/" + name for name in modules]

def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in paths}

before = hashes()
original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
local = threading.local()
network = {"internal_socketpairs": 0, "prohibited_socket_attempts": 0, "prohibited_transport_attempts": 0}

def connect(sock, address, original):
    if getattr(local, "pair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original(sock, address)
    network["prohibited_socket_attempts"] += 1
    raise AssertionError("No provider or database connection is authorized")

def pair(*args, **kwargs):
    local.pair = True
    try:
        result = original_pair(*args, **kwargs)
        network["internal_socketpairs"] += 1
        return result
    finally:
        local.pair = False

socket.socket.connect = lambda sock, address: connect(sock, address, original_connect)
socket.socket.connect_ex = lambda sock, address: connect(sock, address, original_ex)
socket.socketpair = pair

async def deny_transport(*args, **kwargs):
    network["prohibited_transport_attempts"] += 1
    raise AssertionError("No provider or database async transport is authorized")

asyncio.BaseEventLoop.create_connection = deny_transport
import pytest

argv = ["tests/unit/" + m for m in modules]
if stage == "baseline":
    argv = [argv[0] + "::test_knowledge_tool_and_prompt_require_evidence_not_model_confidence",
            argv[1] + "::test_all_offered_native_voices_and_hidden_xai_have_sanitized_wire_evidence"]
argv += ["-q", "--tb=short", "--disable-warnings", "-o", "addopts="]
code = int(pytest.main(argv))
after = hashes()
report = {
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "python": sys.executable, "cwd": str(Path.cwd()), "runner_argv": sys.argv,
    "pytest_argv": argv, "environment": {k: os.environ.get(k) for k in
        ["PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "ENVIRONMENT", "DATABASE_URL"]},
    "versions": {p: importlib.metadata.version(p) for p in
        ["pytest", "pytest-asyncio", "pydantic", "httpx", "websockets"]},
    "source_before": before, "source_after": after, "source_unchanged": before == after,
    "pytest_exit_code": code, "network": network,
    "scope": "Actual prompt/config/campaign/runtime serialization with synthetic storage and WebSocket acknowledgements. No provider, DB, speech or customer execution. Existing venv, not exact-lock dependency qualification.",
}
result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
assert before == after and network["prohibited_socket_attempts"] == network["prohibited_transport_attempts"] == 0
raise SystemExit(code)
