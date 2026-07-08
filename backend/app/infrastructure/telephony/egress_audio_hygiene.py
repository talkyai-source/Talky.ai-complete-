"""Egress audio hygiene — kill the intermittent telephony "buzz" before µ-law.

Root cause (2026-07-08, investigated on call +447754566590 / pipeline
1bb80967): the callee occasionally hears a faint buzz/hum under the agent's
voice. It is NOT crude resample aliasing — egress uses soxr (band-limited
sinc), which would buzz on *every* call, not a few. Two evidence-backed,
intermittent sources were found:

  1. µ-law near-zero buzz (THIS module fixes it):
     TTS int16 is downsampled and then **µ-law companded**. µ-law has its
     FINEST quantisation resolution near zero amplitude, so a near-silent
     stretch that carries a small **DC offset** or a **±1–2 LSB dither
     floor** does NOT encode as clean silence — it lands as an audible
     low-level buzz/hum on the wire. (Digital silence on µ-law is 0xFF; a
     dithered/offset near-zero signal is not.) TTS inter-word gaps and quiet
     tails are exactly such stretches, which is why it shows up on only some
     calls / some moments.

  2. Egress UNDERRUN buzz (NOT fixed here — it is the async-DB-adapter item):
     when the event loop stalls (slow Groq turns, sync-DB blocking), the
     real-time TTS pacer drifts and the C++ gateway jitter buffer underruns
     (see ``telephony_audio_gap`` warnings). A repeated/garbage frame then
     buzzes. That is a scheduling problem, not an encoding one. This pass
     still *reduces* how bad an underrun-repeated near-zero frame sounds
     (a gated frame is clean silence, not a buzzing floor).

The fix: on the 16 kHz int16 TTS buffer, just before downsample + µ-law,
remove per-frame DC and gate true near-silence to hard digital zero so the
µ-law encoder emits clean silence instead of a near-zero buzz floor.

Transparent to speech: the gate only fires below ~-62 dBFS peak — far under
any audible speech energy — and DC removal on a speech frame (mean ≈ 0) is a
no-op. Pure, stateless, no I/O; safe to call on every egress chunk.
"""
from __future__ import annotations

# Peak int16 magnitude below which a whole frame is treated as silence.
# 24 / 32768 ≈ -62.7 dBFS — inaudible; real speech (even quiet fricatives)
# sits well above this, so gating here can never clip a word.
_GATE_PEAK: int = 24


def clean_egress_pcm16(pcm16: bytes) -> bytes:
    """Return ``pcm16`` (little-endian int16) with DC removed and true
    near-silence gated to clean digital zero, so the downstream µ-law encode
    emits real silence instead of an audible near-zero buzz/hum.

    Fail-soft: on any problem the input is returned unchanged — audio hygiene
    must never drop or corrupt a live turn's audio."""
    try:
        if not pcm16 or len(pcm16) < 2:
            return pcm16
        import numpy as np

        x = np.frombuffer(pcm16, dtype=np.int16)
        if x.size == 0:
            return pcm16

        xf = x.astype(np.float32)
        # Remove DC offset (a constant/near-constant offset is the hum source
        # under µ-law). For a voiced speech frame the mean is ≈ 0, so this is a
        # no-op; for a silent-but-offset frame it re-centres it on zero.
        xf = xf - float(xf.mean())

        # Noise gate: if the frame is truly near-silent, emit CLEAN silence so
        # µ-law encodes 0xFF instead of a dithered near-zero buzz floor.
        if float(np.abs(xf).max()) < _GATE_PEAK:
            return b"\x00" * len(pcm16)

        return np.clip(xf, -32768, 32767).astype(np.int16).tobytes()
    except Exception:
        return pcm16
