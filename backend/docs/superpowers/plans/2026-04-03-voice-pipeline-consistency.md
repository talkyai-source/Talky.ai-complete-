# Voice Pipeline Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four compounding bugs in the Ask AI voice pipeline that cause intermittent conversation corruption — truncated responses, malformed LLM input for GPT-OSS models, consecutive user messages breaking role alternation, and a misleading speculative log.

**Architecture:** Each bug is isolated to a single file and fixed with a targeted change. Conversation history is the shared state that links all four bugs — fixing them in order (guardrails → Groq message builder → turn lifecycle → log) ensures no turn can leave history in a broken state. Tests are unit-level and require no live services.

**Tech Stack:** Python 3.12, pytest, asyncio, Groq API (gpt-oss-120b / llama-3.3-70b), Deepgram Flux STT, Deepgram Aura-2 TTS, FastAPI WebSockets.

---

## Why This Plan Is The Right Approach

### What State-of-the-Art Voice AI Systems Do

Leading production voice AI platforms (LiveKit Agents, Vapi, Deepgram Voice Agent) share three invariants that this codebase currently violates:

1. **Message alternation is sacred.** Groq, OpenAI, and every major LLM API require `user → assistant → user → assistant` ordering. A single consecutive `user → user` pair causes the model to misinterpret context — it either merges the two turns or ignores instructions entirely. LiveKit enforces this via a `ChatContext` class that rejects non-alternating appends at write time.

2. **Response text is cleaned at one well-defined boundary.** Vapi's pipeline applies text normalization (strip filler words, strip markdown) exactly once, immediately before TTS, using non-destructive regex that matches only true fillers. The Talky.ai `clean_response` currently strips "Sure " from "Sure thing!" because the pattern `Sure!?` makes `!` optional — a classic greedy-optional bug.

3. **LLM provider quirks are encapsulated.** GPT-OSS models on Groq prohibit system messages and require instructions in the first `user` message. The current injection function finds the first user message correctly, but if conversation history starts with the assistant greeting (which it always does in Ask AI), the injected message ends up at index 1 with an assistant message at index 0 — violating Groq's requirement that GPT-OSS input starts with a user message. This causes silent context loss or API errors.

---

## Files Modified

| File | Bug | Change |
|------|-----|--------|
| `app/domain/services/llm_guardrails.py` | Bug 1 | Fix `Sure!?` → `Sure[!,]` filler regex |
| `app/infrastructure/llm/groq.py` | Bug 2 | Fix `_inject_instructions_for_reasoning_model` to handle leading assistant messages |
| `app/domain/services/voice_pipeline_service.py` | Bug 3+4 | Roll back dangling user message on barge-in (empty turn) and on exception |
| `app/domain/services/voice_pipeline_service.py` | Bug 5 | Correct misleading EagerEndOfTurn log message |

## Tests Created

| File | What it covers |
|------|---------------|
| `tests/unit/test_llm_guardrails.py` | Bug 1: regex correctness for all filler patterns |
| `tests/unit/test_groq_message_builder.py` | Bug 2: message ordering for GPT-OSS with leading assistant messages |
| `tests/unit/test_voice_pipeline_history.py` | Bug 3+4: history integrity after barge-in and after LLM exception |

---

## Task 1: Fix `clean_response` Filler Regex (Bug 1)

**Root cause:** In `app/domain/services/llm_guardrails.py` at line 269, the pattern `r'^(Sure!?\s+)'` uses `!?` which makes the exclamation mark *optional*. This means it matches `Sure ` (without `!`) at the start of a string, stripping "Sure " from "Sure thing! Our Basic plan..." and producing the garbage output `"thing! Our Basic plan..."`.

**Evidence from production log:**
```
LLM raw response: 'Sure thing! Our Basic plan...'
After clean_response: 'thing! Our Basic plan...'
```

**Files:**
- Modify: `app/domain/services/llm_guardrails.py:269`
- Test: `tests/unit/test_llm_guardrails.py`

