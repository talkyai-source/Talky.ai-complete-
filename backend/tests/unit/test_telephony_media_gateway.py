from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway


@pytest.mark.asyncio
async def test_hangup_call_uses_adapter_pbx_call_id():
    gateway = TelephonyMediaGateway()
    adapter = AsyncMock()

    await gateway.on_call_started(
        "pipeline-call-id",
        {"adapter": adapter, "pbx_call_id": "pbx-call-id"},
    )

    result = await gateway.hangup_call("pipeline-call-id", "user_goodbye")

    assert result is True
    adapter.hangup.assert_awaited_once_with("pbx-call-id")


@pytest.mark.asyncio
async def test_hangup_call_returns_false_for_missing_session():
    gateway = TelephonyMediaGateway()

    result = await gateway.hangup_call("missing-call-id", "user_goodbye")

    assert result is False


# ---------------------------------------------------------------------------
# FIX #13 — tts_recording_buffer must be capped like recording_buffer.
#
# on_audio_received already caps the caller-side recording_buffer at
# _MAX_RECORDING_BYTES (60 min @ internal sample rate) with drop-oldest
# eviction. send_audio appended every TTS chunk to tts_recording_buffer with
# NO cap at all, so a long agent-heavy call grows it without bound. These
# tests pin the fix: tts_recording_buffer_bytes is tracked and the list is
# evicted from the front once it exceeds the same byte budget.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_audio_caps_tts_recording_buffer_bytes():
    gateway = TelephonyMediaGateway()
    # 8kHz internal rate so send_audio skips the 16->8 resample step (no
    # soxr dependency needed for this unit test) and the µ-law encode is the
    # only conversion in play.
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-cap-test", {"adapter": adapter, "pbx_call_id": "pbx-cap-test"},
    )

    session = gateway._sessions["call-cap-test"]
    max_bytes = (gateway._sample_rate * 2) * 60 * 60  # mirrors send_audio's cap

    # send_audio's real-time TTS pacing loop (unrelated to the recording
    # buffer — it throttles delivery to the C++ gateway) sleeps to keep the
    # gateway's pre-buffer bounded at ~200ms. Driving enough audio through
    # to overflow a 60-minute recording cap would otherwise make this test
    # take real wall-clock minutes; patch asyncio.sleep out so we exercise
    # the actual encode + buffer-accounting + eviction code with no delay.
    chunk = b"\x00\x01" * 100_000  # 200,000 bytes of s16le silence per call
    total_pushed = 0
    iterations = 0
    with (
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
        # A huge batch size collapses the inner wire-packetization loop
        # (unrelated to what's under test) to ~1 pass per send_audio call,
        # instead of one 20ms-packet pass per 320 bytes of the ~57.6MB this
        # test needs to push to overflow the cap.
        patch.dict(os.environ, {"TELEPHONY_TTS_BATCH_PACKETS": "1000000"}),
        # pcm_to_ulaw is a pure-Python per-sample loop — fine for real
        # traffic but would make pushing ~57.6MB through it (what's needed
        # to exceed the recording cap) take real minutes in a unit test.
        # It runs downstream of (and has no bearing on) the
        # tts_recording_buffer accounting under test here, so stub it to a
        # cheap fixed-ratio stand-in.
        patch(
            "app.utils.audio_utils.pcm_to_ulaw",
            new=lambda pcm: bytes(len(pcm) // 2),
        ),
    ):
        while total_pushed <= max_bytes + len(chunk) * 5:
            await gateway.send_audio("call-cap-test", chunk)
            total_pushed += len(chunk)
            iterations += 1
            if iterations > 2000:  # safety valve against an infinite loop bug
                raise AssertionError("test runaway — cap never engaged")

    assert session.tts_recording_buffer_bytes <= max_bytes
    # The tracked counter must match what's actually still in the list —
    # otherwise the counter and the eviction are out of sync.
    actual_bytes = sum(len(pcm) for _, pcm in session.tts_recording_buffer)
    assert session.tts_recording_buffer_bytes == actual_bytes
    # Eviction actually dropped data (buffer isn't just growing unbounded).
    assert actual_bytes < total_pushed


@pytest.mark.asyncio
async def test_clear_recording_buffer_resets_tts_byte_counter():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-clear-test", {"adapter": adapter, "pbx_call_id": "pbx-clear-test"},
    )

    await gateway.send_audio("call-clear-test", b"\x00\x01" * 100)
    session = gateway._sessions["call-clear-test"]
    assert session.tts_recording_buffer_bytes > 0

    gateway.clear_recording_buffer("call-clear-test")

    assert session.tts_recording_buffer == []
    assert session.tts_recording_buffer_bytes == 0
    assert session.recording_buffer == []
    assert session.recording_buffer_bytes == 0
