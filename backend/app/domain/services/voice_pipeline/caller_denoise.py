"""Noise suppression for the caller audio that goes to speech recognition.

Only the STT feed is cleaned. audio_ingest measures the raw frame first, so
barge-in onset, the echo probe and the silence monitor keep reading the
caller's real level. The resilient_stt watchdog does read the STT feed, so with
cleaning on its RMS gate sees the cleaned level (steady noise no longer counts
as speech); audio_ingest logs both levels so that change is visible.

Engine: WebRTC noise suppression (webrtc-noise-gain), 16 kHz mono PCM16 in
10 ms frames, auto gain off. Off unless CALLER_NOISE_SUPPRESSION=webrtc;
CALLER_NOISE_SUPPRESSION_LEVEL is 1-4 (default 2). Runs only when both the STT
rate and the gateway's feed rate are 16 kHz. A part frame waits for the next
chunk (only the last one, under 10 ms, is not sent when the stream ends), and
any engine error turns cleaning off for the rest of the stream and passes the
raw audio through.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_BYTES = 320  # 10 ms of 16 kHz mono PCM16
DEFAULT_LEVEL = 2

Engine = Callable[[bytes], bytes]


def _settings() -> tuple[str, int]:
    engine = (os.getenv("CALLER_NOISE_SUPPRESSION") or "off").strip().lower()
    try:
        level = int(os.getenv("CALLER_NOISE_SUPPRESSION_LEVEL") or DEFAULT_LEVEL)
    except ValueError:
        level = DEFAULT_LEVEL
    return engine, min(4, max(1, level))


def _webrtc_engine(level: int) -> Engine:
    from webrtc_noise_gain import AudioProcessor

    processor = AudioProcessor(0, level)  # 0 = no auto gain: STT hears the caller's own level
    return lambda frame: processor.Process10ms(frame).audio


class CallerDenoiser:
    """One per caller-audio stream; state is per call."""

    def __init__(
        self,
        call_id: str,
        *,
        sample_rate,
        feed_sample_rate=SAMPLE_RATE,
        engine_factory: Optional[Callable[[int], Engine]] = None,
    ) -> None:
        self.call_id = call_id
        self.enabled = False
        self.reason = "off"
        self._engine: Optional[Engine] = None
        self._rest = b""
        name, level = _settings()
        if name == "webrtc":
            rates = (sample_rate, feed_sample_rate)
            if any(type(r) is not int or r != SAMPLE_RATE for r in rates):
                self.reason = "unsupported_sample_rate"
            else:
                try:
                    self._engine = (engine_factory or _webrtc_engine)(level)
                    self.enabled, self.reason = True, "on"
                except Exception as exc:
                    self.reason = "unavailable"
                    logger.warning(
                        "caller_denoise_unavailable call_id=%s error_type=%s",
                        call_id, type(exc).__name__,
                    )
        logger.info(
            "caller_denoise call_id=%s engine=%s level=%s enabled=%s reason=%s",
            call_id, name, level, self.enabled, self.reason,
        )

    def process(self, pcm: bytes) -> bytes:
        if not self.enabled:
            if self._rest:
                pcm, self._rest = self._rest + pcm, b""
            return pcm
        buf = self._rest + pcm
        whole = len(buf) - len(buf) % FRAME_BYTES
        out = bytearray()
        try:
            for i in range(0, whole, FRAME_BYTES):
                cleaned = self._engine(buf[i:i + FRAME_BYTES])
                if len(cleaned) != FRAME_BYTES:
                    raise ValueError(f"engine returned {len(cleaned)} bytes")
                out += cleaned
        except Exception as exc:
            self.enabled, self.reason, self._rest = False, "engine_error", b""
            logger.warning(
                "caller_denoise_disabled call_id=%s error_type=%s — raw audio from here on",
                self.call_id, type(exc).__name__,
            )
            return bytes(out) + buf[len(out):]
        self._rest = buf[whole:]
        return bytes(out)