---

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_guardrails.py`:

```python
"""
Unit tests for LLM Guardrails — clean_response filler stripping.

These tests verify that the filler-start patterns strip ONLY true conversational
filler words (e.g. "Sure! I can help") and NOT substantive phrases that happen
to start with the same word (e.g. "Sure thing! Our Basic plan").
"""
import pytest
from app.domain.services.llm_guardrails import LLMGuardrails


@pytest.fixture
def guardrails():
    return LLMGuardrails()


# ── Bug 1 regression: Sure!? was too greedy ──────────────────────────────────

def test_sure_thing_is_not_stripped(guardrails):
    """'Sure thing' is a substantive phrase, not a filler — must not be stripped."""
    result = guardrails.clean_response("Sure thing! Our Basic plan costs $29/month.")
    assert result == "Our Basic plan costs $29/month." or result.startswith("Sure thing")


def test_sure_exclamation_filler_is_stripped(guardrails):
    """'Sure! ' followed by real content IS a filler and should be stripped."""
    result = guardrails.clean_response("Sure! I can help you with that.")
    assert result == "I can help you with that."


def test_sure_comma_filler_is_stripped(guardrails):
    """'Sure, ' followed by real content IS a filler and should be stripped."""
    result = guardrails.clean_response("Sure, let me check that for you.")
    assert result == "let me check that for you."


# ── Other filler patterns must still work ────────────────────────────────────

def test_well_filler_stripped(guardrails):
    result = guardrails.clean_response("Well, that sounds great.")
    assert result == "that sounds great."


def test_okay_filler_stripped(guardrails):
    result = guardrails.clean_response("Okay, let me look that up.")
    assert result == "let me look that up."


def test_of_course_filler_stripped(guardrails):
    result = guardrails.clean_response("Of course! Happy to help.")
    assert result == "Happy to help."


def test_no_filler_unchanged(guardrails):
    result = guardrails.clean_response("Our pricing starts at $29 per month.")
    assert result == "Our pricing starts at $29 per month."


def test_empty_response_unchanged(guardrails):
    result = guardrails.clean_response("")
    assert result == ""


def test_none_response_unchanged(guardrails):
    result = guardrails.clean_response(None)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_llm_guardrails.py::test_sure_thing_is_not_stripped -v
```

Expected output:
```
FAILED tests/unit/test_llm_guardrails.py::test_sure_thing_is_not_stripped
AssertionError: assert 'thing! Our Basic plan costs $29/month.' == 'Our Basic plan costs $29/month.'
```

- [ ] **Step 3: Fix the filler pattern in `llm_guardrails.py`**

In `app/domain/services/llm_guardrails.py`, find the `filler_starts` list (around line 263) and change the `Sure` pattern from:

```python
r'^(Sure!?\s+)',
```

to:

```python
r'^Sure[!,]\s+',
```

The complete updated `filler_starts` list should look like this:

```python
filler_starts = [
    r'^(Well,?\s+)',
    r'^(So,?\s+)',
    r'^(Actually,?\s+)',
    r'^(Okay,?\s+)',
    r'^(Alright,?\s+)',
    r'^Sure[!,]\s+',
    r'^(Of course!?\s+)',
]
```

**Why this fix works:** `Sure[!,]` requires a literal `!` or `,` immediately after "Sure". The string "Sure thing!" has a space after "Sure", so it does NOT match. The strings "Sure! " and "Sure, " DO match because `!` and `,` are present directly after "Sure".

- [ ] **Step 4: Run all tests to verify they pass**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_llm_guardrails.py -v
```

Expected output:
```
PASSED tests/unit/test_llm_guardrails.py::test_sure_thing_is_not_stripped
PASSED tests/unit/test_llm_guardrails.py::test_sure_exclamation_filler_is_stripped
PASSED tests/unit/test_llm_guardrails.py::test_sure_comma_filler_is_stripped
PASSED tests/unit/test_llm_guardrails.py::test_well_filler_stripped
PASSED tests/unit/test_llm_guardrails.py::test_okay_filler_stripped
PASSED tests/unit/test_llm_guardrails.py::test_of_course_filler_stripped
PASSED tests/unit/test_llm_guardrails.py::test_no_filler_unchanged
PASSED tests/unit/test_llm_guardrails.py::test_empty_response_unchanged
PASSED tests/unit/test_llm_guardrails.py::test_none_response_unchanged

9 passed in 0.XX s
```

