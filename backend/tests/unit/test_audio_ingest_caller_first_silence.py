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
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest
from app.domain.services.voice_pipeline.turn_director import OPENING_PHRASES

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
async def test_stale_backchannel_stamp_does_not_crash_silence_monitor():
    """F-17 — the crash site. turn_ender stamps ``session._last_backchannel_monotonic``
    (via ``time.monotonic()``) whenever it suppresses a backchannel; every
    ``_silence_monitor`` tick reads it back via
    ``(_now() - _bc_at) < 2.5``. The old code stamped an AWARE
    ``datetime.now(timezone.utc)`` but read it back with the monitor's NAIVE
    ``_now = datetime.utcnow`` — ``TypeError: can't subtract offset-naive and
    offset-aware datetimes`` on the very next tick. The `while
    session.stt_active:` loop had no enclosing try/except, so that TypeError
    silently killed the whole monitor task: no more silence nudges, no 60s
    auto-hangup, for the rest of the call.

    This drives the REAL ``_silence_monitor`` closure (not a reimplementation
    of the comparison) with a stale (10s-old) monotonic stamp on the session,
    exactly as turn_ender leaves it after suppressing a backchannel, and
    proves the monitor survived by observing it still reach its normal
    opening "Hello?" nudge. Before the fix this assertion fails outright —
    the monitor dies on tick 1 and never nudges.
    """
    session = _make_session("user")
    session._last_backchannel_monotonic = time.monotonic() - 10.0
    pipeline = _make_pipeline()

    await _run_until_silence_tick(session, pipeline)

    assert pipeline.synthesize_and_send_audio.await_args_list, (
        "silence monitor produced no nudge — it likely died on the "
        "_last_backchannel_monotonic freshness comparison"
    )
    spoken_phrases = [
        call.args[1] for call in pipeline.synthesize_and_send_audio.await_args_list
    ]
    assert "Hello?" in spoken_phrases


@pytest.mark.asyncio
async def test_opening_ladder_is_bounded_and_never_nags_past_its_cap():
    """2026-08-11 retune — a human re-checks a silent line 2-3 times, not
    forever. This collapses every opening timer to near-zero (including the
    new VOICE_OPENING_NUDGE_GAP_S repeat gap) with asyncio.sleep mocked out,
    so the loop runs far more ticks than VOICE_OPENING_MAX_NUDGES inside its
    1.5s wall-clock window — a regression back to an unbounded/forgotten cap
    would nudge many more than 3 times here. Proves the real
    ``_silence_monitor`` loop enforces the bound, not just that the pure
    ``silence_action`` decision alone would (it has no memory of prior
    nudges — see the NOTE in test_silence_action.py)."""
    session = _make_session("user")
    pipeline = _make_pipeline()

    ingest = AudioIngest(pipeline)
    with (
        patch.dict(
            os.environ,
            {
                "VOICE_OPENING_HELLO_S": "0.01",
                "VOICE_MID_NUDGE_S": "0.01",
                "VOICE_SILENCE_HANGUP_S": "30",
                "VOICE_NUDGE_MIN_GAP_S": "0.01",
                "VOICE_OPENING_NUDGE_GAP_S": "0.01",
                "VOICE_OPENING_MAX_NUDGES": "3",
            },
        ),
        # Collapse the monitor's tick sleep WITHOUT removing its YIELD.
        #
        # An AsyncMock here hangs this test indefinitely: awaiting it returns
        # without ever suspending, so with every timer collapsed the monitor
        # becomes a tight loop that never hands control back to the event
        # loop — and the `asyncio.wait_for` timeout below is a loop timer, so
        # it can never fire. `_instant_yield` awaits the REAL sleep (captured
        # at import, before this patch is installed, so it cannot recurse into
        # itself) with a zero delay: instant, but a genuine scheduling point.
        patch("asyncio.sleep", new=_instant_yield),
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

    spoken_phrases = [
        call.args[1] for call in pipeline.synthesize_and_send_audio.await_args_list
    ]
    assert 1 <= len(spoken_phrases) <= 3, (
        f"opening ladder nudged {len(spoken_phrases)} times — "
        f"VOICE_OPENING_MAX_NUDGES=3 was not enforced: {spoken_phrases}"
    )
    # Escalates through the real ladder in order, never repeating a rung or
    # inventing text outside it. Asserted against the live constant rather
    # than a hardcoded copy — the rung WORDING is a product decision that has
    # already changed twice; the ORDER and the CAP are what this test owns.
    assert spoken_phrases == list(OPENING_PHRASES)[: len(spoken_phrases)]


@pytest.mark.asyncio
async def test_agent_first_after_a_real_introduction_still_suppresses_mid_nudge():
    """The 2026-07-08 guard, intact. Agent-first, caller never spoke, and the
    agent HAS already introduced itself — a MID nudge here would make "I'm
    still here whenever you're ready" the second thing the prospect hears.
    should_suppress_mid_nudge must still swallow it."""
    session = _make_session("agent")
    session._has_introduced = True          # a full opener was delivered
    pipeline = _make_pipeline()

    await _run_until_silence_tick(session, pipeline)

    assert not pipeline.synthesize_and_send_audio.await_args_list, (
        "agent-first session with a caller who never spoke must not get a MID "
        "nudge — should_suppress_mid_nudge should have swallowed it"
    )


@pytest.mark.asyncio
async def test_agent_first_after_a_BARE_HELLO_does_get_re_greeted():
    """THE 2026-08-12 REGRESSION.

    Once turn 1 became a bare two-word pickup greeting, an agent-first call
    fell into a hole: `opening` required is_caller_first (False here) so the
    ladder never applied, AND should_suppress_mid_nudge fired (not caller-
    first, callee never spoke) so the mid nudge was skipped too. The agent
    said "Hi there." and went silent until the 60s hangup — reported live as
    "it stops after speaking one time, no follow up".

    A bare hello and the re-greet ladder are two halves of one design.
    """
    session = _make_session("agent")
    session._has_introduced = False         # only a bare hello was spoken
    pipeline = _make_pipeline()

    await _run_until_silence_tick(session, pipeline)

    spoken = [c.args[1] for c in pipeline.synthesize_and_send_audio.await_args_list]
    assert spoken, "a bare hello with no follow-up is dead air — must re-greet"
    # And it must be the OPENING ladder, NOT the needy MID phrase that the
    # suppression above exists to prevent.
    assert spoken[0] == OPENING_PHRASES[0], spoken
    assert "still here" not in spoken[0].lower(), (
        "the MID re-offer must never be the first thing a prospect hears"
    )
