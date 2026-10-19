"""Per-turn reply time, measured from when the caller actually stopped talking
(2026-10-20). Pipecat's UserBotLatencyObserver back-dates the start to the
real end of speech and splits the interval into parts that add up; the turn's
own clock (speech_end_time) starts only once the end-of-turn decision is made,
so the time spent deciding was invisible.
"""
from __future__ import annotations

from app.domain.services.latency_tracker import LatencyMetrics
from app.domain.services.voice_pipeline.turn_latency import turn_latency_report


def _metrics(**marks: float) -> LatencyMetrics:
    m = LatencyMetrics(call_id="c", turn_id=1)
    for name, value in marks.items():
        setattr(m, name, value)
    return m


def _typical() -> LatencyMetrics:
    # turn decided at 10.0; first token +0.25; voice starts +0.40, first audio +0.55
    return _metrics(
        speech_end_time=10.0, llm_start_time=10.0, llm_first_token_time=10.25,
        tts_start_time=10.40, tts_first_chunk_time=10.55, response_start_time=10.55,
    )


def test_reply_time_starts_at_the_real_end_of_speech_and_parts_add_up():
    report = turn_latency_report(_typical(), caller_voice_last_at=9.6)
    assert report == {
        "reply_ms": 950,
        "end_of_turn_ms": 400,
        "ai_first_word_ms": 250,
        "voice_first_audio_ms": 150,
        "other_ms": 150,
    }
    assert sum(v for k, v in report.items() if k != "reply_ms") == report["reply_ms"]


def test_without_a_voice_timestamp_the_clock_starts_at_the_turn_decision():
    report = turn_latency_report(_typical(), caller_voice_last_at=None)
    assert report["reply_ms"] == 550
    assert report["end_of_turn_ms"] is None


def test_an_implausible_voice_timestamp_is_not_used():
    # Later than the turn decision (caller noise while the agent spoke), or a
    # stale one from long ago: neither describes this turn's end of speech.
    assert turn_latency_report(_typical(), caller_voice_last_at=10.2)["end_of_turn_ms"] is None
    assert turn_latency_report(_typical(), caller_voice_last_at=-5.0)["end_of_turn_ms"] is None


def test_no_report_until_the_agent_actually_started_speaking():
    assert turn_latency_report(_metrics(speech_end_time=10.0), caller_voice_last_at=9.5) is None
    assert turn_latency_report(None, caller_voice_last_at=9.5) is None


def test_missing_sub_measurements_stay_null_and_other_absorbs_the_rest():
    m = _metrics(speech_end_time=10.0, response_start_time=10.8)
    assert turn_latency_report(m, caller_voice_last_at=None) == {
        "reply_ms": 800,
        "end_of_turn_ms": None,
        "ai_first_word_ms": None,
        "voice_first_audio_ms": None,
        "other_ms": 800,
    }


# ── wiring: one caller turn through the real turn handler ────────────────────

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline_service import VoicePipelineService
from tests.unit.test_voice_pipeline_service import _make_session


@pytest.mark.asyncio
async def test_turn_complete_carries_the_reply_time_for_the_test_agent(caplog):
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(), tts_provider=AsyncMock(),
        media_gateway=AsyncMock(), mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = _typical()
    service.transcript_service = MagicMock()
    service._run_turn = AsyncMock(return_value=("Sure, happy to help.", 10.0, 10.0))
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.conversation_history.append(Message(role=MessageRole.USER, content="Hi there"))
    session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content="How can I help?"))
    session.current_user_input = "Tell me your prices please"
    session._caller_voice_last_at = 9.6  # last voiced frame, 0.4 s before the turn decision
    websocket = AsyncMock()
    this_turn = session.turn_id

    with caplog.at_level("INFO"):
        await service.handle_turn_end(session, websocket)

    sent = [c.args[0] for c in websocket.send_json.call_args_list if c.args]
    complete = [m for m in sent if isinstance(m, dict) and m.get("type") == "turn_complete"]
    assert complete, sent
    assert complete[-1]["turn_id"] == this_turn
    assert complete[-1]["latency"]["reply_ms"] == 950
    assert complete[-1]["latency"]["end_of_turn_ms"] == 400
    # Phone calls have no Test agent socket: the same numbers go to the log.
    assert any("turn_latency" in r.getMessage() and "reply_ms=950" in r.getMessage()
               for r in caplog.records)