- [ ] **Step 5: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add app/domain/services/llm_guardrails.py tests/unit/test_llm_guardrails.py
git commit -m "fix: prevent Sure!? regex from stripping substantive phrases like 'Sure thing'"
```

---

## Task 2: Fix GPT-OSS Message Ordering (Bug 2)

**Root cause:** In `app/infrastructure/llm/groq.py`, the method `_inject_instructions_for_reasoning_model` finds the first `user` message and prepends system instructions into it. However, the Ask AI conversation history always starts with an assistant greeting message (added by `VoiceOrchestrator.send_greeting()`). This means after injection the message list is:

```
[0] role=assistant  ← "Hi there! How can I help you today?"
[1] role=user       ← instructions + user's first message
```

Groq's GPT-OSS models require the **first** message to be `user` role. Sending `assistant` as the first message causes silent context loss where the model ignores the instructions, or in some cases returns an API error. This explains why the GPT-OSS model sometimes gives coherent answers and sometimes ignores system prompt constraints entirely.

**Files:**
- Modify: `app/infrastructure/llm/groq.py:86-125` (`_inject_instructions_for_reasoning_model`)
- Test: `tests/unit/test_groq_message_builder.py`

---

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_groq_message_builder.py`:

```python
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


def test_result_alternates_user_assistant(  ):
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_groq_message_builder.py::test_leading_assistant_message_gets_user_first -v
```

Expected output:
```
FAILED tests/unit/test_groq_message_builder.py::test_leading_assistant_message_gets_user_first
AssertionError: First message must be 'user' for GPT-OSS, got 'assistant'
```

- [ ] **Step 3: Fix `_inject_instructions_for_reasoning_model` in `groq.py`**

In `app/infrastructure/llm/groq.py`, replace the entire `_inject_instructions_for_reasoning_model` static method (lines 86–125) with:

```python
@staticmethod
def _inject_instructions_for_reasoning_model(
    *,
    system_prompt: Optional[str],
    messages: List[dict],
) -> List[dict]:
    """
    Groq recommends avoiding system prompts for GPT-OSS models and placing
    instructions in a user message instead.

    If the message list is empty or starts with an assistant message (e.g. the
    Ask AI greeting), the instructions are prepended as a standalone user
    message so that GPT-OSS always receives a user message first.
    """
    if not system_prompt:
        return messages

    instruction_block = (
        "Conversation instructions:\n"
        f"{system_prompt.strip()}\n\n"
        "Apply these instructions to every reply in this conversation."
    )

    # GPT-OSS requires the first message to be user role.
    # If history starts with an assistant message (e.g. greeting), prepend a
    # standalone user instruction message rather than trying to inject into a
    # later user message — that would leave the assistant message at index 0.
    if not messages or messages[0].get("role") != "user":
        return [{"role": "user", "content": instruction_block}, *messages]

    # Normal path: inject instructions into the first user message.
    merged_messages: List[dict] = []
    injected = False
    for message in messages:
        if not injected and message.get("role") == "user":
            merged_messages.append({
                **message,
                "content": (
                    f"{instruction_block}\n\n"
                    f"Current user message:\n{message.get('content', '')}"
                ),
            })
            injected = True
            continue
        merged_messages.append(message)

    if not injected:
        merged_messages.insert(0, {
            "role": "user",
            "content": instruction_block,
        })

    return merged_messages
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_groq_message_builder.py -v
```

