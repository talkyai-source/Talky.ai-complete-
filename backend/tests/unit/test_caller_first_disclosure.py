"""Caller-first calls answer the callee's first words with the identity opener.

Live: tenant 790ca2db (two-party consent, empty country list) kept no
recording on any caller-first call: 65 of 65 discarded on 2026-10-20 with
recording_retention_suppressed ... tenant_policy_two_party_announce. The notice
used to play after the caller's first "Hello?" through the instant opener,
which was disconnected on 2026-10-07. Owner decision 2026-10-21: play the
notice again, then the agent's reply.

Owner decision 2026-11-05 (QA F12-03): on an outbound caller-first call that
reply is the identity opener ("Hi, this is Sarah, an AI assistant from ..., on
a recorded line. Am I speaking with John?"), said as the notice where the
platform's own notice is owed. It ends on a question, so the model does not
also answer that utterance. Inbound caller-first calls keep the notice and
then the model's reply.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.telephony.modes import agent_first
from app.domain.services.voice_pipeline import caller_first_disclosure
from app.domain.services.voice_pipeline.caller_first_disclosure import speak_disclosure_before_first_reply
from app.domain.services.voice_pipeline.first_line_guard import (
    is_first_line_greeting_repeat,
    note_playback,
)
from tests.unit.test_first_line_guard import _session


def _speaker(monkeypatch, *, platform_notice_owed=True):
    calls = []

    async def fake_speak(voice_session, *, opener_with_notice=None):
        calls.append((opener_with_notice, voice_session.call_session._disclosure_in_flight))
        voice_session._opener_spoken_as_notice = bool(platform_notice_owed and opener_with_notice)

    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", fake_speak)
    return calls


class _Pipeline:
    def __init__(self):
        self.spoken = []
        self.transcript_service = SimpleNamespace(accumulate_turn=lambda **_: None)

    async def synthesize_and_send_audio(self, session, text, websocket=None, **_):
        self.spoken.append(text)
        return False


def _with_voice_session(s):
    vs = SimpleNamespace(call_session=s, pipeline=_Pipeline())
    s._voice_session_ref = vs
    return vs


@pytest.mark.asyncio
async def test_the_first_words_get_the_opener_said_as_the_notice(monkeypatch):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    assert await speak_disclosure_before_first_reply(s, "Hello?") is True
    [(opener, guard_armed)] = calls
    assert "an AI assistant from" in opener and "on a recorded line" in opener and guard_armed
    assert vs.pipeline.spoken == []                       # one line, said once
    assert [m.content for m in s.conversation_history] == ["Hello?"]
    assert s._disclosure_in_flight is False
    # A later caller turn never gets it again.
    assert await speak_disclosure_before_first_reply(s, "Yes, speaking.") is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_with_no_platform_notice_owed_the_opener_is_spoken_itself(monkeypatch):
    _speaker(monkeypatch, platform_notice_owed=False)
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    assert await speak_disclosure_before_first_reply(s, "Hello?") is True
    [opener] = vs.pipeline.spoken
    assert "an AI assistant from" in opener and "recorded" not in opener
    assert [(m.role, m.content) for m in s.conversation_history] == [
        (MessageRole.USER, "Hello?"), (MessageRole.ASSISTANT, opener),
    ]
    assert s._has_introduced is True


@pytest.mark.asyncio
async def test_an_inbound_caller_first_call_keeps_the_notice_then_the_model(monkeypatch):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)
    s._call_direction = "inbound"
    vs = _with_voice_session(s)
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert calls == [(None, True)] and vs.pipeline.spoken == []
    assert s.conversation_history == []


@pytest.mark.asyncio
async def test_after_a_silent_pickup_greeting_the_model_replies_as_usual(monkeypatch):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    vs._recording_disclosure_attempted = True        # the greeting flow ran
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="Hi, this is ..."))
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert calls == [] and vs.pipeline.spoken == []


@pytest.mark.asyncio
async def test_a_turn_zero_reprompt_does_not_skip_the_notice_or_the_opener(monkeypatch):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)
    _with_voice_session(s)
    s.conversation_history.append(Message(role=MessageRole.ASSISTANT,
                                          content="Sorry, I didn't catch that - could you say that again?"))
    assert await speak_disclosure_before_first_reply(s, "Hello?") is True
    assert len(calls) == 1 and "AI assistant" in calls[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("words", [
    "I don't want to hear from your company anymore.",
    "What is this about?",
    "I'm busy right now.",
])
async def test_other_first_words_get_the_notice_and_the_model(monkeypatch, words):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    assert await speak_disclosure_before_first_reply(s, words) is False
    assert calls == [(None, True)] and vs.pipeline.spoken == []
    assert s.conversation_history == []


@pytest.mark.parametrize("words", ["Hello?", "Hi.", "Yes?", "Hello? Hello?", "Who's this?",
                                   "Hello, who is calling?", "Yeah, John speaking.", "Hello, this is John."])
def test_pickup_words_get_the_opener(words):
    from app.domain.services.voice_pipeline.caller_first_disclosure import is_pickup_words

    assert is_pickup_words(words)


@pytest.mark.asyncio
async def test_no_telephony_session_means_no_opener(monkeypatch):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)   # browser test agent: no _voice_session_ref
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert calls == []


@pytest.mark.asyncio
async def test_a_notice_already_given_is_not_repeated(monkeypatch):
    calls = _speaker(monkeypatch)
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    vs._recording_disclosure_attempted = True        # e.g. the inbound gate spoke it
    s._call_direction = "inbound"
    assert await speak_disclosure_before_first_reply(s, "Hello?") is False
    assert calls == []


@pytest.mark.asyncio
async def test_the_guard_flag_clears_even_if_playback_is_cancelled(monkeypatch):
    import asyncio

    async def cancelled(voice_session, *, opener_with_notice=None):
        raise asyncio.CancelledError

    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", cancelled)
    s = _session(tts_active=False, started_ago=None)
    _with_voice_session(s)
    with pytest.raises(asyncio.CancelledError):
        await speak_disclosure_before_first_reply(s, "Hello?")
    assert s._disclosure_in_flight is False


def test_a_bare_hello_does_not_cut_off_the_notice():
    s = _session(started_ago=None)
    s._disclosure_in_flight = True
    assert is_first_line_greeting_repeat(s, "Hello?", now=time.monotonic() + 30)


def test_the_notice_is_not_counted_as_the_agents_first_line():
    s = _session(started_ago=None)
    s._disclosure_in_flight = True
    note_playback(s, True, now=1.0)
    assert getattr(s, "_first_line_started_at", None) is None
    s._disclosure_in_flight = False
    note_playback(s, True, now=5.0)                  # the agent's opener after the notice
    assert s._first_line_started_at == 5.0


# ── turn_ender wiring: real TranscriptHandler + TurnEnder ──────────────────

async def _first_turn(monkeypatch, *, first_speaker="user", prior_user_turn=False, opener_spoken=True):
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import (
        _Chunk, _Pipeline as _TurnPipeline, deliver_end_of_turn, real_session,
    )
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    seen = []

    async def fake_helper(session, caller_text=None, *, opener=True):
        seen.append(caller_text)
        return opener_spoken and opener

    monkeypatch.setattr(caller_first_disclosure, "speak_disclosure_before_first_reply", fake_helper)
    p, s = _TurnPipeline(), real_session()
    # Enough of the pipeline for the turn to reach the model, so "no model
    # reply" below is measured rather than an early exit.
    p._run_turn = AsyncMock(return_value=("Reply.", 0.0, 0.0))
    p._is_ask_ai_session = lambda session: False
    p._supports_llm_end_session_action = lambda session: False
    p._turn_epochs = {}
    p.latency_tracker.mark_speech_end = lambda *a, **k: None
    p.latency_tracker.mark_llm_start = lambda *a, **k: None
    s._first_speaker = first_speaker
    if prior_user_turn:
        s.conversation_history.append(Message(role=MessageRole.USER, content="Hello?"))
        s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="Hi, is this Mike?"))
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=True, confidence=0.97))
    s._last_transcript_confidence = 0.97
    await deliver_end_of_turn(p, s)
    return seen, p._run_turn.await_count


@pytest.mark.asyncio
async def test_the_first_caller_turn_gets_the_opener_and_no_model_reply(monkeypatch):
    seen, model_turns = await _first_turn(monkeypatch)
    assert seen == ["Hello?"] and model_turns == 0


@pytest.mark.asyncio
async def test_when_no_opener_was_spoken_the_model_replies(monkeypatch):
    seen, model_turns = await _first_turn(monkeypatch, opener_spoken=False)
    assert seen == ["Hello?"] and model_turns == 1


@pytest.mark.asyncio
async def test_agent_first_calls_are_untouched(monkeypatch):
    seen, _ = await _first_turn(monkeypatch, first_speaker="agent")
    assert seen == []


@pytest.mark.asyncio
async def test_later_turns_do_not_play_it(monkeypatch):
    seen, _ = await _first_turn(monkeypatch, prior_user_turn=True)
    assert seen == []


# Security review of the first cut: an opt-out or a goodbye as the first
# words returned on the opener path, before the opt-out was recorded.
@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["Don't call me again.", "Not interested, bye."])
async def test_an_opt_out_or_goodbye_first_is_not_answered_with_the_opener(monkeypatch, words):
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import (
        _Chunk, _Pipeline as _TurnPipeline, deliver_end_of_turn, real_session,
    )
    from app.domain.services.dialer import opt_out
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    calls = _speaker(monkeypatch)
    purge = AsyncMock(return_value=True)
    monkeypatch.setattr(opt_out, "purge_opt_out_before_farewell", purge)
    p, s = _TurnPipeline(), real_session()
    p._run_turn = AsyncMock(return_value=("Understood. Goodbye.", 0.0, 0.0))
    p._is_ask_ai_session = lambda session: False
    p._supports_llm_end_session_action = lambda session: True
    p._turn_epochs = {}
    p.latency_tracker.mark_speech_end = lambda *a, **k: None
    p.latency_tracker.mark_llm_start = lambda *a, **k: None
    s._first_speaker = "user"
    vs = _with_voice_session(s)
    await TranscriptHandler(p).handle(s, _Chunk(text=words, is_final=True, confidence=0.97))
    s._last_transcript_confidence = 0.97
    await deliver_end_of_turn(p, s)
    assert calls == [(None, True)]                 # the notice only, no opener
    assert vs.pipeline.spoken == []
    assert p._run_turn.await_count == 1             # the model answers them
    if "call me" in words:
        purge.assert_awaited_once()                 # and the opt-out is recorded
