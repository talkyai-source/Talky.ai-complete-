"""Stability audit 2026-10-02: wiring guards (behaviour is covered elsewhere).

A barge-in broke the LLM token loop and the TTS chunk loop without closing the
provider stream, so its HTTP stream / websocket and concurrency slot stayed held
until garbage collection. And on browser gateways a total voice failure ended the
turn in silence with only a log line.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "app" / "domain" / "services" / "voice_pipeline"


def test_the_llm_stream_is_always_closed_after_the_token_loop():
    src = (ROOT / "turn_streamer.py").read_text(encoding="utf-8")
    loop = src.index("async for token in _token_iter:")
    tail = src[loop:loop + 40000]
    assert '_aclose = getattr(_token_iter, "aclose", None)' in tail
    assert tail.index("finally:") < tail.index("t_llm_done = time.monotonic()")


def test_the_tts_stream_is_closed_on_barge_in_after_silencing():
    src = (ROOT / "tts_playback.py").read_text(encoding="utf-8")
    branch = src[src.index('logger.info(f"Barge-in interrupted TTS for call {call_id}")'):]
    branch = branch[: branch.index("                    break\n")]
    assert "await _tts_iter.aclose()" in branch
    assert branch.index("clear_output_buffer") < branch.index("await _tts_iter.aclose()")


def test_a_total_voice_failure_tells_the_browser():
    src = (ROOT / "tts_playback.py").read_text(encoding="utf-8")
    assert '"code": "voice_unavailable"' in src