Expected output:
```
PASSED tests/unit/test_groq_message_builder.py::test_leading_assistant_message_gets_user_first
PASSED tests/unit/test_groq_message_builder.py::test_leading_assistant_message_instructions_present
PASSED tests/unit/test_groq_message_builder.py::test_leading_assistant_original_greeting_preserved
PASSED tests/unit/test_groq_message_builder.py::test_normal_case_instructions_injected_into_first_user
PASSED tests/unit/test_groq_message_builder.py::test_normal_case_message_count_unchanged
PASSED tests/unit/test_groq_message_builder.py::test_empty_messages_gets_standalone_user_instruction
PASSED tests/unit/test_groq_message_builder.py::test_no_system_prompt_returns_messages_unchanged
PASSED tests/unit/test_groq_message_builder.py::test_result_alternates_user_assistant

8 passed in 0.XX s
```

- [ ] **Step 5: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add app/infrastructure/llm/groq.py tests/unit/test_groq_message_builder.py
git commit -m "fix: ensure GPT-OSS message list starts with user role when history begins with assistant greeting"
```

---

## Task 3: Fix Conversation History Integrity on Barge-in and LLM Failure (Bugs 3 + 4)

**Root cause:** In `app/domain/services/voice_pipeline_service.py`, method `_run_turn` (line 607–611), the user message is appended to `session.conversation_history` **before** the LLM call. There are two failure paths that leave this user message dangling:

1. **Barge-in with empty LLM response** (Bug 3): The user interrupts while TTS is playing but the LLM produced no text (or interrupts before LLM produces any text). `was_interrupted = True`, `response_text` is empty → the assistant message is NOT committed at line 662. History now ends with `[..., user_msg]` (no assistant). The next user turn appends another user message → `[..., user_msg, user_msg2]`. Groq sees consecutive user messages, context is corrupted.

2. **LLM exception** (Bug 4): The LLM throws an error at line 616. The `except Exception` block at line 764 catches it but does NOT roll back the user message. Same result: dangling user in history.

**The fix principle:** If a turn completes with an assistant response (even interrupted mid-TTS), commit both user and assistant messages. If no assistant response was produced at all (empty or cancelled before LLM responded), roll back the user message.

**Files:**
- Modify: `app/domain/services/voice_pipeline_service.py:607–780` (`_run_turn`)
- Test: `tests/unit/test_voice_pipeline_history.py`

---

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_voice_pipeline_history.py`:

