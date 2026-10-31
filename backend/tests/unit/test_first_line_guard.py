"""A repeated bare "Hello?" must not cut off the agent's first line.

Live 2026-10-20 ("Estimation new", caller-first): the caller's "Hello?" waited
~1.8 s for the agent; callers said "Hello?" again and that StartOfTurn cut the
agent's first line off within a second, 26 times in one day. Owner decision
2026-10-21: protect the first line from a bare repeated greeting; real speech
still interrupts. Real CallSession throughout (see the warning in
test_caller_speaking_cleared_on_end_of_turn.py about SimpleNamespace).
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline import first_line_guard as guard
from app.domain.services.voice_pipeline.first_line_guard import (
    is_first_line_greeting_repeat,
    is_repeat_greeting,
    note_playback,
)


def _session(first_speaker="user", tts_active=True, started_ago=0.5) -> CallSession:
    s = CallSession(
        call_id="06a6f8c9-89c9-4707-ab3b-98d12f4930fe",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-out-1",
        system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = first_speaker
    s.tts_active = tts_active
    if started_ago is not None:
        s._first_line_started_at = time.monotonic() - started_ago
    return s


@pytest.mark.parametrize("text", ["Hello?", "hello", "Hello? Hello?", "hello hello", "Hi there.", "Hey", "Hiya"])
def test_greetings_count(text):
    assert is_repeat_greeting(text)


@pytest.mark.parametrize("text", [
    "Yes", "Yeah.", "Yep", "Speaking", "Yes, speaking", "Hello, who is this?", "stop",
    "", "hello hello hello hello hello", "there", "Hi, is this about the estimate?",
])
def test_answers_and_real_speech_do_not(text):
    assert not is_repeat_greeting(text)


@pytest.mark.parametrize("text", [
    "Are you there?", "Hello? Are you there?", "You still there?", "Can you hear me?",
    "Hello, can you hear me?", "Hello hello are you there",
])
def test_line_checks_count(text):
    assert is_repeat_greeting(text)


@pytest.mark.parametrize("text", ["Are you a robot?", "Can you call me back?", "Is anyone there with you?"])
def test_questions_that_are_not_line_checks_do_not(text):
    assert not is_repeat_greeting(text)


def test_guard_applies_in_the_first_line_window_on_caller_first_calls():
    assert is_first_line_greeting_repeat(_session(), "Hello?")


@pytest.mark.parametrize("kwargs", [
    {"first_speaker": "agent"},                       # agent-first calls too (owner 2026-10-22)
    {"started_ago": 3.0},                             # past the first line's window
    {"started_ago": 30.0, "_first_line_done": True},  # any later reply
])
@pytest.mark.parametrize("text", ["Hello?", "Are you there?"])
def test_guard_applies_whenever_the_agent_is_speaking(kwargs, text):
    """Call a3e8f75e (2026-10-21): each "Hello?" cancelled a reply as it began."""
    done = kwargs.pop("_first_line_done", False)
    s = _session(**kwargs)
    s._first_line_done = done
    assert is_first_line_greeting_repeat(s, text)


@pytest.mark.parametrize("kwargs", [
    {"tts_active": False, "started_ago": None},                         # agent has not spoken yet
    {"tts_active": False, "started_ago": 3.0},                          # agent silent, past the window
    {"tts_active": False, "started_ago": 0.5, "first_speaker": "agent"},  # agent-first, agent silent
])
def test_guard_does_not_apply_while_the_agent_is_silent(kwargs):
    assert not is_first_line_greeting_repeat(_session(**kwargs), "Hello?")


def test_guard_covers_the_gap_between_the_first_lines_sentences():
    s = _session(tts_active=False, started_ago=0.8)   # between sentence 1 and 2
    assert is_first_line_greeting_repeat(s, "Hello?")


def test_after_the_first_line_a_hello_to_a_silent_agent_is_a_real_turn():
    s = _session(tts_active=False)
    s._first_line_done = True
    assert not is_first_line_greeting_repeat(s, "Hello?")


def test_a_yes_during_the_first_line_still_gets_through():
    assert not is_first_line_greeting_repeat(_session(), "Yes, speaking.")


def test_speech_before_the_frame_stamp_starts_the_window_now():
    s = _session(started_ago=None)
    assert is_first_line_greeting_repeat(s, "hello")
    assert s._first_line_started_at is not None


def test_note_playback_marks_the_first_line_start_and_end_once():
    s = _session(started_ago=None)
    note_playback(s, False, now=1.0)
    assert getattr(s, "_first_line_started_at", None) is None
    note_playback(s, True, now=2.0)
    note_playback(s, True, now=3.0)
    assert s._first_line_started_at == 2.0
    note_playback(s, False, now=4.0)
    assert not getattr(s, "_first_line_done", False)   # quiet for 0 s: maybe a sentence gap
    note_playback(s, False, now=4.7)
    assert s._first_line_done is True                  # quiet for 0.7 s: the line ended
    note_playback(s, True, now=9.0)          # a later reply is not the first line
    assert s._first_line_started_at == 2.0 and s._first_line_done is True


def test_a_sentence_gap_does_not_end_the_first_line():
    s = _session(started_ago=None)
    note_playback(s, True, now=1.0)
    note_playback(s, False, now=1.5)   # TTS between "Hi, I'm Sarah." and "Is this Mike?"
    note_playback(s, False, now=1.9)
    note_playback(s, True, now=2.0)
    note_playback(s, False, now=2.4)
    assert not getattr(s, "_first_line_done", False)


def test_window_is_configurable(monkeypatch):
    # The window covers the first line's sentence gaps (agent momentarily silent).
    monkeypatch.setenv("VOICE_FIRST_LINE_GUARD_S", "1.0")
    assert not is_first_line_greeting_repeat(_session(tts_active=False, started_ago=1.5), "Hello?")
    assert is_first_line_greeting_repeat(_session(tts_active=False, started_ago=0.8), "Hello?")
    monkeypatch.setenv("VOICE_FIRST_LINE_GUARD_S", "not-a-number")
    assert guard.guard_seconds() == guard.DEFAULT_GUARD_S


# ── the three interrupt paths ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_barge_in_keeps_the_first_line_playing():
    from app.domain.services.voice_pipeline_service import VoicePipelineService

    s = _session()
    event = asyncio.Event()
    fake = SimpleNamespace(_barge_in_events={s.call_id: event}, _barge_in_epoch={})
    await VoicePipelineService.handle_barge_in(fake, s, None, transcript_text="Hello?")
    assert not event.is_set() and s.tts_active is True


@pytest.mark.asyncio
async def test_transcript_handler_ignores_the_repeat_and_its_end_marker():
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline, eot_marker
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _session()
    calls = []

    async def barge(session, websocket=None, **k):
        calls.append(k)

    p.handle_barge_in = barge
    p._utterance_seq[s.call_id] = 3
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=True))
    await TranscriptHandler(p).handle(s, eot_marker())
    assert calls == []                 # the agent was not stopped
    assert not p._pending_llm_tasks    # and no reply was started
    assert s.current_user_input in ("", None)


@pytest.mark.asyncio
async def test_a_greeting_that_grows_into_real_words_interrupts_at_once():
    """'Hello? Who is this?': the StartOfTurn partial was a bare 'Hello', so its
    interrupt was skipped; the first chunk with real words must interrupt then,
    not leave the agent talking over the question until EndOfTurn."""
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _session()
    stops = []

    async def barge(session, websocket=None, **k):
        stops.append(k.get("transcript_text"))

    p.handle_barge_in = barge
    p._utterance_seq[s.call_id] = 2
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=False))
    assert stops == []
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello? Who is this?", is_final=False))
    assert stops == ["Hello? Who is this?"]
    assert p._utterance_seq[s.call_id] == 3          # counted as the new utterance it is
    assert s._first_line_barge_skipped is False


@pytest.mark.asyncio
async def test_transcript_handler_lets_an_answer_through_during_the_first_line():
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _session()
    await TranscriptHandler(p).handle(s, _Chunk(text="Yes, speaking.", is_final=False))
    assert "Yes, speaking." in (s.current_user_input or "")
    assert getattr(s, "_suppressed_backchannel_seq", None) is None


async def _signal(s, phase, text):
    """AssemblyAI's two-step barge-in (BargeInSignal pause -> confirm/resume)."""
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Pipeline
    from app.domain.models.conversation import BargeInSignal
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p = _Pipeline()
    stops = []

    async def barge(session, websocket=None, **k):
        stops.append(k.get("transcript_text"))

    p.handle_barge_in = barge
    await TranscriptHandler(p).handle(s, BargeInSignal(text=text, phase=phase))
    return stops


