"""Provider-neutral wait budgets and bounded chat-completion tool continuation.

Wire formats stay in each adapter. These helpers never retry a tool execution.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import aclosing


class LLMTimeoutError(Exception):
    """No response arrived inside the provider wait budget."""


class LLMStreamStalled(LLMTimeoutError):
    """A response ended incompletely after some content was produced."""


async def close_stream(stream):
    close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
    if close:
        try:
            result = close()
            if hasattr(result, "__await__"):
                await result
        except Exception:
            # Cleanup must not replace the original timeout/cancellation.
            pass


async def stream_with_timeout(stream, timeout_seconds=10.0, *, timeout_error=LLMTimeoutError):
    """Charge only provider awaits, never time spent playing yielded content."""
    waited = 0.0
    received = 0
    loop = asyncio.get_running_loop()
    try:
        while True:
            remaining = timeout_seconds - waited
            try:
                if remaining <= 0:
                    raise asyncio.TimeoutError
                start = loop.time()
                try:
                    token = await asyncio.wait_for(
                        stream.__anext__(), remaining if received == 0 else min(remaining, 2.0)
                    )
                finally:
                    waited += loop.time() - start
            except StopAsyncIteration:
                return
            except (asyncio.TimeoutError, TimeoutError) as exc:
                error = LLMStreamStalled if received else timeout_error
                raise error("LLM provider wait budget exhausted") from exc
            received += 1
            yield token
    finally:
        await close_stream(stream)


def accumulate_tool_calls(acc, fragments):
    """Reassemble OpenAI-compatible SDK objects or raw JSON SSE fragments."""
    def field(value, name, default=None):
        return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)

    for fragment in fragments or ():
        index = field(fragment, "index", 0) or 0
        slot = acc.setdefault(index, {"id": None, "name": None, "arguments": ""})
        slot["id"] = field(fragment, "id") or slot["id"]
        function = field(fragment, "function")
        if function:
            slot["name"] = field(function, "name") or slot["name"]
            slot["arguments"] += field(function, "arguments", "") or ""


def finalize_tool_calls(acc):
    calls = []
    for index, slot in sorted(acc.items()):
        if not slot.get("name"):
            continue
        raw = slot.get("arguments") or "{}"
        try:
            arguments = json.loads(raw)
            valid = isinstance(arguments, dict)
        except (ValueError, TypeError):
            arguments, valid = {}, False
        calls.append({
            "id": slot.get("id") or f"call_{index}", "name": slot["name"],
            "arguments_raw": raw, "arguments": arguments if valid else {},
            "arguments_valid": valid,
        })
    return calls


def assistant_tool_message(calls, content=None):
    return {
        "role": "assistant", "content": content,
        "tool_calls": [{"id": c["id"], "type": "function", "function": {
            "name": c["name"], "arguments": c["arguments_raw"],
        }} for c in calls],
    }


def _matches_schema(value, schema):
    """Validate the structural subset emitted by our tool schemas, recursively."""
    if not isinstance(schema, dict):
        return schema is not False
    if "anyOf" in schema and not any(_matches_schema(value, s) for s in schema["anyOf"]):
        return False
    if "oneOf" in schema and sum(_matches_schema(value, s) for s in schema["oneOf"]) != 1:
        return False
    if "allOf" in schema and not all(_matches_schema(value, s) for s in schema["allOf"]):
        return False
    if "not" in schema and _matches_schema(value, schema["not"]):
        return False
    kinds = schema.get("type")
    if kinds:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        matches = {
            "null": value is None, "string": isinstance(value, str),
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "boolean": isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
        }
        if not any(matches.get(kind, False) for kind in kinds):
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        if any(key not in value for key in schema.get("required", ())):
            return False
        for key, item in value.items():
            if not _matches_schema(item, props.get(key, schema.get("additionalProperties", {}))):
                return False
    if isinstance(value, list) and "items" in schema:
        return all(_matches_schema(item, schema["items"]) for item in value)
    return True


async def execute_tool_call(call, tools, runner, *, timeout_seconds=8.0):
    """Reject unoffered/invalid requests; domain executors own authorization."""
    specs = {s.get("function", s).get("name"): s.get("function", s) for s in tools}
    name, args = call.get("name"), call.get("arguments")
    spec = specs.get(name)
    status = None
    if spec is None:
        status = "unknown_tool"
    elif call.get("arguments_valid") is False or not isinstance(args, dict):
        status = "invalid_arguments"
    else:
        schema = spec.get("parameters") or {}
        if not _matches_schema(args, schema):
            status = "invalid_arguments"
    if status is None:
        try:
            result = await asyncio.wait_for(runner(name, args), timeout_seconds)
            return result if isinstance(result, str) else json.dumps(result)
        except asyncio.TimeoutError:
            status = "outcome_unknown"
        except Exception:
            status = "execution_failed"
    return json.dumps({"success": False, "confirmation_allowed": False, "status": status,
                       "message": "The action could not be confirmed. Do not retry it automatically."})


MAX_NAVIGATION_ROUNDS = 4

TOOL_BUDGET_NOTE = (
    "No more tool calls are available in this reply. Answer the caller now from the "
    "results you have; if they do not contain the answer, say plainly what you cannot "
    "confirm. Do not say you will check again."
)


def _with_budget_note(content):
    """Attach the end-of-budget note to the last tool result, keeping its JSON."""
    try:
        payload = json.loads(content) if isinstance(content, str) else None
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        return json.dumps({**payload, "tool_budget": TOOL_BUDGET_NOTE}, ensure_ascii=False)
    return f"{content}\n{TOOL_BUDGET_NOTE}" if content else TOOL_BUDGET_NOTE


def ends_turn(result):
    """True when a tool result finishes the reply (an end_call decision)."""
    try:
        payload = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("ends_turn") is True


class ToolRoundBudget:
    """Keep ordinary decisions bounded while crediting verified navigation only."""

    def __init__(self, max_tool_rounds, navigation_round_allowed=None):
        if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= 3:
            raise ValueError("max_tool_rounds must be an integer from 1 to 3")
        if navigation_round_allowed is not None and not callable(navigation_round_allowed):
            raise ValueError("navigation_round_allowed must be callable")
        self.remaining = max_tool_rounds
        self.navigation_left = MAX_NAVIGATION_ROUNDS
        self.navigation_round_allowed = navigation_round_allowed

    def finish_round(self, results):
        if self.navigation_round_allowed is not None and self.navigation_left:
            try:
                allowed = self.navigation_round_allowed(results) is True
            except Exception:
                allowed = False
            if allowed:
                self.navigation_left -= 1
                return
        self.remaining -= 1


async def stream_tool_turn(provider, messages, *, tools=None, tool_runner=None,
                           require_tool_result_before_content=False, timeout_seconds=10.0,
                           max_tool_rounds=1,
                           read_only_tools=(),
                           navigation_round_allowed=None,
                           **kwargs):
    """Bounded tool dialogue; the default remains one decision and one answer.

    Live knowledge can browse a catalog then read a section. Identical tool
    write requests share their result throughout the turn. Read-only tools can
    run again so their current evidence stays aligned with the returned result.
    The optional callback may credit at most four verified navigation rounds;
    it does not increase the ordinary decision budget or remove the final cap.
    """
    budget = ToolRoundBudget(max_tool_rounds, navigation_round_allowed)
    if not tools or tool_runner is None:
        async with aclosing(provider.stream_chat_with_timeout(messages, timeout_seconds=timeout_seconds, **kwargs)) as stream:
            async for token in stream:
                yield token
        return
    extra = []
    results = {}
    while budget.remaining:
        calls, content = [], []
        continuation = {"extra_messages": list(extra)} if extra else {}
        async with aclosing(provider.stream_chat_with_timeout(
            messages, timeout_seconds=timeout_seconds, tools=tools, tool_choice="auto",
            tool_calls_sink=calls, **continuation, **kwargs,
        )) as stream:
            async for token in stream:
                content.append(token)
                if not require_tool_result_before_content:
                    yield token
        if not calls:
            if require_tool_result_before_content and content:
                yield "".join(content)
            return
        extra.append(assistant_tool_message(calls, "".join(content) or None))
        round_results = []
        for call in calls:
            key = (call["name"], json.dumps(call["arguments"], sort_keys=True), call.get("arguments_valid", True))
            if key not in results or call["name"] in read_only_tools:
                results[key] = await execute_tool_call(call, tools, tool_runner)
            extra.append({"role": "tool", "tool_call_id": call["id"], "content": results[key]})
            round_results.append((call, results[key]))
        budget.finish_round(round_results)
        # Words spoken with an end_call were the model's closing; another round
        # only repeats them ("Goodbye, Uzair! Take care. Goodbye, and thank
        # you!", test call f5dcac8e). Nothing spoken yet: it still gets a round.
        if (not require_tool_result_before_content and "".join(content).strip()
                and any(ends_turn(result) for _, result in round_results)):
            return
    # The answer round keeps the tool definitions with tool_choice="none": the
    # history holds tool calls and results, and a request that drops the tools
    # while showing those calls is malformed for OpenAI-compatible models.
    # DeepSeek answered it with the single token "0" (test call 6b9cd4c4).
    # The last result says the lookups are over, so the model answers from
    # what it has instead of promising to check again.
    if extra and extra[-1].get("role") == "tool":
        extra[-1] = {**extra[-1], "content": _with_budget_note(extra[-1].get("content"))}
    answer = []
    async with aclosing(provider.stream_chat_with_timeout(
        messages, timeout_seconds=timeout_seconds, extra_messages=extra,
        tools=tools, tool_choice="none", **kwargs,
    )) as stream:
        async for token in stream:
            if require_tool_result_before_content:
                answer.append(token)
            else:
                yield token
    if answer:
        yield "".join(answer)
