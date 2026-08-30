from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_recording_gate_blocks_both_sides_but_keeps_live_media_flowing():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "inbound-call",
        {
            "adapter": adapter,
            "pbx_call_id": "pbx-inbound-call",
            "recording_enabled": False,
        },
    )
    session = gateway._sessions["inbound-call"]

    await gateway.on_audio_received("inbound-call", b"\xff" * 160)
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        # One complete 20 ms PCMU packet after s16le -> ulaw conversion.
        await gateway.send_audio("inbound-call", b"\x00\x00" * 160)

    # STT and TTS delivery continue, but neither side is retained before the
    # disclosure/policy gate opens.
    assert session.input_queue.qsize() == 1
    adapter.send_tts_audio.assert_awaited()
    assert session.recording_buffer == []
    assert session.recording_buffer_bytes == 0
    assert session.tts_recording_buffer == []
    assert session.tts_recording_buffer_bytes == 0
    assert session.caller_sample_count == 0

    assert gateway.set_recording_enabled("inbound-call", True) is True
    await gateway.on_audio_received("inbound-call", b"\xff" * 160)
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        await gateway.send_audio("inbound-call", b"\x00\x00" * 160)

    assert session.recording_buffer_bytes > 0
    assert session.tts_recording_buffer_bytes > 0
    assert session.caller_sample_count > 0


@pytest.mark.asyncio
async def test_closing_recording_gate_purges_already_buffered_audio():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-policy-change",
        {"adapter": adapter, "pbx_call_id": "pbx-policy-change"},
    )

    await gateway.on_audio_received("call-policy-change", b"\xff" * 160)
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        await gateway.send_audio("call-policy-change", b"\x00\x00" * 80)
    session = gateway._sessions["call-policy-change"]
    assert session.recording_buffer_bytes > 0
    assert session.tts_recording_buffer_bytes > 0

    assert gateway.set_recording_enabled("call-policy-change", False) is True

    assert session.recording_enabled is False
    assert session.recording_buffer == []
    assert session.tts_recording_buffer == []
    assert session.recording_buffer_bytes == 0
    assert session.tts_recording_buffer_bytes == 0
    assert session.caller_sample_count == 0
    assert session.agent_rec_cursor == 0


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


# ---------------------------------------------------------------------------
# QUICK-WIN fix #1 — f32le chunk-split no longer drops audio.
#
# send_audio's old carry (in tts_playback.py) only kept 2-byte (int16)
# alignment. For an f32le provider (Google/Cartesia, 4 bytes/sample), a
# chunk whose length is even-but-not-a-multiple-of-4 used to sail straight
# through that guard into pcm_float32_to_int16() -> np.frombuffer(...,
# dtype=float32), which raises ValueError("buffer size must be a multiple
# of element size") on a misaligned buffer. send_audio's except-block then
# swallowed the exception and dropped the ENTIRE chunk instead of just the
# orphan tail.
#
# The fix moves the alignment carry into TelephonyMediaGateway.send_audio
# itself, keyed on the session's _tts_source_format (align=4 for f32le,
# align=2 for s16le), so a 4002-byte f32le chunk (not a multiple of 4) is
# no longer dropped: it's fully accepted across two calls — the first 4000
# bytes convert immediately and the trailing 2-byte orphan is carried and
# combined with the next chunk instead of blowing up the whole chunk.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_audio_f32le_chunk_not_multiple_of_4_is_not_dropped():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "f32le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-f32le-split", {"adapter": adapter, "pbx_call_id": "pbx-f32le-split"},
    )
    session = gateway._sessions["call-f32le-split"]

    # 4002 bytes: even (passes an int16-only alignment guard) but NOT a
    # multiple of 4 — this is exactly the shape that used to crash
    # pcm_float32_to_int16() and get silently dropped.
    chunk = b"\x00\x01\x02\x03" * 1000 + b"\xff\xee"
    assert len(chunk) == 4002
    assert len(chunk) % 2 == 0
    assert len(chunk) % 4 != 0

    # Must not raise, and must not be silently swallowed as a dropped chunk:
    # 4000 bytes (1000 float32 samples) are processed and land in the
    # recording buffer on this call; the trailing 2-byte orphan is carried
    # forward instead of vanishing.
    await gateway.send_audio("call-f32le-split", chunk)

    assert session.tts_recording_buffer_bytes > 0
    assert session._tts_pending_bytes == b"\xff\xee"

    # Feeding the next chunk completes the orphan sample instead of losing
    # it forever — 2 pending bytes + a 6-byte chunk is a multiple of 4, so
    # nothing is left dangling after this call.
    await gateway.send_audio("call-f32le-split", b"\x04\x05\x06\x07\x08\x09")
    assert session._tts_pending_bytes == b""