@pytest.mark.asyncio
async def test_assemblyai_pause_does_not_hold_the_first_line_for_a_repeated_hello():
    s = _session()
    await _signal(s, "pause", "Hello?")
    assert getattr(s, "_tts_paused_at", None) is None


@pytest.mark.asyncio
async def test_assemblyai_confirm_of_a_repeated_hello_resumes_instead_of_stopping():
    s = _session()
    s._tts_paused_at = time.monotonic()   # held by a pause whose text was not known yet
    stops = await _signal(s, "confirm", "Hello?")
    assert stops == [] and s._tts_paused_at is None


@pytest.mark.asyncio
async def test_assemblyai_real_words_still_stop_the_first_line():
    s = _session()
    stops = await _signal(s, "confirm", "Stop calling me")
    assert stops == ["Stop calling me"]


async def _drive_barge_in_callback(texts):
    """Run AudioIngest with a fake STT that fires the barge-in callback."""
    from tests.unit.test_audio_ingest_terminal_stt_failure import _make_session
    from app.domain.services.voice_pipeline.audio_ingest import AudioIngest

    s = _make_session()
    s.stt_active = True
    s.tts_active = True
    s._first_speaker = "user"
    s._first_line_started_at = time.monotonic()
    event = asyncio.Event()
    pipeline = MagicMock()
    pipeline.media_gateway.get_audio_queue.return_value = None
    pipeline._barge_in_events = {s.call_id: event}
    pipeline._barge_in_epoch = {}
    pipeline._utterance_seq = {}

    class _STT:
        async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kwargs):
            for t in texts:
                on_barge_in(t)
            return
            yield  # pragma: no cover

    pipeline.stt_provider = _STT()
    await AudioIngest(pipeline).process(s)
    return event, pipeline._utterance_seq.get(s.call_id, 0)


