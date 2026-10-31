"""Offline integration through the actual TurnRunner -> TurnStreamer path.

LLM output and the TTS submission port are synthetic. These verify orchestration,
admission and history, not model quality, provider connectivity or caller hearing.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.llm.groq import LLMStreamStalled


class ScriptedLLM:
    def __init__(self, chunks, *, stall=False, block=False):
        self.chunks = chunks
        self.stall = stall
        self.block = block
        self.requests = []
        self.waiting = asyncio.Event()

    async def stream_chat_with_timeout(self, messages, **kwargs):
        self.requests.append({"messages": list(messages), **kwargs})
        for chunk in self.chunks:
            yield chunk
        if self.stall:
            raise LLMStreamStalled("Synthetic incomplete provider stream")
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    llm = ScriptedLLM(["The office opens in the morning."])
    service = VoicePipelineService(
        stt_provider=MagicMock(), llm_provider=llm,
        tts_provider=MagicMock(), media_gateway=MagicMock(),
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.transcript_service = MagicMock()
    service._shutdown_session_for_end_action = AsyncMock()
    session = CallSession(
        call_id="ag07-synthetic-call", campaign_id="ag07-campaign",
        lead_id="ag07-lead", provider_call_id="ag07-provider-call",
        system_prompt="Answer the caller using the supplied company knowledge.",
        voice_id="synthetic-voice",
    )
    session.turn_id = 3
    session.knowledge_mode = "inline"
    session.barge_in_event = asyncio.Event()
    service._barge_in_events[session.call_id] = session.barge_in_event
    submitted = []

    async def submit(s, text, *_args, **_kwargs):
        submitted.append(text)
        s.tts_active = False
        # No transport playback receipt: this proves submission only.
        s._tts_playout_completed = False
        return False

    service.synthesize_and_send_audio = submit
    return service, session, llm, submitted


def assistant_history(session):
    return [m.content for m in session.conversation_history if m.role == MessageRole.ASSISTANT]


async def test_two_turns_use_prepared_context_and_commit_only_submitted_speech(pipeline):
    service, session, llm, submitted = pipeline
    first, _, _ = await service._run_turn(session, "When does the office open?")
    assert llm.requests[0]["messages"][-1].content == "When does the office open?"
    assert "LIVE STRUCTURED STATE v1" in llm.requests[0]["system_prompt"]
    assert first == " ".join(submitted)
    llm.chunks = ["Which service interests you?"]
    second, _, _ = await service._run_turn(session, "Tell me about your services.")
    assert [m.content for m in llm.requests[-1]["messages"]] == [
        "When does the office open?", first, "Tell me about your services.",
    ]
    assert assistant_history(session) == [first, second]
    assert " ".join(assistant_history(session)) == " ".join(submitted)
    assert not session._tts_playout_completed
    assert service.transcript_service.accumulate_turn.call_count == 2


async def test_caller_denial_reaches_state_and_blocks_existing_customer_claim(pipeline):
    service, session, llm, submitted = pipeline
    llm.chunks = ["As our existing customer, your account is ready."]
    await service._run_turn(session, "I am not your customer.")
    assert "customer_relationship=denied" in llm.requests[0]["system_prompt"]
    assert "existing customer" not in " ".join(submitted).lower()
    assert assistant_history(session) == [" ".join(submitted)]
    service._shutdown_session_for_end_action.assert_not_awaited()


async def test_unavailable_action_cannot_create_a_sent_receipt_or_false_history(pipeline):
    service, session, llm, submitted = pipeline
    llm.chunks = ["I have sent the email."]
    await service._run_turn(session, "Please email the details.")
    assert submitted
    assert "have sent" not in " ".join(submitted).lower()
    assert assistant_history(session) == [" ".join(submitted)]
    assert not getattr(session, "_voice_action_results", {})


async def test_incomplete_stream_discards_unsubmitted_tail(pipeline):
    service, session, llm, submitted = pipeline
    llm.chunks = ["The office opens in the morning. ", "The secret unpublished tail"]
    llm.stall = True
    response, _, _ = await service._run_turn(session, "When does the office open?")
    assert response == " ".join(submitted) == "The office opens in the morning."
    assert assistant_history(session) == [response]
    assert "unpublished" not in str(session.conversation_history)


async def test_cancellation_preserves_caller_and_only_submitted_partial(pipeline):
    service, session, llm, submitted = pipeline
    llm.chunks = ["The office opens in the morning. "]
    llm.block = True
    task = asyncio.create_task(service._run_turn(session, "When does the office open?"))
    await asyncio.wait_for(llm.waiting.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert submitted == ["The office opens in the morning."]
    assert session.conversation_history[0].content == "When does the office open?"
    assert assistant_history(session) == [submitted[0] + " [interrupted by caller]"]
    service._shutdown_session_for_end_action.assert_not_awaited()


async def test_zero_output_timeout_uses_recovery_and_preserves_caller(pipeline):
    service, session, llm, submitted = pipeline
    llm.chunks = []
    llm.stall = True
    response, _, _ = await service._run_turn(session, "Please explain the service.")
    assert submitted and "repeat" in " ".join(submitted).lower()
    assert response == " ".join(submitted)
    assert assistant_history(session) == [response]
    assert session.conversation_history[0].content == "Please explain the service."
