"""Unit tests for the Gemini LLM provider.

Mirrors the structure of test_groq_llm.py — mocks the SDK at the
`client.aio.models.generate_content_stream` boundary so no network is needed.

These tests do not require the real `google-genai` package; a module-level
fixture stubs `google.genai.types` with minimal data containers so tests run
in CI environments that haven't installed the optional dependency.
"""
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Stub `google.genai.types` before the provider module imports it at call time.
# The provider does `from google.genai import types as genai_types` *inside*
# stream_chat(), so as long as the stub is in sys.modules by the time the
# first stream_chat is awaited, everything resolves to our fakes.
# ---------------------------------------------------------------------------

@dataclass
class _FakePart:
    text: str


@dataclass
class _FakeContent:
    role: str
    parts: List[_FakePart] = field(default_factory=list)


@dataclass
class _FakeThinkingConfig:
    thinking_budget: Optional[int] = None


@dataclass
class _FakeGenerateContentConfig:
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    system_instruction: Optional[str] = None
    thinking_config: Optional[_FakeThinkingConfig] = None


_fake_types_module = SimpleNamespace(
    Content=_FakeContent,
    Part=_FakePart,
    GenerateContentConfig=_FakeGenerateContentConfig,
    ThinkingConfig=_FakeThinkingConfig,
)

# Install the stub package hierarchy. Must happen BEFORE
# `from app.infrastructure.llm.gemini import ...` so that any lazy imports the
# provider does at call time resolve to the stub.
_fake_genai_pkg = SimpleNamespace(
    Client=lambda **_: SimpleNamespace(),
    types=_fake_types_module,
)
sys.modules.setdefault("google", SimpleNamespace(genai=_fake_genai_pkg))
sys.modules.setdefault("google.genai", _fake_genai_pkg)
sys.modules.setdefault("google.genai.types", _fake_types_module)


from app.domain.models.conversation import Message, MessageRole  # noqa: E402
from app.infrastructure.llm.gemini import GeminiLLMProvider  # noqa: E402


def _fake_chunk(text):
    """Mimic a google-genai stream chunk; .text may be None for safety chunks."""
    return SimpleNamespace(text=text)