@pytest.mark.asyncio
async def test_barge_in_callback_skips_a_repeated_hello():
    event, seq = await _drive_barge_in_callback(["Hello?"])
    assert not event.is_set() and seq == 0


@pytest.mark.asyncio
async def test_barge_in_callback_still_stops_for_real_speech():
    event, seq = await _drive_barge_in_callback(["Stop"])
    assert event.is_set() and seq == 1


# ── later in the call (owner decision 2026-10-22, call a3e8f75e) ─────────────

def _mid_call(first_speaker="agent", tts_active=True) -> CallSession:
    s = _session(first_speaker=first_speaker, tts_active=tts_active, started_ago=40.0)
    s._first_line_done = True
    return s


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Hello?", "Are you there?"])
async def test_handle_barge_in_keeps_a_later_reply_playing(text):
    from app.domain.services.voice_pipeline_service import VoicePipelineService

    s = _mid_call()
    event = asyncio.Event()
    fake = SimpleNamespace(_barge_in_events={s.call_id: event}, _barge_in_epoch={})
    await VoicePipelineService.handle_barge_in(fake, s, None, transcript_text=text)
    assert not event.is_set() and s.tts_active is True


@pytest.mark.asyncio
async def test_a_later_reply_ignores_a_hello_and_a_following_yeah():
    """A finished "Hello?" must not leave a skipped interrupt behind for the
    next utterance: the "yeah" after it is a backchannel, not a stop."""
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _mid_call()
    stops = []

    async def barge(session, websocket=None, **k):
        stops.append(k.get("transcript_text"))

    p.handle_barge_in = barge
    p._utterance_seq[s.call_id] = 5
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=True))
    assert s._first_line_barge_skipped is False
    await TranscriptHandler(p).handle(s, _Chunk(text="Yeah.", is_final=True))
    assert stops == []
    assert not p._pending_llm_tasks