# ---------------------------------------------------------------------------
# FIX — barge-in must clear the orphan partial-sample carry too.
#
# clear_output_buffer used to reset tts_buffer and _tts_send_deadline but
# NOT _tts_pending_bytes. A barge-in truncates a TTS stream mid-chunk-
# boundary, so 1-3 stale bytes could be left sitting in _tts_pending_bytes
# waiting for a "next chunk" that will never come (this utterance is being
# discarded). Left uncleared, those bytes silently prepend onto the FIRST
# chunk of the NEXT utterance and byte-shift the whole f32le float stream —
# permanent loud static for the rest of the call. These tests pin the fix
# for both barge-in (clear_output_buffer) and normal completion
# (flush_tts_buffer, which also has no "next chunk" coming).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_output_buffer_resets_tts_pending_bytes():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "f32le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-bargein-pending", {"adapter": adapter, "pbx_call_id": "pbx-bargein-pending"},
    )
    session = gateway._sessions["call-bargein-pending"]

    # Leave a genuine 2-byte orphan fragment behind (4-byte f32le alignment).
    await gateway.send_audio("call-bargein-pending", b"\x00\x01\x02\x03" + b"\xff\xee")
    assert session._tts_pending_bytes == b"\xff\xee"

    await gateway.clear_output_buffer("call-bargein-pending")

    assert session._tts_pending_bytes == b""
    assert session.tts_buffer == b""
    assert session._tts_send_deadline is None


@pytest.mark.asyncio
async def test_barge_in_then_next_turn_does_not_leave_misaligned_carry():
    """End-to-end: send_audio leaves an orphan fragment -> barge-in clears it
    via clear_output_buffer -> the NEXT turn's send_audio starts from a clean
    slate instead of prepending stale bytes onto the new utterance."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "f32le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-bargein-next-turn", {"adapter": adapter, "pbx_call_id": "pbx-bargein-next-turn"},
    )
    session = gateway._sessions["call-bargein-next-turn"]

    # Turn 1: mid-stream chunk leaves a 3-byte orphan (not a multiple of 4).
    await gateway.send_audio("call-bargein-next-turn", b"\x00\x01\x02\x03" * 10 + b"\xaa\xbb\xcc")
    assert session._tts_pending_bytes == b"\xaa\xbb\xcc"

    # Barge-in fires — the rest of turn 1 is discarded.
    await gateway.clear_output_buffer("call-bargein-next-turn")
    assert session._tts_pending_bytes == b""

    # Turn 2 starts clean: a chunk that is itself a clean multiple of 4
    # must NOT pick up the discarded turn-1 orphan bytes as a prefix.
    await gateway.send_audio("call-bargein-next-turn", b"\x10\x11\x12\x13" * 5)
    assert session._tts_pending_bytes == b""


@pytest.mark.asyncio
async def test_flush_tts_buffer_discards_orphan_pending_bytes():
    """Normal (non-barge-in) utterance completion must also drop any
    leftover partial-sample fragment — it has no completing bytes coming
    either, so carrying it into the next utterance would misalign it the
    same way an uncleared barge-in would."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "f32le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-flush-pending", {"adapter": adapter, "pbx_call_id": "pbx-flush-pending"},
    )
    session = gateway._sessions["call-flush-pending"]

    await gateway.send_audio("call-flush-pending", b"\x00\x01\x02\x03" + b"\x99\x88")
    assert session._tts_pending_bytes == b"\x99\x88"

    await gateway.flush_tts_buffer("call-flush-pending")

    assert session._tts_pending_bytes == b""


