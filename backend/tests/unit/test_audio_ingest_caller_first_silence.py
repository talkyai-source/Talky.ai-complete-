"""FIX — caller-first silence monitor must actually reach the OPENING
"Hello?" ladder.

Root cause: audio_ingest.py's ``_silence_monitor`` computed
``_is_caller_first = _first_speaker_label(session) == "inbound"``, but
``_first_speaker_label`` (turn_helpers.py) only ever returns ``"user"`` or
``"agent"`` (see its docstring) — it never returns ``"inbound"``. So
``_is_caller_first`` was permanently False, the OPENING "Hello?" ladder never
fired, and ``should_suppress_mid_nudge`` swallowed the MID nudge too (its
suppression rule is keyed on ``not is_caller_first``) — a caller-first call
with a silent callee got 60s of total dead air with no nudge at all.

Fix: compare against ``"user"`` (mirrors turn_ender.py's own
``_first_speaker_label(session) == "user"`` check for the instant-opener
path). These tests drive the REAL ``_silence_monitor`` closure inside
``AudioIngest.process`` (not just the pure ``turn_director`` helpers) so a
regression back to the "inbound" typo — or any other break in the wiring
between ``_first_speaker`` and the monitor — is actually caught.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest


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
            response_max_sentences=2,
        ),
    )
    session.barge_in_event = asyncio.Event()
    session.stt_active = True
    # Set the same way production code does (telephony/lifecycle.py,
    # telephony/prewarm.py both assign directly onto the CallSession).
    session._first_speaker = first_speaker
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
    return pipeline


async def _run_until_silence_tick(session: CallSession, pipeline: MagicMock) -> None:
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
                "VOICE_SILENCE_HANGUP_S": "30",
                "VOICE_NUDGE_MIN_GAP_S": "0.03",
            },
        ),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        task = asyncio.ensure_future(ingest.process(session))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
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
async def test_caller_first_session_reaches_opening_hello_nudge():
    session = _make_session("user")
    pipeline = _make_pipeline()

    await _run_until_silence_tick(session, pipeline)

    assert pipeline.synthesize_and_send_audio.await_args_list, (
        "caller-first session with a silent callee never nudged — "
        "_is_caller_first must be True for first_speaker='user'"
    )
    spoken_phrases = [
        call.args[1] for call in pipeline.synthesize_and_send_audio.await_args_list
    ]
    assert "Hello?" in spoken_phrases


@pytest.mark.asyncio
async def test_agent_first_session_still_suppresses_mid_nudge():
    """Same silent-callee scenario, but agent-first: should_suppress_mid_nudge
    must still swallow the nudge (the caller never spoke, so a MID nudge
    would be the first thing they hear from us — the 2026-07-08 bug this
    suppression exists for). Confirms the FIX 2 change to '== "user"' does
    not accidentally flip agent-first behaviour."""
    session = _make_session("agent")
    pipeline = _make_pipeline()

    await _run_until_silence_tick(session, pipeline)

    assert not pipeline.synthesize_and_send_audio.await_args_list, (
        "agent-first session with a caller who never spoke must not be "
        "nudged — should_suppress_mid_nudge should have swallowed it"
    )