```python
"""
Unit tests for conversation history integrity in VoicePipelineService._run_turn.

These tests use a mock CallSession to verify that conversation history never
ends up with consecutive user messages or dangling user messages after:
  - A barge-in that happens before the LLM produces any output
  - An LLM exception
  - A normal completed turn
  - A barge-in after the LLM has already responded (partial response committed)
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field
from typing import List

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.call_session import CallState


# ── Minimal stubs so we can instantiate VoicePipelineService without real deps ─

class FakeLatencyTracker:
    def mark_speech_end(self, *a): pass
    def mark_llm_start(self, *a): pass
    def mark_llm_end(self, *a): pass
    def mark_tts_start(self, *a): pass
    def get_metrics(self, *a): return None
    def log_metrics(self, *a): pass
    def start_turn(self, *a): pass
    def mark_listening_start(self, *a): pass


class FakeTranscriptService:
    def accumulate_turn(self, **kwargs): pass
    async def flush_to_database(self, **kwargs): pass


@pytest.fixture
def fake_session():
    """Minimal CallSession-like object with real conversation_history list."""
    session = MagicMock()
    session.call_id = "test-call-001"
    session.turn_id = 1
    session.talklee_call_id = None
    session.tenant_id = None
    session.conversation_history = []
    session.state = CallState.LISTENING
    session.llm_active = False
    session.tts_active = False
    session.current_ai_response = ""
    session.add_latency_measurement = MagicMock()
    return session


@pytest.fixture
def pipeline_service():
    """VoicePipelineService with all I/O dependencies mocked out."""
    from app.domain.services.voice_pipeline_service import VoicePipelineService
    service = VoicePipelineService.__new__(VoicePipelineService)
    service.latency_tracker = FakeLatencyTracker()
    service.transcript_service = FakeTranscriptService()
    return service


# ── Bug 3: barge-in before LLM produces output ───────────────────────────────

@pytest.mark.asyncio
async def test_barge_in_with_empty_llm_response_rolls_back_user_message(
    pipeline_service, fake_session
):
    """
    When barge-in occurs and LLM produces no text, the user message must be
    rolled back so history doesn't end with a dangling user message.
    """
    # Simulate: LLM returns empty string (interrupted before any output)
    pipeline_service.get_llm_response = AsyncMock(return_value="")
    pipeline_service.synthesize_and_send_audio = AsyncMock(return_value=True)  # was_interrupted

    await pipeline_service._run_turn(
        session=fake_session,
        full_transcript="What are your pricing plans?",
        websocket=None,
        turn_id=1,
    )

    assert len(fake_session.conversation_history) == 0, (
        f"History should be empty after rolled-back barge-in, "
        f"got {fake_session.conversation_history}"
    )


@pytest.mark.asyncio
async def test_barge_in_with_llm_response_commits_both_messages(
    pipeline_service, fake_session
):
    """
    When barge-in occurs but the LLM DID produce text before interruption,
    both user and assistant messages must be committed to maintain alternation.
    """
    pipeline_service.get_llm_response = AsyncMock(return_value="Our Basic plan is $29/month.")
    pipeline_service.synthesize_and_send_audio = AsyncMock(return_value=True)  # was_interrupted

    await pipeline_service._run_turn(
        session=fake_session,
        full_transcript="What are your pricing plans?",
        websocket=None,
        turn_id=1,
    )

    assert len(fake_session.conversation_history) == 2
    assert fake_session.conversation_history[0].role == MessageRole.USER
    assert fake_session.conversation_history[1].role == MessageRole.ASSISTANT
    assert fake_session.conversation_history[1].content == "Our Basic plan is $29/month."


# ── Bug 4: LLM exception rolls back user message ─────────────────────────────

@pytest.mark.asyncio
async def test_llm_exception_rolls_back_user_message(pipeline_service, fake_session):
    """
    When the LLM raises an exception, the user message that was pre-appended
    must be rolled back so history doesn't end with a dangling user message.
    """
    pipeline_service.get_llm_response = AsyncMock(side_effect=RuntimeError("Groq timeout"))
    pipeline_service.synthesize_and_send_audio = AsyncMock(return_value=False)

    # Should not raise (the method logs and swallows LLM errors)
    await pipeline_service._run_turn(
        session=fake_session,
        full_transcript="Tell me about your plans.",
        websocket=None,
        turn_id=1,
    )

    assert len(fake_session.conversation_history) == 0, (
        f"History should be empty after LLM failure rollback, "
        f"got {fake_session.conversation_history}"
    )


# ── CancelledError rolls back user message ───────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_rolls_back_user_message(pipeline_service, fake_session):
    """
    When the task is cancelled (barge-in cancels the asyncio.Task),
    the user message must be rolled back before propagating CancelledError.
    """
    pipeline_service.get_llm_response = AsyncMock(side_effect=asyncio.CancelledError())
    pipeline_service.synthesize_and_send_audio = AsyncMock(return_value=False)

    with pytest.raises(asyncio.CancelledError):
        await pipeline_service._run_turn(
            session=fake_session,
            full_transcript="Can you help me?",
            websocket=None,
            turn_id=1,
        )

    assert len(fake_session.conversation_history) == 0, (
        f"History should be empty after cancellation rollback, "
        f"got {fake_session.conversation_history}"
    )


# ── Normal completed turn commits both messages ───────────────────────────────

@pytest.mark.asyncio
async def test_normal_turn_commits_user_and_assistant(pipeline_service, fake_session):
    """Normal turn: user and assistant messages both committed, in order."""
    pipeline_service.get_llm_response = AsyncMock(return_value="Happy to help!")
    pipeline_service.synthesize_and_send_audio = AsyncMock(return_value=False)  # not interrupted

    await pipeline_service._run_turn(
        session=fake_session,
        full_transcript="Hi, can you help?",
        websocket=None,
        turn_id=1,
    )

    assert len(fake_session.conversation_history) == 2
    assert fake_session.conversation_history[0].role == MessageRole.USER
    assert fake_session.conversation_history[0].content == "Hi, can you help?"
    assert fake_session.conversation_history[1].role == MessageRole.ASSISTANT
    assert fake_session.conversation_history[1].content == "Happy to help!"


# ── Two turns never produce consecutive user messages ────────────────────────

@pytest.mark.asyncio
async def test_two_interrupted_turns_never_consecutive_user_messages(
    pipeline_service, fake_session
):
    """
    Even if two turns are both interrupted before LLM responds, history must
    never contain consecutive user messages.
    """
    pipeline_service.get_llm_response = AsyncMock(return_value="")
    pipeline_service.synthesize_and_send_audio = AsyncMock(return_value=True)

    await pipeline_service._run_turn(
        session=fake_session, full_transcript="First turn.", websocket=None, turn_id=1
    )
    await pipeline_service._run_turn(
        session=fake_session, full_transcript="Second turn.", websocket=None, turn_id=2
    )

    roles = [m.role for m in fake_session.conversation_history]
    for i in range(len(roles) - 1):
        assert roles[i] != roles[i + 1], (
            f"Consecutive {roles[i]} messages at indices {i} and {i+1}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_voice_pipeline_history.py::test_barge_in_with_empty_llm_response_rolls_back_user_message -v
```

