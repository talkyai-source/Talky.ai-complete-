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

- **Silence is a failure mode too (2026-08-13).** Every trigger above
  is an *exception*. A socket that connects, accepts audio and simply
  never answers raises nothing, so it fell straight through all of
  them. On 2 of 36 answered calls that day Flux was pre-connected,
  took 400+ chunks, and returned zero events — not one StartOfTurn —
  while the caller talked at RMS 3504 (peak 28988). Nothing failed
  over, because from here a dead stream and a quiet room are the same
  observation: no transcripts.

  They are only distinguishable acoustically, so the wrapper now
  measures the one thing that separates them — VOICED audio going in
  with nothing coming out. That check belongs here rather than in any
  one caller: this is the component whose whole job is keeping
  transcripts flowing, and fixing it here covers telephony, browser
  and ask-AI at once. See ``_SilentStreamWatchdog``.
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
import os
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
    # Seconds of VOICED caller audio with zero transcript events before the
    # stream is declared dead and we fail over. Not wall-clock: a quiet caller
    # never accumulates any of it, so this can never fire on plain silence.
    #
    # 6s is chosen against the two observed dead calls (17s and 19s of wasted
    # audio) and against the re-greet ladder, which starts at 2.5s and is spent
    # by ~15s: firing at 6s salvages the call while the caller is still on the
    # line. Set to 0 to disable the watchdog entirely.
    silent_stream_voiced_seconds: float = 6.0


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


# Matches the threshold audio_ingest already documents on every audio_level
# line it emits: ">500=speech-likely, <100=silence-likely". Deliberately the
# same number so the log a human reads while debugging and the number the code
# acts on cannot drift apart.
_SPEECH_RMS_THRESHOLD = 500.0

# Stride for the RMS estimate. A 40ms/16kHz frame is 640 samples; every 8th
# sample is 80 multiply-adds per frame, ~2k/second per call. Speech energy is
# broadband, so decimating it barely moves the RMS while making the check free
# enough to run on every frame of every call.
_RMS_STRIDE = 8

# How long the agent's echo keeps arriving after it stops speaking. Matches the
# post-TTS unmute tail the pipeline already uses (`_STT_UNMUTE_TAIL_S`, 0.25s),
# which was itself set from SOTA guidance (Coval / Gladia / Deepgram / Retell
# all recommend a 200-500ms decay window on PSTN before trusting the mic again).
# One constant governs both, so the two cannot drift apart.
_ECHO_TAIL_S = float(os.getenv("VOICE_STT_ECHO_TAIL_S", "0.25"))


def _chunk_rms(chunk: AudioChunk, stride: int = _RMS_STRIDE) -> float:
    """Approximate RMS of a 16-bit mono PCM chunk. Returns 0.0 for anything
    it cannot interpret, which fails SAFE: an unreadable chunk contributes no
    voiced time and so can never trip the watchdog on its own."""
    data = getattr(chunk, "data", None)
    if not data or len(data) < 2:
        return 0.0
    try:
        import struct as _struct

        count = len(data) // 2
        samples = _struct.unpack_from(f"<{count}h", data, 0)
    except Exception:
        return 0.0
    stride = max(1, stride)
    total = 0.0
    n = 0
    for i in range(0, count, stride):
        s = samples[i]
        total += s * s
        n += 1
    if not n:
        return 0.0
    return (total / n) ** 0.5


