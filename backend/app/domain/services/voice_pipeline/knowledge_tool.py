"""On-demand campaign knowledge via an LLM function tool (#2, voice latency).

The default per-turn path injects the top-k knowledge block into the system
prompt on EVERY turn (see ``turn_streamer._knowledge_block_for_turn``). That
runs an FTS query and grows the prompt even on smalltalk turns ("yes, we can
talk", "okay", greetings) that need no company facts at all.

This module is the opt-in alternative: expose a ``lookup_company_knowledge``
function tool so the model fetches facts ONLY when it decides it needs them.
Most turns then carry zero knowledge — no retrieval, no injection, smaller
prompt, faster reply. On the minority of turns that do need facts, the model
self-authors a focused query (robust to STT mishears in the raw transcript)
and the answer round-trip is covered by the existing thinking-filler.

Gated behind ``VOICE_KB_MODE=tool`` (default ``inject``) so it ships dark and
can be flipped per-environment without a redeploy. Wired for Groq (OpenAI-style
tool calls) and Gemini (native function calling); gpt-oss and any other provider
fall back to the inject path automatically.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from app.domain.models.session import CallSession

# Reuse the exact per-turn budget + trimming from the inject path so the two
# modes return identically-sized facts (one source of truth for KB sizing).
from app.domain.services.voice_pipeline.kb_budget import (
    _KB_MAX_CHUNKS,
    _KB_CHUNK_CHARS,
    _KB_TOTAL_CHARS,
    _KNOWLEDGE_RETRIEVE_TIMEOUT_S,
    _trim_kb_body,
)

logger = logging.getLogger(__name__)

KB_TOOL_NAME = "lookup_company_knowledge"

# OpenAI/Groq function-tool schema. One string arg: the focused question the
# model wants answered from the company knowledge base.
KNOWLEDGE_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": KB_TOOL_NAME,
        "description": (
            "Look up the company's official knowledge base for facts about the "
            "product, pricing, plans, features, policies, coverage, hours, or "
            "any specific detail about the business. Call this ONLY when the "
            "caller asks something concrete you are not already certain of. Do "
            "NOT call it for greetings, smalltalk, confirmations, or chit-chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A short, focused search query for the fact you need, "
                        "e.g. 'price of the premium plan' or 'do you offer "
                        "refunds'. Phrase it clearly even if the caller was "
                        "vague or misheard."
                    ),
                }
            },
            "required": ["query"],
        },
    },
}

_TOOL_ADDENDUM = (
    "## Company knowledge\n"
    f"You have a tool `{KB_TOOL_NAME}` that looks up the company's official "
    "knowledge base. Use it ONLY when the caller asks a concrete question "
    "about the product, pricing, plans, features, policies, coverage, or "
    "hours that you are not already certain of — then answer naturally from "
    "what it returns, staying faithful to those facts. For greetings, "
    "smalltalk, confirmations, or anything you can answer from the "
    "conversation so far, just reply directly and do NOT call the tool."
)


def kb_tool_mode_enabled() -> bool:
    """True when on-demand tool-call KB is selected (``VOICE_KB_MODE=tool``)."""
    return os.getenv("VOICE_KB_MODE", "inject").strip().lower() == "tool"


def knowledge_tools_for(session: CallSession, provider) -> list | None:
    """Return the tool spec list when on-demand KB applies to this turn, else
    None (caller then uses the inject path). Gated to keep the tool path off
    for providers/models we haven't wired for tool-calling.
    """
    if not kb_tool_mode_enabled():
        return None
    if getattr(session, "knowledge_mode", None) not in ("retrieve", "map_retrieve"):
        return None
    # Providers wired with stream_chat_with_tools: Groq (OpenAI-style tool
    # calls) and Gemini (native function calling). Any other provider falls
    # back to the inject path.
    provider_name = getattr(provider, "name", "")
    if provider_name not in ("groq", "gemini"):
        return None
    # gpt-oss on Groq uses a reasoning request contract (instructions moved to a
    # user message) that we don't drive tools through — inject for it.
    model = str(getattr(provider, "_model", "") or "")
    if provider_name == "groq" and model.startswith("openai/gpt-oss-"):
        return None
    return [KNOWLEDGE_TOOL_SPEC]


def tool_system_addendum() -> str:
    """Short system-prompt addendum that teaches the model when to call the tool."""
    return _TOOL_ADDENDUM


async def run_knowledge_lookup(session: CallSession, query: str) -> str:
    """Execute a knowledge lookup for the model's tool call and return a small
    facts block (same budget as the inject path). Fail-soft: returns a clear
    "nothing found" sentinel on any error so the model still answers gracefully
    instead of the turn stalling.
    """
    q = (query or "").strip()
    if not q:
        return "No specific information found in the company knowledge base."
    try:
        from app.core.container import get_container
        from app.services.scripts.knowledge.retrieval import retrieve_knowledge

        container = get_container()
        if not getattr(container, "is_initialized", False):
            return "No specific information found in the company knowledge base."
        pool = getattr(getattr(container, "db_client", None), "pool", None)
        if pool is None:
            return "No specific information found in the company knowledge base."

        _t0 = time.monotonic()
        try:
            hits = await asyncio.wait_for(
                retrieve_knowledge(
                    pool, session.tenant_id, session.campaign_id, q, k=_KB_MAX_CHUNKS,
                    bump_hits=False,
                ),
                timeout=_KNOWLEDGE_RETRIEVE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "KB_TOOL call=%s TIMEOUT >%.0fms q=%r — answering without facts",
                session.call_id[:8], _KNOWLEDGE_RETRIEVE_TIMEOUT_S * 1000, q[:60],
            )
            return "No specific information found in the company knowledge base."
        _ms = (time.monotonic() - _t0) * 1000.0

        if not hits:
            logger.info("KB_TOOL call=%s NO_HITS %.0fms q=%r",
                        session.call_id[:8], _ms, q[:60])
            return "No specific information found in the company knowledge base."

        logger.info(
            "KB_TOOL call=%s HITS=%d %.0fms q=%r headings=%s",
            session.call_id[:8], len(hits), _ms, q[:60],
            [h.get("heading") for h in hits],
        )

        # Same budget as the inject path: prefer the spoken-ready voice_answer,
        # trim each node, stop at the total char budget (already ranked best-first).
        lines: list[str] = []
        used = 0
        for h in hits:
            raw = h.get("voice_answer") or h.get("summary") or h.get("content") or ""
            body = _trim_kb_body(raw, _KB_CHUNK_CHARS)
            if not body:
                continue
            entry = f"- {h['heading']}: {body}"
            if used + len(entry) > _KB_TOTAL_CHARS and used > 0:
                break
            lines.append(entry)
            used += len(entry)
        return "\n".join(lines) if lines else (
            "No specific information found in the company knowledge base."
        )
    except Exception as exc:
        logger.warning("KB_TOOL call=%s error: %s",
                       getattr(session, "call_id", "?")[:8], exc)
        return "No specific information found in the company knowledge base."
