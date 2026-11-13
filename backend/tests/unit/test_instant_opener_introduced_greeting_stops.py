"""Round-2 follow-up on issue #6 (review finding, 2026-09-24).

CURRENT CONTRACT (owner decision 2026-11-05, QA F12-03, 644750c2): the instant greeting was
disconnected (01195ba4). On a caller-first outbound call the callee's pickup words get the identity
opener, spoken through voice_session.pipeline; it ends on a question, so the model runs no second
turn on those words (turn_ender.py:465-486, caller_first_disclosure.py). Any other first words still
get the normal model turn. The tests below pin that; the narrative that follows is the history of the
double-reply defect they were first written for.

THE DEFECT
----------
Round 1 (test_instant_opener_continues_to_llm.py) made turn_ender.py fall
through into a normal LLM turn after the instant-opener greeting plays, so a
content-free "Hi, hello." pickup is followed by the identity/permission line
instead of silence. That fix was written for the *bare* pre-synth greeting
only.

But when TELEPHONY_LLM_OPENER_ENABLED is on, the pre-synth greeting is
LLM-authored and already carries identity AND ends in a single trailing
question (llm_opener.py's validated shape). `_send_outbound_greeting`
recognises this via `_looks_like_bare_pickup_greeting` and sets
`session._has_introduced = True` (agent_first.py ~680). The unconditional
fall-through then runs a *second* full LLM turn on the caller's SAME
"Hello?", speaking straight over the greeting's own question — the caller
never gets a chance to answer it.

THE FIX
-------
turn_ender.py now only falls through into the normal LLM turn when the
greeting that just played did NOT introduce the agent
(`session._has_introduced` still False, the bare "Hi, hello." case this
fallthrough was built for). When the greeting DID introduce
(`_has_introduced` True), the old behaviour is kept: return here and let the
callee reply to the question that was just asked.

Same harness/style as test_instant_opener_continues_to_llm.py: drives the
REAL TurnEnder.handle and REAL TurnRunner.run; only `_send_outbound_greeting`
(out of fence) and `_stream_llm_and_tts` (network call) are faked/stubbed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.turn_ender import TurnEnder
from app.domain.services.voice_pipeline.turn_runner import TurnRunner


def _session() -> CallSession:
    s = CallSession(
        call_id="6e0e221b-0000-0000-0000-000000000001",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="talky-out-2",
        system_prompt="sp",
        voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = "user"
    return s


class _FakeVoiceSession:
    def __init__(self, call_session: CallSession) -> None:
        self._presynth_greeting_audio = [b"chunk"]
        self._presynth_greeting_text = (
            "Hi, it's Alex from Dojo — got sixty seconds?"
        )
        self.call_id = call_session.call_id
        self.call_session = call_session


def _pipeline_stub(llm_reply="Sure, sixty seconds is fine."):
    p = MagicMock()
    p._is_repetitive_transcript = lambda text: False
    p._barge_in_events = {}
    p._pending_llm_tasks = {}
    p._turn_epochs = {}
    p._utterance_seq = {}
    p._is_ask_ai_session = lambda s: False
    p._supports_llm_end_session_action = lambda s: True
    p.latency_tracker = MagicMock()
    p.latency_tracker.get_metrics.return_value = None
    p._stream_llm_and_tts = AsyncMock(return_value=(llm_reply, 10.0, 10.0))
    p._run_turn = AsyncMock(side_effect=TurnRunner(p).run)
    p.synthesize_and_send_audio = AsyncMock(return_value=False)
    return p


@pytest.mark.asyncio
async def test_an_introducing_greeting_returns_instead_of_running_a_second_llm_turn(
    monkeypatch,
):
    """The identity opener answers the pickup words and ends on a question —
    the model must NOT also run a turn on the SAME "Hello?"
    (turn_ender.py:465-486; the opener is built by caller_first_disclosure.py:101-107,
    644750c2 / QA F12-03, which replaced the instant-opener greeting)."""
    session = _session()
    voice_session = _FakeVoiceSession(session)
    session._voice_session_ref = voice_session
    pipeline = _pipeline_stub()

    voice_session.pipeline = pipeline
    await TurnEnder(pipeline).handle(session, None, user_text="Hello?")

    assert pipeline._run_turn.await_args is None, (
        "the identity opener answers the pickup words; the model must not also reply"
    )
    spoken = pipeline.synthesize_and_send_audio.await_args.args[1]
    assert "an AI assistant from" in spoken
    # The caller's words come first, then the opener (caller_first_disclosure.py:89, 106).
    assert [(m.role, m.content) for m in session.conversation_history] == [
        (MessageRole.USER, "Hello?"), (MessageRole.ASSISTANT, spoken),
    ]
    assert session._has_introduced is True  # caller_first_disclosure.py:107


@pytest.mark.asyncio
async def test_a_bare_greeting_still_falls_through_to_the_llm_turn(monkeypatch):
    """Only pickup words get the identity opener (caller_first_disclosure.py:30-49, 75-77);
    anything else on a caller-first call still gets the normal model turn."""
    session = _session()
    voice_session = _FakeVoiceSession(session)
    session._voice_session_ref = voice_session
    pipeline = _pipeline_stub()

    voice_session.pipeline = pipeline
    # 644750c2: only pickup words get the identity opener; anything else gets the normal model turn.
    await TurnEnder(pipeline).handle(session, None, user_text="I am busy right now, what is this about?")

    assert pipeline._run_turn.await_args is not None, (
        "first words that are not pickup words must still reach the LLM turn"
    )
    roles = [m.role for m in session.conversation_history]
    assert roles[:2] == [MessageRole.USER, MessageRole.ASSISTANT]