class _SilentStreamWatchdog:
    """Trips when VOICED audio has been fed to an STT stream for long enough
    that a working provider would certainly have answered by now.

    Why voiced-time and not wall-clock: a caller who says nothing produces the
    same empty transcript stream as a provider that has died. Wall-clock cannot
    tell them apart and would fail over on every thoughtful pause. Acoustic
    energy can, and it is the only signal that can.

    Three properties matter and each one is load-bearing:

    * **It re-arms.** ``observe_transcript`` zeroes the counter, so this
      detects a stream that dies at minute nine exactly as well as one that was
      never alive. The observed incident is just the turn-0 case.
    * **It ignores audio it cannot attribute to the caller.** See below — this
      is the property that was wrong until 2026-08-18.
    * **It fails safe.** Anything unparseable scores 0.0 and contributes no
      voiced time, so a malformed chunk can never cause a spurious failover.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    WHOSE VOICE IS IT? (2026-08-18 — the correction that matters)

    The original version discounted audio only while the provider reported
    itself ``muted``. That guard was written for a platform that does not exist
    here: on telephony ``mute_during_tts`` is **False by design**
    (``telephony_settings.py:214``), because muting STT while the agent speaks
    would destroy barge-in. There is not one ``mute()`` call anywhere in the
    telephony path. So ``muted`` was constant-False on every phone call, and the
    echo protection documented above **never once applied**.

    What that cost: on 2026-08-18, six of fourteen answered calls abandoned Flux
    within seconds of answering. In all six the agent was mid-utterance — the
    recording disclosure, the pre-synthesised greeting, or a normal reply — and
    the "voiced caller audio" the watchdog counted was our own TTS returning on
    a 2-wire line at RMS 700-4200. Every trip was a false positive.

    PSTN has no client-side echo cancellation (a browser gets it free from
    getUserMedia; a phone does not), and we have no server-side AEC, so the echo
    genuinely arrives. The fix is therefore NOT to duck the microphone — that is
    the industry's other option and it trades away barge-in, which is the whole
    reason telephony leaves STT live. The fix is to stop **counting** audio we
    cannot attribute.

    While the agent is speaking, line energy is either echo or a real barge-in
    and we cannot tell which without AEC. "Caller talked and got nothing back"
    is therefore unprovable during that window, so the watchdog abstains and
    resumes counting once the agent is quiet. Detection of a genuinely dead
    stream is deferred to the next listening window, which is seconds away
    because a caller who is talking keeps talking.
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """

    def __init__(
        self,
        *,
        voiced_seconds: float,
        rms_threshold: float = _SPEECH_RMS_THRESHOLD,
        echo_tail_seconds: float = _ECHO_TAIL_S,
    ) -> None:
        self._voiced_needed_ms = max(0.0, voiced_seconds) * 1000.0
        self._rms_threshold = rms_threshold
        self._enabled = voiced_seconds > 0
        self._echo_tail_ms = max(0.0, echo_tail_seconds) * 1000.0
        self.voiced_ms = 0.0
        # Voiced audio deliberately NOT counted, so "the guard is working" and
        # "the guard was never wired up" are different observations. The whole
        # class of bug this replaces was invisible precisely because a guard
        # that does nothing and a guard with nothing to do logged identically.
        self.suppressed_ms = 0.0
        self.tripped = False
        # Distance, in un-attributable audio, since the agent last spoke. Used
        # to keep suppressing through the echo decay tail after TTS stops.
        self._tail_remaining_ms = 0.0

    def observe_transcript(self) -> None:
        """The stream answered — it is alive; start the clock over."""
        self.voiced_ms = 0.0

    def observe_audio(
        self,
        chunk: AudioChunk,
        *,
        muted: bool = False,
        agent_speaking: bool = False,
    ) -> bool:
        """Feed one outbound chunk. Returns True once the stream is judged
        dead (and stays True thereafter).

        ``agent_speaking`` is the telephony-truthful form of ``muted``: the
        provider is not muted, but the audio on the line cannot be attributed
        to the caller, so it must not count toward "the caller went unanswered".
        """
        if not self._enabled or self.tripped:
            return self.tripped

        duration_ms = _chunk_duration_ms(chunk)
        if agent_speaking:
            # Re-arm the decay tail for as long as the agent keeps talking.
            self._tail_remaining_ms = self._echo_tail_ms
        elif self._tail_remaining_ms > 0.0:
            self._tail_remaining_ms -= duration_ms

        unattributable = muted or agent_speaking or self._tail_remaining_ms > 0.0
        if _chunk_rms(chunk) < self._rms_threshold:
            return False
        if unattributable:
            self.suppressed_ms += duration_ms
            return False

        self.voiced_ms += duration_ms
        if self.voiced_ms >= self._voiced_needed_ms:
            self.tripped = True
        return self.tripped