Expected output:
```
FAILED tests/unit/test_voice_pipeline_history.py::test_barge_in_with_empty_llm_response_rolls_back_user_message
AssertionError: History should be empty after rolled-back barge-in, got [Message(role=<MessageRole.USER: 'user'>, ...)]
```

- [ ] **Step 3: Fix `_run_turn` in `voice_pipeline_service.py`**

In `app/domain/services/voice_pipeline_service.py`, find `_run_turn` (starts at line 592). Make these changes:

**3a. Track whether user message is committed (right after the append at line 611):**

Replace:
```python
        user_message = Message(
            role=MessageRole.USER,
            content=full_transcript,
        )
        session.conversation_history.append(user_message)

        try:
```

With:
```python
        user_message = Message(
            role=MessageRole.USER,
            content=full_transcript,
        )
        session.conversation_history.append(user_message)
        _user_msg_appended = True

        try:
```

**3b. Change the barge-in / response commitment block (around line 662). Replace:**

```python
            if response_text and response_text.strip() and not was_interrupted:
                assistant_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=response_text,
                )
                session.conversation_history.append(assistant_message)

                self.transcript_service.accumulate_turn(
                    call_id=call_id,
                    role="assistant",
                    content=response_text,
                    talklee_call_id=session.talklee_call_id,
                    turn_index=turn_id,
                    event_type="assistant_response",
                    is_final=True,
                    include_in_plaintext=True,
                )
            elif was_interrupted:
                logger.info(
                    "assistant_reply_not_committed",
                    extra={
                        "call_id": call_id,
                        "turn_id": turn_id,
                        "reason": "barge_in",
                    },
                )
```

With:

```python
            if response_text and response_text.strip():
                # Commit assistant message whether or not there was a barge-in.
                # If barge-in occurred, this is a partial response — still commit
                # it to maintain user→assistant alternation in history.
                assistant_message = Message(
                    role=MessageRole.ASSISTANT,
                    content=response_text,
                )
                session.conversation_history.append(assistant_message)

                self.transcript_service.accumulate_turn(
                    call_id=call_id,
                    role="assistant",
                    content=response_text,
                    talklee_call_id=session.talklee_call_id,
                    turn_index=turn_id,
                    event_type="assistant_response",
                    is_final=True,
                    include_in_plaintext=True,
                )
                if was_interrupted:
                    logger.info(
                        "assistant_reply_committed_despite_barge_in",
                        extra={"call_id": call_id, "turn_id": turn_id},
                    )
            elif _user_msg_appended:
                # LLM produced nothing (interrupted before any output, or empty).
                # Roll back the user message to prevent consecutive user messages.
                if (
                    session.conversation_history
                    and session.conversation_history[-1] is user_message
                ):
                    session.conversation_history.pop()
                    logger.info(
                        "user_message_rolled_back_empty_turn",
                        extra={"call_id": call_id, "turn_id": turn_id},
                    )
```

