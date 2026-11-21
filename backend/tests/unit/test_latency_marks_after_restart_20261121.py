"""Model start marked again while the previous answer is in flight (no
start_turn): the first-token time must be measured from THIS start."""
import time
from types import SimpleNamespace

from app.domain.services.latency_tracker import LatencyTracker
from app.domain.services.voice_pipeline.turn_latency import turn_latency_report

CID = "call-restart-1"


def _tracker():
    t = LatencyTracker()
    t.start_turn(CID, 1)
    return t


def test_first_token_after_restart_is_second_interval_not_negative():
    t = _tracker()
    m = t._metrics[CID]
    t.mark_llm_start(CID)
    m.llm_start_time = time.monotonic() - 12.0  # first generation started long ago
    t.mark_llm_first_token(CID)
    m.llm_first_token_time = m.llm_start_time + 0.4
    t.mark_llm_end(CID)
    t.mark_llm_start(CID)  # in-flight restart, no start_turn
    assert m.llm_first_token_time is None and m.llm_end_time is None
    t.mark_llm_first_token(CID)
    ms = m.llm_first_token_ms
    assert ms is not None and 0 <= ms < 1000


def test_negative_first_token_is_reported_missing():
    t = _tracker()
    m = t._metrics[CID]
    m.llm_start_time = 100.0
    m.llm_first_token_time = 99.0
    assert m.llm_first_token_ms is None


def test_turn_latency_report_after_restart_has_ai_first_word():
    t = _tracker()
    m = t._metrics[CID]
    m.speech_end_time = time.monotonic()
    t.mark_llm_start(CID)
    t.mark_llm_first_token(CID)  # previous answer's token
    time.sleep(0.02)
    t.mark_llm_start(CID)  # in-flight restart, no start_turn
    time.sleep(0.05)
    t.mark_llm_first_token(CID)
    time.sleep(0.05)
    m.response_start_time = time.monotonic()
    rep = turn_latency_report(m, None)
    assert rep["ai_first_word_ms"] is not None and rep["ai_first_word_ms"] >= 40
    assert rep["other_ms"] < rep["ai_first_word_ms"] + 200


def test_ordinary_turn_unchanged():
    m = SimpleNamespace(
        speech_end_time=10.0, response_start_time=11.0, llm_start_time=10.1,
        llm_first_token_time=10.5, tts_start_time=10.6, tts_first_chunk_time=10.9,
    )
    assert turn_latency_report(m, None) == {
        "reply_ms": 1000, "end_of_turn_ms": None, "ai_first_word_ms": 400,
        "voice_first_audio_ms": 300, "other_ms": 300,
    }
    t = _tracker()
    t.mark_llm_start(CID)
    t.mark_llm_first_token(CID)
    assert t._metrics[CID].llm_first_token_ms >= 0
