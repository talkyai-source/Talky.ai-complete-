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

import logging
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


@dataclass
class TTSFailoverPolicy:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0
    # Optional mapping of primary voice_id → secondary voice_id. Lets a
    # tenant say "use Cartesia's Tessa normally, ElevenLabs' Bella on
    # fallback" so the two voices sound similar. Missing entries pass
    # through unchanged.
    voice_id_map: Dict[str, str] | None = None


class ResilientTTSProvider(TTSProvider):
    """Primary + secondary TTS with circuit-breaker-gated startup
    failover. Satisfies `TTSProvider` so call sites see a single
    opaque provider."""

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
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 16000,
        **kwargs,
    ) -> AsyncIterator[AudioChunk]:
        """Stream TTS audio. Promotes secondary on primary failure
        during synthesis start; raises on mid-stream drops so the
        caller can decide recovery strategy."""
        # Fast-path: circuit open → go straight to secondary if we
        # have one. Otherwise we let the primary raise so the caller
        # sees the real error.
        if self._breaker.state.value == "open" and self._secondary is not None:
            logger.info("resilient_tts_primary_circuit_open — using secondary")
            async for chunk in self._stream_secondary(text, voice_id, sample_rate, **kwargs):
                yield chunk
            return

        started = False
        try:
            async with self._breaker:
                async for chunk in self._primary.stream_synthesize(
                    text, voice_id, sample_rate, **kwargs,
                ):
                    started = True
                    yield chunk
                return
        except CircuitOpenError:
            # Breaker tripped between the fast-path check and the
            # context-manager enter — race-safe to retry on secondary.
            if self._secondary is not None:
                async for chunk in self._stream_secondary(text, voice_id, sample_rate, **kwargs):
                    yield chunk
            return
        except Exception as exc:
            # If we never started yielding, the failure was in the
            # handshake / header — safe to retry on secondary with the
            # SAME text. If we DID start, the caller already heard part
            # of the utterance on the primary voice; swapping mid-
            # utterance is worse than truncating, so re-raise and let
            # the caller decide.
            if started:
                logger.error(
                    "resilient_tts_mid_stream_drop provider=%s err=%s — "
                    "utterance truncated, not failing over",
                    self._primary.name, exc,
                )
                raise

            if self._secondary is None:
                logger.error(
                    "resilient_tts_startup_failed_no_secondary provider=%s err=%s",
                    self._primary.name, exc,
                )
                raise

            logger.warning(
                "resilient_tts_startup_failed_failover_to=%s err=%s",
                self._secondary.name, exc,
            )
            async for chunk in self._stream_secondary(text, voice_id, sample_rate, **kwargs):
                yield chunk

    # ──────────────────────────────────────────────────────────────────

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
        async for chunk in self._secondary.stream_synthesize(
            text, mapped_voice, sample_rate, **kwargs,
        ):
            yield chunk
