from __future__ import annotations

from app.domain.services.voice_pipeline.emergency_audio import (
    VOICE_HOLD,
    VOICE_TERMINAL,
    load_emergency_clip,
)


def test_emergency_clips_are_checksum_pinned_complete_pcmu_frames():
    for name in (VOICE_HOLD, VOICE_TERMINAL):
        clip = load_emergency_clip(name)
        assert clip
        assert len(clip) % 160 == 0
        assert len(clip) <= 96_000


def test_emergency_clips_are_distinct_messages():
    assert load_emergency_clip(VOICE_HOLD) != load_emergency_clip(VOICE_TERMINAL)
