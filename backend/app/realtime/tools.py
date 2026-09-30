"""Realtime tool wire format; execution remains in shared business services."""
from typing import Any
from app.domain.services.voice_pipeline.action_tools import VOICE_ACTION_NAMES, _ACTION_DESCRIPTIONS, _ACTION_PARAMETERS

def realtime_voice_action_tools() -> list[dict[str, Any]]:
    """All actions in the flattened OpenAI/xAI Realtime tool shape."""
    tools: list[dict[str, Any]] = []
    for action in VOICE_ACTION_NAMES:
        tools.append(
            {
                "type": "function",
                "name": action,
                "description": _ACTION_DESCRIPTIONS[action],
                "parameters": _ACTION_PARAMETERS[action],
            }
        )
    return tools
