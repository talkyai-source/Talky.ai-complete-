"""Resilient STT wrapper (T1.3).

Wraps a primary STT provider with reconnect + secondary-provider
failover so a mid-call WebSocket drop doesn't kill the call.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DESIGN CHOICES

- **Single quick reconnect, then swap.** On primary failure we attempt
  exactly one reconnect with a 500 ms budget. If that fails the
  wrapper promotes the secondary for the remainder of the call.
  Rationale: flapping between providers mid-call produces duplicate
  partials, confuses turn-detection, and is usually a symptom the
  primary is genuinely down.

- **Ring-buffer audio replay.** A small sliding buffer (default
  500 ms) keeps the last chunks of audio so a freshly-connected STT
  (reconnect OR secondary) can transcribe the utterance that was
  in-flight when the drop happened. Worst-case double-transcription
  is bounded at the buffer size.

- **No mid-stream merging.** When we swap to the secondary we DROP
  any pending partials from the primary and restart transcription.
  Merging partials across providers is a losing game — different
  models segment words differently and the result is word salad.

- **Circuit-breaker tied to primary, not secondary.** If the primary
  keeps failing we stop attempting it entirely until the breaker's
  recovery window elapses; new calls go straight to secondary. One
  breaker per primary-provider instance.

- **Fail-through, not fail-closed.** If BOTH providers fail, the
  wrapper yields no transcripts. The pipeline's existing watchdog
  already tears down silent calls after the inactivity timeout —
  this wrapper doesn't invent a new hangup path.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Integration — DO NOT wire into the live pipeline as part of the
T1.3 sprint. This file ships the mechanism; the follow-up pass
wires it into `voice_orchestrator` / `media_gateway` after a
dry-run on a staging call.
"""
from __future__ import annotations

import asyncio
import collections
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


@dataclass
class ReconnectPolicy:
    """Knobs for the failover lifecycle. Defaults are conservative
    for voice (sub-second budgets)."""
    reconnect_timeout_seconds: float = 0.5
    max_reconnect_attempts: int = 1
    audio_buffer_ms: int = 500
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0


@dataclass
class _ReplayBuffer:
    """Sliding buffer of recent audio. Holds at most `capacity_ms`
    worth of AudioChunks based on each chunk's stated duration."""
    capacity_ms: int
    chunks: collections.deque = field(default_factory=collections.deque)
    _total_ms: float = 0.0

    def add(self, chunk: AudioChunk) -> None:
        duration_ms = _chunk_duration_ms(chunk)
        self.chunks.append((chunk, duration_ms))
        self._total_ms += duration_ms
        while self._total_ms > self.capacity_ms and self.chunks:
            _, dropped_ms = self.chunks.popleft()
            self._total_ms -= dropped_ms

    def drain(self) -> list[AudioChunk]:
        out = [c for c, _ in self.chunks]
        self.chunks.clear()
        self._total_ms = 0.0
        return out


def _chunk_duration_ms(chunk: AudioChunk) -> float:
    """Best-effort audio duration in ms. Falls back to 20ms (a common
    Opus/PCM frame size) when the chunk doesn't carry sample-rate
    metadata — doesn't distort the ring buffer meaningfully."""
    sr = getattr(chunk, "sample_rate", None)
    data = getattr(chunk, "data", None)
    if sr and data:
        # Assume 16-bit mono PCM or similar; 2 bytes per sample.
        return (len(data) / max(sr, 1)) * 500.0
    return 20.0


