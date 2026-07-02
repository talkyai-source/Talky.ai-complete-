"""Unit tests for FIX #1b: a terminal STT failure (Deepgram primary AND any
failover secondary both down) must propagate out of the pipeline task
instead of being swallowed.

Before this fix:
  AudioIngest.process's ``except Exception as e: logger.error(...)`` (no
  re-raise) meant ``process_audio_stream`` / ``start_pipeline`` / the
  ``pipeline_task`` asyncio.Task all completed *without* an exception, so
  ``telephony/lifecycle.py``'s ``_pipeline_done_cb`` (``task.exception()``)
  never fired and the call sat on dead air until the ~300s inactivity
  watchdog (see test_telephony_lifecycle_watchdog_hangup.py for that half).

After this fix:
  the STT-stream except block raises ``TerminalSTTError`` (chained from the
  original exception), and ``VoicePipelineService.start_pipeline`` re-raises
  it (after its ``finally`` cleanup runs) instead of absorbing it like a
  generic pipeline error.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest, TerminalSTTError
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _make_session() -> CallSession:
    import asyncio

    session = CallSession(
        call_id="call-terminal-stt",
        campaign_id="demo",
        lead_id="lead-123",
        provider_call_id="provider-123",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-123",
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=AgentConfig(
            goal=AgentGoal.INFORMATION_GATHERING,
            business_type="voice ai platform",
            agent_name="Assistant",
            company_name="Talky.ai",
            rules=ConversationRule(),
            flow=ConversationFlow(),
            response_max_sentences=2,
        ),
    )
    session.barge_in_event = asyncio.Event()
    return session


class _FailingSTT:
    """Stand-in for ResilientSTTProvider once BOTH primary and secondary
    have failed: stream_transcribe raises before yielding anything, mirroring
    resilient_stt.py's un-caught propagation out of the secondary's
    ``_stream_with_provider`` call when it also faults."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
        raise self._exc
        yield  # pragma: no cover - never reached; makes this an async generator


class TestAudioIngestRaisesTerminalSTTError:
    @pytest.mark.asyncio
    async def test_stt_stream_exception_is_reraised_as_terminal_error(self):
        session = _make_session()
        pipeline = MagicMock()
        pipeline.media_gateway.get_audio_queue.return_value = None  # never consumed
        pipeline.stt_provider = _FailingSTT(RuntimeError("deepgram + failover both down"))
        pipeline._barge_in_events = {}
        pipeline._barge_in_epoch = {}

        ingest = AudioIngest(pipeline)

        with pytest.raises(TerminalSTTError) as excinfo:
            await ingest.process(session)

        assert "deepgram + failover both down" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    @pytest.mark.asyncio
    async def test_normal_generator_completion_does_not_raise(self):
        """If stream_transcribe simply ends (no exception — e.g. the caller
        hung up and cancellation isn't in play here), process() must return
        normally. Guards against over-eager conversion of every code path
        into TerminalSTTError."""
        session = _make_session()
        pipeline = MagicMock()
        pipeline.media_gateway.get_audio_queue.return_value = None
        pipeline._barge_in_events = {}
        pipeline._barge_in_epoch = {}

        class _EmptySTT:
            async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
                return
                yield  # pragma: no cover

        pipeline.stt_provider = _EmptySTT()

        # Must not raise.
        ingest = AudioIngest(pipeline)
        await ingest.process(session)


class TestStartPipelinePropagatesTerminalFailure:
    @pytest.mark.asyncio
    async def test_start_pipeline_reraises_terminal_stt_error(self):
        """The task VoicePipelineService.start_pipeline runs inside (the
        real ``pipeline_task`` in telephony/lifecycle.py) must end with an
        exception so _pipeline_done_cb's task.exception() check fires."""
        stt_provider = _FailingSTT(RuntimeError("deepgram + failover both down"))
        service = VoicePipelineService(
            stt_provider=stt_provider,
            llm_provider=AsyncMock(),
            tts_provider=AsyncMock(),
            media_gateway=AsyncMock(),
        )
        service.latency_tracker = MagicMock()
        service.media_gateway.get_audio_queue = MagicMock(return_value=None)

        session = _make_session()

        with pytest.raises(TerminalSTTError):
            await service.start_pipeline(session, None, None)

        # finally-block cleanup still ran despite the re-raise.
        assert session.stt_active is False
        assert session.call_id not in service._barge_in_events

    @pytest.mark.asyncio
    async def test_start_pipeline_swallows_non_terminal_pipeline_errors(self):
        """A generic (non-STT) pipeline error must keep its existing
        swallow-and-log behaviour — only the terminal-STT signal changes."""
        service = VoicePipelineService(
            stt_provider=AsyncMock(),
            llm_provider=AsyncMock(),
            tts_provider=AsyncMock(),
            media_gateway=AsyncMock(),
        )
        service.latency_tracker = MagicMock()

        async def _boom(*args, **kwargs):
            raise ValueError("unrelated pipeline bug")

        service.process_audio_stream = _boom

        session = _make_session()

        # Must NOT raise — matches pre-fix behaviour for generic errors.
        await service.start_pipeline(session, None, None)
        assert session.stt_active is False
