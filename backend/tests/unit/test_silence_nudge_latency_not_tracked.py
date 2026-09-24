"""A silence-monitor nudge must never be tracked as turn latency.

THE DEFECT (production, 2026-09-23)
------------------------------------
latency_tracker.mark_audio_start/mark_tts_end are first-write-wins (no
staleness check). The nudge ("Hello?"/"Still there?") plays through the SAME
synthesize_and_send_audio path as a real LLM reply, and audio_ingest.py's
nudge call site never passed `track_latency=False` — so the nudge's TTS
stamped the call's live LatencyMetrics object. When the real reply's audio
started LATER, the first-write-wins guard refused to overwrite the earlier
(nudge) timestamp, and total_latency_ms went negative:

    3a17c06c turn 0: "[OK] Turn 0 latency: -3795ms"
    6aaeb4dd turn 17: "[OK] Turn 17 latency: -6292ms"

(4 occurrences on 2026-09-23.) THE FIX (scoped to the nudge call site only —
latency_tracker.py's own first-write-wins guards are out of this fix's fence)
is for audio_ingest.py's nudge call to pass `track_latency=False`, exactly as
the existing canned-apology/recovery call sites already do (turn_streamer.py).

Drives the REAL AudioIngest._silence_monitor (via the harness in
test_audio_ingest_caller_first_silence.py) and inspects the actual kwargs of
the call to the (mocked) TTS collaborator — the collaborator is not the unit
under test; the call SITE's arguments are.
"""
from __future__ import annotations

import pytest

from tests.unit.test_audio_ingest_caller_first_silence import (
    _make_pipeline,
    _make_session,
    _run_until_silence_tick,
)


@pytest.mark.asyncio
async def test_opening_nudge_passes_track_latency_false():
    session = _make_session("user")
    pipeline = _make_pipeline()

    await _run_until_silence_tick(session, pipeline)

    calls = pipeline.synthesize_and_send_audio.await_args_list
    assert calls, "no nudge fired — nothing to check"
    for call in calls:
        assert call.kwargs.get("track_latency") is False, (
            f"nudge call {call} did not pass track_latency=False — its TTS "
            "will stamp the turn's live LatencyMetrics and can block the "
            "real reply's later, legitimate stamps"
        )