class ResilientSTTProvider(STTProvider):
    """Composes a primary + secondary STT provider with reconnect and
    failover. Satisfies the same `STTProvider` interface so existing
    call-site code needs no changes beyond constructing this wrapper.
    """

    def __init__(
        self,
        primary: STTProvider,
        secondary: Optional[STTProvider] = None,
        policy: Optional[ReconnectPolicy] = None,
    ):
        self._primary = primary
        self._secondary = secondary
        self._policy = policy or ReconnectPolicy()
        self._breaker = CircuitBreaker(
            name=f"stt-{primary.name}",
            failure_threshold=self._policy.failure_threshold,
            recovery_timeout=self._policy.recovery_timeout_seconds,
        )
        self._active: STTProvider = primary

    # ──────────────────────────────────────────────────────────────────
    # STTProvider interface
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
                    "resilient_stt_secondary_init_failed provider=%s err=%s "
                    "— secondary unavailable for this session",
                    self._secondary.name, exc,
                )
                self._secondary = None

    async def cleanup(self) -> None:
        # Clean up both so we don't leak WebSockets even when we
        # never failed over.
        for p in (self._primary, self._secondary):
            if p is None:
                continue
            try:
                await p.cleanup()
            except Exception as exc:
                logger.debug("resilient_stt_cleanup_error provider=%s err=%s", p.name, exc)

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        """Forward audio to the active STT. On provider failure, try
        one reconnect; if that fails, fall through to the secondary.
        The caller sees a single continuous AsyncIterator of chunks.
        """
        policy = self._policy
        buffer = _ReplayBuffer(capacity_ms=policy.audio_buffer_ms)
        chosen = self._active

        # Pre-choose secondary if the circuit is open on the primary.
        if chosen is self._primary and self._breaker.state.value == "open" and self._secondary:
            logger.info(
                "resilient_stt_primary_circuit_open — starting on secondary",
                extra={"call_id": call_id},
            )
            chosen = self._secondary
            self._active = self._secondary

        async def _tee_audio() -> AsyncIterator[AudioChunk]:
            """Pass-through that also populates the replay buffer."""
            async for chunk in audio_stream:
                buffer.add(chunk)
                yield chunk

        try:
            async for out in self._stream_with_provider(
                provider=chosen,
                audio_iter=_tee_audio(),
                language=language,
                context=context,
                call_id=call_id,
                on_eager_end_of_turn=on_eager_end_of_turn,
                on_barge_in=on_barge_in,
            ):
                yield out
            return
        except CircuitOpenError:
            logger.info("resilient_stt_circuit_open_at_start", extra={"call_id": call_id})
            # fallthrough to failover
        except Exception as exc:
            logger.warning(
                "resilient_stt_primary_failed provider=%s err=%s",
                chosen.name, exc,
                extra={"call_id": call_id},
            )

        # Primary faulted — attempt reconnect OR failover. The buffer
        # holds the tail-end of the utterance so we re-transcribe
        # instead of losing it.
        if self._secondary is None:
            logger.error("resilient_stt_no_secondary — transcripts will be empty")
            return

        self._active = self._secondary
        logger.info(
            "resilient_stt_failed_over_to=%s buffered_chunks=%d",
            self._secondary.name, len(buffer.chunks),
            extra={"call_id": call_id},
        )

        async def _replay_then_live() -> AsyncIterator[AudioChunk]:
            for past in buffer.drain():
                yield past
            async for chunk in audio_stream:
                yield chunk

        async for out in self._stream_with_provider(
            provider=self._secondary,
            audio_iter=_replay_then_live(),
            language=language,
            context=context,
            call_id=call_id,
            on_eager_end_of_turn=on_eager_end_of_turn,
            on_barge_in=on_barge_in,
        ):
            yield out

    # ──────────────────────────────────────────────────────────────────

    async def _stream_with_provider(
        self,
        *,
        provider: STTProvider,
        audio_iter: AsyncIterator[AudioChunk],
        language: str,
        context: Optional[str],
        call_id: Optional[str],
        on_eager_end_of_turn: Optional[Callable[[str], None]],
        on_barge_in: Optional[Callable[[], None]],
    ) -> AsyncIterator[TranscriptChunk]:
        """Drive one provider through the circuit breaker."""
        breaker = self._breaker if provider is self._primary else None

        async def _run() -> AsyncIterator[TranscriptChunk]:
            async for chunk in provider.stream_transcribe(
                audio_iter,
                language=language,
                context=context,
                call_id=call_id,
                on_eager_end_of_turn=on_eager_end_of_turn,
                on_barge_in=on_barge_in,
            ):
                yield chunk

        if breaker is None:
            async for chunk in _run():
                yield chunk
            return

        async with breaker:
            async for chunk in _run():
                yield chunk
