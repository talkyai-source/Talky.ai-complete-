"""Realtime tool wire format; execution remains in shared business services."""
from typing import Any
from app.domain.services.voice_pipeline.action_tools import VOICE_ACTION_NAMES, _ACTION_PARAMETERS

# Realtime wording is independent; names and argument schemas remain the shared
# execution contract. Keep descriptions consistent with prompts.py.
_REALTIME_ACTION_DESCRIPTIONS = {
    "schedule_callback": "Request a callback after confirming the caller's intended time and agreement. Report only the outcome returned by this tool.",
    "send_email": "Request an email after confirming the recipient, purpose and agreement to send. Report only the outcome returned by this tool.",
    "submit_form": "Request form submission after confirming the required details and agreement to submit. Report only the outcome returned by this tool.",
    "transfer_call": "Request transfer to the confirmed destination after the caller agrees. Report only the outcome returned by this tool.",
    "end_call": "End the call after a short goodbye when the caller clearly asks to finish. No extra confirmation is needed; do not claim the line is already disconnected.",
}

def realtime_voice_action_tools() -> list[dict[str, Any]]:
    """All actions in the flattened OpenAI/xAI Realtime tool shape."""
    tools: list[dict[str, Any]] = []
    for action in VOICE_ACTION_NAMES:
        tools.append(
            {
                "type": "function",
                "name": action,
                "description": _REALTIME_ACTION_DESCRIPTIONS[action],
                "parameters": _ACTION_PARAMETERS[action],
            }
        )
    return tools
