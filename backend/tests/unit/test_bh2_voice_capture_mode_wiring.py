"""bh2 voice-pipeline-8: STT capture mode must be wired into the live turn path.

When the agent asks for (or reads back) an email, the caller's next turn is a
slow, pause-heavy spell-out. ``capture_mode.maybe_enter`` relaxes the STT's
end-of-turn detection for that one turn and ``maybe_exit`` restores it. Commit
01195ba4 ("refactor: record contacts through model tools") deleted the only two
call sites from ``TurnRunner.run``, so nothing armed it any more: the spell-out
was end-pointed at the first pause and half an address reached the model.

These tests drive the real turn path (``handle_turn_end`` -> ``_run_turn`` ->
``TurnRunner.run``) with a fake STT provider that records the calls the
pipeline makes. They do not exercise ``capture_mode`` in isolation (that is
covered by test_capture_mode.py).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline import capture_mode
from app.domain.services.voice_pipeline_service import VoicePipelineService

CALL_ID = "call-bh8-capture"


class _FluxLike:
    """The two methods DeepgramFlux exposes for capture mode, recorded in order."""

    name = "flux-fake"

    def __init__(self, log):
        self.log = log

    def enter_capture_mode(self, call_id):
        self.log.append(("enter", call_id))

    def reset_capture_mode(self, call_id):
        self.log.append(("reset", call_id))


def _service(stt):
    svc = VoicePipelineService(
        stt_provider=stt,
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=MagicMock(),
        mute_during_tts=False,
    )
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    return svc


def _session():
    s = CallSession(
        call_id=CALL_ID, campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )
    s.barge_in_event = asyncio.Event()
    # A prior exchange, so the turn-0 floor does not apply to these turns.
    s.conversation_history.append(Message(role=MessageRole.USER, content="earlier"))
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="reply"))
    return s


class _Call:
    """One call: caller turns go through the real handle_turn_end path, the
    model's reply is scripted, and what the STT had been told at the moment the
    model ran is recorded (so ordering is observable, not just the end state)."""

    def __init__(self, stt, log):
        self.log = log
        self.svc = _service(stt)
        self.session = _session()
        self.at_model = []

    async def turn(self, caller_says, agent_replies):
        async def model(session, websocket=None):
            self.at_model.append(list(self.log))
            return agent_replies, 1.0, 1.0

        self.svc._stream_llm_and_tts = model
        await self.svc.handle_turn_end(
            self.session, None, source="final", user_text=caller_says,
        )


@pytest.fixture(autouse=True)
def _clean_capture_state():
    capture_mode.clear(CALL_ID)
    yield
    capture_mode.clear(CALL_ID)


async def test_an_email_ask_arms_capture_mode_and_the_next_caller_turn_reverts_it():
    log = []
    call = _Call(_FluxLike(log), log)

    await call.turn("I would like a quote", "Sure. What's your email address?")
    assert log == [("enter", CALL_ID)]
    assert capture_mode.is_capture_active(CALL_ID)
    assert call.at_model == [[]]  # nothing armed before the ask was spoken

    await call.turn("j o h n at gmail dot com", "Thanks, I have that.")
    assert log == [("enter", CALL_ID), ("reset", CALL_ID)]
    # The revert happens as the spell-out turn ARRIVES, before the model answers
    # it, so the caller's following turn is back on normal turn-detection.
    assert call.at_model[1] == [("enter", CALL_ID), ("reset", CALL_ID)]
    assert not capture_mode.is_capture_active(CALL_ID)


async def test_an_email_readback_for_confirmation_also_arms_capture_mode():
    """A correction to a read-back address is the same slow spell-out."""
    log = []
    call = _Call(_FluxLike(log), log)

    await call.turn(
        "it is john at gmail dot com",
        "So that's john at gmail dot com, did I get that right?",
    )

    assert log == [("enter", CALL_ID)]
    assert capture_mode.is_capture_active(CALL_ID)


async def test_a_reply_that_asks_for_nothing_leaves_the_stt_alone():
    log = []
    call = _Call(_FluxLike(log), log)

    await call.turn("what are your hours", "We are open nine to five, Monday to Friday.")

    assert log == []
    assert not capture_mode.is_capture_active(CALL_ID)


async def test_capture_mode_reaches_the_flux_behind_the_resilient_stt_wrapper():
    """Production hands the pipeline a ResilientSTTProvider, not the Flux itself."""
    from app.domain.services.resilient_stt import ResilientSTTProvider

    log = []
    call = _Call(ResilientSTTProvider(primary=_FluxLike(log)), log)

    await call.turn("go on", "Could you spell your email for me?")

    assert log == [("enter", CALL_ID)]


async def test_the_real_flux_provider_is_reconfigured_by_the_turn_path_then_restored():
    """End to end against the real provider object: what the turn path asks of
    it is the Configure that Flux will receive (relaxed, then the call's own
    thresholds back)."""
    from app.infrastructure.stt.deepgram_flux import (
        CAPTURE_EOT_THRESHOLD,
        CAPTURE_EOT_TIMEOUT_MS,
        DeepgramFluxSTTProvider,
    )

    flux = DeepgramFluxSTTProvider()
    await flux.initialize({"api_key": "k", "eot_threshold": 0.6, "eot_timeout_ms": 800})
    call = _Call(flux, [])

    await call.turn("go on", "What's your email address?")
    relaxed = flux._pending_config[CALL_ID]["thresholds"]
    assert relaxed["eot_timeout_ms"] == CAPTURE_EOT_TIMEOUT_MS
    assert relaxed["eot_threshold"] == CAPTURE_EOT_THRESHOLD

    await call.turn("j o h n at gmail dot com", "Thanks, I have that.")
    restored = flux._pending_config[CALL_ID]["thresholds"]
    assert restored["eot_timeout_ms"] == 800
    assert restored["eot_threshold"] == 0.6


async def test_pipeline_teardown_clears_the_calls_capture_state():
    log = []
    call = _Call(_FluxLike(log), log)
    await call.turn("go on", "What's your email address?")
    assert capture_mode.is_capture_active(CALL_ID)

    # The STT loop ends (hangup): start_pipeline's per-call cleanup runs.
    call.svc.process_audio_stream = AsyncMock(return_value=None)
    await call.svc.start_pipeline(call.session)

    assert not capture_mode.is_capture_active(CALL_ID)


async def test_teardown_clears_state_for_a_call_that_ended_while_armed():
    """The cleanup on its own, independent of how the call got armed: the
    tracking is module-level, so without it a call that hung up right after an
    email ask stays 'active' forever (and a failover would re-arm its id)."""
    log = []
    call = _Call(_FluxLike(log), log)
    capture_mode.maybe_enter(call.svc.stt_provider, CALL_ID, "What's your email address?")
    assert capture_mode.is_capture_active(CALL_ID)

    call.svc.process_audio_stream = AsyncMock(return_value=None)
    await call.svc.start_pipeline(call.session)

    assert not capture_mode.is_capture_active(CALL_ID)
    assert log == [("enter", CALL_ID)]  # tracking dropped; no Configure sent to a dead socket
