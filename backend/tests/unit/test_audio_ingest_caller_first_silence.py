"""Actual ingest task preserves activity ownership and a silent disconnection deadline."""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest

# Captured BEFORE any test patches asyncio.sleep, so awaiting it inside a
# replacement for asyncio.sleep cannot recurse into the patch.
_REAL_SLEEP = asyncio.sleep


async def _instant_yield(*_args, **_kwargs) -> None:
    """A drop-in for asyncio.sleep that takes no time but DOES yield.

    Tests that collapse every timer need the monitor loop to keep handing
    control back to the event loop; an AsyncMock does not, which starves the
    loop and stops timeouts firing. See its use below.
    """
    await _REAL_SLEEP(0)


def _make_session(first_speaker: str) -> CallSession:
    session = CallSession(
        call_id="call-silence-test",
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
        ),
    )
    session.barge_in_event = asyncio.Event()
    session.stt_active = True
    # Set the same way production code does (telephony/lifecycle.py,
    # telephony/prewarm.py both assign directly onto the CallSession).
    session._first_speaker = first_speaker
    # A silent caller on a live line still sends audio frames -- line noise,
    # room tone. The harness's fake STT never reads the stream, so the ingest
    # loop that stamps first audio never runs; state that precondition here.
    # Since 2026-09-23 the nudge clock counts from first caller audio (see
    # test_opening_nudge_waits_for_caller_audio below): with NO audio at all we
    # could not hear an answer to "Hello?" anyway.
    session._caller_audio_started_at = time.monotonic()
    return session


class _ParkedSTT:
    """Never yields a transcript — the caller stays silent for the whole
    test, which is exactly the scenario the silence monitor exists for."""

    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable; keeps this an async generator


def _make_pipeline() -> MagicMock:
    pipeline = MagicMock()
    pipeline.media_gateway.get_audio_queue.return_value = asyncio.Queue(maxsize=10)
    pipeline.stt_provider = _ParkedSTT()
    pipeline._barge_in_events = {}
    pipeline._barge_in_epoch = {}
    pipeline.latency_tracker = MagicMock()
    pipeline.synthesize_and_send_audio = AsyncMock()
    pipeline._shutdown_session_for_end_action = AsyncMock()
    return pipeline


async def _run_until_silence_tick(session: CallSession, pipeline: MagicMock, *, hangup_s=30, duration=0.1) -> None:
    """Drive AudioIngest.process for a short, real wall-clock window with
    the monitor's 1s poll interval collapsed to near-zero so opening/mid
    thresholds of a few hundredths of a second are crossed quickly, without
    an actual multi-second test."""
    ingest = AudioIngest(pipeline)
    with (
        patch.dict(
            os.environ,
            {
                "VOICE_OPENING_HELLO_S": "0.03",
                "VOICE_MID_NUDGE_S": "0.03",
                "VOICE_SILENCE_HANGUP_S": str(hangup_s),
                "VOICE_NUDGE_MIN_GAP_S": "0.03",
            },
        ),
        patch("asyncio.sleep", new=_instant_yield),
    ):
        task = asyncio.ensure_future(ingest.process(session))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=duration)
        except asyncio.TimeoutError:
            pass
        finally:
            session.stt_active = False
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("speaker", ["user", "agent"])
async def test_silence_never_generates_speech_before_deadline(speaker):
    session, pipeline = _make_session(speaker), _make_pipeline()
    await _run_until_silence_tick(session, pipeline)
    pipeline.synthesize_and_send_audio.assert_not_awaited()
    pipeline._shutdown_session_for_end_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_silence_deadline_disconnects_once_without_speech():
    session, pipeline = _make_session("user"), _make_pipeline()
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    pipeline._shutdown_session_for_end_action.assert_awaited_once_with(session, None, "silence_timeout", "")
    pipeline.synthesize_and_send_audio.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("activity", ["tts_active", "llm_active", "_last_backchannel_monotonic", "_caller_last_text_at", "_caller_turn_open_since"])
async def test_current_activity_prevents_timeout(activity):
    session, pipeline = _make_session("user"), _make_pipeline()
    setattr(session, activity, True if activity.endswith("active") else time.monotonic())
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    pipeline._shutdown_session_for_end_action.assert_not_awaited()
    pipeline.synthesize_and_send_audio.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("activity", ["_last_backchannel_monotonic", "_caller_last_text_at", "_caller_turn_open_since"])
async def test_stale_activity_cannot_hold_deadline_forever(activity):
    session, pipeline = _make_session("user"), _make_pipeline()
    setattr(session, activity, time.monotonic() - 30)
    await _run_until_silence_tick(session, pipeline, hangup_s=0.03)
    pipeline._shutdown_session_for_end_action.assert_awaited_once()
    pipeline.synthesize_and_send_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_model_turn_leaves_full_idle_interval_after_playback(monkeypatch):
    from types import SimpleNamespace
    from app.domain.services.voice_pipeline import audio_ingest

    session, pipeline = _make_session("agent"), _make_pipeline()
    clock = SimpleNamespace(value=0.0)
    # A 70-second answer must not consume the caller's following idle interval.
    ticks = iter([(70, True), (71, False), (75, False), (130, False), (131, False)])
    observed = []

    async def tick(_delay):
        clock.value, session.tts_active = next(ticks)
        await _REAL_SLEEP(0)

    async def close(*args):
        observed.append((clock.value, args))

    pipeline._shutdown_session_for_end_action.side_effect = close
    monkeypatch.setattr(audio_ingest, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setenv("VOICE_SILENCE_HANGUP_S", "60")
    with patch("asyncio.sleep", new=tick):
        task = asyncio.create_task(AudioIngest(pipeline).process(session))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        except asyncio.TimeoutError:
            pass
        finally:
            session.stt_active = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert observed == [(131, (session, None, "silence_timeout", ""))]
    pipeline.synthesize_and_send_audio.assert_not_awaited()
