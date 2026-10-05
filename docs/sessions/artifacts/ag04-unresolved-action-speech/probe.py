"""Local, SDK-free validator probe; run from the worktree backend directory."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types


parser = argparse.ArgumentParser()
parser.add_argument("--source-ref")
parser.add_argument("--output", required=True)
args = parser.parse_args()
path = "backend/app/domain/services/llm_guardrails.py"
if args.source_ref:
    source = subprocess.check_output(["git", "show", f"{args.source_ref}:{path}"])
    module = types.ModuleType("synthetic_guard_probe")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{args.source_ref}:{path}", "exec"), module.__dict__)
else:
    from app.domain.services import llm_guardrails as module

    source = Path("app/domain/services/llm_guardrails.py").read_bytes()

from app.domain.services.voice_pipeline.action_tools import safe_failure_speech

texts = [
    "The email was sent to you.",
    "The email was not sent.",
    "The email could not be sent.",
    "I haven't sent the email.",
    "The email failed.",
    "I can't confirm whether the email was sent.",
    "The sending outcome is unknown. It needs checking before another attempt.",
    "The email was not sent. Please ask me to send it again.",
    "Please try again now.",
    "I will resend the email now.",
    "Please send the email again.",
    "Please check the existing request before trying again.",
]
rows = []
for status in ("unknown", "in_progress", "failed"):
    result = {
        "version": 1, "action": "send_email", "success": False,
        "status": status, "confirmation_allowed": False,
        "request_id": "synthetic-original-request", "message_id": None,
    }
    for text in texts + [safe_failure_speech("send_email", result)]:
        valid, reason = module.LLMGuardrails().validate_response(
            text, action_results={"send_email": result},
        )
        rows.append({"receipt_status": status, "text": text, "valid": valid, "reason": reason})

report = {
    "source_ref": args.source_ref or "working_tree",
    "source_path": path,
    "source_sha256": hashlib.sha256(source).hexdigest(),
    "execution": "Actual validator class; baseline source loaded verbatim from local git when --source-ref is set.",
    "effects": {"provider_calls": 0, "database_calls": 0, "model_calls": 0},
    "rows": rows,
}
Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"source_ref": report["source_ref"], "rows": len(rows), "output": args.output}))
