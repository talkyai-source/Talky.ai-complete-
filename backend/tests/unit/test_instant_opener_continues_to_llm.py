"""The instant opener must not leave a bare "Hello?" with nothing but itself.

THE DEFECT (production, 2026-09-23, call 6e0e221b)
------------------------------------------------------
The callee answered with "Hello?" (18:09:26.57). instant_opener.py played the
pre-synthesised greeting ("Hi, hello."/"Hey there." — 10 characters, per
`outbound_greeting_presynth_done`), and turn_ender.py's instant-opener branch
then RETURNED, skipping the LLM turn entirely for that utterance. Since the
2026-08-11 opener redesign the pre-synth greeting carries no name and no
reason for the call, so the callee got nothing else: 15.7s of silence
followed, until they hung up at 18:09:45.7 (`ChannelHangupRequest`).

THE FIX
-------
After the instant greeting plays, turn_ender.py falls through into the
normal LLM turn for the SAME utterance instead of returning. instant_opener's
own pre-emptive history append (which used to make it the terminal action for
the turn) is removed — the greeting text is already in
conversation_history (appended by `_send_outbound_greeting`), so the LLM
turn's own history management appends the caller's utterance immediately
AFTER it, in the same assistant/user order a real agent-first turn 1 uses.
That is exactly what already drives the "you haven't introduced yourself
yet" prompt path proven on agent-first calls (`_has_introduced=False`), so
the model continues with identity/permission instead of repeating "hello".

Drives the REAL TurnEnder.handle AND the REAL TurnRunner.run — the "no
duplicate, no reordering" claim lives inside run()'s own history management,
so that must not be mocked out. Only `_stream_llm_and_tts` (the actual
LLM/TTS network call, several layers further down) is stubbed.
`_send_outbound_greeting` itself (telephony/modes/agent_first.py, out of
this fix's fence) is replaced with a fake that reproduces only its
documented history/has_introduced contract — the rest of that function is
real-audio/media-gateway plumbing unrelated to this fix.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.turn_ender import TurnEnder
from app.domain.services.voice_pipeline.turn_runner import TurnRunner


def _session() -> CallSession:
    s = CallSession(
        call_id="6e0e221b-0000-0000-0000-000000000000",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="talky-out-1",
        system_prompt="sp",
        voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = "user"
    return s


class _FakeVoiceSession:
    """Presynth audio present, so try_instant_opener proceeds to the
    (faked) greeting player — mirrors test_instant_opener.py's own
    _FakeVoiceSession."""

    def __init__(self, call_session: CallSession) -> None:
        self._presynth_greeting_audio = [b"chunk"]
        self._presynth_greeting_text = "Hi, hello."
        self.call_id = call_session.call_id
        self.call_session = call_session


async def _fake_send_outbound_greeting(voice_session) -> None:
    """Reproduces exactly the two documented effects try_instant_opener
    relies on from the real `_send_outbound_greeting` (agent_first.py):
    the spoken greeting is appended to history as an ASSISTANT turn, and
    `_has_introduced` reflects that it was a bare pickup — everything else
    in that function is telephony/media-gateway plumbing this fix does not
    touch."""
    session = voice_session.call_session
    session.conversation_history.append(
        Message(role=MessageRole.ASSISTANT, content=voice_session._presynth_greeting_text)
    )
    session._has_introduced = False


def _pipeline_stub(llm_reply="Alex here from Dojo — got a minute?"):
    """`_run_turn` is the REAL TurnRunner.run bound to this stub — the "no
    duplicate, no reordering" claim under test lives inside its own history
    management, so it must not be mocked out. Only `_stream_llm_and_tts`
    (the actual LLM/TTS network call several layers further down) is
    stubbed."""
    p = MagicMock()
    p._is_repetitive_transcript = lambda text: False
    p._barge_in_events = {}
    p._pending_llm_tasks = {}
    p._turn_epochs = {}
    p._utterance_seq = {}
    p._is_ask_ai_session = lambda s: False
    p._supports_llm_end_session_action = lambda s: True
    # Real dict/None returns for the post-`_run_turn` telemetry block, which
    # otherwise compares a MagicMock attribute against an int and raises —
    # unrelated to this fix, just scaffolding so the turn completes cleanly.
    p.latency_tracker = MagicMock()
    p.latency_tracker.get_metrics.return_value = None
    p._stream_llm_and_tts = AsyncMock(return_value=(llm_reply, 10.0, 10.0))
    p._run_turn = AsyncMock(side_effect=TurnRunner(p).run)
    p.synthesize_and_send_audio = AsyncMock(return_value=False)
    return p


@pytest.mark.asyncio
async def test_bare_hello_gets_the_instant_greeting_and_a_real_llm_turn(monkeypatch):
    session = _session()
    voice_session = _FakeVoiceSession(session)
    session._voice_session_ref = voice_session
    pipeline = _pipeline_stub()

    monkeypatch.setattr(
        "app.domain.services.telephony.modes.agent_first._send_outbound_greeting",
        _fake_send_outbound_greeting,
    )

    await TurnEnder(pipeline).handle(session, None, user_text="Hello?")

    assert pipeline._run_turn.await_args is not None, (
        "the LLM turn never ran after the instant greeting played"
    )
    called_transcript = pipeline._run_turn.await_args.args[1]
    assert called_transcript == "Hello?"

    roles = [m.role for m in session.conversation_history]
    assert roles[:2] == [MessageRole.ASSISTANT, MessageRole.USER], (
        f"expected greeting-then-user (no duplicate, no reordering), got {roles!r}"
    )
    assert session.conversation_history[0].content == "Hi, hello."
    assert session.conversation_history[1].content == "Hello?"


@pytest.mark.asyncio
async def test_a_failed_instant_opener_still_falls_back_to_the_llm_turn(monkeypatch):
    """No presynth audio available -> try_instant_opener no-ops (returns
    False) -> the normal LLM turn must still run, exactly as before."""
    session = _session()
    session._voice_session_ref = None  # no presynth audio -> try_instant_opener no-ops
    pipeline = _pipeline_stub()

    await TurnEnder(pipeline).handle(session, None, user_text="Hello?")

    assert pipeline._run_turn.await_args is not None, (
        "a failed instant opener must still fall through to the LLM turn"
    )
    roles = [m.role for m in session.conversation_history]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT], (
        "no instant greeting played, so the normal turn's own USER-then-"
        f"ASSISTANT append is the only history activity — got {roles!r}"
    )
    assert session.conversation_history[0].content == "Hello?"


@pytest.mark.asyncio
async def test_a_real_question_never_triggers_the_instant_opener(monkeypatch):
    """'Who's this?' is not a bare greeting -- must go straight to the LLM,
    with no instant-opener greeting at all."""
    session = _session()
    voice_session = _FakeVoiceSession(session)
    session._voice_session_ref = voice_session
    pipeline = _pipeline_stub()

    called = {"n": 0}

    async def _spy_greeting(vs):
        called["n"] += 1
        await _fake_send_outbound_greeting(vs)

    monkeypatch.setattr(
        "app.domain.services.telephony.modes.agent_first._send_outbound_greeting",
        _spy_greeting,
    )

    await TurnEnder(pipeline).handle(session, None, user_text="Who's this?")

    assert called["n"] == 0, "a real question must not trigger the instant opener"
    assert pipeline._run_turn.await_args is not None
    roles = [m.role for m in session.conversation_history]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]
