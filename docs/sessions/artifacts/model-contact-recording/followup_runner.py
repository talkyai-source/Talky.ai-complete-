"""Source-bound offline harness checks; never dispatches a provider request."""
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
phase = sys.argv[1]
assert phase == "followup"
target = OUT / (phase + ".json")
assert not target.exists(), "Preserve every execution artifact"
assert not (ROOT / "backend/.env").exists(), "No dotenv fixture is authorized"
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
modules = ["test_model_contact_recording.py"]
paths = [
    "backend/app/domain/services/voice_pipeline/contact_recording.py",
    "backend/app/domain/services/voice_pipeline/lead_slot_capture.py",
    "backend/app/domain/services/voice_pipeline/turn_runner.py",
    "backend/app/domain/services/voice_pipeline/turn_ender.py",
    "backend/app/realtime/bridge.py", "backend/app/realtime/tools.py",
    "backend/app/domain/services/voice_pipeline/contact_capture.py",
    "backend/app/services/scripts/call_state_tracker.py",
    "backend/app/domain/services/lead_capture_service.py",
    "backend/app/domain/services/transcript_service.py",
    "backend/app/domain/services/phone_number_normalizer.py",
    "backend/app/core/db_utils.py",
    "backend/app/realtime/openai.py", "backend/app/realtime/xai.py",
    "backend/app/realtime/playout_buffer.py", "backend/app/realtime/prompts.py",
    "backend/app/realtime/prompt_config.py",
    "backend/tests/unit/test_model_contact_recording.py",
    "backend/tests/unit/test_instant_opener_continues_to_llm.py",
    "backend/tests/qualification/ag04_native.py",
    "backend/tests/fixtures/conversation/ag04_native.json",
    "docs/sessions/artifacts/model-contact-recording/followup_runner.py",
    "backend/app/domain/services/voice_pipeline/live_structured_state.py",
    "backend/app/domain/services/voice_pipeline/action_execution.py",
    "backend/tests/unit/test_realtime_relationship_ownership.py",
]

def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in paths}

before = hashes()
original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
local = threading.local()
network = {"internal_socketpairs": 0, "prohibited_socket_attempts": 0, "prohibited_transport_attempts": 0}

def connect(sock, address, original):
    if getattr(local, "pair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original(sock, address)
    network["prohibited_socket_attempts"] += 1
    raise AssertionError("No external socket is authorized for offline qualification")

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
    raise AssertionError("No external async transport is authorized")

asyncio.BaseEventLoop.create_connection = deny_transport
import pytest

selected = [
    "test_duplicate_final_does_not_become_another_caller_turn",
    "test_provider_keeps_speech_item_identity_and_commit_order",
    "test_normal_continuation_preserves_current_history_and_active_response",
    "test_delayed_opt_out_is_retained_without_hanging_up_newer_question",
    "test_current_replacement_cannot_erase_accepted_opt_out",
    "test_evicted_item_late_opt_out_survives_without_current_hangup",
    "test_current_asr_revision_is_evidence_on_original_row_not_a_new_turn",
    "test_revision_annotation_respects_identity_sealing_and_bounded_metadata",
    "test_corrected_final_revokes_old_action_before_external_effect",
]
modules += ["test_realtime_relationship_ownership.py::" + test for test in selected]
argv = ["tests/unit/" + m for m in modules] + ["-q", "--tb=short", "--disable-warnings", "-o", "addopts="]
code = int(pytest.main(argv))
after = hashes()
report = {
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "python": sys.executable, "cwd": str(Path.cwd()), "runner_argv": sys.argv, "pytest_argv": argv,
    "environment": {k: os.environ.get(k) for k in
        ["PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "ENVIRONMENT", "DATABASE_URL", "VOICE_KB_MODE"]},
    "versions": {p: importlib.metadata.version(p) for p in ["pytest", "pytest-asyncio", "groq", "httpx", "pydantic"]},
    "source_before": before, "source_after": after, "source_unchanged": before == after,
    "pytest_exit_code": code, "network": network,
    "scope": "Actual contact tool, persistence service validation/SQL construction and native event parsers/bridge with synthetic SQL/model/audio ports. No provider or SQL execution or customer acceptance. Existing venv, not exact-lock qualification.",
}
target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
assert before == after and network["prohibited_socket_attempts"] == network["prohibited_transport_attempts"] == 0
raise SystemExit(code)
