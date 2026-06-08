"""Streaming ReAct loop for the assistant — true token-by-token output.

Mirrors the agent graph (agent <-> tools) but streams the *final* answer
token-by-token instead of returning the whole reply at once. Each turn is
streamed from Groq with ``stream=True``:

  * a TOOL turn carries ``delta.tool_calls`` (reassembled by index) and no
    user-facing text → we execute the tools and loop back to the model;
  * a TEXT turn carries ``delta.content`` → we emit each chunk live.

Because tool turns don't produce content deltas, emitting content as it
arrives is safe. The loop is bounded by ``MAX_TOOL_ITERATIONS``.

Yields events (dicts):
  {"type": "token",      "delta": str}    incremental answer text
  {"type": "tool_start", "name": str}     a tool is about to run (status/UX)
  {"type": "final",      "content": str}  the full final answer text (terminal)
  {"type": "error",      "content": str}  fatal error (terminal)

Reuses the agent's SYSTEM_PROMPT + GROQ_TOOL_SCHEMAS + the shared
dispatch_tool, so prompt, schemas, and tool routing have one source of truth.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi.encoders import jsonable_encoder
from groq import AsyncGroq

from app.infrastructure.assistant.agent import SYSTEM_PROMPT
from app.infrastructure.assistant.tools.dispatch import dispatch_tool
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS
from app.infrastructure.assistant.model_config import normalize_model

logger = logging.getLogger(__name__)

# Safety cap on agent<->tools round-trips for a single user message.
MAX_TOOL_ITERATIONS = 6


def _dump_json(data: Any) -> str:
    """Encode tool results using FastAPI's JSON-safe conversion rules."""
    return json.dumps(jsonable_encoder(data))


def _build_convo(
    system_prompt: str, chat_messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Turn the stored {role, content} history into Groq chat messages."""
    convo: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in chat_messages:
        role = m.get("role", "user")
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        if role not in ("user", "assistant", "system", "tool"):
            role = "user"
        convo.append({"role": role, "content": m.get("content", "") or ""})
    return convo


async def stream_assistant_reply(
    *,
    chat_messages: List[Dict[str, Any]],
    tenant_id: str,
    user_id: Optional[str],
    conversation_id: Optional[str],
    db_client: Any,
    model: Optional[str],
) -> AsyncIterator[Dict[str, Any]]:
    """Run the streaming ReAct loop and yield token/tool_start/final events."""
    groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    system_prompt = SYSTEM_PROMPT.format(current_time=datetime.utcnow().isoformat())
    convo = _build_convo(system_prompt, chat_messages)
    resolved_model = normalize_model(model)

    try:
        for _iteration in range(MAX_TOOL_ITERATIONS):
            stream = await groq.chat.completions.create(
                model=resolved_model,
                messages=convo,
                tools=GROQ_TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=2000,
                stream=True,
            )

            content_parts: List[str] = []
            # index -> {"id", "name", "arguments"} accumulated across deltas
            tool_calls_acc: Dict[int, Dict[str, str]] = {}

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                tool_deltas = getattr(delta, "tool_calls", None)
                if tool_deltas:
                    for tcd in tool_deltas:
                        idx = tcd.index if tcd.index is not None else 0
                        acc = tool_calls_acc.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tcd.id:
                            acc["id"] = tcd.id
                        fn = getattr(tcd, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                acc["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                acc["arguments"] += fn.arguments

                content = getattr(delta, "content", None)
                if content:
                    content_parts.append(content)
                    yield {"type": "token", "delta": content}

            # --- turn finished ---
            if tool_calls_acc:
                # Tool turn: record the assistant tool-call message, run each
                # tool, append its result, then loop back to the model.
                convo.append(
                    {
                        "role": "assistant",
                        "content": "".join(content_parts),
                        "tool_calls": [
                            {
                                "id": tc["id"] or f"call_{idx}",
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"] or "{}",
                                },
                            }
                            for idx, tc in sorted(tool_calls_acc.items())
                        ],
                    }
                )

                for idx, tc in sorted(tool_calls_acc.items()):
                    name = tc["name"]
                    try:
                        parsed = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        args = parsed if isinstance(parsed, dict) else {}
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    yield {"type": "tool_start", "name": name}
                    result = await dispatch_tool(
                        name, tenant_id, db_client, conversation_id, args
                    )
                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"] or f"call_{idx}",
                            "content": _dump_json(result),
                        }
                    )
                continue  # re-enter the model with the tool results

            # Text turn with no tool calls → this was the final answer.
            yield {"type": "final", "content": "".join(content_parts)}
            return

        # Hit the iteration cap without a clean final answer.
        logger.warning("stream_assistant_reply: hit MAX_TOOL_ITERATIONS")
        yield {
            "type": "final",
            "content": "I wasn't able to finish that — please try rephrasing.",
        }

    except Exception as exc:
        logger.error("stream_assistant_reply fatal: %s", exc, exc_info=True)
        yield {"type": "error", "content": f"I encountered an error: {exc}"}
