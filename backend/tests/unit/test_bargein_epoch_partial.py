"""Tests for repeated-interruption robustness: P1 (turn-epoch gating of stale
barge-ins) and P3 (commit only the spoken partial to history)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.models.conversation import MessageRole
from app.domain.services.voice_pipeline_service import VoicePipelineService


class _StreamingLLM:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *a, **k):
        for c in self._chunks:
            yield c

    async def stream_chat_with_tools(self, *a, **k):
        for c in self._chunks:
            yield c


def _make_service(chunks):
    svc = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLM(chunks),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    svc.latency_tracker = MagicMock()
    svc.latency_tracker.get_metrics.return_value = None
    svc.transcript_service = MagicMock()
    return svc


def _make_session():
    s = CallSession(
        call_id="call-1", campaign_id="c", lead_id="l", provider_call_id="p",
        system_prompt="Use plain spoken text.", voice_id="v",
    )
    s.barge_in_event = asyncio.Event()
    return s


def _assistant_msgs(session):
    return [m.content for m in session.conversation_history if m.role == MessageRole.ASSISTANT]


# ---------------------------------------------------------------------------
# P3 — commit ONLY the spoken partial when barged into
# ---------------------------------------------------------------------------
def test_barge_in_commits_only_spoken_sentences():
    svc = _make_service(["Hello there. ", "How are you doing today. ", "Goodbye now."])
    session = _make_session()
    svc._barge_in_events[session.call_id] = session.barge_in_event

    calls = {"n": 0}

    async def synth(session_, text, websocket=None, track_latency=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return False                       # sentence 1 delivered
        session_.barge_in_event.set()          # caller barges in before sentence 2
        return True

    svc.synthesize_and_send_audio = AsyncMock(side_effect=synth)

    asyncio.run(svc._run_turn(session, "Hi", AsyncMock(), turn_id=1))

    spoken = _assistant_msgs(session)
    assert spoken == ["Hello there. [interrupted by caller]"], spoken
    # The unheard sentences 2 & 3 must NOT be in history.
    assert "How are you" not in spoken[0]
    assert "Goodbye" not in spoken[0]


def test_no_barge_in_commits_full_reply():
    svc = _make_service(["Hello there. ", "All good here."])
    session = _make_session()
    svc._barge_in_events[session.call_id] = session.barge_in_event
    svc.synthesize_and_send_audio = AsyncMock(return_value=False)  # never interrupted

    asyncio.run(svc._run_turn(session, "Hi", AsyncMock(), turn_id=1))

    spoken = _assistant_msgs(session)
    assert spoken and "[interrupted by caller]" not in spoken[0]
    assert "Hello there." in spoken[0]


# ---------------------------------------------------------------------------
# P1 — a barge-in that targeted an OLDER turn epoch is ignored
# ---------------------------------------------------------------------------
def test_stale_epoch_barge_in_is_ignored():
    svc = _make_service(["Hello there. ", "All good here."])
    session = _make_session()
    svc._barge_in_events[session.call_id] = session.barge_in_event
    # This turn is epoch 5; a leftover barge-in targeted epoch 3 (older).
    session._current_turn_epoch = 5
    svc._barge_in_epoch[session.call_id] = 3
    session.barge_in_event.set()  # stale event is set the whole time
    svc.synthesize_and_send_audio = AsyncMock(return_value=False)

    asyncio.run(svc._run_turn(session, "Hi", AsyncMock(), turn_id=1))

    spoken = _assistant_msgs(session)
    # Stale barge-in ignored → the agent delivered its full reply, not silence.
    assert spoken and "[interrupted by caller]" not in spoken[0]
    assert "Hello there." in spoken[0]


def test_current_epoch_barge_in_is_honored():
    svc = _make_service(["Hello there. ", "How are you doing today. "])
    session = _make_session()
    svc._barge_in_events[session.call_id] = session.barge_in_event
    session._current_turn_epoch = 5
    svc._barge_in_epoch[session.call_id] = 5  # targets THIS turn

    calls = {"n": 0}

    async def synth(session_, text, websocket=None, track_latency=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        session_.barge_in_event.set()
        return True

    svc.synthesize_and_send_audio = AsyncMock(side_effect=synth)
    asyncio.run(svc._run_turn(session, "Hi", AsyncMock(), turn_id=1))

    spoken = _assistant_msgs(session)
    assert spoken == ["Hello there. [interrupted by caller]"], spoken
