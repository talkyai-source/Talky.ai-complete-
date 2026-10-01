"""Bounded STT failover with cancellation-safe call-owned input.

Each provider owns one connection. This wrapper promotes the secondary once on
unexpected failure, replays only buffered uncommitted speech, and propagates a
terminal error if recovery is unavailable. Caller cancellation never retries.
"""
from __future__ import annotations

import asyncio
import collections
from contextlib import aclosing
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
    # Audio replayed into the fallback provider when the active one is
    # declared dead. This MUST cover the watchdog window below, or the very
    # speech that proved the stream was dead is the speech that gets thrown
    # away: at 500ms against a 6s window, a caller who spoke for six seconds
    # had 5.5s of it dropped and had to say everything again. 8s of 16kHz
    # mono PCM is ~256KB per call, which is affordable.
    audio_buffer_ms: int = int(
        os.getenv("STT_REPLAY_BUFFER_MS", "8000")
    )
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
    #
    # NOT wall-clock, but read the caveat above carefully: "a quiet caller
    # never accumulates any of it" is true of a SILENT room, not a noisy one.
    # The counter only ever adds, and steady room noise above the RMS gate
    # never dips below it, so a fan or street noise walks the counter to 6s
    # with nobody speaking and declares a perfectly healthy provider dead.
    # That is what happened on 4 of 17 production sessions in the week to
    # 2026-09-22. The gate below is the defence; this window is env-tunable so
    # it can be widened without a deploy if that is ever not enough.
    silent_stream_voiced_seconds: float = float(
        os.getenv("STT_SILENT_VOICED_SECONDS", "6.0")
    )


class _CallAudioInput:
    """A provider cancellation must not close the caller's live input iterator.

    Exactly one shielded read may be in flight. A replacement sender receives
    its result, including a frame which arrived during the handover. The owning
    wrapper cancels/closes it once the call stream itself ends.
    """
    def __init__(self, source):
        self.source = source.__aiter__()
        self.pending = None
        self.exhausted = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.exhausted:
            raise StopAsyncIteration
        if self.pending is None:
            self.pending = asyncio.ensure_future(self.source.__anext__())
        pending = self.pending
        try:
            result = await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Retain the read for the next provider; only aclose owns it.
            raise
        except StopAsyncIteration:
            self.exhausted = True
            self.pending = None
            raise
        except Exception:
            self.pending = None
            raise
        else:
            self.pending = None
            return result

    async def aclose(self):
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
            self.pending = None
        close = getattr(self.source, "aclose", None)
        if close is not None:
            await close()


@dataclass
class _ReplayBuffer:
    """Sliding buffer of recent audio. Holds at most `capacity_ms`
    worth of AudioChunks based on each chunk's stated duration."""
    capacity_ms: int
    chunks: collections.deque = field(default_factory=collections.deque)
    _total_ms: float = 0.0
    truncated: bool = False

    def add(self, chunk: AudioChunk) -> None:
        duration_ms = _chunk_duration_ms(chunk)
        self.chunks.append((chunk, duration_ms))
        self._total_ms += duration_ms
        while self._total_ms > self.capacity_ms and self.chunks:
            _, dropped_ms = self.chunks.popleft()
            self._total_ms -= dropped_ms
            self.truncated = True

    def drain(self) -> list[AudioChunk]:
        out = [c for c, _ in self.chunks]
        self.chunks.clear()
        self._total_ms = 0.0
        self.truncated = False
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
# What counts as "voiced" for the DESCRIPTIVE audio-level log: a low bar on
# purpose, because that log is for humans reading a call afterwards.
_SPEECH_RMS_THRESHOLD = 500.0

# What counts as "voiced" for the WATCHDOG, which is load-bearing: crossing it
# for long enough tears down a working provider mid-call.
#
# DELIBERATELY THE SAME VALUE, as a knob rather than a change. Raising it is
# the obvious response to a false trip on room noise, and it is the wrong one:
# on the 2026-09-22 session the caller really was saying "hello" into a stream
# that answered nothing, and a higher gate would have delayed that rescue for
# any softly-spoken caller. The fix for a false trip is to make failing over
# cheap (see audio_buffer_ms), not to make the watchdog blind. Exposed as an
# env knob so a specific noisy deployment can be handled without a deploy, and
# so the next person reads this before reaching for it.
_WATCHDOG_RMS_THRESHOLD = float(
    os.getenv("STT_WATCHDOG_RMS_THRESHOLD", str(_SPEECH_RMS_THRESHOLD))
)

# How fast the voiced counter drains while the line is quiet, as a multiple of
# real time. 1.0 means a second of quiet cancels a second of voice, so the
# watchdog needs speech that is more than half voiced to make progress. 0.0
# restores the old monotonic behaviour if a deployment ever needs it back.
_WATCHDOG_DECAY_RATIO = float(os.getenv("STT_WATCHDOG_DECAY_RATIO", "1.0"))

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

