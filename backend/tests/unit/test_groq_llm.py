from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import asyncio
import logging

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.groq import (
    GroqLLMProvider,
    LLMTimeoutError,
    _THINKING_RESERVE_TOKENS,
    _emit_usage_log,
    _extract_stream_usage,
)


def _fake_chunk(token: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=token))]
    )


class _FakeStream:
    def __init__(self, tokens):
        self._tokens = tokens

    def __aiter__(self):
        async def _gen():
            for token in self._tokens:
                yield _fake_chunk(token)

        return _gen()


@pytest.mark.asyncio
async def test_reasoning_models_hide_reasoning_by_default():
    provider = GroqLLMProvider()
    create = AsyncMock(return_value=_FakeStream(["Hello"]))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )

    tokens = []
    async for token in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="Tell me about your plans")],
        system_prompt="Use plain spoken text only.",
        model="openai/gpt-oss-120b",
    ):
        tokens.append(token)

    assert "".join(tokens) == "Hello"
    assert create.await_args.kwargs["include_reasoning"] is False
    # GPT-OSS reasoning can't be turned off (floors at "low"); its reasoning
    # tokens share the completion ceiling, so we reserve answer headroom on top.
    assert create.await_args.kwargs["reasoning_effort"] == "low"
    assert (
        create.await_args.kwargs["max_completion_tokens"]
        == provider._max_tokens + _THINKING_RESERVE_TOKENS
    )
    sent_messages = create.await_args.kwargs["messages"]
    assert sent_messages[0]["role"] == "user"
    assert "Conversation instructions:" in sent_messages[0]["content"]
    assert "Use plain spoken text only." in sent_messages[0]["content"]
    assert "Current user message:" in sent_messages[0]["content"]
    assert all(message["role"] != "system" for message in sent_messages)


@pytest.mark.asyncio
async def test_non_reasoning_models_do_not_force_reasoning_format():
    provider = GroqLLMProvider()
    create = AsyncMock(return_value=_FakeStream(["Hi"]))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )

    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="Hello")],
        system_prompt="Use plain spoken text only.",
        model="llama-3.3-70b-versatile",
    ):
        pass

    assert "reasoning_format" not in create.await_args.kwargs
    assert "include_reasoning" not in create.await_args.kwargs
    assert create.await_args.kwargs["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_qwen3_defaults_to_hidden_non_thinking_mode_for_voice_dialogue():
    provider = GroqLLMProvider()
    create = AsyncMock(return_value=_FakeStream(["Hi"]))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )

    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="Tell me about the product")],
        system_prompt="Use plain spoken text only.",
        model="qwen/qwen3-32b",
    ):
        pass

    assert create.await_args.kwargs["reasoning_effort"] == "none"
    assert create.await_args.kwargs["reasoning_format"] == "hidden"
    assert create.await_args.kwargs["top_p"] == 0.8
    # Thinking fully off → no reserve; max_completion_tokens is purely the answer.
    assert create.await_args.kwargs["max_completion_tokens"] == provider._max_tokens


@pytest.mark.asyncio
async def test_qwen3_6_27b_disables_thinking_with_no_reserve():
    """Qwen 3.6 27B (dot, not dash) is matched by the Qwen3 family rule and runs
    with reasoning_effort="none" → thinking off, so no answer-token reserve."""
    provider = GroqLLMProvider()
    create = AsyncMock(return_value=_FakeStream(["Hi"]))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="Tell me about the product")],
        system_prompt="Use plain spoken text only.",
        model="qwen/qwen3.6-27b",
    ):
        pass

    assert create.await_args.kwargs["reasoning_effort"] == "none"
    assert create.await_args.kwargs["reasoning_format"] == "hidden"
    assert create.await_args.kwargs["max_completion_tokens"] == provider._max_tokens


# --------------------------------------------------------------------------- #
# stream_chat_with_timeout — budget must measure Groq-wait time, NOT the time
# the consumer holds the generator suspended (TTS/playback) between token pulls.
# --------------------------------------------------------------------------- #

def _groq_stub(provider, tokens, *, before_token_delays):
    """Replace provider.stream_chat with a generator that sleeps
    `before_token_delays[i]` seconds *inside its own await* before yielding
    tokens[i]. That sleep is the time the CALLER (stream_chat_with_timeout)
    spends awaiting Groq for that token."""
    async def _fake_stream_chat(messages, **kwargs):
        for i, tok in enumerate(tokens):
            await asyncio.sleep(before_token_delays[i])
            yield tok
    provider.stream_chat = _fake_stream_chat


