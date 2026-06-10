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
  {"type": "proposal",   "tool", "args", "result"}  edit preview → diff card (terminal)
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
from groq import APIError, AsyncGroq

from app.infrastructure.assistant.agent import SYSTEM_PROMPT
from app.infrastructure.assistant.tools.dispatch import dispatch_tool
from app.infrastructure.assistant.tools.llm_schemas import GROQ_TOOL_SCHEMAS
from app.infrastructure.assistant.model_config import normalize_model
from app.infrastructure.assistant.proposals import is_preview_result, PROPOSAL_TOOLS

logger = logging.getLogger(__name__)

# Safety cap on agent<->tools round-trips for a single user message.
MAX_TOOL_ITERATIONS = 6

# Retries when the model emits a MALFORMED tool call (Groq code
# "tool_use_failed", e.g. `<function=name({...})`). This is a known
# intermittent llama failure mode — the same prompt usually succeeds on the
# next attempt, so we retry the turn instead of killing the conversation.
MAX_TOOL_USE_RETRIES = 2


def _is_tool_use_failed(exc: APIError) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body.get("code") == "tool_use_failed":
        return True
    return "tool_use_failed" in str(body or exc)


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
        iterations = 0
        tool_use_retries = 0
        while iterations < MAX_TOOL_ITERATIONS:
            iterations += 1

            content_parts: List[str] = []
            # index -> {"id", "name", "arguments"} accumulated across deltas
            tool_calls_acc: Dict[int, Dict[str, str]] = {}

            try:
                stream = await groq.chat.completions.create(
                    model=resolved_model,
                    messages=convo,
                    tools=GROQ_TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=0.7,
                    max_tokens=2000,
                    stream=True,
                )

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

            except APIError as api_exc:
                # The model emitted a syntactically broken tool call — nothing
                # was appended to convo, so simply re-run the same turn.
                if _is_tool_use_failed(api_exc) and tool_use_retries < MAX_TOOL_USE_RETRIES:
                    tool_use_retries += 1
                    iterations -= 1  # a retry doesn't consume tool budget
                    logger.warning(
                        "stream_assistant_reply: malformed tool call from model "
                        "(tool_use_failed) — retry %d/%d",
                        tool_use_retries, MAX_TOOL_USE_RETRIES,
                    )
                    continue
                raise

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
                    # An edit tool's preview becomes a first-class proposal: end
                    # the turn here and let the UI's Apply/Reject drive the
                    # confirm=true apply. We do NOT feed the preview back to the
                    # model (that produced the fragile "type yes" loop).
                    if name in PROPOSAL_TOOLS and is_preview_result(result):
                        yield {
                            "type": "proposal",
                            "tool": name,
                            "args": args,
                            "result": result,
                        }
                        return
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
