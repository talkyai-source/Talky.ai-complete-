"""Baseline actual native-method probe; synthetic ports, network forbidden."""
import asyncio
import json
from pathlib import Path

from scripts.evaluate_ag04_conversations import offline_network_guard
from tests.qualification.ag04_native import NativeReplay, _corpus

ROOT = Path.cwd().parent


def cases():
    yield {
        "id": "probe.failed_action", "semantic_ids": [], "expect": {},
        "synthetic_action_results": {"send_email": {
            "success": False, "status": "failed", "confirmation_allowed": False,
            "provider": "synthetic-email", "request_id": "synthetic-failed-request-1",
            "message_id": None, "message": "Synthetic provider rejected the send request."}},
        "steps": [
            {"kind": "caller", "item": "request", "text": "Please email the details."},
            {"kind": "tool", "name": "send_email", "arguments": {}},
            {"kind": "response", "text": "The email was sent to you."},
            {"kind": "response", "text": "The email could not be sent."},
        ],
    }
    for variant, text in (
        ("third_party_literal", "My colleague's email is other@example.com."),
        ("third_party_reported", 'He said "My email is other@example.com".'),
        ("unclear_phone_invalid", "My phone number is +123"),
        ("unclear_phone_incomplete", "My number ends in seven three, I cannot remember the rest."),
    ):
        email = variant.startswith("third")
        yield {
            "id": "probe." + variant, "semantic_ids": [], "expect": {},
            "steps": [
                {"kind": "caller", "item": "unusable-source", "text": text, "checkpoint": "unusable"},
                {"kind": "caller", "item": "bare-confirmation", "text": "Yes.", "checkpoint": "bare-yes"},
                {"kind": "caller", "item": "own-source",
                 "text": "My email is alex@example.com." if email else "My phone number is +1 415 555 2671.",
                 "checkpoint": "own-pending"},
                {"kind": "response", "text": "Your email is alex@example.com, correct?" if email
                 else "Your phone number is +1 415 555 2671, correct?"},
                {"kind": "caller", "item": "own-confirmation", "text": "Yes.", "checkpoint": "own-confirmed"},
            ],
        }


async def main():
    attempts = []
    rows = []
    with offline_network_guard(attempts):
        for case in cases():
            for provider in ("openai", "xai"):
                row = await NativeReplay(case, provider, _corpus(ROOT)).run()
                rows.append({key: row[key] for key in (
                    "scenario_id", "profile", "raw_output", "submitted_speech", "history",
                    "contacts", "contact_checkpoints", "observed", "effects", "end", "media")})
    output = ROOT / "tmp/native-parity-initial.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source_base": "be44bf72", "blocked_network_attempts": attempts,
                                 "rows": rows}, indent=2), encoding="utf-8")
    for row in rows:
        print(json.dumps({"case": row["scenario_id"], "provider": row["profile"]["provider"],
                          "speech": row["submitted_speech"], "repairs": row["observed"]["repair_requests"],
                          "contacts": row["contact_checkpoints"], "effects": row["effects"]}))


asyncio.run(main())