@pytest.mark.asyncio
async def test_slow_consumer_playback_does_not_truncate_stream():
    """Groq answers fast; the consumer (us) spends a LONG time between token
    pulls simulating real-time-paced TTS/playback. Total wall-clock far exceeds
    timeout_seconds, but Groq-wait is ~0 → the reply must NOT be truncated."""
    provider = GroqLLMProvider()
    toks = ["Hello", " there", " friend", ", welcome!"]
    # Groq itself is instant for every token.
    _groq_stub(provider, toks, before_token_delays=[0.0] * len(toks))

    received = []
    # Budget is small (0.4s); consumer playback between tokens (0.25s each) makes
    # total wall-clock ~1s >> 0.4s. Old wall-clock code would have truncated.
    async for token in provider.stream_chat_with_timeout(
        messages=[Message(role=MessageRole.USER, content="hi")],
        timeout_seconds=0.4,
    ):
        received.append(token)
        await asyncio.sleep(0.25)  # downstream playback while generator suspended

    assert received == toks, "healthy reply truncated by consumer-side playback time"


@pytest.mark.asyncio
async def test_genuine_groq_midstream_stall_is_caught():
    """Groq sends two tokens fast, then goes silent (no token for >2s) while the
    consumer is ready. The inter-token stall guard must break cleanly, keeping
    the tokens already delivered — not hang, not raise."""
    provider = GroqLLMProvider()
    # Third token would arrive only after 5s of Groq silence.
    _groq_stub(provider, ["Hi", " there", " END"], before_token_delays=[0.0, 0.0, 5.0])

    received = []
    async for token in provider.stream_chat_with_timeout(
        messages=[Message(role=MessageRole.USER, content="hi")],
        timeout_seconds=10.0,  # generous total budget; the 2s intertoken guard fires first
    ):
        received.append(token)

    assert received == ["Hi", " there"], "stall guard should keep delivered tokens only"


@pytest.mark.asyncio
async def test_slow_first_token_still_raises_timeout():
    """TTFT guard: if Groq is slow to the FIRST token beyond the budget, we still
    raise LLMTimeoutError (nothing was delivered yet)."""
    provider = GroqLLMProvider()
    _groq_stub(provider, ["late"], before_token_delays=[1.0])

    with pytest.raises(LLMTimeoutError):
        async for _ in provider.stream_chat_with_timeout(
            messages=[Message(role=MessageRole.USER, content="hi")],
            timeout_seconds=0.3,
        ):
            pass


# --------------------------------------------------------------------------- #
# Usage telemetry — Groq 0.37.x normal streaming usage arrives on
# chunk.x_groq.usage, NOT chunk.usage (that field is only populated when
# stream_options.include_usage is set, which Groq's SDK rejects). Extraction
# must prefer chunk.usage if a future SDK ever populates both, fall back to
# x_groq.usage, and degrade to None (never raise) when neither is present.
# --------------------------------------------------------------------------- #

class _RawChunkStream:
    """Like _FakeStream but yields pre-built chunk objects verbatim, so tests
    can control usage/x_groq shape on the final chunk."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


def _usage_chunk(
    *,
    prompt_tokens=42,
    completion_tokens=7,
    cached_tokens=10,
    queue_time=0.01,
    prompt_time=0.02,
    completion_time=0.05,
    total_time=0.08,
    req_id="req_123",
):
    """A Groq-shaped final streaming chunk: choices=[] (no content), usage=None
    (stream_options.include_usage was never set), x_groq.usage carrying the
    real numbers — the actual 0.37.x streaming shape."""
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        queue_time=queue_time,
        prompt_time=prompt_time,
        completion_time=completion_time,
        total_time=total_time,
        id=req_id,
    )
    return SimpleNamespace(choices=[], usage=None, x_groq=SimpleNamespace(usage=usage))


def test_extract_stream_usage_prefers_usage_field_when_present():
    usage_direct = SimpleNamespace(prompt_tokens=1)
    chunk = SimpleNamespace(
        usage=usage_direct,
        x_groq=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2)),
    )
    assert _extract_stream_usage(chunk) is usage_direct


def test_extract_stream_usage_falls_back_to_x_groq_usage():
    """This is the actual Groq 0.37.x streaming shape: chunk.usage is None,
    the real numbers are under chunk.x_groq.usage."""
    usage_xgroq = SimpleNamespace(prompt_tokens=2)
    chunk = SimpleNamespace(usage=None, x_groq=SimpleNamespace(usage=usage_xgroq))
    assert _extract_stream_usage(chunk) is usage_xgroq


def test_extract_stream_usage_returns_none_without_raising_when_absent():
    chunk = SimpleNamespace(choices=[])  # no usage, no x_groq at all
    assert _extract_stream_usage(chunk) is None


def test_emit_usage_log_never_raises_on_missing_fields(caplog):
    """Fail-soft guard: a bare/partial usage object (or None) must never raise
    — every field read degrades to a default (-1 for timings, 0 for counts)."""
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.groq")
    _emit_usage_log(None, model="llama-3.3-70b-versatile")  # must not raise
    _emit_usage_log(SimpleNamespace(), model="llama-3.3-70b-versatile")  # must not raise
    lines = [r.message for r in caplog.records if r.message.startswith("llm_usage")]
    assert len(lines) == 2
    assert "queue_time=-1.000" in lines[0]


@pytest.mark.asyncio
async def test_stream_chat_logs_x_groq_usage_with_server_timing_fields(caplog):
    """End-to-end: stream_chat must extract usage from x_groq.usage (the real
    0.37.x shape) and log Groq's server timing fields + client TTFT."""
    provider = GroqLLMProvider()
    chunks = [_fake_chunk("Hello"), _usage_chunk()]
    create = AsyncMock(return_value=_RawChunkStream(chunks))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.groq")
    tokens = []
    async for tok in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="hi")],
        model="llama-3.3-70b-versatile",
    ):
        tokens.append(tok)

    assert "".join(tokens) == "Hello"
    usage_lines = [r.message for r in caplog.records if r.message.startswith("llm_usage")]
    assert usage_lines, "expected an llm_usage log line"
    line = usage_lines[-1]
    assert "partial=False" in line
    assert "queue_time=0.010" in line
    assert "prompt_time=0.020" in line
    assert "completion_time=0.050" in line
    assert "total_time=0.080" in line
    assert "req_id=req_123" in line
    assert "prompt_tokens=42" in line
    assert "completion_tokens=7" in line
    assert "client_ttft_ms=" in line
    assert "client_net_remainder_ms=" in line


