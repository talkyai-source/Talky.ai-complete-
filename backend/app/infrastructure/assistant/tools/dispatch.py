"""Shared tool dispatch for the assistant.

One source of truth for routing a tool name to its implementation, used by
BOTH the LangGraph ``tool_executor`` (agent.py) and the streaming ReAct loop
(streaming.py). Returns the raw tool-result dict; each caller wraps it
(``ToolMessage`` for the graph, a ``role=tool`` dict for the stream) as needed.

Keeping the routing here means the two execution paths can never drift.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.infrastructure.assistant.tools import ALL_TOOLS
from app.infrastructure.assistant.tools.coercion import coerce_bool

logger = logging.getLogger(__name__)

# Tools that accept a ``conversation_id`` kwarg (for action attribution /
# audit). Every other tool is called with just (tenant_id, db_client, **args).
_CONVO_AWARE = {"send_email", "send_sms", "initiate_call", "start_campaign", "report_issue", "create_campaign"}

# Boolean flags small models routinely emit as strings ("confirm": "true").
# The Groq schemas accept ["boolean", "string"] for these so validation can't
# reject the call; this funnel normalises them exactly once, with the same
# defaults the schemas advertise.
_BOOL_ARGS = {
    "confirm": False,
    "unread_only": False,
    "only_leads": False,
    "today_only": True,
}


async def dispatch_tool(
    func_name: str,
    tenant_id: str,
    db_client: Any,
    conversation_id: Optional[str],
    args: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Route ``func_name`` to its tool and return the raw result dict.

    Never raises: a missing tool, bad arguments, or an unexpected error all
    come back as ``{"error": ...}`` so the agent loop can keep going and the
    model can react to the failure.
    """
    entry = ALL_TOOLS.get(func_name)
    if not entry or not entry.get("function"):
        return {"error": f"Unknown tool: {func_name}"}

    fn = entry["function"]
    call_args = dict(args) if isinstance(args, dict) else {}
    for key, default in _BOOL_ARGS.items():
        if key in call_args:
            call_args[key] = coerce_bool(call_args[key], default)

    try:
        if func_name in _CONVO_AWARE:
            return await fn(tenant_id, db_client, conversation_id=conversation_id, **call_args)
        return await fn(tenant_id, db_client, **call_args)
    except TypeError as exc:
        # Bad / extra kwargs from the model — surface as a tool error rather
        # than crashing the turn.
        logger.warning("dispatch_tool %s bad args %s: %s", func_name, call_args, exc)
        return {"error": f"Invalid arguments for {func_name}: {exc}"}
    except Exception as exc:  # tool internals already guard, but be safe
        logger.error("dispatch_tool %s failed: %s", func_name, exc, exc_info=True)
        return {"error": str(exc)}