class _FakeStream:
    """Async iterable matching what generate_content_stream returns."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk
        return _gen()


def _wire_mock_client(provider, chunks):
    """Attach a fake genai client that returns the given chunk sequence."""
    create = AsyncMock(return_value=_FakeStream(chunks))
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=create)
        )
    )
    return create


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_chat_yields_text_in_order():
    provider = GeminiLLMProvider()
    provider._model = "gemini-2.5-flash"
    provider._temperature = 0.7
    provider._max_tokens = 50
    create = _wire_mock_client(
        provider,
        [_fake_chunk("Hello"), _fake_chunk(", "), _fake_chunk("world!")],
    )

    tokens = []
    async for token in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="hi")],
        system_prompt="Reply briefly.",
    ):
        tokens.append(token)

    assert "".join(tokens) == "Hello, world!"
    # Verify call shape
    assert create.await_args.kwargs["model"] == "gemini-2.5-flash"
    contents = create.await_args.kwargs["contents"]
    assert len(contents) == 1
    assert contents[0].role == "user"
    cfg = create.await_args.kwargs["config"]
    assert cfg.temperature == 0.7
    assert cfg.max_output_tokens == 50
    assert cfg.system_instruction == "Reply briefly."


@pytest.mark.asyncio
async def test_stream_chat_filters_none_text_chunks():
    """Safety chunks / metadata chunks return text=None — must not yield empty strings."""
    provider = GeminiLLMProvider()
    _wire_mock_client(
        provider,
        [_fake_chunk("Hi"), _fake_chunk(None), _fake_chunk(""), _fake_chunk("!")],
    )

    tokens = []
    async for token in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="ping")],
    ):
        tokens.append(token)

    assert tokens == ["Hi", "!"]


@pytest.mark.asyncio
async def test_assistant_messages_become_model_role():
    """Gemini uses 'model' for assistant turns, not 'assistant'."""
    provider = GeminiLLMProvider()
    create = _wire_mock_client(provider, [_fake_chunk("ok")])

    history = [
        Message(role=MessageRole.USER, content="What's 2+2?"),
        Message(role=MessageRole.ASSISTANT, content="4"),
        Message(role=MessageRole.USER, content="And 3+3?"),
    ]
    async for _ in provider.stream_chat(messages=history):
        pass

    contents = create.await_args.kwargs["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]


@pytest.mark.asyncio
async def test_empty_history_gets_placeholder_user_turn():
    """Gemini rejects empty contents — provider injects a single-space user turn."""
    provider = GeminiLLMProvider()
    create = _wire_mock_client(provider, [_fake_chunk("hello")])

    async for _ in provider.stream_chat(messages=[], system_prompt="Greet the user."):
        pass

    contents = create.await_args.kwargs["contents"]
    assert len(contents) == 1
    assert contents[0].role == "user"


@pytest.mark.asyncio
async def test_empty_messages_are_skipped():
    """Empty/whitespace messages are dropped before being sent."""
    provider = GeminiLLMProvider()
    create = _wire_mock_client(provider, [_fake_chunk("ok")])

    history = [
        Message(role=MessageRole.USER, content=""),
        Message(role=MessageRole.USER, content="   "),
        Message(role=MessageRole.USER, content="real question"),
    ]
    async for _ in provider.stream_chat(messages=history):
        pass

    contents = create.await_args.kwargs["contents"]
    assert len(contents) == 1
    assert contents[0].parts[0].text == "real question"


@pytest.mark.asyncio
async def test_stream_chat_raises_when_not_initialized():
    provider = GeminiLLMProvider()
    # Don't wire a client.
    with pytest.raises(RuntimeError, match="not initialized"):
        async for _ in provider.stream_chat(
            messages=[Message(role=MessageRole.USER, content="hi")],
        ):
            pass


@pytest.mark.asyncio
async def test_temperature_validation():
    provider = GeminiLLMProvider()
    _wire_mock_client(provider, [_fake_chunk("x")])

    with pytest.raises(ValueError, match="Temperature"):
        async for _ in provider.stream_chat(
            messages=[Message(role=MessageRole.USER, content="hi")],
            temperature=2.5,
        ):
            pass


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_provider_identity():
    provider = GeminiLLMProvider()
    assert provider.name == "gemini"
    assert provider.supports_streaming is True


# ---------------------------------------------------------------------------
# Initialize: temperature out-of-range from config rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_rejects_invalid_temperature(monkeypatch):
    """Passing temperature outside [0,2] in config should raise immediately."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    provider = GeminiLLMProvider()

    with pytest.raises(ValueError, match="Temperature"):
        await provider.initialize({"temperature": 3.0})


@pytest.mark.asyncio
async def test_initialize_rejects_missing_api_key(monkeypatch):
    """No api_key + no GEMINI_API_KEY env should raise with a clear message."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    provider = GeminiLLMProvider()

    with pytest.raises(ValueError, match="Gemini API key"):
        await provider.initialize({})


# ---------------------------------------------------------------------------
# Thinking budget (Gemini 2.5 family)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thinking_budget_disabled_attaches_thinking_config():
    """thinking_budget=0 on init → ThinkingConfig(thinking_budget=0) on every call."""
    provider = GeminiLLMProvider()
    provider._thinking_budget = 0
    create = _wire_mock_client(provider, [_fake_chunk("hi")])

    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="hello")],
    ):
        pass

    cfg = create.await_args.kwargs["config"]
    assert cfg.thinking_config is not None
    assert cfg.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_thinking_budget_unset_omits_thinking_config():
    """Default (None) path must not send a ThinkingConfig — let Gemini decide."""
    provider = GeminiLLMProvider()
    # _thinking_budget stays None (default)
    create = _wire_mock_client(provider, [_fake_chunk("hi")])

    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="hello")],
    ):
        pass

    cfg = create.await_args.kwargs["config"]
    assert cfg.thinking_config is None


@pytest.mark.asyncio
async def test_thinking_budget_can_be_overridden_per_call():
    """Per-call kwargs should beat the provider-level default."""
    provider = GeminiLLMProvider()
    provider._thinking_budget = 0  # default off
    create = _wire_mock_client(provider, [_fake_chunk("hi")])

    async for _ in provider.stream_chat(
        messages=[Message(role=MessageRole.USER, content="hello")],
        thinking_budget=512,
    ):
        pass

    cfg = create.await_args.kwargs["config"]
    assert cfg.thinking_config.thinking_budget == 512