@pytest.mark.asyncio
async def test_stream_chat_logs_partial_usage_on_early_close(caplog):
    """Barge-in: the consumer stops pulling tokens and closes the generator
    before a final usage chunk ever arrives. stream_chat's finally block must
    still emit a partial=True usage line with whatever was captured, rather
    than staying silent (and must not raise / break cleanup)."""
    provider = GroqLLMProvider()
    chunks = [_fake_chunk("Hello"), _fake_chunk(" there"), _fake_chunk(" friend!")]
    create = AsyncMock(return_value=_RawChunkStream(chunks))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.groq")
    agen = provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="hi")],
        model="llama-3.3-70b-versatile",
    )
    first = await agen.__anext__()
    assert first == "Hello"
    await agen.aclose()  # simulates barge-in cutting the stream early

    usage_lines = [r.message for r in caplog.records if r.message.startswith("llm_usage")]
    assert usage_lines, "expected a partial llm_usage log line on early close"
    assert "partial=True" in usage_lines[-1]


# ── PII-in-debug-logs fix: message CONTENT is masked by default ─────────────

_PII_SYSTEM_PROMPT = (
    "You work for Acme Roofing. Caller SSN on file: 078-05-1120. "
    "Their address is 42 Wallaby Way, Sydney and their policy number is "
    "AC-99182-XJ. Never repeat this number back verbatim."
)
_PII_USER_MESSAGE = "Yeah my email is jane.doe.private@example.com, call me back"


@pytest.mark.asyncio
async def test_debug_log_does_not_leak_message_content_by_default(caplog, monkeypatch):
    """Groq's per-message debug log must NOT include caller PII / tenant
    system-prompt text by default — only structural info (role, length)."""
    import app.infrastructure.llm.groq as groq_module

    monkeypatch.setattr(groq_module, "_LOG_MESSAGE_CONTENT", False)

    provider = GroqLLMProvider()
    create = AsyncMock(return_value=_FakeStream(["ok"]))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    caplog.set_level(logging.DEBUG, logger="app.infrastructure.llm.groq")
    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content=_PII_USER_MESSAGE)],
        system_prompt=_PII_SYSTEM_PROMPT,
        model="llama-3.3-70b-versatile",
    ):
        pass

    message_lines = [r.message for r in caplog.records if r.message.startswith("Message ")]
    assert message_lines, "expected per-message debug log lines"
    full_log_text = "\n".join(r.message for r in caplog.records)

    # Content must be absent everywhere in the debug output...
    assert "jane.doe.private@example.com" not in full_log_text
    assert "078-05-1120" not in full_log_text
    assert "AC-99182-XJ" not in full_log_text
    assert "Acme Roofing" not in full_log_text
    # ...but structural info (role + length) must still be there.
    for line in message_lines:
        assert "role=" in line
        assert "content_len=" in line
        assert "content='" not in line  # old truncated-preview format is gone


@pytest.mark.asyncio
async def test_debug_log_includes_content_when_explicitly_opted_in(caplog, monkeypatch):
    """LOG_MESSAGE_CONTENT opt-in still gives the old truncated-content
    preview for local debugging — proves the gate actually gates both ways."""
    import app.infrastructure.llm.groq as groq_module

    monkeypatch.setattr(groq_module, "_LOG_MESSAGE_CONTENT", True)

    provider = GroqLLMProvider()
    create = AsyncMock(return_value=_FakeStream(["ok"]))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    caplog.set_level(logging.DEBUG, logger="app.infrastructure.llm.groq")
    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content=_PII_USER_MESSAGE)],
        system_prompt=_PII_SYSTEM_PROMPT,
        model="llama-3.3-70b-versatile",
    ):
        pass

    full_log_text = "\n".join(r.message for r in caplog.records)
    assert "jane.doe.private@example.com" in full_log_text
