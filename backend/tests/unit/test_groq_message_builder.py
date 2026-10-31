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

def test_instructions_ride_on_the_latest_user_message():
    """Changed 2026-09-02. The block used to be stitched onto the FIRST user
    message — the oldest turn. The per-turn parts of the system prompt (LIVE
    STATE, CAPTURED, "ACTION THIS TURN: read the email back") therefore sat
    next to "hello" while the caller's current words were at the far end, and
    because that first message changed every turn the cacheable prefix was
    destroyed. Groq's guidance is "instructions in the user message"; the user
    message that matters is the current one."""
    messages = [
        {"role": "user", "content": "Hello there."},
        {"role": "assistant", "content": "Hi! How can I help?"},
        {"role": "user", "content": "What are your plans?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    assert result[0] == messages[0]  # oldest turn untouched
    assert result[1] == messages[1]
    assert result[-1]["role"] == "user"
    assert SYSTEM_PROMPT in result[-1]["content"]
    assert "What are your plans?" in result[-1]["content"]
    assert SYSTEM_PROMPT not in result[0]["content"]


def test_prefix_is_stable_across_turns_for_caching():
    """Everything before the current user message must be byte-identical
    between turn N and turn N+1, or the provider can never cache the prefix."""
    turn_n = [
        {"role": "user", "content": "Hello there."},
        {"role": "assistant", "content": "Hi! How can I help?"},
        {"role": "user", "content": "What are your plans?"},
    ]
    turn_n1 = turn_n + [
        {"role": "assistant", "content": "Two plans — starter and pro."},
        {"role": "user", "content": "Price of pro?"},
    ]
    r_n = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT, messages=turn_n,
    )
    r_n1 = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT + " LIVE STATE: turn 2", messages=turn_n1,
    )
    # The first three raw messages of turn N+1 equal the first two of turn N
    # plus the ORIGINAL third message (instructions have moved on).
    assert r_n1[:2] == r_n[:2]
    assert r_n1[2] == turn_n[2]


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


def test_leading_assistant_gets_a_neutral_user_lead_in_and_instructions_last():
    """GPT-OSS wants a user message first. When history opens with the agent's
    greeting, a short neutral user lead-in is prepended; the instructions still
    ride on the LATEST user message, not on that lead-in."""
    messages = [
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What plans do you offer?"},
    ]
    result = GroqLLMProvider._inject_instructions_for_reasoning_model(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
    )
    assert [m["role"] for m in result] == ["user", "assistant", "user"]
    assert SYSTEM_PROMPT not in result[0]["content"]
    assert result[1] == messages[0]
    assert SYSTEM_PROMPT in result[-1]["content"]
    assert "What plans do you offer?" in result[-1]["content"]