# A provider that sent ANY message within this many seconds is alive, whatever
# the silent-stream watchdog's voiced-audio count says. Flux sends a TurnInfo
# roughly every 0.25 s while connected, so 2 s is ~8 missed messages.
_LIVENESS_WINDOW_S = float(os.getenv("STT_WATCHDOG_LIVENESS_S", "2.0"))


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
        rms_threshold: float = _WATCHDOG_RMS_THRESHOLD,
        decay_ratio: float = _WATCHDOG_DECAY_RATIO,
        echo_tail_seconds: float = _ECHO_TAIL_S,
    ) -> None:
        self._voiced_needed_ms = max(0.0, voiced_seconds) * 1000.0
        self._rms_threshold = rms_threshold
        self._decay_ratio = max(0.0, float(decay_ratio))
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

    def clear_trip(self) -> None:
        """Undo a trip that proved false: the provider is still talking."""
        self.tripped = False
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
            # DECAY, not just "ignore". The counter used to be a monotonic sum
            # reset only by a transcript, so it measured the total quiet-line
            # energy a call had EVER contained: six seconds accumulated a
            # hundred milliseconds at a time across several minutes tripped it
            # exactly like six seconds of someone talking into a dead stream.
            # On a line with a little constant hum that is a matter of when,
            # not if, and it tears down a provider that was working.
            #
            # Draining on quiet makes the window mean what its name says:
            # SUSTAINED voiced audio that got no answer. Real speech keeps the
            # counter climbing, because an utterance is voiced far more often
            # than not; an intermittently noisy line drains back to zero
            # between bursts and never arrives.
            self.voiced_ms = max(0.0, self.voiced_ms - duration_ms * self._decay_ratio)
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
        self, audio_stream, language="en", context=None, call_id=None,
        on_eager_end_of_turn=None, on_barge_in=None,
    ) -> AsyncIterator[TranscriptChunk]:
        source = _CallAudioInput(audio_stream)
        try:
            async with aclosing(self._stream_with_recovery(
                source, language=language, context=context, call_id=call_id,
                on_eager_end_of_turn=on_eager_end_of_turn, on_barge_in=on_barge_in,
            )) as stream:
                async for chunk in stream:
                    yield chunk
        finally:
            await source.aclose()

    async def _stream_with_recovery(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        """Promote the secondary once; both failed is a terminal error."""
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
                "suppressed_ms=%.0f probe=%s probe_errors=%d alive_overrides=%d",
                chosen.name, outcome, watchdog.voiced_ms, watchdog.suppressed_ms,
                "installed" if self._agent_speaking_probe is not None else "ABSENT",
                self._probe_errors, alive_overrides,
                extra={"call_id": call_id},
            )

        def _provider_message_age(provider: STTProvider) -> Optional[float]:
            """Seconds since the provider last sent ANY message, or None when
            it cannot say (then the watchdog behaves exactly as before)."""
            fn = getattr(provider, "seconds_since_last_message", None)
            if fn is None:
                return None
            try:
                age = fn(call_id)
            except Exception:
                return None
            return float(age) if isinstance(age, (int, float)) else None

        alive_overrides = 0

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
            nonlocal alive_overrides
            async for chunk in audio_stream:
                buffer.add(chunk)
                if watchdog.observe_audio(
                    chunk,
                    muted=_provider_muted(chosen),
                    agent_speaking=_agent_speaking(),
                ):
                    # A loud line is not a dead stream. Nine "stalls" from
                    # 2026-09-10 to 09-27 (e.g. c79e7f3b, 09-27 19:42:44) were
                    # all on lines whose caller side never went quiet (RMS
                    # 1,000-10,000, clipping at 32,768, versus 7 on a normal
                    # line), so 6 s of "voiced" audio with no words built up
                    # after every agent reply while Flux, correctly, heard no
                    # words in the noise — and no Flux connection had closed or
                    # errored. Failing over then handed the call to Nova, whose
                    # bare VAD cut the agent off on that same noise for 90 s.
                    # A dead stream sends nothing; a live one keeps sending
                    # status messages even with no words. Only the former
                    # fails over.
                    age = _provider_message_age(chosen)
                    if age is not None and age < _LIVENESS_WINDOW_S:
                        alive_overrides += 1
                        if alive_overrides == 1 or alive_overrides % 10 == 0:
                            logger.info(
                                "resilient_stt_watchdog_alive provider=%s "
                                "last_message_age_s=%.2f overrides=%d — loud "
                                "line without words, provider still sending; "
                                "not failing over",
                                chosen.name, age, alive_overrides,
                                extra={"call_id": call_id},
                            )
                        watchdog.clear_trip()
                        yield chunk
                        continue
                    logger.error(
                        "resilient_stt_stream_silent provider=%s voiced_s=%.1f "
                        "— %.1fs of caller speech went in and no transcript "
                        "event came back; treating the stream as dead and "
                        "failing over (suppressed_ms=%.0f of agent audio was "
                        "correctly not counted; last_message_age_s=%s)",
                        chosen.name,
                        policy.silent_stream_voiced_seconds,
                        watchdog.voiced_ms / 1000.0,
                        watchdog.suppressed_ms,
                        "unknown" if age is None else f"{age:.2f}",
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
                if getattr(out, "is_final", False):
                    # Completed caller speech must never be replayed as a new turn.
                    buffer.drain()
                yield out
            # A provider that swallowed the watchdog's exception ends its
            # stream cleanly instead of raising. Without this re-check that
            # would look like a normal end-of-call and return silently — the
            # exact failure this watchdog exists to stop.
            if not watchdog.tripped and audio_stream.exhausted:
                _emit_audit("healthy")
                return
            if not watchdog.tripped:
                raise RuntimeError(f"{chosen.name} ended before caller input exhausted")
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
            raise RuntimeError("STT failed and no secondary provider is available")

        was_muted = _provider_muted(self._active)
        self._active = self._secondary
        if was_muted and call_id:
            await self.mute(call_id)
        # Capture mode is call-owned; apply it to a Flux replacement as well.
        if call_id:
            from app.domain.services.voice_pipeline.capture_mode import is_capture_active
            if is_capture_active(call_id):
                enter = getattr(self._secondary, "enter_capture_mode", None)
                if callable(enter):
                    enter(call_id)
        logger.info(
            "resilient_stt_failed_over_to=%s buffered_chunks=%d",
            self._secondary.name, len(buffer.chunks),
            extra={"call_id": call_id},
        )

        # The final provider must fail visibly too; there is no third attempt.
        secondary_watchdog = _SilentStreamWatchdog(
            voiced_seconds=policy.silent_stream_voiced_seconds,
        )
        secondary_reported = False
        repeat_required = buffer.truncated
        if repeat_required:
            # Invalidate any primary partial immediately. The secondary still
            # consumes the bounded tail to find the lost turn's ending, but its
            # incomplete text must never reach extraction or the LLM.
            yield TranscriptChunk(text="", is_final=False, metadata={"stt_recovery": "reset"})

        def _recovery_eager(text: str) -> None:
            if not repeat_required and on_eager_end_of_turn is not None:
                on_eager_end_of_turn(text)

        async def _replay_then_live() -> AsyncIterator[AudioChunk]:
            nonlocal secondary_reported
            for past in buffer.drain():
                yield past
            async for chunk in audio_stream:
                if (
                    # agent_speaking is NOT optional here. Without it this
                    # counts our own TTS echo as caller speech -- the exact
                    # 2026-08-18 false positive the primary path was fixed for
                    # -- and since this signal is what tells us whether the
                    # FALLBACK is also deaf, an echo-blind version quietly
                    # lies in the one diagnostic used to judge the provider.
                    secondary_watchdog.observe_audio(
                        chunk,
                        muted=_provider_muted(self._secondary),
                        agent_speaking=_agent_speaking(),
                    )
                    and not secondary_reported
                ):
                    age = _provider_message_age(self._secondary)
                    if age is not None and age < _LIVENESS_WINDOW_S:
                        secondary_watchdog.clear_trip()
                        yield chunk
                        continue
                    secondary_reported = True
                    logger.error(
                        "resilient_stt_secondary_also_silent provider=%s "
                        "voiced_s=%.1f — both STT engines accepted caller "
                        "speech without answering; no further failover exists",
                        self._secondary.name,
                        secondary_watchdog.voiced_ms / 1000.0,
                        extra={"call_id": call_id},
                    )
                    raise STTStreamSilentError("Secondary STT accepted speech without responding")
                yield chunk

        async for out in self._stream_with_provider(
            provider=self._secondary,
            audio_iter=_replay_then_live(),
            language=language,
            context=context,
            call_id=call_id,
            on_eager_end_of_turn=_recovery_eager,
            on_barge_in=on_barge_in,
        ):
            secondary_watchdog.observe_transcript()
            if repeat_required:
                if getattr(out, "is_final", False):
                    repeat_required = False
                    yield TranscriptChunk(text="", is_final=False, metadata={"stt_recovery": "repeat_required"})
                continue
            yield out
        if secondary_watchdog.tripped or not audio_stream.exhausted:
            raise RuntimeError("Secondary STT ended before caller input exhausted")

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
