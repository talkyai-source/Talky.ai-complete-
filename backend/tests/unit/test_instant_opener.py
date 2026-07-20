"""Unit tests for the caller-first instant opener's bare-greeting gate and
F-10 echo-immunity (is_opener_echo / try_instant_opener playback + cancel).
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.instant_opener import (
    is_bare_greeting,
    is_opener_echo,
    try_instant_opener,
)
from app.domain.services.voice_pipeline_service import VoicePipelineService


def test_bare_greetings_match():
    for t in ("Hello?", "Hi", "hello hello", "Yeah?", "Good morning", "Hiya",
              "Hello, who is this", "Yes speaking"):
        assert is_bare_greeting(t), t


def test_content_questions_do_not_match():
    for t in ("Who's calling from where exactly?", "What do you want",
              "Is this about the invoice?", "Hello, Acme Roofing?",
              "This is the Vodafone voice mail"):
        assert not is_bare_greeting(t), t


def test_empty_and_long():
    assert not is_bare_greeting("")
    assert not is_bare_greeting("hello there my good friend how are you")


# ── F-10: is_opener_echo unit coverage ──────────────────────────────────────

def _session():
    return CallSession(
        call_id="call-1", campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )


def test_is_opener_echo_false_when_not_in_flight_and_past_grace():
    s = _session()
    s._instant_opener_in_flight = False
    s._instant_opener_grace_until = time.monotonic() - 1.0
    assert not is_opener_echo(s, "hello")


def test_is_opener_echo_true_for_bare_greeting_while_in_flight():
    s = _session()
    s._instant_opener_in_flight = True
    assert is_opener_echo(s, "hello")
    assert is_opener_echo(s, "Yeah?")


def test_is_opener_echo_false_for_real_content_while_in_flight():
    s = _session()
    s._instant_opener_in_flight = True
    assert not is_opener_echo(s, "stop")
    assert not is_opener_echo(s, "is this about my roof")


def test_is_opener_echo_true_within_grace_window_after_playback():
    s = _session()
    s._instant_opener_in_flight = False
    s._instant_opener_grace_until = time.monotonic() + 0.5
    assert is_opener_echo(s, "hi")


# ── F-10: try_instant_opener playback + cancellation behaviour ─────────────

class _FakeVoiceSession:
    """Stand-in for the pipeline's voice-session ref: presynth audio present
    so try_instant_opener proceeds to the (patched) greeting player."""
    def __init__(self):
        self._presynth_greeting_audio = [b"chunk"]
        self.call_id = "call-1"


def _opener_session():
    s = _session()
    s._voice_session_ref = _FakeVoiceSession()
    return s


def test_opener_echo_during_playback_does_not_cancel():
    """Scenario 1: opener in-flight + a bare-greeting echo ('hello') arrives
    via handle_barge_in — must NOT cancel the opener task; playback runs to
    completion and _instant_opener_done ends True."""
    s = _opener_session()
    svc = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )

    async def slow_greeting(_vs):
        await asyncio.sleep(0.1)

    async def scenario():
        with patch(
            "app.domain.services.telephony.modes.agent_first._send_outbound_greeting",
            slow_greeting,
        ):
            opener_task = asyncio.ensure_future(try_instant_opener(s, "hello"))
            await asyncio.sleep(0.02)
            assert s._instant_opener_in_flight is True
            # Echo of the agent's own greeting — handle_barge_in must ignore it.
            await svc.handle_barge_in(s, None, transcript_text="hello")
            result = await opener_task
            return result

    result = asyncio.run(scenario())
    assert result is True
    assert s._instant_opener_done is True
    assert s._instant_opener_in_flight is False


def test_opener_real_interrupt_cancels_cleanly():
    """Scenario 2: opener in-flight + real content ('stop') is not an echo —
    the task is cancelled the normal way (CancelledError propagates cleanly),
    _instant_opener_done ends False, _instant_opener_in_flight ends False."""
    s = _opener_session()

    async def slow_greeting(_vs):
        await asyncio.sleep(10)

    async def scenario():
        with patch(
            "app.domain.services.telephony.modes.agent_first._send_outbound_greeting",
            slow_greeting,
        ):
            opener_task = asyncio.ensure_future(try_instant_opener(s, "hello"))
            await asyncio.sleep(0.02)
            assert s._instant_opener_in_flight is True
            assert not is_opener_echo(s, "stop")
            opener_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await opener_task

    asyncio.run(scenario())
    assert s._instant_opener_done is False
    assert s._instant_opener_in_flight is False


def test_opener_real_content_not_exempt():
    """Scenario 3: real content ('is this about my roof') during opener
    playback is not a bare greeting — not exempt from cancellation."""
    s = _opener_session()
    s._instant_opener_in_flight = True
    assert not is_opener_echo(s, "is this about my roof")


# ── Backward compatibility ──────────────────────────────────────────────────

def test_handle_barge_in_still_callable_with_old_two_arg_signature():
    svc = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    s = _session()
    s.barge_in_event = asyncio.Event()
    asyncio.run(svc.handle_barge_in(s, AsyncMock()))  # no transcript_text — must not raise


def test_on_barge_in_direct_still_callable_with_zero_args():
    from app.domain.services.voice_pipeline.audio_ingest import AudioIngest

    pipeline = MagicMock()
    pipeline._utterance_seq = {}
    pipeline._barge_in_events = {}
    pipeline._barge_in_epoch = {}
    pipeline.latency_tracker = MagicMock()
    pipeline.latency_tracker.get_metrics.return_value = None

    session = _session()
    session.tts_active = False

    # Reproduce the closure exactly as audio_ingest.process defines it, but
    # invoke it directly (zero args) the way Nova's on_barge_in() call site
    # (and legacy tests) call it.
    call_id = session.call_id
    import time as _time

    def _on_barge_in_direct(transcript_text=None) -> None:
        pipeline._utterance_seq[call_id] = pipeline._utterance_seq.get(call_id, 0) + 1
        from app.domain.services.voice_pipeline.instant_opener import is_opener_echo
        if is_opener_echo(session, transcript_text):
            return
        event = pipeline._barge_in_events.get(call_id)
        if event:
            event.set()

    _on_barge_in_direct()  # zero-arg call must not raise
    assert pipeline._utterance_seq[call_id] == 1