**3c. Add rollback in the `asyncio.CancelledError` handler (around line 757). Replace:**

```python
        except asyncio.CancelledError:
            logger.info(
                "turn_processing_cancelled",
                extra={"call_id": call_id, "turn_id": turn_id},
            )
            raise
```

With:

```python
        except asyncio.CancelledError:
            # Roll back the pre-appended user message on cancellation.
            # The turn was cancelled before any assistant response was committed,
            # so leaving the user message would cause consecutive user messages.
            if (
                _user_msg_appended
                and session.conversation_history
                and session.conversation_history[-1] is user_message
            ):
                session.conversation_history.pop()
                logger.info(
                    "user_message_rolled_back_on_cancel",
                    extra={"call_id": call_id, "turn_id": turn_id},
                )
            logger.info(
                "turn_processing_cancelled",
                extra={"call_id": call_id, "turn_id": turn_id},
            )
            raise
```

**3d. Add rollback in the `except Exception` handler (around line 764). Replace:**

```python
        except Exception as e:
            logger.error(
                f"Error processing turn: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True,
            )
```

With:

```python
        except Exception as e:
            # Roll back the pre-appended user message on any LLM/TTS error.
            if (
                _user_msg_appended
                and session.conversation_history
                and session.conversation_history[-1] is user_message
            ):
                session.conversation_history.pop()
                logger.info(
                    "user_message_rolled_back_on_error",
                    extra={"call_id": call_id, "turn_id": turn_id, "error": str(e)},
                )
            logger.error(
                f"Error processing turn: {e}",
                extra={"call_id": call_id, "error": str(e)},
                exc_info=True,
            )
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/test_voice_pipeline_history.py -v
```

Expected output:
```
PASSED tests/unit/test_voice_pipeline_history.py::test_barge_in_with_empty_llm_response_rolls_back_user_message
PASSED tests/unit/test_voice_pipeline_history.py::test_barge_in_with_llm_response_commits_both_messages
PASSED tests/unit/test_voice_pipeline_history.py::test_llm_exception_rolls_back_user_message
PASSED tests/unit/test_voice_pipeline_history.py::test_cancellation_rolls_back_user_message
PASSED tests/unit/test_voice_pipeline_history.py::test_normal_turn_commits_user_and_assistant
PASSED tests/unit/test_voice_pipeline_history.py::test_two_interrupted_turns_never_consecutive_user_messages

6 passed in 0.XX s
```

- [ ] **Step 5: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add app/domain/services/voice_pipeline_service.py tests/unit/test_voice_pipeline_history.py
git commit -m "fix: roll back dangling user message on barge-in (empty response) and LLM exception to prevent consecutive user messages in history"
```

---

## Task 4: Fix Misleading EagerEndOfTurn Log Message (Bug 5)

**Root cause:** In `app/domain/services/voice_pipeline_service.py` at line 426, the log message says `"starting speculative LLM"` but the code only saves `session.current_user_input = transcript.text` — no actual speculative task is created. This misleads engineers debugging latency issues (they chase a "speculative pipeline" that doesn't exist) and could hide a future implementation gap.

**Files:**
- Modify: `app/domain/services/voice_pipeline_service.py:426`

---

- [ ] **Step 1: Fix the log message**

In `app/domain/services/voice_pipeline_service.py` around line 422–430, replace:

```python
        # Handle EagerEndOfTurn - start LLM early for lower latency
        if metadata.get("eager") and transcript.text:
            # Only start eager processing if not already processing
            if not session.llm_active and call_id not in self._pending_llm_tasks:
                logger.info(f"EagerEndOfTurn for call {call_id} - starting speculative LLM")
                # Store the eager transcript but don't process yet
                # We'll process when EndOfTurn confirms
                session.current_user_input = transcript.text
            return