@pytest.mark.asyncio
async def test_flush_tts_buffer_clears_buffer_even_when_send_fails():
    """A failed final send must not leave the padded packet in tts_buffer:
    the next utterance would be prepended with those stale bytes and the
    whole stream byte-shifted."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-flush-fail", {"adapter": adapter, "pbx_call_id": "pbx-flush-fail"},
    )
    session = gateway._sessions["call-flush-fail"]

    # A partial (< 160 byte) packet left in the packetisation buffer.
    session.tts_buffer = b"\x01" * 40
    adapter.send_tts_audio.side_effect = RuntimeError("pbx write failed")

    await gateway.flush_tts_buffer("call-flush-fail")

    assert session.tts_buffer == b""


def _force_audio_route_failures(gateway, call_id, count):
    """Drive on_audio_received down its decode-failure branch `count` times."""
    session = gateway._sessions[call_id]
    session.consecutive_audio_route_failures = count - 1
    return session


@pytest.mark.asyncio
async def test_force_hangup_failure_is_logged_and_deactivates_session():
    """The audio-route force-end used to fire-and-forget hangup_call and zero
    the failure counter immediately: a raising hangup vanished silently and
    the call stayed 'active' with a dead audio route until the 300s watchdog."""
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-hangup-fail", {"adapter": adapter, "pbx_call_id": "pbx-hangup-fail"},
    )
    session = _force_audio_route_failures(
        gateway, "call-hangup-fail", gateway._AUDIO_ROUTE_FORCE_END_THRESHOLD
    )
    adapter.hangup.side_effect = RuntimeError("ARI down")

    # Force the decode/resample branch to fail on this chunk.
    with patch(
        "app.utils.audio_utils.ulaw_to_pcm", side_effect=RuntimeError("bad packet")
    ):
        await gateway.on_audio_received("call-hangup-fail", b"\xff" * 160)

    task = session.force_hangup_task
    assert task is not None
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # let the done-callback run

    # Counter is NOT zeroed (the hangup never succeeded) and the session is
    # marked inactive so the normal teardown path takes over.
    assert session.consecutive_audio_route_failures >= (
        gateway._AUDIO_ROUTE_FORCE_END_THRESHOLD
    )
    assert session.is_active is False


@pytest.mark.asyncio
async def test_force_hangup_success_clears_failure_counter():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    await gateway.on_call_started(
        "call-hangup-ok", {"adapter": adapter, "pbx_call_id": "pbx-hangup-ok"},
    )
    session = _force_audio_route_failures(
        gateway, "call-hangup-ok", gateway._AUDIO_ROUTE_FORCE_END_THRESHOLD
    )

    with patch(
        "app.utils.audio_utils.ulaw_to_pcm", side_effect=RuntimeError("bad packet")
    ):
        await gateway.on_audio_received("call-hangup-ok", b"\xff" * 160)

    task = session.force_hangup_task
    assert task is not None
    await task
    await asyncio.sleep(0)

    adapter.hangup.assert_awaited_once_with("pbx-hangup-ok")
    assert session.consecutive_audio_route_failures == 0
    assert session.is_active is True


@pytest.mark.asyncio
async def test_send_audio_transient_tts_failure_retries_and_clears_counter_on_success():
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    # First send fails, second succeeds
    adapter.send_tts_audio.side_effect = [RuntimeError("gateway blip"), None]

    await gateway.on_call_started(
        "call-tts-transient", {"adapter": adapter, "pbx_call_id": "pbx-tts-transient"},
    )
    session = gateway._sessions["call-tts-transient"]

    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        # Send chunk 1 (fails once)
        await gateway.send_audio("call-tts-transient", b"\x00\x00" * 160)
        assert session.consecutive_tts_failures == 1
        assert session.chunks_sent == 0

        # Send chunk 2 (succeeds)
        await gateway.send_audio("call-tts-transient", b"\x00\x00" * 160)
        assert session.consecutive_tts_failures == 0
        assert session.chunks_sent == 1


@pytest.mark.asyncio
async def test_send_audio_typed_tts_failure_propagates_immediately():
    from app.infrastructure.telephony.asterisk_adapter import TtsDeliveryError

    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = AsyncMock()
    adapter.send_tts_audio.side_effect = TtsDeliveryError("gateway connection refused")

    await gateway.on_call_started(
        "call-tts-circuit-breaker", {"adapter": adapter, "pbx_call_id": "pbx-tts-cb"},
    )
    session = gateway._sessions["call-tts-circuit-breaker"]

    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        with pytest.raises(TtsDeliveryError) as exc:
            await gateway.send_audio("call-tts-circuit-breaker", b"\x00\x00" * 160)

        assert "gateway connection refused" in str(exc.value)
        assert session.consecutive_tts_failures == 1
        assert session.chunks_sent == 0
