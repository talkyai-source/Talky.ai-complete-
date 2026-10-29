"""One bot turn: bounded tool loop, then one structured final answer.

Logs only counts, tool names, durations and error kinds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date
from typing import Any

from app.domain.services.support.bot.diagnostics import ToolExecutor, result_json
from app.domain.services.support.bot.prompt import build_system_prompt
from app.domain.services.support.bot.result import FINAL_PROPERTIES, parse_final_result
from app.domain.services.support.bot.tools import TOOL_SCHEMAS
from app.domain.services.support.bot.types import BotRequest, BotTurn, Finding, Source, ToolTrace
from app.domain.services.support.config import (
    BOT_MAX_OUTPUT_TOKENS,
    BOT_MAX_TOOL_CALLS,
    BOT_TIMEOUT_SECONDS,
)
from app.infrastructure.assistant.llm_client import get_assistant_client
from app.infrastructure.llm.structured_output import response_format_for

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 12
APOLOGY = ("Sorry, I could not look into this just now. I can pass this to a person on our team "
           "who will follow up with you.")
_FINAL_INSTRUCTION = (
    "Now reply with ONE JSON object only, with keys: answer, sources (slugs of help articles you used), "
    "resolution_prompt, needs_clarification, propose_escalation, escalation_reason, category."
)


class _State:
    def __init__(self) -> None:
        self.traces: list[ToolTrace] = []
        self.findings: list[Finding] = []
        self.sources: dict[str, Source] = {}
        self.account: dict[str, Any] | None = None
        self.executed = 0


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


async def _tool_loop(client: Any, adapt: Any, request: BotRequest, executor: ToolExecutor,
                     messages: list[dict[str, Any]], state: _State) -> None:
    while True:
        resp = await client.chat.completions.create(**adapt({
            "model": request.model, "messages": messages, "tools": TOOL_SCHEMAS,
            "tool_choice": "auto", "temperature": 0.2, "max_tokens": BOT_MAX_OUTPUT_TOKENS}))
        message = _field(_field(resp, "choices")[0], "message")
        calls = list(_field(message, "tool_calls") or [])
        if not calls:
            return
        messages.append({"role": "assistant", "content": _field(message, "content") or "", "tool_calls": [
            {"id": _field(c, "id"), "type": "function",
             "function": {"name": _field(_field(c, "function"), "name"),
                          "arguments": _field(_field(c, "function"), "arguments") or "{}"}} for c in calls]})
        for call in calls:
            fn = _field(call, "function")
            name = str(_field(fn, "name") or "")
            if state.executed >= BOT_MAX_TOOL_CALLS:
                content = json.dumps({"error": "tool_budget_exhausted"})
            else:
                state.executed += 1
                started = time.monotonic()
                outcome = await executor.execute(name, _field(fn, "arguments"))
                logger.info("support_bot_tool tool=%s ms=%d", name[:40], int((time.monotonic() - started) * 1000))
                state.traces.append(outcome.trace)
                state.findings.extend(outcome.findings)
                for source in outcome.sources:
                    state.sources[source.slug] = source
                if outcome.account is not None:
                    state.account = outcome.account
                content = result_json(outcome)
            messages.append({"role": "tool", "tool_call_id": _field(call, "id"), "content": content})
        if state.executed >= BOT_MAX_TOOL_CALLS:
            return


async def _final(client: Any, adapt: Any, request: BotRequest, messages: list[dict[str, Any]],
                 state: _State) -> str:
    resp = await client.chat.completions.create(**adapt({
        "model": request.model, "messages": messages + [{"role": "user", "content": _FINAL_INSTRUCTION}],
        "temperature": 0.2, "max_tokens": BOT_MAX_OUTPUT_TOKENS,
        "response_format": response_format_for(request.model, properties=FINAL_PROPERTIES,
                                               name="support_reply")}))
    return str(_field(_field(_field(resp, "choices")[0], "message"), "content") or "")


async def run_turn(request: BotRequest, *, executor: ToolExecutor, client_factory=get_assistant_client) -> BotTurn:
    started = time.monotonic()
    state = _State()

    def failed(kind: str) -> BotTurn:
        logger.warning("support_bot_turn_failed kind=%s tools=%d", kind, state.executed)
        return BotTurn(answer=APOLOGY, error=kind, tool_calls=tuple(state.traces),  # type: ignore[arg-type]
                       findings=tuple(state.findings), account=state.account, model=request.model,
                       latency_ms=int((time.monotonic() - started) * 1000))

    try:
        client, adapt = client_factory(request.model)
    except ValueError:
        return failed("not_configured")
    route = (request.page_context or {}).get("route")
    messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(
        page_route=route if isinstance(route, str) else None, today=date.today())}]
    for role, content in request.history[-HISTORY_LIMIT:]:
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    messages.append({"role": "user", "content": request.user_text})
    try:
        async with asyncio.timeout(BOT_TIMEOUT_SECONDS):
            await _tool_loop(client, adapt, request, executor, messages, state)
            raw = await _final(client, adapt, request, messages, state)
    except TimeoutError:
        return failed("timeout")
    except Exception as exc:  # noqa: BLE001 - kind only
        logger.warning("support_bot_provider_error type=%s", type(exc).__name__)
        return failed("provider_error")
    try:
        fields = parse_final_result(raw, allowed_sources=state.sources)
    except ValueError:
        return failed("invalid_output")
    latency = int((time.monotonic() - started) * 1000)
    logger.info("support_bot_turn tools=%d findings=%d ms=%d", state.executed, len(state.findings), latency)
    return BotTurn(tool_calls=tuple(state.traces), findings=tuple(state.findings), account=state.account,
                   model=request.model, latency_ms=latency, **fields)
