"""Caller-first outbound calls speak the recording notice again.

Live: tenant 790ca2db (two-party consent, empty country list) kept no
recording on any caller-first call: 65 of 65 discarded on 2026-10-20 with
recording_retention_suppressed ... tenant_policy_two_party_announce. The notice
used to play after the caller's first "Hello?" through the instant opener,
which was disconnected on 2026-10-07. Owner decision 2026-10-21: play the
notice as before (no scripted greeting), then the agent's normal reply.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

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


@pytest.fixture
def spoken(monkeypatch):
    calls = []

    async def fake_speak(voice_session):
        calls.append((voice_session, voice_session.call_session._disclosure_in_flight))

    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", fake_speak)
    return calls


def _with_voice_session(s):
    vs = SimpleNamespace(call_session=s)
    s._voice_session_ref = vs
    return vs


@pytest.mark.asyncio
async def test_the_notice_is_spoken_once_with_the_guard_armed(spoken):
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    await speak_disclosure_before_first_reply(s)
    await speak_disclosure_before_first_reply(s)   # second caller turn: never again
    assert spoken == [(vs, True)]
    assert s._disclosure_in_flight is False


@pytest.mark.asyncio
async def test_no_telephony_session_means_no_notice(spoken):
    s = _session(tts_active=False, started_ago=None)   # browser test agent: no _voice_session_ref
    await speak_disclosure_before_first_reply(s)
    assert spoken == []


@pytest.mark.asyncio
async def test_a_notice_already_given_is_not_repeated(spoken):
    s = _session(tts_active=False, started_ago=None)
    vs = _with_voice_session(s)
    vs._recording_disclosure_attempted = True        # e.g. the inbound gate spoke it
    await speak_disclosure_before_first_reply(s)
    assert spoken == []


@pytest.mark.asyncio
async def test_the_guard_flag_clears_even_if_playback_is_cancelled(monkeypatch):
    import asyncio

    async def cancelled(voice_session):
        raise asyncio.CancelledError

    monkeypatch.setattr(agent_first, "_speak_recording_disclosure", cancelled)
    s = _session(tts_active=False, started_ago=None)
    _with_voice_session(s)
    with pytest.raises(asyncio.CancelledError):
        await speak_disclosure_before_first_reply(s)
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

async def _first_turn(monkeypatch, *, first_speaker="user", prior_user_turn=False):
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import (
        _Chunk, _Pipeline, deliver_end_of_turn, real_session,
    )
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    seen = []

    async def fake_helper(session):
        seen.append(session.call_id)

    monkeypatch.setattr(caller_first_disclosure, "speak_disclosure_before_first_reply", fake_helper)
    p, s = _Pipeline(), real_session()
    s._first_speaker = first_speaker
    if prior_user_turn:
        s.conversation_history.append(Message(role=MessageRole.USER, content="Hello?"))
        s.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="Hi, is this Mike?"))
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=True, confidence=0.97))
    s._last_transcript_confidence = 0.97
    await deliver_end_of_turn(p, s)
    return seen


@pytest.mark.asyncio
async def test_the_first_caller_turn_plays_the_notice_on_caller_first_calls(monkeypatch):
    assert len(await _first_turn(monkeypatch)) == 1


@pytest.mark.asyncio
async def test_agent_first_calls_are_untouched(monkeypatch):
    assert await _first_turn(monkeypatch, first_speaker="agent") == []


@pytest.mark.asyncio
async def test_later_turns_do_not_play_it(monkeypatch):
    assert await _first_turn(monkeypatch, prior_user_turn=True) == []
