"""
Unit tests for conversation history integrity in VoicePipelineService._run_turn.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.domain.models.conversation import Message, MessageRole


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
    session = MagicMock()
    session.call_id = "test-call-001"
    session.turn_id = 1
    session.talklee_call_id = None
    session.tenant_id = None
    session.conversation_history = []
    session.llm_active = False
    session.tts_active = False
    session.current_ai_response = ""
    session.add_latency_measurement = MagicMock()
    return session


@pytest.fixture
def pipeline_service():
    from app.domain.services.voice_pipeline_service import VoicePipelineService
    service = VoicePipelineService.__new__(VoicePipelineService)
    service.latency_tracker = FakeLatencyTracker()
    service.transcript_service = FakeTranscriptService()
    return service


@pytest.mark.asyncio
async def test_barge_in_with_empty_llm_response_rolls_back_user_message(
    pipeline_service, fake_session
):
    """Empty response from _stream_llm_and_tts: user message rolled back."""
    pipeline_service._stream_llm_and_tts = AsyncMock(return_value=("", 100.0, 0.0))

    await pipeline_service._run_turn(
        session=fake_session, full_transcript="What are your pricing plans?",
        websocket=None, turn_id=1,
    )

    assert len(fake_session.conversation_history) == 0, (
        f"Expected empty history, got {[m.role for m in fake_session.conversation_history]}"
    )


@pytest.mark.asyncio
async def test_barge_in_with_llm_response_commits_both_messages(
    pipeline_service, fake_session
):
    """Non-empty response (even with interrupted TTS): both messages committed."""
    pipeline_service._stream_llm_and_tts = AsyncMock(
        return_value=("Our Basic plan is $29/month.", 100.0, 50.0)
    )

    await pipeline_service._run_turn(
        session=fake_session, full_transcript="What are your pricing plans?",
        websocket=None, turn_id=1,
    )

    assert len(fake_session.conversation_history) == 2
    assert fake_session.conversation_history[0].role == MessageRole.USER
    assert fake_session.conversation_history[1].role == MessageRole.ASSISTANT
    assert fake_session.conversation_history[1].content == "Our Basic plan is $29/month."


@pytest.mark.asyncio
async def test_llm_exception_rolls_back_user_message(pipeline_service, fake_session):
    """Exception inside _stream_llm_and_tts: user message rolled back."""
    pipeline_service._stream_llm_and_tts = AsyncMock(
        side_effect=RuntimeError("Groq timeout")
    )

    await pipeline_service._run_turn(
        session=fake_session, full_transcript="Tell me about your plans.",
        websocket=None, turn_id=1,
    )

    assert len(fake_session.conversation_history) == 0, (
        f"Expected empty history after LLM failure, got {[m.role for m in fake_session.conversation_history]}"
    )


@pytest.mark.asyncio
async def test_cancellation_before_any_sentence_keeps_user_message(
    pipeline_service, fake_session
):
    """asyncio.CancelledError with nothing spoken yet: the user message is
    KEPT (only what the cancelled task itself appended after it is
    dropped), error re-raised.

    UPDATED 2026-09-24 — this test used to assert the OPPOSITE (full
    rollback, discarding the caller's own utterance), which was the
    production defect itself: each Flux EndOfTurn dispatches only its own
    segment, and nothing downstream re-queues a rolled-back fragment into a
    later turn, so a caller who spoke in fragments had their words silently
    erased (51450718: "I want full body checkup" never engaged with;
    b97ce4c5: 8 turns lost this way in one call). See
    tests/unit/test_bargein_cancel_keeps_caller_turn.py for the full
    production evidence and an end-to-end (real task-cancellation) proof.
    """
    pipeline_service._stream_llm_and_tts = AsyncMock(
        side_effect=asyncio.CancelledError()
    )

    with pytest.raises(asyncio.CancelledError):
        await pipeline_service._run_turn(
            session=fake_session, full_transcript="Can you help me?",
            websocket=None, turn_id=1,
        )

    assert len(fake_session.conversation_history) == 1, (
        f"Expected the user message to survive, got "
        f"{[m.role for m in fake_session.conversation_history]}"
    )
    assert fake_session.conversation_history[0].role == MessageRole.USER
    assert fake_session.conversation_history[0].content == "Can you help me?"


@pytest.mark.asyncio
async def test_normal_turn_commits_user_and_assistant(pipeline_service, fake_session):
    """Normal turn: both messages committed in correct order."""
    pipeline_service._stream_llm_and_tts = AsyncMock(
        return_value=("Happy to help!", 80.0, 120.0)
    )

    await pipeline_service._run_turn(
        session=fake_session, full_transcript="Hi, can you help?",
        websocket=None, turn_id=1,
    )

    assert len(fake_session.conversation_history) == 2
    assert fake_session.conversation_history[0].role == MessageRole.USER
    assert fake_session.conversation_history[0].content == "Hi, can you help?"
    assert fake_session.conversation_history[1].role == MessageRole.ASSISTANT
    assert fake_session.conversation_history[1].content == "Happy to help!"


@pytest.mark.asyncio
async def test_two_interrupted_turns_never_consecutive_user_messages(
    pipeline_service, fake_session
):
    """Two back-to-back empty turns: no consecutive user messages in history."""
    pipeline_service._stream_llm_and_tts = AsyncMock(return_value=("", 100.0, 0.0))

    await pipeline_service._run_turn(
        session=fake_session, full_transcript="First.", websocket=None, turn_id=1
    )
    await pipeline_service._run_turn(
        session=fake_session, full_transcript="Second.", websocket=None, turn_id=2
    )

    roles = [m.role for m in fake_session.conversation_history]
    for i in range(len(roles) - 1):
        assert roles[i] != roles[i + 1], (
            f"Consecutive {roles[i]} at indices {i} and {i+1}"
        )
