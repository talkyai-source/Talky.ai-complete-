"""A reply cut off during its first sentence still leaves a trace in history.

Synthetic caller on the deployed agent (scripts/synthetic_caller.py,
2026-10-09, Estimation campaign): the caller asked about the company and,
1.5 s into the answer, said "Okay. Thank you. Bye." The barge-in cancelled the
reply while its first, long sentence was still playing. With no sentence
finished, the cancellation path kept only the caller's words, so the model saw
"Can I know about your company?" unanswered and answered it for 20 s instead
of saying goodbye (3 of 4 runs). When one short sentence had finished, history
read "I'd be happy to. [interrupted by caller]" and the agent closed politely.

The soft-interrupt path in turn_streamer already writes a bare
"[interrupted by caller]" when nothing finished; the cancellation path now
does the same once the reply's audio had started. Unfinished words are still
never recorded as said.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import MessageRole
from tests.unit.test_bargein_cancel_keeps_caller_turn import _make_service, _make_session


class _LongFirstSentence:
    async def stream_chat_with_timeout(self, *a, **k):
        for chunk in ["We're Allstate Estimation, a UK construction estimating firm working with ",
                      "contractors, builders and developers on detailed takeoffs. ", "What projects do you have? "]:
            yield chunk

    stream_chat_with_tools = stream_chat_with_timeout


async def _cut_off_while_speaking(started_speaking: bool):
    svc, session = _make_service(), _make_session()
    svc.llm_provider = _LongFirstSentence()
    playing = asyncio.Event()

    async def synth(session_, text, websocket=None, track_latency=False):
        playing.set()
        await asyncio.sleep(10)  # the first sentence is still playing
        return False  # pragma: no cover

    async def never_speaks(session_, websocket=None):
        playing.set()
        await asyncio.sleep(10)
        return ("unreachable", 0.0, 0.0)  # pragma: no cover

    if started_speaking:
        svc.synthesize_and_send_audio = AsyncMock(side_effect=synth)
    else:
        svc._stream_llm_and_tts = never_speaks
    task = asyncio.ensure_future(svc._run_turn(session, "Can I know about your company?", AsyncMock(), turn_id=2))
    await asyncio.wait_for(playing.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return session


@pytest.mark.asyncio
async def test_a_reply_cut_off_in_its_first_sentence_is_marked_not_erased():
    session = await _cut_off_while_speaking(True)
    history = [(m.role, m.content) for m in session.conversation_history]
    assert history == [(MessageRole.USER, "Can I know about your company?"),
                       (MessageRole.ASSISTANT, "[interrupted by caller]")]


@pytest.mark.asyncio
async def test_a_reply_cancelled_before_any_audio_still_leaves_only_the_caller():
    session = await _cut_off_while_speaking(False)
    assert [m.role for m in session.conversation_history] == [MessageRole.USER]


@pytest.mark.asyncio
async def test_the_next_turn_starts_with_a_clean_flag():
    session = await _cut_off_while_speaking(True)
    assert session._reply_audio_started is True
    svc = _make_service()
    seen = []

    async def reply(session_, websocket=None):
        seen.append(getattr(session_, "_reply_audio_started", None))
        return ("Goodbye, thanks for your time.", 0.0, 0.0)

    svc._stream_llm_and_tts = reply
    session._reply_audio_started = False  # what _stream_llm_and_tts does first
    await svc._run_turn(session, "Okay. Thank you. Bye.", AsyncMock(), turn_id=3)
    roles = [m.role for m in session.conversation_history]
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.USER, MessageRole.ASSISTANT]
    assert seen == [False]
