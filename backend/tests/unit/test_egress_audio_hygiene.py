"""Tests for egress_audio_hygiene.clean_egress_pcm16 — the µ-law buzz fix."""
import numpy as np

from app.infrastructure.telephony.egress_audio_hygiene import (
    clean_egress_pcm16,
    _GATE_PEAK,
)


def _pcm(samples) -> bytes:
    return np.asarray(samples, dtype=np.int16).tobytes()


def test_near_silence_with_dc_offset_is_gated_to_clean_silence():
    # A "silent" frame carrying a DC offset + tiny dither — the exact buzz
    # source under µ-law. Must become HARD digital silence.
    frame = np.full(320, 60, dtype=np.int16)          # DC offset 60
    frame[::2] += 1                                    # ±1 LSB dither floor
    out = clean_egress_pcm16(frame.tobytes())
    assert out == b"\x00" * len(frame.tobytes())
    assert set(np.frombuffer(out, dtype=np.int16).tolist()) == {0}


def test_loud_speech_passes_through_essentially_unchanged():
    # A voiced tone well above the gate — must NOT be gated or clipped.
    t = np.arange(320)
    tone = (8000 * np.sin(2 * np.pi * 300 * t / 16000)).astype(np.int16)
    out = clean_egress_pcm16(tone.tobytes())
    arr = np.frombuffer(out, dtype=np.int16)
    assert arr.size == tone.size
    assert int(np.abs(arr).max()) > _GATE_PEAK * 10          # not gated
    # DC removal on a ~zero-mean tone is a no-op within rounding.
    assert abs(float(arr.mean())) < 5


def test_dc_offset_removed_from_audible_frame():
    t = np.arange(320)
    tone = (2000 * np.sin(2 * np.pi * 250 * t / 16000)) + 500  # +500 DC
    out = clean_egress_pcm16(tone.astype(np.int16).tobytes())
    arr = np.frombuffer(out, dtype=np.int16)
    assert abs(float(arr.mean())) < 10       # DC gone


def test_just_below_gate_is_silenced_just_above_survives():
    below = np.full(160, _GATE_PEAK - 1, dtype=np.int16)
    # subtract-mean zeroes a constant frame, so this is silence regardless —
    # use an alternating pattern to keep a real peak just under the gate.
    below = np.array([_GATE_PEAK - 1, -(_GATE_PEAK - 1)] * 80, dtype=np.int16)
    assert clean_egress_pcm16(below.tobytes()) == b"\x00" * len(below.tobytes())

    above = np.array([_GATE_PEAK + 50, -(_GATE_PEAK + 50)] * 80, dtype=np.int16)
    out = clean_egress_pcm16(above.tobytes())
    assert int(np.abs(np.frombuffer(out, dtype=np.int16)).max()) >= _GATE_PEAK


def test_empty_and_malformed_input_returned_unchanged():
    assert clean_egress_pcm16(b"") == b""
    assert clean_egress_pcm16(b"\x01") == b"\x01"          # < 2 bytes
    odd = b"\x01\x02\x03"                                   # odd length int16
    assert clean_egress_pcm16(odd) == odd                  # fail-soft, unchanged


def test_output_length_always_matches_input():
    for n in (2, 160, 320, 640):
        buf = (np.random.default_rng(0).integers(-9000, 9000, n) ).astype(np.int16).tobytes()
        assert len(clean_egress_pcm16(buf)) == len(buf)
