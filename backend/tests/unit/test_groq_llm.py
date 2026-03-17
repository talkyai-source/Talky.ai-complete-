from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.groq import GroqLLMProvider


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
    assert create.await_args.kwargs["max_completion_tokens"] == provider._max_tokens
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