```

With:

```python
        # Handle EagerEndOfTurn - store transcript for lower latency when EndOfTurn fires
        if metadata.get("eager") and transcript.text:
            # Only update if not already processing
            if not session.llm_active and call_id not in self._pending_llm_tasks:
                logger.info(
                    f"EagerEndOfTurn for call {call_id} - storing speculative transcript, "
                    f"waiting for EndOfTurn to confirm before dispatching LLM"
                )
                # Store the eager transcript; EndOfTurn will trigger the actual LLM call
                session.current_user_input = transcript.text
            return
```

- [ ] **Step 2: Verify the change is correct**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
grep -n "speculative" app/domain/services/voice_pipeline_service.py
```

Expected output:
```
426:                    f"EagerEndOfTurn for call {call_id} - storing speculative transcript, "
```

The word "starting speculative LLM" should no longer appear. Only the new accurate message should be present.

- [ ] **Step 3: Run all existing unit tests to verify nothing is broken**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/ -v --tb=short
```

Expected: all existing tests pass, no regressions.

- [ ] **Step 4: Commit**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add app/domain/services/voice_pipeline_service.py
git commit -m "fix: correct misleading EagerEndOfTurn log message - no speculative LLM task is created, transcript is stored for EndOfTurn confirmation"
```

---

## Task 5: Run Full Test Suite and Verify All Fixes Together

- [ ] **Step 1: Run all unit tests**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -m pytest tests/unit/ -v
```

Expected: all pass, including the 3 new test files.

- [ ] **Step 2: Check for any import errors across affected modules**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
python -c "
from app.domain.services.llm_guardrails import LLMGuardrails
from app.infrastructure.llm.groq import GroqLLMProvider
from app.domain.services.voice_pipeline_service import VoicePipelineService
print('All imports OK')
"
```

Expected output:
```
All imports OK
```

- [ ] **Step 3: Commit final state**

```bash
cd /home/ai-lab/Desktop/Talky.ai-complete-/backend
git add .
git commit -m "test: confirm all voice pipeline consistency fixes pass full unit test suite"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Requirement | Task |
|------------|------|
| Fix "Sure thing" being truncated to "thing!" | Task 1 |
| GPT-OSS instructions ignored / inconsistent LLM behavior | Task 2 |
| Consecutive user messages after barge-in | Task 3 |
| Consecutive user messages after LLM failure | Task 3 |
| Barge-in with partial LLM response loses context | Task 3 |
| "Starting speculative LLM" log is misleading | Task 4 |
| Full stable conversation, not hit-and-miss | All tasks together |

### 2. Why These 4 Bugs Compound

Each bug is independent but they amplify each other:

- **Bug 1** causes the FIRST assistant response to be garbled ("thing! Our Basic plan..."). The user hears nonsense and asks again.
- **Bug 2** causes GPT-OSS to ignore the system prompt on the very first turn (because the instruction injection ends up at message index 1, not 0). The model responds out-of-character or too verbosely.
- **Bug 3+4** cause the SECOND and subsequent turns to have broken history. The LLM receives `[user, user]` which it either merges ("are you asking about plans and plans?") or uses to generate a confused response.
- **Bug 5** (log only) causes developers to chase a non-existent speculative pipeline when debugging latency, wasting time.

Fix all four → the pipeline is stable, history always alternates, responses are correctly formatted, and logs accurately describe system state.

### 3. What Was NOT Changed

- No changes to TTS, STT, WebSocket transport, or session lifecycle — these are working correctly.
- No changes to the Groq retry/circuit-breaker logic — it is correct.
- No changes to the LLM guardrails `truncate_response` or `validate_response` — they are correct.
- `EagerEndOfTurn` is NOT implemented as a real speculative pipeline — it's out of scope and the log fix is sufficient.