class STTStreamSilentError(RuntimeError):
    """The active STT stream accepted voiced audio and never answered.

    Raised into the failover path so a dead-but-not-erroring provider is
    treated exactly like one that raised — including the replay buffer, so the
    utterance the caller was mid-way through is re-transcribed by the secondary
    rather than lost.
    """


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
        # Set by whoever owns the session (see set_agent_speaking_probe). None
        # means "nobody told us", which is reported per call rather than
        # assumed benign — an uninstalled probe is exactly how the previous
        # version of this guard came to be dead code for four days.
        self._agent_speaking_probe: Optional[Callable[[], bool]] = None
        self._probe_errors = 0

    def set_agent_speaking_probe(
        self, probe: Optional[Callable[[], bool]]
    ) -> None:
        """Tell the watchdog how to ask "is the agent talking right now?".

        Deliberately a callable rather than a flag we cache: the answer changes
        many times per second and there must be exactly ONE source of truth for
        it (``CallSession.tts_active``). A mirrored copy would drift, and a
        drifted copy of this particular signal silently re-creates the bug.

        Optional on purpose — the wrapper is constructed by the orchestrator,
        which has the config but not yet the live session, so the session owner
        installs this later. ``stream_transcribe`` reports whether it arrived.
        """
        self._agent_speaking_probe = probe

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

    async def pre_connect(self, call_id: str) -> None:
        """Forward pre-warm to the primary so flipping
        ``STT_FAILOVER_ENABLED=true`` does not silently regress the
        ringing-phase WebSocket pre-handshake (~300ms saved per call).
        Secondary stays cold until failover actually fires — opening
        two Deepgram sockets per call would double quota burn for no
        latency benefit.
        """
        primary_pre = getattr(self._primary, "pre_connect", None)
        if primary_pre is not None:
            await primary_pre(call_id)

    async def mute(self, call_id: str) -> None:
        """Forward mute to whichever provider is live right now. Read
        `self._active` at call time (not cached) so this follows a
        mid-call failover instead of muting an orphaned provider."""
        fn = getattr(self._active, "mute", None)
        if fn is not None:
            await fn(call_id)

    async def unmute(self, call_id: str) -> None:
        fn = getattr(self._active, "unmute", None)
        if fn is not None:
            await fn(call_id)

    def is_muted(self, call_id: str) -> bool:
        fn = getattr(self._active, "is_muted", None)
        return bool(fn(call_id)) if fn is not None else False

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

        # Decide the starting provider for THIS stream fresh, every call — never
        # inherit self._active from a prior stream_transcribe() on this wrapper.
        # self._breaker (failure/success counters, CLOSED/OPEN/HALF_OPEN state) is
        # UNCHANGED and stays shared/process-lifetime — a genuinely-down primary
        # stays deprioritised via the breaker, not via a sticky _active.
        self._active = self._primary
        chosen = self._primary

        watchdog = _SilentStreamWatchdog(
            voiced_seconds=policy.silent_stream_voiced_seconds,
        )

        def _provider_muted(provider: STTProvider) -> bool:
            """True while the provider is deliberately discarding frames, so
            the watchdog does not count our own TTS echo as caller speech.

            NOTE: constant False on telephony — ``mute_during_tts`` is off by
            design there so barge-in works. That is why ``_agent_speaking``
            below exists; do not delete this as redundant, it still covers the
            browser and ask-AI paths where muting IS used.
            """
            if not call_id:
                return False
            fn = getattr(provider, "is_muted", None)
            if fn is None:
                return False
            try:
                return bool(fn(call_id))
            except Exception:
                return False

        def _agent_speaking() -> bool:
            """Is our own TTS on the line right now?

            Fails toward the PREVIOUS behaviour (counting the audio) rather
            than toward suppression, because a broken probe must not be able to
            silently disable dead-stream detection. The failure is counted, and
            the count is reported per call, so "the probe is broken" cannot
            masquerade as "the agent never spoke".
            """
            probe = self._agent_speaking_probe
            if probe is None:
                return False
            try:
                return bool(probe())
            except Exception:
                self._probe_errors += 1
                return False

        # WIRING CHECK, reported once per call. The guard above is worthless if
        # nobody installed the probe, and the entire class of bug it replaces
        # was invisible because a guard that never ran logged exactly like a
        # guard with nothing to do. One grep now answers it:
        #     journalctl ... | grep resilient_stt_echo_guard | grep ABSENT
        logger.info(
            "resilient_stt_echo_guard probe=%s echo_tail_s=%.2f voiced_needed_s=%.1f",
            "installed" if self._agent_speaking_probe is not None else "ABSENT",
            _ECHO_TAIL_S,
            policy.silent_stream_voiced_seconds,
            extra={"call_id": call_id},
        )

        def _emit_audit(outcome: str) -> None:
            """Per-call verdict, written on healthy calls too."""
            logger.info(
                "resilient_stt_audit provider=%s outcome=%s counted_voiced_ms=%.0f "
                "suppressed_ms=%.0f probe=%s probe_errors=%d",
                chosen.name, outcome, watchdog.voiced_ms, watchdog.suppressed_ms,
                "installed" if self._agent_speaking_probe is not None else "ABSENT",
                self._probe_errors,
                extra={"call_id": call_id},
            )

        async def _tee_audio() -> AsyncIterator[AudioChunk]:
            """Pass-through that also populates the replay buffer and feeds the
            silent-stream watchdog.

            Raising from here is what converts "dead but not erroring" into the
            ordinary failover path: the exception travels out through the
            provider's own ``async for`` over this iterator and lands in the
            ``except Exception`` below. A provider that swallows it instead is
            covered by the ``watchdog.tripped`` re-check after the loop, so the
            failover happens either way.
            """
            async for chunk in audio_stream:
                buffer.add(chunk)
                if watchdog.observe_audio(
                    chunk,
                    muted=_provider_muted(chosen),
                    agent_speaking=_agent_speaking(),
                ):
                    logger.error(
                        "resilient_stt_stream_silent provider=%s voiced_s=%.1f "
                        "— %.1fs of caller speech went in and no transcript "
                        "event came back; treating the stream as dead and "
                        "failing over (suppressed_ms=%.0f of agent audio was "
                        "correctly not counted)",
                        chosen.name,
                        policy.silent_stream_voiced_seconds,
                        watchdog.voiced_ms / 1000.0,
                        watchdog.suppressed_ms,
                        extra={"call_id": call_id},
                    )
                    _emit_audit("failover")
                    raise STTStreamSilentError(
                        f"{chosen.name} accepted "
                        f"{watchdog.voiced_ms / 1000.0:.1f}s of voiced audio "
                        f"without emitting a transcript event"
                    )
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
                watchdog.observe_transcript()
                yield out
            # A provider that swallowed the watchdog's exception ends its
            # stream cleanly instead of raising. Without this re-check that
            # would look like a normal end-of-call and return silently — the
            # exact failure this watchdog exists to stop.
            if not watchdog.tripped:
                _emit_audit("healthy")
                return
        except CircuitOpenError:
            logger.info("resilient_stt_circuit_open_at_start", extra={"call_id": call_id})
            # fallthrough to failover
        except STTStreamSilentError:
            pass  # already logged at ERROR above; fall through to failover
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

        # The secondary gets a watchdog too, but a LOGGING one: there is no
        # third provider to fail over to, so tripping it must not raise and
        # kill the stream. Its value is diagnostic — "both engines went deaf"
        # and "the caller genuinely said nothing" produce identical transcripts
        # and, without this line, identical logs.
        secondary_watchdog = _SilentStreamWatchdog(
            voiced_seconds=policy.silent_stream_voiced_seconds,
        )
        secondary_reported = False

        async def _replay_then_live() -> AsyncIterator[AudioChunk]:
            nonlocal secondary_reported
            for past in buffer.drain():
                yield past
            async for chunk in audio_stream:
                if (
                    secondary_watchdog.observe_audio(
                        chunk, muted=_provider_muted(self._secondary)
                    )
                    and not secondary_reported
                ):
                    secondary_reported = True
                    logger.error(
                        "resilient_stt_secondary_also_silent provider=%s "
                        "voiced_s=%.1f — both STT engines accepted caller "
                        "speech without answering; no further failover exists",
                        self._secondary.name,
                        secondary_watchdog.voiced_ms / 1000.0,
                        extra={"call_id": call_id},
                    )
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
            secondary_watchdog.observe_transcript()
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
