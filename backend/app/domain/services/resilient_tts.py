"""Resilient TTS wrapper (T1.3).

Wraps a primary TTS provider with circuit-breaker-gated failover to a
secondary.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DESIGN CHOICES

- **Fail fast on synthesis start, not mid-stream.** TTS failures
  during header/handshake (connection refused, auth expired, rate
  limit) are recoverable — we catch them, flip to secondary, and
  replay the SAME text there. This gives the caller one continuous
  audio stream without a voice change.

- **Mid-stream drops are utterance-level errors.** If the primary
  closes the socket mid-synthesis, we ABORT the current utterance
  and raise. The caller (voice pipeline) can:
    a) speak a short "one moment" recovery phrase via secondary, or
    b) just swallow the truncation and let the LLM re-prompt next
       turn.
  We deliberately do NOT stitch half-rendered audio from two different
  voices — that's a worse caller experience than a brief silence.

- **Breaker governs the primary only.** If the primary's circuit is
  open when synthesize is called, we go straight to secondary. No
  probing mid-call.

- **Same sample-rate requirement.** The wrapper does not resample —
  both providers must be initialised at the same sample rate or the
  media gateway will reject the mismatched chunks. Config validation
  is the caller's responsibility.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Integration — this wrapper is drop-in for `TTSProvider`. The wiring
is a one-line change in the TTS factory once the secondary provider
client is configured.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk
from app.utils.resilience import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass
class TTSFailoverPolicy:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0
    # Optional mapping of primary voice_id → secondary voice_id. Lets a
    # tenant say "use Cartesia's Tessa normally, ElevenLabs' Bella on
    # fallback" so the two voices sound similar. Cross-vendor fallback
    # requires a mapping; a primary voice ID is never sent to another vendor.
    voice_id_map: Dict[str, str] | None = None


class ResilientTTSProvider(TTSProvider):
    """Primary + secondary TTS with circuit-breaker-gated startup
    failover. Satisfies `TTSProvider` so call sites see a single
    opaque provider."""

    handles_startup_recovery = True
    startup_attempt_timeout_seconds = 3.0

    @property
    def startup_timeout_seconds(self) -> float:
        """Total startup budget consumed here, not retried again by playback."""
        return self.startup_attempt_timeout_seconds * (2 if self._secondary else 1)

    async def _bounded_start(self, iterator):
        """Only first audio is retriable. Close sockets immediately on cancel."""
        async with aclosing(iterator) as stream:
            async def first_audio():
                async for chunk in stream:
                    if getattr(chunk, "data", None):
                        return chunk
                raise RuntimeError("TTS provider completed without audio")

            first = await asyncio.wait_for(first_audio(), self.startup_attempt_timeout_seconds)
            yield first
            async for chunk in stream:
                yield chunk

    def __init__(
        self,
        primary: TTSProvider,
        secondary: Optional[TTSProvider] = None,
        policy: Optional[TTSFailoverPolicy] = None,
    ):
        self._primary = primary
        self._secondary = secondary
        self._policy = policy or TTSFailoverPolicy()
        self._breaker = CircuitBreaker(
            name=f"tts-{primary.name}",
            failure_threshold=self._policy.failure_threshold,
            recovery_timeout=self._policy.recovery_timeout_seconds,
        )

    # ──────────────────────────────────────────────────────────────────
    # TTSProvider interface
    # ──────────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return f"resilient({self._primary.name})"

    async def initialize(self, config: dict) -> None:
        await self._primary.initialize(config)
        if self._secondary is not None:
            try:
                await self._secondary.initialize(config)
            except Exception as exc:
                logger.warning(
                    "resilient_tts_secondary_init_failed provider=%s err=%s",
                    self._secondary.name, exc,
                )
                self._secondary = None

    async def cleanup(self) -> None:
        for p in (self._primary, self._secondary):
            if p is None:
                continue
            try:
                await p.cleanup()
            except Exception as exc:
                logger.debug("resilient_tts_cleanup_error provider=%s err=%s", p.name, exc)

    async def get_available_voices(self) -> List[Dict]:
        # Voices are discovery-only; prefer primary. On primary
        # failure we surface secondary's voices so the UI still works.
        try:
            return await self._primary.get_available_voices()
        except Exception:
            if self._secondary is not None:
                return await self._secondary.get_available_voices()
            raise

    async def stream_synthesize(
        self, text: str, voice_id: str, sample_rate: int = 16000, **kwargs,
    ) -> AsyncIterator[AudioChunk]:
        """One primary attempt then one fallback, before the first audio only."""
        started = False
        try:
            async with self._breaker:
                async with aclosing(self._bounded_start(self._primary.stream_synthesize(
                    text, voice_id, sample_rate, **kwargs,
                ))) as stream:
                    async for chunk in stream:
                        started = True
                        yield chunk
            return
        except Exception as exc:
            if started or self._secondary is None:
                raise
            logger.warning(
                "resilient_tts_startup_failed_failover_to=%s err=%s",
                self._secondary.name, type(exc).__name__,
            )
        async with aclosing(self._stream_secondary(text, voice_id, sample_rate, **kwargs)) as stream:
            async for chunk in stream:
                yield chunk

    # ──────────────────────────────────────────────────────────────────

    # Which PCM byte format each TTS vendor yields. The gateway is told the
    # primary's format ONCE per session (voice_orchestrator tts_source_format),
    # so a secondary that yields the other format would have its bytes
    # reinterpreted — four float32 samples became eight unrelated int16 values
    # in the 2026-09-06 audit (F07). Normalise the secondary to the primary.
    _F32_PROVIDERS = frozenset({"cartesia", "google"})

    @classmethod
    def _pcm_format(cls, provider_name: str) -> str:
        base = str(provider_name or "").lower()
        for vendor in cls._F32_PROVIDERS:
            if vendor in base:
                return "f32le"
        return "s16le"

    @staticmethod
    def _convert_pcm(data: bytes, src: str, dst: str) -> bytes:
        if src == dst or not data:
            return data
        import numpy as np

        if src == "f32le" and dst == "s16le":
            usable = len(data) - (len(data) % 4)
            samples = np.frombuffer(data[:usable], dtype=np.float32)
            return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        if src == "s16le" and dst == "f32le":
            usable = len(data) - (len(data) % 2)
            samples = np.frombuffer(data[:usable], dtype=np.int16)
            return (samples.astype(np.float32) / 32768.0).tobytes()
        return data

    async def _stream_secondary(
        self,
        text: str,
        voice_id: str,
        sample_rate: int,
        **kwargs,
    ) -> AsyncIterator[AudioChunk]:
        assert self._secondary is not None  # caller-guarded
        mapped_voice = (
            self._policy.voice_id_map.get(voice_id, voice_id)
            if self._policy.voice_id_map
            else voice_id
        )
        vendors = {"cartesia", "elevenlabs", "deepgram", "google"}
        if (
            self._primary.name in vendors and self._secondary.name in vendors
            and self._primary.name != self._secondary.name
            and not (self._policy.voice_id_map or {}).get(voice_id)
        ):
            raise RuntimeError("Cross-vendor TTS fallback requires a secondary voice mapping")
        src_fmt = self._pcm_format(self._secondary.name)
        dst_fmt = self._pcm_format(self._primary.name)
        carry = b""
        async with aclosing(self._bounded_start(self._secondary.stream_synthesize(
            text, mapped_voice, sample_rate, **kwargs,
        ))) as stream:
            async for chunk in stream:
                if getattr(chunk, "sample_rate", sample_rate) != sample_rate:
                    raise RuntimeError("Secondary TTS sample rate does not match the media gateway")
                if src_fmt != dst_fmt and getattr(chunk, "data", None):
                    data = carry + chunk.data
                    width = 4 if src_fmt == "f32le" else 2
                    usable = len(data) - len(data) % width
                    carry = data[usable:]
                    if not usable:
                        continue
                    chunk = AudioChunk(
                        data=self._convert_pcm(data[:usable], src_fmt, dst_fmt),
                        sample_rate=sample_rate,
                        channels=getattr(chunk, "channels", 1),
                    )
                yield chunk
        if carry:
            raise RuntimeError("Secondary TTS ended with an incomplete PCM sample")
