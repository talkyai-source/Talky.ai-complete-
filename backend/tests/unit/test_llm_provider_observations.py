"""Actual LLM adapters with synthetic SDK/HTTP ports; no provider traffic."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm import cerebras, deepseek, gemini, groq, openai
from app.infrastructure.llm.streaming import LLMStreamStalled
from app.infrastructure.metrics import provider_observations as observations


PROVIDERS = ["groq", "cerebras", "deepseek", "gemini", "openai"]
KEY = "synthetic-private-llm-key"
TEXT = "private answer that must never enter telemetry"
MESSAGES = [Message(role=MessageRole.USER, content="private customer question")]


@pytest.fixture(autouse=True)
def captured(monkeypatch):
    rows = []
    monkeypatch.setattr(observations, "_signing_key", lambda: b"synthetic-signing-key")
    monkeypatch.setattr(observations, "publish", rows.append)
    real_connect = socket.socket.connect

    def no_network(sock, address):
        # Windows asyncio's self-pipe uses loopback sockets.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return real_connect(sock, address)
        raise AssertionError("External sockets forbidden in synthetic LLM tests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    for module in (groq, cerebras, gemini):
        monkeypatch.setattr(module, "_LLM_RETRY_BASE_DELAY", 0)
    return rows


class Refused(RuntimeError):
    def __init__(self, status):
        super().__init__("private provider failure body")
        self.status_code = status


class Chunks:
    def __init__(self, values):
        self.values = iter(values)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = next(self.values, None)
        if value is None:
            raise StopAsyncIteration
        if isinstance(value, asyncio.Event):
            await value.wait()
            raise StopAsyncIteration
        if isinstance(value, BaseException):
            raise value
        parsed = deepseek._ns(value)
        if "function_calls" in value:
            from google.genai import types

            parsed.function_calls = [types.FunctionCall(**call) for call in value["function_calls"]]
        return parsed

    async def aclose(self):
        self.closed = True


class WireChunks(httpx.AsyncByteStream):
    def __init__(self, values):
        self.values = values
        self.closed = False

    async def __aiter__(self):
        for value in self.values:
            if isinstance(value, asyncio.Event):
                await value.wait()
                return
            if isinstance(value, BaseException):
                raise value
            yield f"data: {json.dumps(value)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed = True


def chunk(provider, text=None, reason=None, tool=False):
    if provider == "gemini":
        parts = [{"text": text}] if text else []
        calls = [{"name": "lookup", "args": {"query": "private tool argument"}}] if tool else []
        return {
            "text": text,
            "candidates": [{"content": {"parts": parts}, "finish_reason": reason}],
            "function_calls": calls,
        }
    delta = {"content": text}
    if tool:
        delta["tool_calls"] = [
            {
                "index": 0,
                "id": "call1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"query":"private tool argument"}'},
            }
        ]
    return {"choices": [{"delta": delta, "finish_reason": reason}]}


def terminal(provider):
    return chunk(provider, reason="STOP" if provider == "gemini" else "stop")


async def setup_provider(monkeypatch, provider, attempts, context=None):
    """Run real initialization; replace only SDK creation or HTTP transport."""
    context = context or {
        "source": "tenant",
        "tenant_id": str(uuid4()),
        "credential_id": str(uuid4()),
    }
    seen, streams = [], []
    pending = iter(attempts)

    async def create(**request):
        seen.append(request)
        outcome = next(pending)
        if isinstance(outcome, asyncio.Event):
            await outcome.wait()
        if isinstance(outcome, BaseException):
            raise outcome
        stream = Chunks(outcome)
        streams.append(stream)
        return stream

    async def close():
        pass

    sdk = NS(chat=NS(completions=NS(create=create)), close=close)
    classes = {
        "groq": groq.GroqLLMProvider,
        "cerebras": cerebras.CerebrasLLMProvider,
        "deepseek": deepseek.DeepSeekLLMProvider,
        "gemini": gemini.GeminiLLMProvider,
        "openai": openai.OpenAILLMProvider,
    }
    if provider == "groq":
        monkeypatch.setattr(groq, "AsyncGroq", lambda **kw: sdk)
    elif provider == "cerebras":
        import cerebras.cloud.sdk as cerebras_sdk

        monkeypatch.setattr(cerebras_sdk, "AsyncCerebras", lambda **kw: sdk)
    elif provider == "gemini":
        from google import genai

        sdk = NS(
            aio=NS(models=NS(generate_content_stream=create), aclose=close), close=lambda: None
        )
        monkeypatch.setattr(genai, "Client", lambda **kw: sdk)
    else:
        original_client = httpx.AsyncClient

        async def handler(request):
            seen.append(request)
            outcome = next(pending)
            if isinstance(outcome, asyncio.Event):
                await outcome.wait()
            if isinstance(outcome, Refused):
                return httpx.Response(outcome.status_code, text=str(outcome))
            if isinstance(outcome, BaseException):
                raise outcome
            stream = WireChunks(outcome)
            streams.append(stream)
            return httpx.Response(200, stream=stream)

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
        )
    p = classes[provider]()
    await p.initialize({"api_key": KEY, "observation_context": context})
    return p, seen, streams, context


async def collect(stream):
    return [value async for value in stream]


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_completed_response_is_bound_to_actual_initialized_key_and_scope(
    monkeypatch, captured, provider
):
    p, seen, streams, context = await setup_provider(
        monkeypatch, provider, [[chunk(provider, TEXT), terminal(provider)]]
    )
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [TEXT]
    finally:
        await p.cleanup()
    assert len(seen) == len(captured) == 1
    row = captured[0]
    assert row.outcome == "completed" and row.protocol_accepted and row.output_seen
    assert row.first_output_ms is not None and row.first_output_ms <= row.elapsed_ms
    assert row.credential_revision == observations.credential_revision(provider, KEY)
    assert row.credential_source == "tenant" and row.tenant_id == context["tenant_id"]
    assert row.credential_id == context["credential_id"]
    assert all(stream.closed for stream in streams)
    assert all(private not in row.to_json() for private in (KEY, TEXT, MESSAGES[0].content))


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("failure", ["empty", "eof", "truncated", "transport"])
async def test_partial_or_empty_stream_never_certifies_completed(
    monkeypatch, captured, provider, failure
):
    frames = [terminal(provider)] if failure == "empty" else [chunk(provider, TEXT)]
    if failure == "truncated":
        frames.append(chunk(provider, reason="MAX_TOKENS" if provider == "gemini" else "length"))
    if failure == "transport":
        frames.append(ConnectionError("private transport error"))
    p, seen, streams, _ = await setup_provider(monkeypatch, provider, [frames])
    try:
        if failure == "empty":
            assert await collect(p.stream_chat(MESSAGES)) == []
        else:
            with pytest.raises((LLMStreamStalled, RuntimeError, ConnectionError)):
                await collect(p.stream_chat(MESSAGES))
    finally:
        await p.cleanup()
    assert len(seen) == len(captured) == 1
    row = captured[0]
    assert row.outcome == ("incomplete" if failure == "empty" else "failed")
    assert row.output_seen == (failure != "empty")
    assert all(stream.closed for stream in streams)
    assert "private transport error" not in row.to_json()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("mode", ["close", "cancel"])
async def test_consumer_close_or_cancel_finishes_once_and_closes_stream(
    monkeypatch, captured, provider, mode
):
    p, _, streams, _ = await setup_provider(
        monkeypatch, provider, [[chunk(provider, TEXT), asyncio.Event()]]
    )
    stream = p.stream_chat(MESSAGES)
    try:
        assert await anext(stream) == TEXT
        if mode == "cancel":
            task = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await stream.aclose()
    finally:
        await p.cleanup()
    assert [row.outcome for row in captured] == ["cancelled"]
    assert captured[0].output_seen and all(s.closed for s in streams)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_pre_dispatch_rejection_is_not_a_provider_attempt(monkeypatch, captured, provider):
    p, seen, _, _ = await setup_provider(monkeypatch, provider, [])

    @asynccontextmanager
    async def refuse():
        raise RuntimeError("local admission refused")
        yield

    if provider == "gemini":
        from app.utils.resilience import CircuitOpenError

        @asynccontextmanager
        async def open_circuit():
            raise CircuitOpenError("local circuit open")
            yield

        p._circuit = open_circuit()
    else:
        p._guard = NS(acquire=refuse)
    try:
        with pytest.raises(Exception):
            await collect(p.stream_chat(MESSAGES))
    finally:
        await p.cleanup()
    assert not seen and not captured


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_auth_rejection_is_not_accepted_and_retry_is_separately_recorded(
    monkeypatch, captured, provider
):
    p, seen, _, _ = await setup_provider(monkeypatch, provider, [Refused(401)] * 3)
    if provider == "deepseek":
        monkeypatch.setattr(deepseek, "platform_key_rotation", lambda _: NS(bench=lambda *a: False))
    try:
        with pytest.raises(Exception):
            await collect(p.stream_chat(MESSAGES))
    finally:
        await p.cleanup()
    assert len(captured) == len(seen) == (1 if provider == "openai" else 3)
    assert all(
        r.outcome == "failed"
        and r.error_category == "authentication"
        and not r.protocol_accepted
        and not r.output_seen
        for r in captured
    )
    assert all("private provider failure body" not in r.to_json() for r in captured)


@pytest.mark.parametrize("provider", ["groq", "cerebras", "deepseek", "openai"])
async def test_tool_only_response_has_real_first_output_and_complete_terminal(
    monkeypatch, captured, provider
):
    p, _, _, _ = await setup_provider(
        monkeypatch, provider, [[chunk(provider, tool=True), chunk(provider, reason="tool_calls")]]
    )
    sink = []
    try:
        assert (
            await collect(
                p.stream_chat(
                    MESSAGES,
                    tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
                    tool_calls_sink=sink,
                )
            )
            == []
        )
    finally:
        await p.cleanup()
    assert sink[0]["arguments"] == {"query": "private tool argument"}
    assert [r.outcome for r in captured] == ["completed"]
    assert captured[0].first_output_ms is not None
    assert "private tool argument" not in captured[0].to_json()


async def test_deepseek_rotation_records_actual_header_key_without_duplicate_outer_event(
    monkeypatch, captured
):
    p, seen, _, context = await setup_provider(
        monkeypatch, "deepseek", [Refused(401), [chunk("deepseek", TEXT), terminal("deepseek")]]
    )
    benched = []
    monkeypatch.setattr(
        deepseek,
        "platform_key_rotation",
        lambda _: NS(
            bench=lambda key, seconds: benched.append(key) or True,
            retry_key=lambda **kw: "synthetic-rotated-key",
        ),
    )
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [TEXT]
    finally:
        await p.cleanup()
    assert [r.headers["Authorization"] for r in seen] == [
        f"Bearer {KEY}",
        "Bearer synthetic-rotated-key",
    ]
    assert benched == [KEY]
    assert [r.outcome for r in captured] == ["failed", "completed"]
    assert [r.credential_revision for r in captured] == [
        observations.credential_revision("deepseek", k) for k in (KEY, "synthetic-rotated-key")
    ]
    assert captured[0].credential_id == context["credential_id"]
    assert captured[1].credential_source == "unattributed" and captured[1].credential_id is None


async def test_gemini_tool_round_and_followup_have_distinct_actual_attempts(monkeypatch, captured):
    p, seen, streams, _ = await setup_provider(
        monkeypatch,
        "gemini",
        [
            [chunk("gemini", tool=True), terminal("gemini")],
            [chunk("gemini", TEXT), terminal("gemini")],
        ],
    )
    called = []

    async def runner(name, args):
        called.append((name, args))
        return "private tool result"

    try:
        result = await collect(
            p.stream_chat_with_tools(
                MESSAGES,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        },
                    }
                ],
                tool_runner=runner,
            )
        )
    finally:
        await p.cleanup()
    assert result == [TEXT] and called == [("lookup", {"query": "private tool argument"})]
    assert len(seen) == 2 and all(s.closed for s in streams)
    assert [r.outcome for r in captured] == ["completed", "completed"]
    assert all(r.first_output_ms is not None for r in captured)
    assert all("private tool" not in r.to_json() for r in captured)


@pytest.mark.parametrize("provider", ["groq", "gemini"])
@pytest.mark.parametrize("finish", ["complete", "truncated"])
async def test_direct_warmup_does_not_promote_truncation_to_completed(
    monkeypatch, captured, provider, finish
):
    p, _, _, _ = await setup_provider(monkeypatch, provider, [])

    async def warmup(**kwargs):
        if provider == "groq":
            return NS(
                choices=[
                    NS(
                        message=NS(content="hi"),
                        finish_reason="stop" if finish == "complete" else "length",
                    )
                ]
            )
        return deepseek._ns(chunk("gemini", "hi", "STOP" if finish == "complete" else "MAX_TOKENS"))

    if provider == "groq":
        p._client.chat.completions.create = warmup
    else:
        p._client.aio.models.generate_content = warmup
    try:
        await p.warm_up()
    finally:
        await p.cleanup()
    assert [r.outcome for r in captured] == ["completed" if finish == "complete" else "incomplete"]
    assert captured[0].protocol_accepted and captured[0].output_seen


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_observation_storage_failure_cannot_change_provider_result(monkeypatch, provider):
    p, _, _, _ = await setup_provider(
        monkeypatch, provider, [[chunk(provider, TEXT), terminal(provider)]]
    )

    def unavailable(item):
        raise ConnectionError("telemetry unavailable")

    monkeypatch.setattr(observations, "publish", unavailable)
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [TEXT]
    finally:
        await p.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_cancel_while_request_is_pending_is_not_provider_admission(
    monkeypatch, captured, provider
):
    p, seen, _, _ = await setup_provider(monkeypatch, provider, [asyncio.Event()])
    stream = p.stream_chat(MESSAGES)
    task = asyncio.create_task(anext(stream))
    try:

        async def dispatched():
            while not seen:
                await asyncio.sleep(0)

        await asyncio.wait_for(dispatched(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()
    finally:
        await p.cleanup()
    assert [r.outcome for r in captured] == ["cancelled"]
    assert not captured[0].protocol_accepted and not captured[0].output_seen


@pytest.mark.parametrize("provider", ["groq", "cerebras", "deepseek", "gemini"])
async def test_existing_pre_output_retry_has_one_failure_and_one_success(
    monkeypatch, captured, provider
):
    p, seen, _, _ = await setup_provider(
        monkeypatch,
        provider,
        [ConnectionError("temporary"), [chunk(provider, TEXT), terminal(provider)]],
    )
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [TEXT]
    finally:
        await p.cleanup()
    assert len(seen) == 2
    assert [r.outcome for r in captured] == ["failed", "completed"]
    assert captured[0].error_category == "transport"


async def test_groq_pool_reports_actual_selected_client_key(monkeypatch, captured):
    calls = []

    def sdk(api_key, **kwargs):
        async def create(**request):
            calls.append(api_key)
            if api_key == "synthetic-key-a":
                raise ConnectionError("first key unavailable")
            return Chunks([chunk("groq", TEXT), terminal("groq")])

        async def close():
            pass

        return NS(chat=NS(completions=NS(create=create)), close=close)

    monkeypatch.setenv("GROQ_API_KEYS", "synthetic-key-a,synthetic-key-b")
    monkeypatch.setattr(groq, "AsyncGroq", sdk)
    p = groq.GroqLLMProvider()
    await p.initialize({"observation_context": {"source": "platform"}})
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [TEXT]
    finally:
        await p.cleanup()
    assert calls == ["synthetic-key-a", "synthetic-key-b"]
    assert [r.outcome for r in captured] == ["failed", "completed"]
    assert [r.credential_revision for r in captured] == [
        observations.credential_revision("groq", key) for key in calls
    ]
    assert all(r.credential_source == "platform" for r in captured)


@pytest.mark.parametrize("failure", ["truncated", "cancel", "timeout", "refused"])
async def test_gemini_tool_stream_keeps_failure_or_cancel_and_never_runs_partial_tool(
    monkeypatch, captured, failure
):
    frames = [chunk("gemini", TEXT, tool=True)]
    if failure == "truncated":
        frames.append(chunk("gemini", reason="MAX_TOKENS"))
    elif failure in {"cancel", "timeout"}:
        frames.append(asyncio.Event())
    p, seen, streams, _ = await setup_provider(
        monkeypatch, "gemini", [Refused(429) if failure == "refused" else frames]
    )
    called = []

    async def runner(name, args):
        called.append(name)
        return "must not run"

    stream = p.stream_chat_with_tools(
        MESSAGES,
        tools=[
            {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        ],
        tool_runner=runner,
        timeout_seconds=0.05,
    )
    try:
        if failure == "cancel":
            assert await anext(stream) == TEXT
            await stream.aclose()
        else:
            with pytest.raises((RuntimeError, gemini.LLMTimeoutError, LLMStreamStalled)):
                await collect(stream)
    finally:
        await stream.aclose()
        await p.cleanup()
    assert not called and len(seen) == 1
    assert all(s.closed for s in streams)
    assert [r.outcome for r in captured] == ["cancelled" if failure == "cancel" else "failed"]
    if failure == "refused":
        assert captured[0].error_category == "rate_limit" and not captured[0].protocol_accepted


async def test_deepseek_refusal_body_failure_closes_http_and_records_transport_failure(
    monkeypatch, captured
):
    p, _, _, _ = await setup_provider(monkeypatch, "deepseek", [])
    monkeypatch.setattr(cerebras, "_LLM_MAX_RETRIES", 0)
    wire = WireChunks([ConnectionError("private body read failure")])
    # Replace the already-created transport, preserving the actual client/adapter.
    p._client._http._transport = httpx.MockTransport(
        lambda request: httpx.Response(401, stream=wire)
    )
    try:
        with pytest.raises(RuntimeError):
            await collect(p.stream_chat(MESSAGES))
    finally:
        await p.cleanup()
    assert wire.closed
    assert [r.outcome for r in captured] == ["failed"]
    assert captured[0].error_category == "transport" and not captured[0].protocol_accepted


@pytest.mark.parametrize("provider", ["groq", "cerebras", "deepseek", "openai"])
@pytest.mark.parametrize("invalid", ["malformed", "array", "nameless"])
@pytest.mark.parametrize("mixed", [False, True])
async def test_invalid_structured_output_cannot_be_masked_by_terminal_or_text(
    monkeypatch, captured, provider, invalid, mixed
):
    frame = chunk(provider, TEXT if mixed else None, tool=True)
    function = frame["choices"][0]["delta"]["tool_calls"][0]["function"]
    if invalid == "nameless":
        function.pop("name")
    else:
        function["arguments"] = "{unfinished" if invalid == "malformed" else "[]"
    p, seen, _, _ = await setup_provider(
        monkeypatch, provider, [[frame, chunk(provider, reason="tool_calls")]]
    )
    sink = []
    try:
        result = await collect(p.stream_chat(MESSAGES, tool_calls_sink=sink))
    finally:
        await p.cleanup()
    assert result == ([TEXT] if mixed else []) and len(seen) == 1
    # Runtime contract remains unchanged: invalid arguments stay marked invalid
    # for existing execution checks; missing-name slots remain omitted.
    assert (not sink) if invalid == "nameless" else (sink[0]["arguments_valid"] is False)
    assert [r.outcome for r in captured] == ["incomplete"]
    assert captured[0].protocol_accepted and captured[0].output_seen


@pytest.mark.parametrize("provider", ["groq", "cerebras", "deepseek", "openai"])
async def test_missing_tool_sink_does_not_bypass_structured_output_evidence(
    monkeypatch, captured, provider
):
    frame = chunk(provider, TEXT, tool=True)
    frame["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] = "{unfinished"
    p, _, _, _ = await setup_provider(monkeypatch, provider, [[frame, terminal(provider)]])
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [TEXT]
    finally:
        await p.cleanup()
    assert [r.outcome for r in captured] == ["incomplete"]


@pytest.mark.parametrize("mixed", [False, True])
async def test_gemini_nameless_native_function_does_not_certify_complete(
    monkeypatch, captured, mixed
):
    frame = chunk("gemini", TEXT if mixed else None, tool=True)
    frame["function_calls"][0].pop("name")
    p, _, _, _ = await setup_provider(monkeypatch, "gemini", [[frame, terminal("gemini")]])
    called = []

    async def runner(name, args):
        called.append(name)
        return "must not run"

    try:
        result = await collect(
            p.stream_chat_with_tools(
                MESSAGES,
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "lookup", "parameters": {"type": "object"}},
                    }
                ],
                tool_runner=runner,
            )
        )
    finally:
        await p.cleanup()
    assert result == ([TEXT] if mixed else []) and not called
    assert [r.outcome for r in captured] == ["incomplete"]


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_whitespace_only_terminal_preserves_text_without_certifying_output(
    monkeypatch, captured, provider
):
    whitespace = " \t\n "
    p, _, _, _ = await setup_provider(
        monkeypatch, provider, [[chunk(provider, whitespace), terminal(provider)]]
    )
    try:
        assert await collect(p.stream_chat(MESSAGES)) == [whitespace]
    finally:
        await p.cleanup()
    assert [r.outcome for r in captured] == ["incomplete"]
    assert captured[0].protocol_accepted and not captured[0].output_seen
    assert captured[0].first_output_ms is None


@pytest.mark.parametrize("provider", ["groq", "gemini", "openai"])
async def test_whitespace_only_warmup_does_not_certify_output(monkeypatch, captured, provider):
    p, _, _, _ = await setup_provider(
        monkeypatch, provider, [[chunk(provider, " \t\n "), terminal(provider)]]
    )

    async def warmup(**kwargs):
        if provider == "groq":
            return NS(choices=[NS(message=NS(content=" \t\n "), finish_reason="stop")])
        return deepseek._ns(chunk("gemini", " \t\n ", "STOP"))

    if provider == "groq":
        p._client.chat.completions.create = warmup
    elif provider == "gemini":
        p._client.aio.models.generate_content = warmup
    try:
        await p.warm_up()
    finally:
        await p.cleanup()
    assert [r.outcome for r in captured] == ["incomplete"]
    assert captured[0].protocol_accepted and not captured[0].output_seen
    assert captured[0].first_output_ms is None


async def test_gemini_whitespace_only_tool_decision_does_not_certify_output(monkeypatch, captured):
    p, _, _, _ = await setup_provider(
        monkeypatch, "gemini", [[chunk("gemini", " \t\n "), terminal("gemini")]]
    )

    async def runner(name, args):
        pytest.fail("A whitespace-only response contains no tool to execute")

    try:
        result = await collect(
            p.stream_chat_with_tools(
                MESSAGES,
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "lookup", "parameters": {"type": "object"}},
                    }
                ],
                tool_runner=runner,
            )
        )
    finally:
        await p.cleanup()
    assert result == [" \t\n "]
    assert [r.outcome for r in captured] == ["incomplete"]
    assert captured[0].protocol_accepted and not captured[0].output_seen
    assert captured[0].first_output_ms is None
