"""
Unit tests for GroqLLMProvider message building.

Focuses on _inject_instructions_for_reasoning_model, which must ensure
GPT-OSS models always receive a user message as the FIRST message in the
array — even when conversation history begins with an assistant greeting.
"""
import pytest
from app.infrastructure.llm.groq import GroqLLMProvider


SYSTEM_PROMPT = "You are a helpful sales assistant. Never reveal you are an AI."


# ── Bug 2 regression: leading assistant message ───────────────────────────────

def test_leading_assistant_message_gets_user_first():
    """
    When history starts with assistant greeting, the injected result must
    start with a user message, not the assistant message.
    """
    messages = [
        {"role": "assistant", "content": "Hi there! How can I help you today?"},
        {"role": "user", "content": "What are your pricing plans?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    assert result[0]["role"] == "user", (
        f"First message must be 'user' for GPT-OSS, got '{result[0]['role']}'"
    )


def test_leading_assistant_message_instructions_present():
    """Instructions must appear somewhere in the result."""
    messages = [
        {"role": "assistant", "content": "Hi there! How can I help you today?"},
        {"role": "user", "content": "Tell me about your plans."},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    all_content = " ".join(m.get("content", "") for m in result)
    assert SYSTEM_PROMPT in all_content


def test_leading_assistant_original_greeting_preserved():
    """The original assistant greeting must still be in the result (not lost)."""
    greeting = "Hi there! How can I help you today?"
    messages = [
        {"role": "assistant", "content": greeting},
        {"role": "user", "content": "What plans do you offer?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    all_content = " ".join(m.get("content", "") for m in result)
    assert greeting in all_content


# ── Normal case: history already starts with user ────────────────────────────

def test_normal_case_instructions_injected_into_first_user():
    """When history starts with user, instructions go into that first user message."""
    messages = [
        {"role": "user", "content": "Hello there."},
        {"role": "assistant", "content": "Hi! How can I help?"},
        {"role": "user", "content": "What are your plans?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    assert result[0]["role"] == "user"
    assert SYSTEM_PROMPT in result[0]["content"]
    assert "Hello there." in result[0]["content"]


def test_normal_case_message_count_unchanged():
    """Normal case: no messages added or removed, just content changed."""
    messages = [
        {"role": "user", "content": "Hello."},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "Plans?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    assert len(result) == len(messages)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_messages_gets_standalone_user_instruction():
    """Empty message list: a standalone user instruction message is prepended."""
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=[],
    )
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert SYSTEM_PROMPT in result[0]["content"]


def test_no_system_prompt_returns_messages_unchanged():
    """No system prompt: messages returned unchanged."""
    messages = [
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "Hello."},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=None,
        messages=messages,
    )
    assert result == messages


def test_result_first_two_roles_when_leading_assistant():
    """
    After injection with leading assistant, the first two messages must be
    user then assistant (the prepended instruction + the original greeting).
    """
    messages = [
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What plans do you offer?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
