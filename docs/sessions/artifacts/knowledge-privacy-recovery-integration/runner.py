"""Bounded combined regression; run this archived runner from repository tmp/."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/sessions/artifacts/knowledge-privacy-recovery-integration"
sys.path.insert(0, str(ROOT / "backend"))
MODULES = [
    "test_knowledge_evidence_budget_order.py", "test_knowledge_enrichment_routing.py",
    "test_agent_knowledge_repairs.py", "test_knowledge_uncertainty_contract.py",
    "test_realtime_knowledge_evidence.py", "test_explicit_secret_boundary.py",
    "test_ag05_transcript_evidence.py", "test_ag05_native_contact_revision.py",
    "test_voice_email_receipt_proof.py", "test_voice_email_inspection_handoff.py",
    "test_admin_gmail_inspection.py", "test_admin_calendar_inspection.py",
    "test_saved_acknowledgement.py", "test_prompt_versions.py",
    "test_core_prompt_contract.py", "test_true_inbound_prompt_separation.py",
]
PATHS = [
    "backend/app/domain/services/explicit_secrets.py",
    "backend/app/domain/services/transcript_service.py",
    "backend/app/domain/services/voice_pipeline/transcript_handler.py",
    "backend/app/domain/services/voice_pipeline/action_execution.py",
    "backend/app/domain/services/voice_pipeline/kb_budget.py",
    "backend/app/domain/services/voice_pipeline/knowledge_tool.py",
    "backend/app/domain/services/call_summary/summarizer.py",
    "backend/app/realtime/bridge.py", "backend/app/realtime/prompts.py",
    "backend/app/services/scripts/prompts/guardrails.py",
    "backend/app/services/scripts/prompts/versions.py",
    "backend/app/services/scripts/knowledge/enricher.py",
    "backend/app/services/scripts/knowledge/retrieval.py",
    "backend/app/api/v1/endpoints/admin/actions.py",
    "backend/app/services/saved_acknowledgement.py",
    "backend/app/services/action_execution.py", "backend/app/services/email_service.py",
    "backend/app/core/security/principal.py",
    *["backend/tests/unit/" + name for name in MODULES],
]


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in PATHS}


before = hashes()
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
for name in PATHS:
    committed = subprocess.check_output(["git", "show", head + ":" + name], cwd=ROOT)
    assert hashlib.sha256(committed.replace(b"\r\n", b"\n")).hexdigest() == before[name], name

local = threading.local()
connect, connect_ex, socketpair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
network = {"internal_socketpairs": 0, "prohibited_socket_attempts": 0, "prohibited_transport_attempts": 0}


def guarded_connect(sock, address, *, original):
    if getattr(local, "socketpair", False) and address[0] in {"127.0.0.1", "::1"}:
        return original(sock, address)
    network["prohibited_socket_attempts"] += 1
    raise AssertionError("No database or provider connection authorized for this unit run")


def guarded_socketpair(*args, **kwargs):
    local.socketpair = True
    try:
        pair = socketpair(*args, **kwargs)
        network["internal_socketpairs"] += 1
        return pair
    finally:
        local.socketpair = False


socket.socket.connect = lambda sock, address: guarded_connect(sock, address, original=connect)
socket.socket.connect_ex = lambda sock, address: guarded_connect(sock, address, original=connect_ex)
socket.socketpair = guarded_socketpair


async def denied_transport(self, *args, **kwargs):
    # Windows Proactor async connections can bypass socket.connect entirely.
    network["prohibited_transport_attempts"] += 1
    raise AssertionError("No database or provider async transport authorized for this unit run")


asyncio.BaseEventLoop.create_connection = denied_transport
import pytest  # noqa: E402

argv = [*["tests/unit/" + name for name in MODULES], "-q", "--tb=short", "--disable-warnings"]
code = int(pytest.main(argv))
after = hashes()
OUTPUT.mkdir(parents=True, exist_ok=True)
(OUTPUT / "result.json").write_text(json.dumps({
    "source_head": head, "cwd": str(Path.cwd()), "python": sys.executable,
    "pytest_argv": argv, "source_hashes_before": before, "source_hashes_after": after,
    "source_unchanged": before == after, "all_recorded_inputs_committed_before_run": True,
    "exit_code": code, **network,
    "limits": "Sixteen affected unit modules with synthetic ports; no actual database, browser, provider, audio or customer-acceptance proof. Counts overlap earlier owner runs.",
}, indent=2) + "\n", encoding="utf-8")
assert before == after and network["prohibited_socket_attempts"] == 0 and network["prohibited_transport_attempts"] == 0
raise SystemExit(code)