@pytest.mark.asyncio
async def test_a_later_hello_that_grows_into_real_words_interrupts_at_once():
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _mid_call()
    stops = []

    async def barge(session, websocket=None, **k):
        stops.append(k.get("transcript_text"))

    p.handle_barge_in = barge
    p._utterance_seq[s.call_id] = 5
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=False))
    assert stops == []
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello? Who is this?", is_final=False))
    assert stops == ["Hello? Who is this?"]


@pytest.mark.asyncio
async def test_assemblyai_confirm_of_a_later_hello_resumes_instead_of_stopping():
    s = _mid_call()
    s._tts_paused_at = time.monotonic()
    stops = await _signal(s, "confirm", "Hello? Are you there?")
    assert stops == [] and s._tts_paused_at is None


@pytest.mark.asyncio
async def test_assemblyai_real_words_still_stop_a_later_reply():
    stops = await _signal(_mid_call(), "confirm", "No, stop")
    assert stops == ["No, stop"]


@pytest.mark.parametrize("age_s,protected", [(0.2, True), (1.0, False)])
def test_the_gateways_queued_tail_still_counts_as_the_agent_speaking(age_s, protected):
    """tts_active drops when Python stops streaming; the gateway still plays its
    queued tail (interrupt._GATEWAY_DRAIN_S) to the caller."""
    s = _mid_call(tts_active=False)
    s._last_tts_chunk_at = time.monotonic() - age_s
    assert is_first_line_greeting_repeat(s, "Hello?") is protected


@pytest.mark.asyncio
async def test_a_skip_left_by_a_withdrawn_or_empty_turn_does_not_make_a_yeah_interrupt():
    """A StartOfTurn/BargeInSignal "Hello" skip with no text chunk after it must
    end with its turn: the next utterance's "yeah" stays a backchannel."""
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline, eot_marker
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _mid_call()
    stops = []

    async def barge(session, websocket=None, **k):
        stops.append(k.get("transcript_text"))

    from app.domain.models.conversation import Message, MessageRole

    s.conversation_history = [
        Message(role=MessageRole.USER, content="Hello?"),
        Message(role=MessageRole.ASSISTANT, content="We help contractors with estimating and takeoffs."),
    ]
    p.handle_barge_in = barge
    p._utterance_seq[s.call_id] = 5
    s._first_line_barge_skipped = True               # set by the audio-level skip only
    await TranscriptHandler(p).handle(s, eot_marker())
    assert s._first_line_barge_skipped is False
    stops.clear()                                    # the marker's own handling is not under test
    await TranscriptHandler(p).handle(s, _Chunk(text="Yeah.", is_final=True))
    assert stops == []


@pytest.mark.asyncio
async def test_a_withdrawn_assemblyai_barge_in_clears_the_skip():
    s = _mid_call()
    s._first_line_barge_skipped = True
    await _signal(s, "resume", "")
    assert s._first_line_barge_skipped is False


@pytest.mark.asyncio
async def test_a_hello_to_a_silent_agent_mid_call_is_answered():
    from tests.unit.test_caller_speaking_cleared_on_end_of_turn import _Chunk, _Pipeline
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler

    p, s = _Pipeline(), _mid_call(tts_active=False)
    await TranscriptHandler(p).handle(s, _Chunk(text="Hello?", is_final=False))
    assert "Hello?" in (s.current_user_input or "")
    assert getattr(s, "_suppressed_backchannel_seq", None) is None
