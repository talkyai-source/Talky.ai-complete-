"""Every incoming call's first line tells the caller it is an AI (21 Nov 2026).

The saved greeting, an after-hours message and the persona answering lines were
spoken as written; none had to say it. Outbound always does (build_identity_opener)
and is unchanged. A first line that was cut off is not recorded as said.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.agent_config import AgentConfig
from app.domain.models.session import CallSession, CallState
from app.domain.services.telephony.config import _build_call_greeting
from app.domain.services.telephony.modes import agent_first
from app.domain.services.voice_pipeline import silent_pickup
from app.domain.services.voice_pipeline.inbound_answer_greeting import (
    build_inbound_answer_greeting,
    ensure_ai_disclosure,
    says_it_is_ai,
)


class _Pipeline:
    def __init__(self, interrupted=False):
        self.spoken = []
        self._interrupted = interrupted
        self.transcript_service = None

    def clear_barge_in_event(self, session):
        pass

    async def synthesize_and_send_audio(self, session, text, websocket=None, **k):
        self.spoken.append(text)
        return self._interrupted


def _voice_session(*, direction="inbound", saved=None, agent="Sarah", interrupted=False,
                   persona=None):
    s = CallSession(
        call_id="b03611e1-1c3a-4707-ab3b-98d12f4930fe",
        campaign_id="d72a4d5c-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-in-1",
        system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = "agent"
    s._call_direction = direction
    if persona:
        s.persona_type = persona
    s.agent_config = AgentConfig.model_construct(agent_name=agent, company_name="Smith Plumbing")
    return SimpleNamespace(
        call_id=s.call_id, call_session=s, config=None,
        _call_direction=direction, _inbound_admission={"call_id": s.call_id},
        _inbound_selected_action="agent", _pinned_inbound_greeting=saved,
        pipeline=_Pipeline(interrupted), pipeline_task=object(), realtime_bridge=None,
        media_gateway=SimpleNamespace(), _presynth_greeting_audio=None,
        _presynth_greeting_text=None,
    )


@pytest.fixture(autouse=True)
def _no_notice(monkeypatch):
    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", AsyncMock())
    monkeypatch.setattr(silent_pickup, "_INBOUND_SETTLE_S", 0.01)
    monkeypatch.setattr(silent_pickup, "_INBOUND_READY_POLL_S", 0.01)


def _speak(vs):
    asyncio.run(agent_first._send_outbound_greeting(vs))
    return vs.pipeline.spoken


def _count_ai(text):
    return text.lower().count("an ai assistant")


SAVED = "Thank you for calling Smith Plumbing, how can I help?"


def test_saved_greeting_without_ai_wording_gets_the_disclosure_once():
    saved = "Thank you for calling Smith Plumbing. We are open until five. How can I help?"
    spoken = _speak(_voice_session(saved=saved))
    assert len(spoken) == 1
    line = spoken[0]
    assert _count_ai(line) == 1
    # before the question; every saved word still there, in order
    assert line == ("Thank you for calling Smith Plumbing. We are open until five. "
                    "This is Sarah, an AI assistant. How can I help?")


def test_one_sentence_saved_greeting_keeps_its_words_in_order():
    line = _speak(_voice_session(saved=SAVED))[0]
    assert _count_ai(line) == 1
    assert line.endswith(SAVED) and "This is Sarah, an AI assistant." in line


@pytest.mark.parametrize("saved", [
    "Hello, this is Sarah, your AI assistant at Smith Plumbing. How can I help?",
    "Smith Plumbing, you are speaking with an automated assistant. What do you need?",
    "Thanks for calling. I'm a virtual assistant for Smith Plumbing. How can I help?",
    "You've reached the artificial intelligence receptionist. How can I help?",
])
def test_saved_greeting_that_already_says_it_is_left_exactly_as_saved(saved):
    assert says_it_is_ai(saved)
    assert _speak(_voice_session(saved=saved)) == [saved]


@pytest.mark.parametrize("saved", [
    "Thanks for calling Smith Plumbing, we use AI-powered scheduling. How can I help?",
    "Smith Plumbing, automated reminders are on. How can I help?",
])
def test_a_passing_mention_of_ai_is_not_a_disclosure(saved):
    assert not says_it_is_ai(saved)
    assert "an AI assistant" in _speak(_voice_session(saved=saved))[0]


def test_no_agent_name_says_an_ai_assistant_and_invents_no_name():
    line = _speak(_voice_session(saved=SAVED, agent=""))[0]
    assert "This is an AI assistant." in line
    assert "This is ," not in line and "your assistant" not in line.lower()


def test_persona_greeting_for_an_incoming_call_carries_the_disclosure():
    for persona in ("receptionist", "customer_support", "lead_gen", None):
        for _ in range(6):  # the persona picks a random variant
            line = _speak(_voice_session(saved=None, persona=persona))[-1]
            assert _count_ai(line) == 1, line


def test_silent_pickup_without_a_saved_greeting_speaks_the_default_line_as_today():
    vs = _voice_session(saved=None)
    vs.call_session._first_speaker = "user"
    expected = build_inbound_answer_greeting(vs.call_session)
    assert asyncio.run(silent_pickup.run_silent_pickup(vs, wait_s=0.01)) is True
    assert vs.pipeline.spoken == [expected]
    assert ensure_ai_disclosure(expected, vs.call_session) == expected


def test_silent_pickup_with_a_saved_greeting_without_ai_wording_adds_it():
    vs = _voice_session(saved=SAVED)
    vs.call_session._first_speaker = "user"
    assert asyncio.run(silent_pickup.run_silent_pickup(vs, wait_s=0.01)) is True
    assert _count_ai(vs.pipeline.spoken[0]) == 1


def test_outbound_first_line_is_unchanged():
    vs = _voice_session(direction="outbound", saved=None)
    expected = _build_call_greeting(vs.call_session, first_speaker="agent")
    assert _speak(vs) == [expected]
    assert expected.startswith("Hi, this is Sarah, an AI assistant from Smith Plumbing")


def test_outbound_pinned_text_without_ai_wording_is_not_touched():
    vs = _voice_session(direction="outbound", saved="Hello there.")
    assert _speak(vs) == ["Hello there."]


# F-B: an interrupted first line is not recorded as delivered.
def test_interrupted_incoming_greeting_is_not_written_to_history_and_introduction_stays_owed():
    vs = _voice_session(saved=SAVED, interrupted=True)
    _speak(vs)
    s = vs.call_session
    assert [m.content for m in s.conversation_history] == []
    assert getattr(s, "_has_introduced", False) is not True


def test_delivered_incoming_greeting_is_written_to_history_with_the_disclosure():
    vs = _voice_session(saved=SAVED, interrupted=False)
    _speak(vs)
    s = vs.call_session
    assert [m.content for m in s.conversation_history] == vs.pipeline.spoken
    assert "an AI assistant" in s.conversation_history[0].content
    assert s._has_introduced is True


def test_interrupted_outbound_slow_path_is_unchanged():
    vs = _voice_session(direction="outbound", saved="Hello there, this is a longer opener today.",
                        interrupted=True)
    _speak(vs)
    assert [m.content for m in vs.call_session.conversation_history] == vs.pipeline.spoken


def test_disclosure_is_not_put_inside_an_abbreviation():
    none = SimpleNamespace(agent_config=None)
    assert ensure_ai_disclosure("Dr. Smith's office, how can I help?", none) == (
        "This is an AI assistant. Dr. Smith's office, how can I help?")
    assert ensure_ai_disclosure("Welcome to Dr. Lee's clinic. How can I help?", none) == (
        "Welcome to Dr. Lee's clinic. This is an AI assistant. How can I help?")


# The line already introduces the agent by the name on file: say it once.
from app.domain.services.telephony_session_config import _PERSONA_GREETINGS  # noqa: E402

_PERSONA_CASES = [(p, i) for p, d in _PERSONA_GREETINGS.items() for i in range(len(d["inbound"]))]
_PERSONA_CASES += [(None, i) for i in range(3)]  # build_telephony_inbound_greeting's variants
_EXPECTED = {
    ("lead_gen", 0): "Hi, this is Sarah, an AI assistant from Smith Plumbing -- thanks for reaching out. How can I help?",
    ("lead_gen", 1): "Hey, Sarah, an AI assistant, here from Smith Plumbing. What can I help you with today?",
    ("customer_support", 0): "Thanks for calling Smith Plumbing -- this is Sarah, an AI assistant, how can I help?",
    ("customer_support", 1): "Hi, Sarah, an AI assistant from Smith Plumbing support. What can I do for you?",
    ("receptionist", 0): "Thank you for calling Smith Plumbing. This is Sarah, an AI assistant -- how can I help you today?",
    ("receptionist", 1): "Hi, Smith Plumbing -- Sarah, an AI assistant, speaking. How can I help?",
    (None, 0): "Hello, Smith Plumbing, this is Sarah, an AI assistant -- how can I help you?",
    (None, 1): "Thanks for calling Smith Plumbing. Sarah, an AI assistant, here -- what can I do for you?",
    (None, 2): "Hi, Smith Plumbing -- Sarah, an AI assistant, speaking. How can I help you?",
}


@pytest.mark.parametrize("persona,index", _PERSONA_CASES)
def test_every_incoming_persona_template_says_the_name_once_with_ai_after_it(monkeypatch, persona, index):
    import random
    monkeypatch.setattr(random, "choice", lambda seq: seq[index])
    vs = _voice_session(saved=None, persona=persona)
    line = _speak(vs)[0]
    assert line == _EXPECTED[(persona, index)]
    assert line.count("Sarah") == 1 and _count_ai(line) == 1


def test_saved_greeting_naming_the_agent_gets_the_short_form():
    line = _speak(_voice_session(saved="You've reached Maya at Acme support. How can I help?", agent="Maya"))[0]
    assert line == "You've reached Maya, an AI assistant at Acme support. How can I help?"
    assert line.count("Maya") == 1


def test_a_different_name_than_the_one_on_file_keeps_the_added_sentence():
    line = _speak(_voice_session(saved="Hi, this is Tom from Smith Plumbing. How can I help?", agent="Sarah"))[0]
    assert line == "Hi, this is Tom from Smith Plumbing. This is Sarah, an AI assistant. How can I help?"


def test_name_match_is_case_insensitive_and_a_company_name_is_not_a_cue():
    assert ensure_ai_disclosure("I'M SARAH, how can I help?", SimpleNamespace(
        agent_config=SimpleNamespace(agent_name="Sarah"))) == "I'M SARAH, an AI assistant, how can I help?"
    out = ensure_ai_disclosure("Thanks for calling Sarah's Plumbing. How can I help?", SimpleNamespace(
        agent_config=SimpleNamespace(agent_name="Sarah")))
    assert out == "Thanks for calling Sarah's Plumbing. This is Sarah, an AI assistant. How can I help?"


# -- An incoming caller who speaks first: who this is, before the model's reply --
from app.domain.models.conversation import Message, MessageRole  # noqa: E402
from app.domain.services.voice_pipeline.caller_first_disclosure import (  # noqa: E402
    speak_disclosure_before_first_reply,
)
from app.domain.services.voice_pipeline.inbound_answer_greeting import (  # noqa: E402
    build_inbound_identity_line,
)


class _CFPipe:
    def __init__(self, interrupts=()):
        self.spoken = []
        self._interrupts = list(interrupts)
        self.transcript_service = SimpleNamespace(accumulate_turn=lambda **_: None)

    async def synthesize_and_send_audio(self, session, text, websocket=None, **_):
        self.spoken.append(text)
        return self._interrupts.pop(0) if self._interrupts else False


def _cf(direction="inbound", agent="Sarah", company="Smith Plumbing", interrupts=(), notice_owed=False):
    from tests.unit.test_first_line_guard import _session
    s = _session(tts_active=False, started_ago=None)
    s._call_direction = direction
    s.agent_config = SimpleNamespace(agent_name=agent, company_name=company)
    vs = SimpleNamespace(call_session=s, pipeline=_CFPipe(interrupts),
                         _recording_disclosure_attempted=not notice_owed)
    s._voice_session_ref = vs
    return s, vs


@pytest.fixture
def notice(monkeypatch):
    """A recording notice that really speaks and records, like the real one."""
    async def speak(voice_session, *, opener_with_notice=None):
        if voice_session._recording_disclosure_attempted:
            return
        voice_session._recording_disclosure_attempted = True
        voice_session.pipeline.spoken.append("This call is recorded.")
        voice_session.call_session.conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content="This call is recorded."))
    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", speak)


IDENT = "Thank you for calling Smith Plumbing. This is Sarah, an AI assistant."


@pytest.mark.asyncio
async def test_caller_says_hello_the_identity_line_comes_before_the_models_reply(notice):
    s, vs = _cf()
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False   # the model replies next
    assert vs.pipeline.spoken == [IDENT]
    assert [m.content for m in s.conversation_history] == [IDENT]
    assert s._has_introduced is True


@pytest.mark.asyncio
async def test_notice_owed_is_spoken_first_then_the_identity_line_each_once(notice):
    s, vs = _cf(notice_owed=True)
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert vs.pipeline.spoken == ["This call is recorded.", IDENT]
    assert [m.content for m in s.conversation_history] == ["This call is recorded.", IDENT]
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert vs.pipeline.spoken == ["This call is recorded.", IDENT]


@pytest.mark.asyncio
async def test_after_a_delivered_greeting_that_said_it_there_is_no_identity_line(notice):
    s, vs = _cf()
    s._has_introduced = True          # silent pick-up / agent-first greeting delivered, with the disclosure
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="Hello. This is an AI assistant."))
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert vs.pipeline.spoken == []


@pytest.mark.asyncio
async def test_interrupted_identity_line_is_not_recorded_and_stays_owed_and_is_not_replayed(notice):
    s, vs = _cf(interrupts=[True])
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert vs.pipeline.spoken == [IDENT]
    assert s.conversation_history == [] and getattr(s, "_has_introduced", False) is False
    # Same as outbound (_caller_first_disclosure_done): one try per call, no restart over the caller.
    s.conversation_history.append(Message(role=MessageRole.USER, content="Hello?"))
    assert await speak_disclosure_before_first_reply(s, "I said hello") is False
    assert vs.pipeline.spoken == [IDENT]


def test_no_agent_name_and_no_company_name_invent_nothing():
    only_co = SimpleNamespace(agent_config=SimpleNamespace(agent_name="", company_name="Smith Plumbing"))
    assert build_inbound_identity_line(only_co) == "Thank you for calling Smith Plumbing. This is an AI assistant."
    only_agent = SimpleNamespace(agent_config=SimpleNamespace(agent_name="Sarah", company_name=""))
    assert build_inbound_identity_line(only_agent) == "This is Sarah, an AI assistant."
    neither = SimpleNamespace(agent_config=SimpleNamespace(agent_name=None, company_name=None))
    assert build_inbound_identity_line(neither) == "This is an AI assistant."


@pytest.mark.asyncio
async def test_no_name_no_company_through_the_real_function_speaks_the_bare_line(notice):
    s, vs = _cf(agent="", company="")
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert vs.pipeline.spoken == ["This is an AI assistant."]


@pytest.mark.asyncio
async def test_outbound_caller_first_is_unchanged(notice):
    s, vs = _cf(direction="outbound", notice_owed=True)
    assert await speak_disclosure_before_first_reply(s, "Hello?") is True
    assert vs.pipeline.spoken == ["This call is recorded.", _build_call_greeting(s, first_speaker="user")]
    assert vs.pipeline.spoken[1].startswith("Hi, this is Sarah, an AI assistant from Smith Plumbing")
    s2, vs2 = _cf(direction="outbound", notice_owed=True)
    assert await speak_disclosure_before_first_reply(s2, "I'm busy right now.") is False
    assert vs2.pipeline.spoken == ["This call is recorded."]


@pytest.mark.asyncio
async def test_opt_out_first_words_get_no_identity_line(notice):
    s, vs = _cf()
    assert await speak_disclosure_before_first_reply(s, "Do not call me again.", opener=False) is False
    assert vs.pipeline.spoken == []


@pytest.mark.asyncio
async def test_through_the_real_turn_ender_the_identity_line_precedes_the_model_and_a_second_turn_adds_none(notice):
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import (
        _Chunk, _Pipeline as _TurnPipeline, deliver_end_of_turn, real_session,
    )
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _TurnPipeline(), real_session()
    order = []
    vs = SimpleNamespace(call_session=s, pipeline=_CFPipe(), _recording_disclosure_attempted=True)
    s._voice_session_ref = vs

    async def run_turn(*a, **k):
        order.append(("model", list(vs.pipeline.spoken)))
        return ("Reply.", 0.0, 0.0)

    p._run_turn = AsyncMock(side_effect=run_turn)
    p._is_ask_ai_session = lambda session: False
    p._supports_llm_end_session_action = lambda session: False
    p._turn_epochs = {}
    p.latency_tracker.mark_speech_end = lambda *a, **k: None
    p.latency_tracker.mark_llm_start = lambda *a, **k: None
    s._first_speaker = "user"
    s._call_direction = "inbound"
    s.agent_config = SimpleNamespace(agent_name="Sarah", company_name="Smith Plumbing")
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=True, confidence=0.97))
    s._last_transcript_confidence = 0.97
    await deliver_end_of_turn(p, s)
    assert order == [("model", [IDENT])]            # the identity line was spoken first, then the model ran once
    assert vs.pipeline.spoken == [IDENT]
    # A second caller turn: nothing is spoken before the reply.
    s.conversation_history.append(Message(role=MessageRole.USER, content="Hello?"))
    assert await speak_disclosure_before_first_reply(s, "Do you do boilers?") is False
    assert vs.pipeline.spoken == [IDENT]
